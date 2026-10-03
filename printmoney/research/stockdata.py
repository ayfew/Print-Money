"""Public daily prices and narrow reported-annual SEC facts, with source lineage."""
from __future__ import annotations

import hashlib
import json
import os
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..util import STATE_DIR
from .data import Bar, Series
from .stocks import STARTER, day, number

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
UA = "Print-Money research/1.0 (public project https://github.com/ayfew/Print-Money)"


class USCalendar:
    def __init__(self):
        import exchange_calendars
        self.calendar = exchange_calendars.get_calendar("XNYS")

    def last_completed(self, now: datetime) -> str:
        session = self.calendar.date_to_session(now.astimezone(timezone.utc).date(), direction="previous")
        if now < self.calendar.session_close(session).to_pydatetime():
            session = self.calendar.previous_session(session)
        return session.date().isoformat()


def _source(source_id, url, kind, payload, now):
    return {"id": source_id, "url": url, "kind": kind, "retrieved_at": now.isoformat(),
            "origin_url": None if kind == "aggregator" else url,
            "corroboration": "single_provider" if kind == "aggregator" else "single_official_source",
            "content_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            "lineage": [url, "provider JSON extraction", "Print-Money derived metrics"]}


def parse_chart(payload: dict, symbol: str, now: datetime, calendar=None) -> dict:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise ValueError("No daily chart result.")
    data = result[0]
    meta = data.get("meta") or {}
    if str(meta.get("symbol", "")).upper() != symbol:
        raise ValueError("Provider symbol identity mismatch.")
    warnings = []
    try:
        completed = calendar.last_completed(now) if calendar else None
    except Exception:
        completed = None
    if not completed:
        warnings.append("US session calendar unavailable; new-entry consideration is blocked.")
    ind = data.get("indicators") or {}
    quote = (ind.get("quote") or [{}])[0]
    adjusted = (ind.get("adjclose") or [{}])[0].get("adjclose") or []
    bars = []
    for i, ts in enumerate(data.get("timestamp") or []):
        if not isinstance(ts, (int, float)):
            continue
        stamp = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        if stamp > (completed or now.date().isoformat()):
            continue
        def at(key):
            values = quote.get(key) or []
            return number(values[i]) if i < len(values) else None
        o, h, l, c, v = (at(k) for k in ("open", "high", "low", "close", "volume"))
        if any(x is None or x <= 0 for x in (o, h, l, c)) or v is None or v < 0:
            continue
        adj = number(adjusted[i]) if i < len(adjusted) else None
        bars.append(Bar(int(ts), o, h, l, adj if adj and adj > 0 else c, v, c))
    bars.sort(key=lambda b: b.ts)
    if not bars:
        raise ValueError("No completed valid daily prices.")
    series = Series(symbol, str(meta.get("longName") or meta.get("shortName") or symbol), bars)
    returns = series.daily_returns()
    prices = series.closes
    window = prices[-60:]
    sd = st.stdev(window) if len(window) > 2 else 0
    z = (window[-1] - st.fmean(window)) / sd if sd else 0
    splits = (data.get("events") or {}).get("splits") or {}
    split_stamps = [s["date"] for s in splits.values() if isinstance(s,dict) and number(s.get("date")) is not None
                    and s["date"] <= now.timestamp()]
    recent_split = any(ts >= bars[max(0,len(bars)-21)].ts for ts in split_stamps)
    volumes = [b.volume for b in bars[-21:-1]]
    median = st.median(volumes) if len(volumes) == 20 else 0
    ratio = bars[-1].volume / median if median > 0 and not recent_split else None
    daily = returns[-1] if returns else None
    trailing = returns[-61:-1]
    return_sd = st.stdev(trailing) if len(trailing) >= 20 else None
    adjusted_basis = (len(adjusted) >= len(data.get("timestamp") or [])
                      and all(number(x) is not None and x > 0 for x in adjusted))
    if not adjusted_basis:
        warnings.append("Adjusted-return basis unavailable; corporate-action-sensitive price anomalies suppressed.")
    return {"symbol": symbol, "name": series.name, "sector": STARTER.get(symbol, "unclassified"),
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
            "warnings": warnings, "sources": [_source("price", f"{YAHOO}/{symbol}", "aggregator", payload, now)],
            "bars": [[b.ts,b.open,b.high,b.low,b.close,b.volume,b.raw_close] for b in bars], "earnings": None}


def annual_facts(payload: dict, cutoff: datetime) -> dict:
    """Only compatible full-year US-GAAP duration facts, known before cutoff day."""
    gaap = (payload.get("facts") or {}).get("us-gaap") or {}
    tags = {"net_income": ("NetIncomeLoss",),
            "operating_cashflow": ("NetCashProvidedByUsedInOperatingActivities",),
            "diluted_eps": ("EarningsPerShareDiluted",),
            "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")}
    rows = {}
    for key, alternatives in tags.items():
        unit = "USD/shares" if key == "diluted_eps" else "USD"
        eligible = []
        for tag in alternatives:
            for r in ((gaap.get(tag) or {}).get("units") or {}).get(unit, []):
                start, end, filed = day(r.get("start")), day(r.get("end")), day(r.get("filed"))
                if (start and end and filed and r.get("form") in ("10-K", "10-K/A")
                    and 320 <= (end-start).days <= 380 and end <= cutoff.date() and filed < cutoff.date()
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
    previous = [r for r in rows["revenue"] if day(r["end"]) and (day(anchor["start"])-day(r["end"])).days == 1]
    old = max(previous, key=lambda r:r["filed"]) if previous else None
    revenue = number(selected.get("revenue",{}).get("val"))
    prior = number(old.get("val")) if old else None
    return {"annual_end": anchor["end"], "annual_start": anchor["start"],
            "filed": max(r["filed"] for r in selected.values()), "currency": "USD", "form": "10-K",
            "accessions": sorted(accessions), "cik": payload.get("cik"),
            "net_income": number(selected.get("net_income",{}).get("val")),
            "operating_cashflow": number(selected.get("operating_cashflow",{}).get("val")),
            "diluted_eps": number(selected.get("diluted_eps",{}).get("val")),
            "revenue": revenue, "revenue_growth": revenue/prior-1 if revenue is not None and prior and prior>0 else None,
            "metric_basis": "reported annual filing; no synthetic TTM", "extracted_tags": {k:r["tag"] for k,r in selected.items()}}


class StockProvider:
    def __init__(self, client=None, calendar=None, cache_dir=None):
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
                    return blob["payload"], fetched
            except (ValueError, KeyError):
                pass
        headers = {"User-Agent": os.environ.get("PRINTMONEY_SEC_USER_AGENT", UA)} if "sec.gov" in url else {"User-Agent": UA}
        response = self.client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if path:
            path.parent.mkdir(parents=True,exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({"retrieved_at":now.isoformat(),"payload":payload}),encoding="utf-8")
            temp.replace(path)
        return payload, now

    def collect(self, symbol, now):
        try:
            payload, fetched = self._json(f"{YAHOO}/{symbol}?range=2y&interval=1d&events=div,splits", "", now)
            item = parse_chart(payload,symbol,fetched,self.calendar)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return {"symbol":symbol,"warnings":[f"Daily price provider unavailable: {type(exc).__name__}"]}
        if item["instrument_type"] != "EQUITY" or item["currency"] != "USD":
            return item
        try:
            if self.tickers is None:
                mapping, _ = self._json(TICKERS,"tickers",now,True)
                self.tickers = {str(v["ticker"]).upper():v for v in mapping.values()}
            identity = self.tickers.get(symbol)
            if not identity:
                item["warnings"].append("No SEC issuer identity; unsupported issuer evidence.")
                return item
            cik = int(identity["cik_str"])
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
            submissions, _ = self._json(submissions_url,f"issuer-{cik}",now,True)
            if 6000 <= int(submissions.get("sic") or 0) <= 6799:
                item["instrument_type"] = "UNSUPPORTED_FINANCIAL"
                item["warnings"].append("Financial issuers require a different accounting policy.")
                return item
            if len(submissions.get("tickers") or []) > 1:
                item["warnings"].append("Multiple securities/share classes; annual per-share comparability not verified.")
                return item
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
            payload, fetched = self._json(url,f"facts-{cik}",now,True)
            if int(payload.get("cik") or 0) != cik:
                raise ValueError("Issuer identity mismatch.")
            item["fundamentals"] = annual_facts(payload,now)
            item["sources"].append(_source("annual",url,"official",payload,fetched))
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            item["warnings"].append(f"SEC annual evidence unavailable: {type(exc).__name__}; no earnings calendar is connected.")
        return item
