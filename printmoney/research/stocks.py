"""Evidence-limited stock research. Labels rank attention, never forecast returns."""
from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

POLICY_VERSION = "annual-research-v3"
IMPLEMENTATION_VERSION = "stock-research-v4"
EVALUATION_DEFINITION = {"version":"next-open-adjusted-v1", "horizon_sessions":{"days-weeks":21,"months-plus":63},
                         "entry":"first exchange open strictly after original cutoff", "exit":"close counting entry session as one",
                         "round_trip_cost_bps":[10,30], "benchmark":"SPY", "cash_interest":0,
                         "adjustment":"verified endpoint adjusted prices; normalized raw entry open"}
STARTER = {"AAPL": "technology", "MSFT": "technology", "GOOGL": "communication",
           "META": "communication", "PG": "consumer", "KO": "consumer",
           "CAT": "industrial", "HON": "industrial", "XOM": "energy", "CVX": "energy",
           "JNJ": "healthcare", "MRK": "healthcare"}


@dataclass
class ResearchRequest:
    symbols: tuple[str, ...] = ()
    market: str | None = None
    horizon: str | None = None
    risk: str | None = None
    budget: Decimal | str | None = None
    currency: str | None = None
    loss_limit: Decimal | str | None = None
    pe_cap: float = 25
    universe: str | None = None

    def __post_init__(self):
        self.universe = self.universe or ("custom" if self.symbols else "starter")
        if self.universe not in ("custom", "starter", "sp500", "nasdaq100", "sp500-nasdaq100"):
            raise ValueError("Unsupported research universe.")
        named = self.universe in ("sp500", "nasdaq100", "sp500-nasdaq100")
        if named and not self.symbols:
            raise ValueError("Named index universe requires resolved membership symbols.")
        self.default_universe = self.universe != "custom"
        aliases = {"BRK-B": "BRK.B", "BF-B": "BF.B"}
        symbols = (s.strip().upper() for s in (self.symbols or STARTER))
        self.symbols = tuple(dict.fromkeys(aliases.get(s, s) for s in symbols))
        maximum = 1000 if named else 50
        if len(self.symbols) > maximum or any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) for s in self.symbols):
            raise ValueError(f"Use at most {maximum} plain ticker symbols for this universe.")
        if self.horizon not in (None, "intraday", "days-weeks", "months-plus"):
            raise ValueError("Unsupported holding horizon.")
        if self.risk not in (None, "low", "moderate", "high"):
            raise ValueError("Risk must be low, moderate or high.")
        for field in ("budget", "loss_limit"):
            value = getattr(self, field)
            if value is not None:
                try:
                    value = Decimal(str(value))
                    if not value.is_finite() or value <= 0:
                        raise ValueError("Money must be finite and positive.")
                except InvalidOperation as exc:
                    raise ValueError("Invalid money amount.") from exc
                setattr(self, field, value)
        if self.currency is not None:
            self.currency = self.currency.upper()
            if not re.fullmatch(r"[A-Z]{3}", self.currency):
                raise ValueError("Use a three-letter currency code.")
        if number(self.pe_cap) is None or self.pe_cap <= 0:
            raise ValueError("Annual-earnings P/E cap must be finite and positive.")


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def day(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    return value


def _known_time(value, now):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.tzinfo is not None and stamp <= now
    except (TypeError, ValueError):
        return False


def _actuals_observed(ends, sources, now, filed=None):
    """Actual periods must exist by source observation/publication, retaining day precision."""
    if not ends or not all(ends) or not sources:
        return False
    for source in sources:
        if not isinstance(source, dict) or not _known_time(source.get("retrieved_at"), now):
            return False
        receipt = datetime.fromisoformat(source["retrieved_at"]).astimezone(timezone.utc)
        # A filing date alone supplies no intraday acceptance time.
        if any(end > receipt.date() for end in ends) or (filed and filed >= receipt.date()):
            return False
        publication_days = []
        if source.get("published_at") is not None:
            if not _known_time(source["published_at"], receipt):
                return False
            publication_days.append(datetime.fromisoformat(source["published_at"]).astimezone(timezone.utc).date())
        if source.get("published_day") is not None:
            published = day(source["published_day"])
            if not published or published > receipt.date():
                return False
            publication_days.append(published)
        if any(any(end > published for end in ends) or (filed and filed > published)
               for published in publication_days):
            return False
    return True


def _event_verified(event, sources, symbol, now):
    if not isinstance(event,dict) or event.get("verification") != "verified" or event.get("symbol") != symbol:
        return False
    if not day(event.get("date")) or not _known_time(event.get("retrieved_at"),now):
        return False
    source = next((s for s in sources if s.get("id")==event.get("source_id")), None)
    if not source or source.get("kind") not in ("official","issuer") or not safe_url(source.get("url")):
        return False
    if not _known_time(source.get("retrieved_at"),now):
        return False
    return "url" not in event or safe_url(event["url"]) == source["url"]


def _annual_valid(facts, item, sources):
    start, end, filed = day(facts.get("annual_start")), day(facts.get("annual_end")), day(facts.get("filed"))
    cik = number(item.get("issuer_cik"))
    accessions = facts.get("accessions")
    if (not start or not end or not filed or not 320 <= (end-start).days <= 380 or filed < end
            or facts.get("currency") != "USD" or facts.get("form") not in ("10-K","10-K/A")
            or cik is None or cik<=0 or not cik.is_integer() or facts.get("cik") != cik
            or not isinstance(accessions,list) or len(accessions)!=1
            or not isinstance(accessions[0],str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}",accessions[0])):
        return False
    url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
    return any(s.get("id")=="annual" and s.get("kind")=="official" and s.get("url")==url for s in sources)


def risk_context(item: dict) -> dict:
    """An unusual tape and an uncorroborated claim cannot identify wrongdoing."""
    flags = []
    if item.get("anomalous_price") and item.get("price_basis") == "adjusted":
        flags.append({"code": "unusual_price_move", "value": item.get("day_return"),
                      "basis": "adjusted daily move >=10% and >=3 trailing return standard deviations"})
    ratio = number(item.get("volume_ratio"))
    if ratio is not None and ratio >= 3 and not item.get("recent_split"):
        flags.append({"code": "unusual_volume", "value": ratio,
                      "basis": "latest volume / prior twenty-session median >=3"})
    limitations = ["anomalies_do_not_establish_manipulation", "retrieval_is_not_original_publication",
                   "local_observation_is_not_earliest_sighting", "upstream_price_origin_not_independently_verified"]
    if item.get("recent_split"):
        limitations.append("corporate_action_limits")
    return {"assessment": "context_only", "flags": flags,
            "liquidity_usd_20_sessions": number(item.get("dollar_turnover")),
            "price_corroboration": "single_provider" if item.get("last") else "unavailable",
            "fundamental_corroboration": "single_official_source" if item.get("fundamentals") else "unavailable",
            "origin_identified": False, "social_feed": "not_connected", "thai_equities_feed": "not_connected",
            "limitations": limitations,
            "possible_explanations": "Not established; company news, corporate actions or market moves may need investigation."}


def _card(req: ResearchRequest, item: dict, now: datetime) -> dict:
    reasons, warnings = [], list(item.get("warnings") or [])
    sources = []
    for source in item.get("sources") or []:
        source = dict(source)
        url = safe_url(source.get("url"))
        if not url:
            reasons.append("unsafe_source_url")
        source["url"] = url
        source.pop("first_seen_at",None)
        source.pop("first_seen_scope",None)
        source["observed_at"] = source.get("retrieved_at")
        source["observation_scope"] = "local retrieval; not earliest sighting or proof of original publication or author"
        source.setdefault("lineage", [source.get("kind", "unknown"), "Print-Money derived research"])
        sources.append(source)
    if not {"price", "annual"}.issubset({s.get("id") for s in sources if s.get("url")}):
        reasons.append("source_evidence_missing")
    if not _known_time(item.get("fetched_at"), now) or any(not _known_time(s.get("retrieved_at"), now) for s in sources):
        reasons.append("retrieval_time_unverified")
    last = number(item.get("last"))
    quote_day = day(item.get("quote_day"))
    expected = day(item.get("last_completed_session"))
    unsupported = (req.market not in (None, "us") or item.get("instrument_type") != "EQUITY"
                   or item.get("currency") != "USD" or item.get("exchange") not in ("NMS", "NGM", "NCM", "NYQ", "ASE", "BTS"))
    if unsupported:
        reasons.append("unsupported_security" if req.market in (None, "us") else "unsupported_market")
    if last is None or last <= 0 or quote_day is None:
        reasons.append("price_unavailable")
    elif quote_day > now.date():
        reasons.append("future_price")
    if expected is None:
        reasons.append("calendar_unavailable")
    elif quote_day and quote_day != expected:
        reasons.append("stale_price" if quote_day < expected else "uncompleted_session")
    if "observed_completed_session" in item:
        observed_completed = day(item.get("observed_completed_session"))
        if observed_completed is None:
            reasons.append("price_session_unverified")
        elif quote_day and quote_day > observed_completed:
            reasons.append("uncompleted_session")
    if (number(item.get("history_days")) or 0) < 252:
        reasons.append("insufficient_history")
    turnover = number(item.get("dollar_turnover"))
    if turnover is None:
        reasons.append("liquidity_unknown")
    elif turnover < 5_000_000:
        reasons.append("thin_liquidity")
    stretch, month_return = number(item.get("zscore")), number(item.get("month_return"))
    if stretch is None or month_return is None or item.get("price_basis") != "adjusted":
        reasons.append("price_stretch_unverified")
    elif stretch > 2 or month_return > .20:
        reasons.append("price_runup")
    facts = dict(item.get("fundamentals") or {})
    filed, period = day(facts.get("filed")), day(facts.get("annual_end"))
    if not filed or not period:
        reasons.append("annual_facts_unknown")
        facts = {}
    elif filed >= now.date() or period > now.date():
        # SEC companyfacts provides filing dates, not intraday acceptance times.
        reasons.append("future_filing")
        facts = {}
    elif (now.date() - filed).days > 450 or (now.date() - period).days > 450:
        reasons.append("annual_facts_stale")
        facts = {}
    elif not _annual_valid(facts,item,sources):
        reasons.append("annual_facts_invalid")
        facts = {}
    if item.get("identity_conflict"):
        reasons.append("issuer_identity_conflict")
        facts = {}
    annual_sources = [s for s in sources if s.get("id") == "annual"]
    if annual_sources and (any(not _known_time(s.get("retrieved_at"), now) for s in annual_sources)
                           or (filed and period and not _actuals_observed([period], annual_sources, now, filed=filed))):
        reasons.append("annual_source_time_unverified")
        facts = {}
    if period and any(day(d) and period < day(d) <= now.date() for d in item.get("split_dates", [])):
        reasons.append("per_share_basis_unverified")
    income, cash = number(facts.get("net_income")), number(facts.get("operating_cashflow"))
    growth, eps = number(facts.get("revenue_growth")), number(facts.get("diluted_eps"))
    pe = (last / eps if last and eps is not None and eps > 0
          and "per_share_basis_unverified" not in reasons else None)
    accounting = str(item.get("accounting_policy") or "annual_operating_policy")
    if accounting.startswith("unsupported"):
        reasons.append("unsupported_accounting_policy")
        pe = None
    if not facts or any(v is None for v in (income, cash, growth, eps)):
        reasons.append("operating_evidence_incomplete")
    elif income <= 0 and cash <= 0:
        reasons.append("negative_operating_evidence")
    elif income <= 0 or cash <= 0 or growth < 0:
        reasons.append("operating_evidence_mixed")
    if pe is None:
        reasons.append("valuation_unknown")
    elif pe > req.pe_cap:
        reasons.append("valuation_above_screen")
    event = item.get("earnings") if isinstance(item.get("earnings"),dict) else {}
    event = event or {}
    event_day = day(event.get("date"))
    if not event_day or event.get("verification") != "verified":
        reasons.append("earnings_unknown")
    elif not _event_verified(event,sources,item["symbol"],now):
        reasons.append("earnings_evidence_unverified")
    elif event_day <= now.date() + timedelta(days=7):
        reasons.append("earnings_blackout")
    if req.horizon is None:
        reasons.append("horizon_unspecified")
    elif req.horizon == "intraday":
        reasons.append("unsupported_horizon")
    elif req.horizon == "days-weeks":
        catalyst=item.get("verified_catalyst")
        if (not _event_verified(catalyst,sources,item["symbol"],now)
                or not now.date() < day(catalyst.get("date")) <= now.date()+timedelta(days=21)):
            reasons.append("short_horizon_condition_missing")
    reasons = list(dict.fromkeys(reasons))
    if unsupported or "price_unavailable" in reasons or "future_price" in reasons:
        status = "excluded"
    elif any(x in reasons for x in ("stale_price", "calendar_unavailable", "uncompleted_session", "unsafe_source_url",
                                   "source_evidence_missing", "retrieval_time_unverified", "per_share_basis_unverified", "unsupported_accounting_policy",
                                   "issuer_identity_conflict", "annual_source_time_unverified", "price_session_unverified")):
        status = "watch"
    elif any(x in reasons for x in ("thin_liquidity", "negative_operating_evidence")):
        status = "avoid"
    else:
        status = "watch" if reasons else "consider"
    return {"symbol": item["symbol"], "name": str(item.get("name") or item["symbol"]),
            "sector": str(item.get("sector") or STARTER.get(item["symbol"], "unclassified")),
            "industry": item.get("industry"), "indices": deepcopy(item.get("indices") or []),
            "provider_symbol": item.get("provider_symbol") or item["symbol"], "accounting_policy": accounting,
            "status": status, "reasons": reasons, "warnings": warnings,
            "last": last, "currency": item.get("currency"), "quote_day": item.get("quote_day"),
            "price_basis": "raw close", "fetched_at": item.get("fetched_at"),
            "last_completed_session": item.get("last_completed_session"),
            "observed_completed_session": item.get("observed_completed_session"),
            "membership_cik": item.get("membership_cik"), "identity_conflict": deepcopy(item.get("identity_conflict")),
            "annual_facts": facts, "annual_earnings_pe": pe,
            "valuation_basis": "raw close / reported annual diluted EPS; not TTM or forward earnings",
            "day_return": number(item.get("day_return")), "month_return": number(item.get("month_return")),
            "dollar_turnover": turnover, "earnings": event or None,
            "catalyst": deepcopy(item.get("verified_catalyst")),
            "return_basis": item.get("price_basis"), "provider_status": deepcopy(item.get("provider_status") or {}),
            "reported_financials": deepcopy(item.get("reported_financials")), "news": deepcopy(item.get("news")),
            "horizon": req.horizon, "sources": sources, "external_text_trust": "untrusted_literal",
            "manipulation_risk": risk_context(item),
            "entry_condition": {"type": "research_gates", "state": "met" if status == "consider" else "unmet"},
            "review_conditions": ["new company filing", "confirmed earnings release", "changed eligibility gate", "selected horizon reached"],
            "invalidation": ["operating evidence contradicts the case", "liquidity or valuation policy no longer satisfied"],
            "uncertainty": "Unvalidated screening policy; no return forecast, fair-value target or guaranteed loss cap."}


def screen(request: ResearchRequest, observations: list[dict], now: datetime) -> dict:
    if now.tzinfo is None:
        raise ValueError("Research clock must include a timezone.")
    now = now.astimezone(timezone.utc)
    by_symbol = {o["symbol"]: o for o in observations}
    calendar, current_completed = None, None
    if any(day(o.get("last_completed_session")) for o in observations):
        try:
            from .stockdata import USCalendar
            calendar = USCalendar()
            current_completed = calendar.last_completed(now)
        except Exception:
            calendar = None
    cards = []
    for symbol in request.symbols:
        item = dict(by_symbol.get(symbol, {"symbol": symbol}))
        if day(item.get("last_completed_session")):
            # Source receipts are immutable; expected market state is evaluated now.
            item["last_completed_session"] = current_completed
            receipts = [s.get("retrieved_at") for s in item.get("sources", []) if s.get("id") == "price"]
            item["observed_completed_session"] = None
            if calendar and receipts and all(_known_time(r, now) for r in receipts):
                try:
                    item["observed_completed_session"] = calendar.last_completed(min(datetime.fromisoformat(r) for r in receipts))
                except Exception:
                    pass
        cards.append(_card(request, item, now))
    order = {"consider": 0, "watch": 1, "avoid": 2, "excluded": 3}
    cards.sort(key=lambda c: (order[c["status"]], len(c["reasons"]), -(c["dollar_turnover"] or 0), c["symbol"]))
    highlights, sectors = [], {}
    for c in cards:
        sector = c["sector"]
        if sectors.get(sector, 0) < 2:
            highlights.append(c)
            sectors[sector] = sectors.get(sector, 0) + 1
        if len(highlights) == 5:
            break
    required = [f for f in ("market", "horizon", "risk", "budget", "currency", "loss_limit") if getattr(request, f) is None]
    if request.currency and request.currency != "USD":
        required.append("verified_currency_conversion")
    have_prices = any(c["last"] for c in cards)
    have_consider = any(c["status"] == "consider" for c in cards)
    usable = any(c["status"] != "excluded" for c in cards)
    status = "unavailable" if not usable else "degraded" if any(c["reasons"] or c["warnings"] for c in cards) else "ok"
    result = {"schema_version": 1, "generated_at": now.isoformat(), "policy_version": POLICY_VERSION,
              "implementation_version":IMPLEMENTATION_VERSION, "evaluation_definition":deepcopy(EVALUATION_DEFINITION),
              "policy_validation": "unvalidated", "mode": "profile_review" if not required else "general",
              "actionability": "conditional", "required_inputs": required,
              "scenario": {"horizon": request.horizon, "risk": request.risk, "budget_currency": request.currency},
              "personalization": {"allocation": "not_implemented", "risk_sizing": "not_implemented", "fx": "not_connected",
                                  "context": "supplied inputs are presence checks; no suitability or allocation model"},
              "policy": {"annual_earnings_pe_cap": request.pe_cap, "minimum_history_sessions": 252,
                         "minimum_daily_dollar_turnover": 5_000_000, "maximum_annual_age_days": 450,
                         "earnings_blackout_days": 7},
              "system_status": status, "coverage": {"market": "US-listed operating companies",
                  "requested_market": request.market, "default_universe": request.default_universe,
                  "universe": list(request.symbols), "universe_name": request.universe,
                  "starter_selection": "explicit legacy option; two issuers per broad sector" if request.universe == "starter" else None,
                  "social": "not_connected", "thai_equities": "not_connected"},
              "evaluated": cards, "highlights": highlights,
              "abstention": {"active": True,
                  "reason": "required_data_unavailable" if not have_prices else "no_qualifying_case" if not have_consider else "personal_context_missing" if required else "personal_allocation_unavailable"},
              "errors": [{"code": "required_data_unavailable", "remedy": "Check provider connectivity and supported US ticker coverage."}] if not usable else [],
              "ranking": "evidence gates, observed liquidity, stable ticker; at most two highlights per sector; not expected returns"}
    for key in order:
        result[key] = [c for c in cards if c["status"] == key]
    from .stockevidence import add_coverage
    return add_coverage(result, now)


def run_stock_research(request: ResearchRequest, provider=None, now=None, record_dir=None) -> dict:
    """Collect and screen once, with isolated provider failures and no trading side effects."""
    explicit_clock = now is not None
    now = now or datetime.now(timezone.utc)
    owned = provider is None
    if owned:
        from .stockdata import StockProvider
        provider = StockProvider()
    observations = []
    try:
        for symbol in request.symbols:
            if request.market not in (None, "us"):
                observations.append({"symbol": symbol, "warnings": ["Requested market is not connected."]})
                continue
            try:
                observations.append(provider.collect(symbol, now))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                observations.append({"symbol": symbol, "warnings": [f"Provider evidence unavailable: {type(exc).__name__}"]})
    finally:
        if owned and hasattr(provider, "close"):
            provider.close()
    if not explicit_clock:
        now = datetime.now(timezone.utc)
    result = screen(request, observations, now)
    if record_dir is not None:
        from ..util import STATE_DIR
        from .stockhistory import private_path, record
        directory = private_path(record_dir, STATE_DIR / "research")
        result["report_id"] = record(result, directory)
    return result
