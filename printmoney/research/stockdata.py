"""Public daily prices and narrow reported-annual SEC facts, with source lineage."""
from __future__ import annotations

import hashlib
import json
import os
import statistics as st
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..util import STATE_DIR
from .data import Bar, Series
from .stocks import STARTER, day, number
from .stockbatch import RequestGate

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
UA = "Print-Money research/1.0 (public project https://github.com/ayfew/Print-Money)"
CHART_ALIASES = {"BRK.B": "BRK-B", "BF.B": "BF-B"}


class USCalendar:
    def __init__(self):
        import exchange_calendars
        self.calendar = exchange_calendars.get_calendar("XNYS")

    def last_completed(self, now: datetime) -> str:
        session = self.calendar.date_to_session(now.astimezone(timezone.utc).date(), direction="previous")
        if now < self.calendar.session_close(session).to_pydatetime():
            session = self.calendar.previous_session(session)
        return session.date().isoformat()
    def is_session(self, stamp: str) -> bool:
        return bool(self.calendar.is_session(stamp))


def _source(source_id, url, kind, payload, now):
    return {"id": source_id, "url": url, "kind": kind, "retrieved_at": now.isoformat(),
            "origin_url": None if kind == "aggregator" else url,
            "corroboration": "single_provider" if kind == "aggregator" else "single_official_source",
            "content_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            "lineage": [url, "provider JSON extraction", "Print-Money derived metrics"]}


def _object(value):
    if not isinstance(value,dict):
        raise ValueError("Provider JSON object required.")
    return value


def _array(value):
    if value is None:
        return []
    if not isinstance(value,list):
        raise ValueError("Provider JSON array required.")
    return value


def _timestamp(value):
    if number(value) is None:
        return None
    try:
        return datetime.fromtimestamp(value,timezone.utc)
    except (ValueError,OSError,OverflowError):
        return None


def parse_chart(payload: dict, symbol: str, now: datetime, calendar=None) -> dict:
    result = _array(_object(_object(payload).get("chart")).get("result"))
    if not result:
        raise ValueError("No daily chart result.")
    data = _object(result[0])
    meta = _object(data.get("meta",{}))
    provider_symbol = CHART_ALIASES.get(symbol, symbol)
    if str(meta.get("symbol", "")).upper() != provider_symbol:
        raise ValueError("Provider symbol identity mismatch.")
    warnings = []
    try:
        completed = calendar.last_completed(now) if calendar else None
    except Exception:
        completed = None
    if not completed:
        warnings.append("US session calendar unavailable; new-entry consideration is blocked.")
    ind = _object(data.get("indicators",{}))
    quote = _object((_array(ind.get("quote")) or [{}])[0])
    adjusted = _array(_object((_array(ind.get("adjclose")) or [{}])[0]).get("adjclose"))
    bars, adjustment_valid, sessions = [], {}, {}
    timestamps=_array(data.get("timestamp"))
    for i, ts in enumerate(timestamps):
        stamp_time=_timestamp(ts)
        if stamp_time is None:
            warnings.append("Malformed daily timestamp omitted.")
            continue
        stamp = stamp_time.date().isoformat()
        if stamp > (completed or now.date().isoformat()):
            continue
        if calendar and hasattr(calendar,"is_session"):
            try:
                if not calendar.is_session(stamp):
                    continue
            except (ValueError,KeyError):
                continue
        def at(key):
            values = _array(quote.get(key))
            return number(values[i]) if i < len(values) else None
        o, h, l, c, v = (at(k) for k in ("open", "high", "low", "close", "volume"))
        if any(x is None or x <= 0 for x in (o, h, l, c)) or v is None or v < 0:
            continue
        adj = number(adjusted[i]) if i < len(adjusted) else None
        bar=Bar(int(ts), o, h, l, adj if adj and adj > 0 else c, v, c)
        verified=adj is not None and adj>0
        if stamp in sessions:
            old=sessions[stamp]
            if ((old.open,old.high,old.low,old.close,old.volume,old.raw_close)!=(o,h,l,bar.close,v,c)
                    or adjustment_valid[old.ts]!=verified):
                raise ValueError("Conflicting duplicate exchange-session rows.")
            warnings.append("Duplicate daily row removed; it is not another exchange session.")
            continue
        sessions[stamp]=bar
        bars.append(bar)
        adjustment_valid[int(ts)] = verified
    bars.sort(key=lambda b: b.ts)
    if not bars:
        raise ValueError("No completed valid daily prices.")
    series = Series(symbol, str(meta.get("longName") or meta.get("shortName") or symbol), bars)
    returns = series.daily_returns()
    prices = series.closes
    window = prices[-60:]
    sd = st.stdev(window) if len(window) > 2 else 0
    z = (window[-1] - st.fmean(window)) / sd if sd else 0
    splits = _object(_object(data.get("events",{})).get("splits",{}))
    split_stamps = [s["date"] for s in splits.values() if isinstance(s,dict) and _timestamp(s.get("date")) is not None
                    and s["date"] <= now.timestamp()]
    recent_split = any(ts >= bars[max(0,len(bars)-21)].ts for ts in split_stamps)
    volumes = [b.volume for b in bars[-21:-1]]
    median = st.median(volumes) if len(volumes) == 20 else 0
    ratio = bars[-1].volume / median if median > 0 and not recent_split else None
    daily = returns[-1] if returns else None
    trailing = returns[-61:-1]
    return_sd = st.stdev(trailing) if len(trailing) >= 20 else None
    adjusted_basis = (len(adjusted) >= len(timestamps)
                      and all(number(x) is not None and x > 0 for x in adjusted))
    if not adjusted_basis:
        warnings.append("Adjusted-return basis unavailable; corporate-action-sensitive price anomalies suppressed.")
    return {"symbol": symbol, "provider_symbol": provider_symbol, "name": series.name, "sector": STARTER.get(symbol, "unclassified"),
            "instrument_type": meta.get("instrumentType"), "exchange": meta.get("exchangeName"),
            "currency": meta.get("currency"), "exchange_timezone": meta.get("exchangeTimezoneName"),
            "quote_day": bars[-1].date.date().isoformat(), "last_completed_session": completed,
            "fetched_at": now.isoformat(), "last": bars[-1].raw_close,
            "history_days": len(bars), "day_return": daily,
            "month_return": prices[-1]/prices[-22]-1 if len(prices)>21 else None,
            "zscore": z, "dollar_turnover": st.fmean(b.raw_close*b.volume for b in bars[-20:]) if len(bars)>=20 else None,
            "volume_ratio": ratio, "recent_split": recent_split,
            "split_dates": [datetime.fromtimestamp(ts,timezone.utc).date().isoformat() for ts in split_stamps],
            "price_basis": "adjusted" if adjusted_basis else "raw_unverified_actions",
            "anomalous_price": bool(adjusted_basis and daily is not None and return_sd is not None and abs(daily)>=.10 and abs(daily)>=3*return_sd),
            "warnings": list(dict.fromkeys(warnings)), "sources": [_source("price", f"{YAHOO}/{provider_symbol}", "aggregator", payload, now)],
            "bars": [[b.ts,b.open,b.high,b.low,b.close,b.volume,b.raw_close,adjustment_valid[b.ts]] for b in bars], "earnings": None}


def annual_facts(payload: dict, cutoff: datetime) -> dict:
    """Only compatible full-year US-GAAP duration facts, known before cutoff day."""
    gaap = _object(_object(_object(payload).get("facts",{})).get("us-gaap",{}))
    tags = {"net_income": ("NetIncomeLoss",),
            "operating_cashflow": ("NetCashProvidedByUsedInOperatingActivities",),
            "diluted_eps": ("EarningsPerShareDiluted",),
            "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")}
    rows = {}
    for key, alternatives in tags.items():
        unit = "USD/shares" if key == "diluted_eps" else "USD"
        eligible = []
        for tag in alternatives:
            for r in _array(_object(_object(gaap.get(tag,{})).get("units",{})).get(unit)):
                r=_object(r)
                start, end, filed = day(r.get("start")), day(r.get("end")), day(r.get("filed"))
                if (start and end and filed and r.get("form") in ("10-K", "10-K/A")
                    and 320 <= (end-start).days <= 380 and end <= filed < cutoff.date()
                    and number(r.get("val")) is not None):
                    eligible.append(dict(r, tag=tag))
            if eligible:
                break
        rows[key] = eligible
    anchors = rows["net_income"]
    if not anchors:
        return {}
    anchor = max(anchors, key=lambda r: (r["end"],r["filed"]))
    selected = {}
    for key, candidates in rows.items():
        compatible = [r for r in candidates if r["end"] == anchor["end"] and r["start"] == anchor["start"]]
        if compatible:
            selected[key] = max(compatible, key=lambda r: r["filed"])
    # If concepts come from different filing versions, refuse to synthesize a statement.
    accessions = {r.get("accn") for r in selected.values()}
    if len(accessions) != 1 or None in accessions:
        return {}
    previous = [r for r in rows["revenue"] if day(r["end"]) and (day(anchor["start"])-day(r["end"])).days == 1
                and r.get("accn")==anchor.get("accn") and r.get("tag")==selected.get("revenue",{}).get("tag")]
    old = max(previous, key=lambda r:r["filed"]) if previous and len({r["val"] for r in previous})==1 else None
    revenue = number(selected.get("revenue",{}).get("val"))
    prior = number(old.get("val")) if old else None
    return {"annual_end": anchor["end"], "annual_start": anchor["start"],
            "filed": max(r["filed"] for r in selected.values()), "currency": "USD", "form": "10-K",
            "accessions": sorted(accessions), "cik": payload.get("cik"),
            "net_income": number(selected.get("net_income",{}).get("val")),
            "operating_cashflow": number(selected.get("operating_cashflow",{}).get("val")),
            "diluted_eps": number(selected.get("diluted_eps",{}).get("val")),
            "revenue": revenue, "revenue_growth": revenue/prior-1 if revenue is not None and prior and prior>0 else None,
            "revenue_comparison": {"prior_value":prior,"start":old["start"],"end":old["end"],
                                   "filed":old["filed"],"accession":old["accn"]} if old else None,
            "metric_basis": "reported annual filing; no synthetic TTM", "extracted_tags": {k:r["tag"] for k,r in selected.items()}}


