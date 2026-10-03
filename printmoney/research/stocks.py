"""Evidence-limited stock research. Labels rank attention, never forecast returns."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

POLICY_VERSION = "annual-research-v1"
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

    def __post_init__(self):
        self.default_universe = not bool(self.symbols)
        self.symbols = tuple(dict.fromkeys(s.strip().upper() for s in (self.symbols or STARTER)))
        if len(self.symbols) > 50 or any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) for s in self.symbols):
            raise ValueError("Use at most 50 plain ticker symbols.")
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
                   "first_seen_only_within_this_local_system", "upstream_price_origin_not_independently_verified"]
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
        source.setdefault("first_seen_at", source.get("retrieved_at"))
        source["first_seen_scope"] = "local retrieval; not proof of original publication or author"
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
    if (number(item.get("history_days")) or 0) < 252:
        reasons.append("insufficient_history")
    turnover = number(item.get("dollar_turnover"))
    if turnover is None:
        reasons.append("liquidity_unknown")
    elif turnover < 5_000_000:
        reasons.append("thin_liquidity")
    if (number(item.get("zscore")) or 0) > 2 or (number(item.get("month_return")) or 0) > .20:
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
    if period and any(day(d) and period < day(d) <= now.date() for d in item.get("split_dates", [])):
        reasons.append("per_share_basis_unverified")
    income, cash = number(facts.get("net_income")), number(facts.get("operating_cashflow"))
    growth, eps = number(facts.get("revenue_growth")), number(facts.get("diluted_eps"))
    pe = last / eps if last and eps is not None and eps > 0 else None
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
    event = item.get("earnings") or {}
    event_day = day(event.get("date"))
    if not event_day or event.get("verification") != "verified":
        reasons.append("earnings_unknown")
    elif event_day < now.date() or event_day <= now.date() + timedelta(days=7):
        reasons.append("earnings_blackout")
    if req.horizon is None:
        reasons.append("horizon_unspecified")
    elif req.horizon == "intraday":
        reasons.append("unsupported_horizon")
    elif req.horizon == "days-weeks" and not item.get("verified_catalyst"):
        reasons.append("short_horizon_condition_missing")
    reasons = list(dict.fromkeys(reasons))
    if unsupported or "price_unavailable" in reasons or "future_price" in reasons:
        status = "excluded"
    elif any(x in reasons for x in ("stale_price", "calendar_unavailable", "uncompleted_session", "unsafe_source_url",
                                   "source_evidence_missing", "retrieval_time_unverified", "per_share_basis_unverified")):
        status = "watch"
    elif any(x in reasons for x in ("thin_liquidity", "negative_operating_evidence")):
        status = "avoid"
    else:
        status = "watch" if reasons else "consider"
    return {"symbol": item["symbol"], "name": str(item.get("name") or item["symbol"]),
            "sector": item.get("sector") or STARTER.get(item["symbol"], "unclassified"),
            "status": status, "reasons": reasons, "warnings": warnings,
            "last": last, "currency": item.get("currency"), "quote_day": item.get("quote_day"),
            "price_basis": "raw close", "fetched_at": item.get("fetched_at"),
            "annual_facts": facts, "annual_earnings_pe": pe,
            "valuation_basis": "raw close / reported annual diluted EPS; not TTM or forward earnings",
            "day_return": number(item.get("day_return")), "month_return": number(item.get("month_return")),
            "dollar_turnover": turnover, "earnings": event or None,
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
    cards = [_card(request, by_symbol.get(s, {"symbol": s}), now) for s in request.symbols]
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
                  "universe": list(request.symbols), "starter_selection": "two established issuers per broad sector; liquidity checked at query time",
                  "social": "not_connected", "thai_equities": "not_connected"},
              "evaluated": cards, "highlights": highlights,
              "abstention": {"active": True,
                  "reason": "required_data_unavailable" if not have_prices else "no_qualifying_case" if not have_consider else "personal_context_missing" if required else "personal_allocation_unavailable"},
              "errors": [{"code": "required_data_unavailable", "remedy": "Check provider connectivity and supported US ticker coverage."}] if not usable else [],
              "ranking": "evidence gates, observed liquidity, stable ticker; at most two highlights per sector; not expected returns"}
    for key in order:
        result[key] = [c for c in cards if c["status"] == key]
    return result


def run_stock_research(request: ResearchRequest, provider=None, now=None, record_dir=None) -> dict:
    """Collect and screen once, with isolated provider failures and no trading side effects."""
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
    result = screen(request, observations, now)
    if record_dir is not None:
        from ..util import STATE_DIR
        from .stockhistory import private_path, record
        directory = private_path(record_dir, STATE_DIR / "research")
        result["report_id"] = record(result, directory)
    return result