class StockProvider:
    def __init__(self, client=None, calendar=None, cache_dir=None, request_gate=None, sec_enabled=True, clock=None):
        self.own_client = client is None
        self.client = client or httpx.Client(timeout=8, headers={"User-Agent": UA})
        self.cache_dir = Path(cache_dir) if cache_dir else STATE_DIR/"research"/"cache" if self.own_client else None
        self.calendar = calendar
        if calendar is None:
            try:
                self.calendar = USCalendar()
            except (ImportError, ValueError):
                self.calendar = None
        self.tickers = None
        self.request_gate = request_gate if request_gate is not None else RequestGate()
        self.sec_enabled = sec_enabled
        self.clock = clock

    def _receipt(self, now):
        stamp = self.clock() if self.clock is not None else datetime.now(timezone.utc) if self.own_client else now
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Provider receipt clock must be timezone aware.")
        return stamp

    def _status(self, url, exc):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return "blocked" if status in (401, 403, 429) or self.request_gate.is_blocked(url) else "missing"

    def close(self):
        if self.own_client:
            self.client.close()

    def _json(self, url, key, now, cache=False):
        path = self.cache_dir / f"{key}.json" if self.cache_dir and cache else None
        if path and path.exists():
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
                fetched = datetime.fromisoformat(blob["retrieved_at"])
                if 0 <= (now-fetched).total_seconds() < 21600:
                    return _object(blob["payload"]), fetched
            except (OSError, ValueError, KeyError, TypeError):
                pass
        headers = {"User-Agent": os.environ.get("PRINTMONEY_SEC_USER_AGENT", UA)} if "sec.gov" in url else {"User-Agent": UA}
        self.request_gate.acquire(url)
        response = self.client.get(url, headers=headers, follow_redirects=False)
        fetched = self._receipt(now)
        self.request_gate.block(url, response.status_code)
        response.raise_for_status()
        payload = _object(response.json())
        if path:
            path.parent.mkdir(parents=True,exist_ok=True)
            temp = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
                    temp = Path(handle.name)
                    json.dump({"retrieved_at":fetched.isoformat(),"payload":payload}, handle, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, path)
            except OSError:
                pass
            finally:
                if temp:
                    temp.unlink(missing_ok=True)
        return payload, fetched

    def collect(self, symbol, now):
        provider_symbol = CHART_ALIASES.get(symbol, symbol)
        price_url = f"{YAHOO}/{provider_symbol}?range=2y&interval=1d&events=div,splits"
        try:
            payload, fetched = self._json(price_url, "", now)
            item = parse_chart(payload,symbol,fetched,self.calendar)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return {"symbol":symbol,"provider_symbol":provider_symbol,"warnings":[f"Daily price provider unavailable: {type(exc).__name__}"],
                    "provider_status":{"price":self._status(price_url,exc),"annual":"blocked" if not self.sec_enabled else "missing"}}
        item["provider_status"] = {"price":"available","annual":"missing"}
        if item["instrument_type"] != "EQUITY" or item["currency"] != "USD":
            item["provider_status"]["annual"] = "unsupported"
            return item
        if not self.sec_enabled:
            item["provider_status"]["annual"] = "blocked"
            item["warnings"].append("Broad SEC collection disabled; reported annual evidence unavailable.")
            return item
        sec_url = TICKERS
        try:
            if self.tickers is None:
                mapping, _ = self._json(TICKERS,"tickers",now,True)
                self.tickers = {str(_object(v)["ticker"]).upper():v for v in mapping.values()}
            identity = self.tickers.get(symbol)
            if not identity:
                item["provider_status"]["annual"] = "unsupported"
                item["warnings"].append("No SEC issuer identity; unsupported issuer evidence.")
                return item
            cik = int(identity["cik_str"])
            item["issuer_cik"] = cik
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
            sec_url = submissions_url
            submissions, _ = self._json(submissions_url,f"issuer-{cik}",now,True)
            if 6000 <= int(submissions.get("sic") or 0) <= 6799:
                item["instrument_type"] = "UNSUPPORTED_FINANCIAL"
                item["provider_status"]["annual"] = "unsupported"
                item["warnings"].append("Financial issuers require a different accounting policy.")
                return item
            if len(submissions.get("tickers") or []) > 1:
                item["provider_status"]["annual"] = "unsupported"
                item["warnings"].append("Multiple securities/share classes; annual per-share comparability not verified.")
                return item
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
            sec_url = url
            payload, fetched = self._json(url,f"facts-{cik}",now,True)
            if int(payload.get("cik") or 0) != cik:
                raise ValueError("Issuer identity mismatch.")
            item["fundamentals"] = annual_facts(payload,now)
            item["provider_status"]["annual"] = "available" if item["fundamentals"] else "missing"
            item["sources"].append(_source("annual",url,"official",payload,fetched))
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            item["provider_status"]["annual"] = self._status(sec_url,exc)
            item["warnings"].append(f"SEC annual evidence unavailable: {type(exc).__name__}; no earnings calendar is connected.")
        return item
