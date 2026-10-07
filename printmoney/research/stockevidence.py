"""Inspect reported facts and count field gaps without changing evidence into advice."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .stocks import _actuals_observed, _event_verified, _known_time, day, number, safe_url

FIELDS = ("price", "fundamentals", "valuation", "earnings", "news", "risk")
STATUSES = ("available", "missing", "stale", "unsupported", "blocked", "unverified", "not_requested")
BASES = {"GAAP", "non-GAAP", "reported_unspecified"}
IMPORT_FIELDS = {"symbol", "fundamentals", "issuer_cik", "earnings", "verified_catalyst",
                 "reported_financials", "news", "sources"}


def _quality(status, reason):
    return {"status": status, "reason": reason}


def _status(value):
    return value if value in STATUSES else "unverified" if value == "partial" else "missing"


def _sources_known(sources, now):
    if not isinstance(sources, list) or not sources:
        return False
    try:
        _check_import_tree(sources, now)
    except ValueError:
        return False
    for source in sources:
        if (not isinstance(source, dict) or not safe_url(source.get("url"))
                or not _known_time(source.get("retrieved_at"), now)):
            return False
        receipt = datetime.fromisoformat(source["retrieved_at"])
        published = source.get("published_at")
        if published is not None and not _known_time(published, receipt):
            return False
        if source.get("published_day") is not None:
            published_day = day(source["published_day"])
            if not published_day or published_day > min(now.date(), receipt.date()):
                return False
    return True


def _period_known(period, now):
    if not isinstance(period, dict) or period.get("kind") not in ("quarter", "annual", "year_to_date"):
        return False
    end, start = day(period.get("end")), day(period.get("start"))
    if (not end or end > now.date() or (period.get("start") is not None and not start)
            or (start and start > end)):
        return False
    if period.get("kind") == "annual" and start and not 320 <= (end-start).days <= 380:
        return False
    if period.get("currency") != "USD" or period.get("basis") not in BASES:
        return False
    metrics = period.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return False
    for name, metric in metrics.items():
        if name not in ("revenue", "net_income", "operating_cashflow", "diluted_eps") or not isinstance(metric, dict):
            return False
        unit = "USD/share" if name == "diluted_eps" else "USD"
        if number(metric.get("value")) is None or metric.get("unit") != unit or metric.get("basis") not in BASES:
            return False
    return True


def reported_quality(release, now):
    if not isinstance(release, dict):
        return _quality("missing", "no reported financial release")
    if release.get("status") != "available":
        return _quality(_status(release.get("status")), "reported release unavailable or incomplete")
    periods = release.get("periods")
    if (not _sources_known(release.get("sources"), now) or not isinstance(periods, list)
            or not periods or not all(_period_known(p, now) for p in periods)):
        return _quality("unverified", "reported period, unit, basis or source clock is unverified")
    if not _actuals_observed([day(p["end"]) for p in periods], release["sources"], now):
        return _quality("unverified", "reported actual period ends after supporting source receipt/publication")
    latest = max(day(p["end"]) for p in periods)
    if (now.date()-latest).days > 450:
        return _quality("stale", "reported period older than 450 days")
    return _quality("available", "dated reported facts; basis labels retained; annual gate separate")


def _check_import_tree(value, now, receipt=None):
    if isinstance(value, dict):
        own_receipts = []
        for field in ("retrieved_at", "fetched_at", "captured_at"):
            if field in value:
                if not _known_time(value[field], now):
                    raise ValueError("Invalid or future evidence receipt clock.")
                own_receipts.append(datetime.fromisoformat(value[field]))
        nearest = min(own_receipts) if own_receipts else receipt
        limit = min(now, nearest) if nearest is not None else now
        for key, item in value.items():
            if key in ("url", "source_url", "origin_url") and item is not None and not safe_url(item):
                raise ValueError("Unsafe evidence source URL.")
            if key in ("published_at", "updated_at", "as_of", "published_day") and item is not None:
                date_only = key == "published_day" or (key == "as_of" and isinstance(item, str)
                                                       and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item))
                if date_only:
                    if not day(item) or day(item) > limit.astimezone(timezone.utc).date():
                        raise ValueError("Invalid or future evidence publication/as-of day clock.")
                elif not _known_time(item, limit):
                    raise ValueError("Invalid or future evidence publication/update clock.")
            _check_import_tree(item, now, nearest)
    elif isinstance(value, list):
        for item in value:
            _check_import_tree(item, now, receipt)
    elif isinstance(value, float) and number(value) is None:
        raise ValueError("Nonfinite evidence number.")


def load_evidence(path, now):
    """Load explicit context imports; price/identity-provider overrides are forbidden."""
    path = Path(path)
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Evidence import exceeds ten MiB.")
    def reject_constant(value):
        raise ValueError("Nonfinite evidence number: " + value)
    data = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("observations"), list):
        raise ValueError("Evidence import requires schema_version1 and observations.")
    if len(data["observations"]) > 1000:
        raise ValueError("Evidence import exceeds supported universe.")
    result = {}
    for item in data["observations"]:
        if not isinstance(item, dict) or set(item) - IMPORT_FIELDS:
            raise ValueError("Unsupported evidence field; price overrides are forbidden.")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol):
            raise ValueError("Invalid evidence symbol.")
        symbol = {"BRK-B": "BRK.B", "BF-B": "BF.B"}.get(symbol, symbol)
        if symbol in result:
            raise ValueError("Conflicting duplicate evidence symbol.")
        _check_import_tree(item, now)
        if "issuer_cik" in item and _cik(item["issuer_cik"]) is None:
            raise ValueError("Invalid imported issuer identity.")
        if "reported_financials" in item and reported_quality(item["reported_financials"], now)["status"] == "unverified":
            raise ValueError("Unverified reported financial evidence.")
        sources = item.get("sources", [])
        if not isinstance(sources, list) or any(not isinstance(s, dict) for s in sources):
            raise ValueError("Invalid evidence source list.")
        if any(s.get("id") == "price" for s in sources):
            raise ValueError("Evidence field cannot override a price source.")
        ids = [s.get("id") for s in sources]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate evidence source identity.")
        facts = item.get("fundamentals")
        if facts:
            if not isinstance(facts, dict):
                raise ValueError("Invalid annual financial evidence.")
            end, filed = day(facts.get("annual_end")), day(facts.get("filed"))
            annual_sources = [s for s in sources if s.get("id") == "annual"]
            if not filed or not _actuals_observed([end], annual_sources, now, filed=filed):
                raise ValueError("Unverified annual financial evidence clock.")
        result[symbol] = deepcopy(item)
    return result


def _cik(value):
    if isinstance(value, str):
        if not re.fullmatch(r"[0-9]{1,10}", value):
            return None
        value = int(value)
    try:
        value = number(value)
        return int(value) if value is not None and 0 < value < 10**10 and value.is_integer() else None
    except OverflowError:
        return None


def enrich_observations(observations, members, now, imports=None):
    """Attach discovery/context, preserving one security row and provider price truth."""
    by_symbol = {item["symbol"]: item for item in observations}
    cik_counts = Counter(cik for m in members if (cik := _cik(m.get("cik"))) is not None)
    output = []
    for member in members:
        symbol = member["symbol"]
        item = deepcopy(by_symbol.get(symbol, {"symbol": symbol, "warnings": ["Observation missing."]}))
        provider_sector = item.get("sector")
        provider_policy = str(item.get("accounting_policy") or "")
        provider_cik, member_cik = _cik(item.get("issuer_cik")), _cik(member.get("cik"))
        if provider_cik is not None:
            item["issuer_cik"] = provider_cik
        for key in ("name", "sector", "industry", "provider_symbol", "indices"):
            if member.get(key):
                item[key] = deepcopy(member[key])
        item["membership_cik"] = member.get("cik")
        if provider_cik and member_cik and provider_cik != member_cik:
            item["identity_conflict"] = {"membership_cik": member_cik, "provider_cik": provider_cik}
        sectors = [str(s or "").strip().casefold() for s in (member.get("sector"), provider_sector)]
        operating = {"information technology", "technology", "communication services", "communication",
                     "telecommunications", "industrials", "industrial", "consumer", "consumer discretionary",
                     "consumer staples", "energy", "health care", "healthcare", "materials", "basic materials", "utilities"}
        if provider_policy.startswith("unsupported"):
            item["accounting_policy"] = provider_policy
        elif any("financial" in s or s in ("banks", "banking", "insurance") for s in sectors):
            item["accounting_policy"] = "unsupported_financials"
        elif any("real estate" in s or s == "reit" for s in sectors):
            item["accounting_policy"] = "unsupported_real_estate"
        elif "." in symbol or cik_counts.get(member_cik, 0) > 1:
            item["accounting_policy"] = "unsupported_share_class"
        elif any(s in operating for s in sectors):
            item["accounting_policy"] = "annual_operating_policy"
        else:
            item["accounting_policy"] = "unsupported_unknown_classification"
        extra = (imports or {}).get(symbol, {})
        imported_cik = _cik(extra.get("issuer_cik"))
        if "issuer_cik" in extra and imported_cik is None:
            raise ValueError("Invalid imported issuer identity.")
        if imported_cik and any(cik and cik != imported_cik for cik in (provider_cik, member_cik)):
            raise ValueError("Imported issuer identity conflicts with known member/provider identity.")
        for key in IMPORT_FIELDS - {"symbol", "sources"}:
            if key in extra:
                item[key] = deepcopy(extra[key])
        if imported_cik is not None:
            item["issuer_cik"] = imported_cik
        sources = list(item.get("sources") or [])
        for source in extra.get("sources", []):
            existing = next((s for s in sources if s.get("id") == source.get("id")), None)
            if existing and existing != source:
                raise ValueError("Conflicting imported source identity.")
            if not existing:
                sources.append(deepcopy(source))
        item["sources"] = sources
        output.append(item)
    return output


def _news_quality(news, now):
    if not isinstance(news, dict):
        return _quality("not_requested", "news context not collected")
    status = news.get("status")
    if status != "available":
        return _quality(_status(status), "issuer news coverage " + str(status))
    try:
        _check_import_tree(news, now)
    except ValueError:
        return _quality("unverified", "headline publication/update/as-of clock is after receipt/cutoff or invalid")
    items = news.get("items")
    if not isinstance(items, list) or not items:
        return _quality("missing", "no inspectable dated headline")
    for item in items:
        if (not isinstance(item, dict) or not safe_url(item.get("url"))
                or not _known_time(item.get("published_at"), now)
                or not _known_time(item.get("retrieved_at"), now)):
            return _quality("unverified", "headline link/publication/retrieval clock unverified")
    latest = max(datetime.fromisoformat(item["published_at"]).date() for item in items)
    return _quality("stale" if (now.date()-latest).days > 90 else "available",
                    "issuer headline context; does not qualify earnings/catalysts")


def add_coverage(report, now):
    """Every row contributes once to each mutually exclusive field denominator."""
    cards = report.get("evaluated", [])
    metrics = {"total": len(cards), "status_counts": dict(Counter(c["status"] for c in cards)),
               "fields": {field: {status: 0 for status in STATUSES} for field in FIELDS},
               "meaning": "evidence availability, not recommendation accuracy or complete financial coverage"}
    for card in cards:
        quality = {}
        sources = card.get("sources") or []
        price_sources = [s for s in sources if s.get("id") == "price"]
        providers = card.get("provider_status") or {}
        quote = day(card.get("quote_day"))
        last = number(card.get("last"))
        if last is None or last <= 0 or not quote:
            quality["price"] = _quality("blocked" if providers.get("price") == "blocked" else "missing", "daily price unavailable")
        elif (not _sources_known(price_sources, now) or not _known_time(card.get("fetched_at"), now) or quote > now.date()
              or any(r in card.get("reasons", []) for r in ("calendar_unavailable", "price_session_unverified"))):
            quality["price"] = _quality("unverified", "price identity/source/date clock unverified")
        elif any(reason in card.get("reasons", []) for reason in ("stale_price", "uncompleted_session")):
            quality["price"] = _quality("stale", "quote differs from expected completed exchange session")
        else:
            quality["price"] = _quality("available", "observed daily raw close; returns have separate basis")
        facts = card.get("annual_facts") or {}
        account = str(card.get("accounting_policy") or "annual_operating_policy")
        reported = reported_quality(card.get("reported_financials"), now)
        reasons = card.get("reasons", [])
        if any(r in reasons for r in ("issuer_identity_conflict", "annual_source_time_unverified")):
            quality["fundamentals"] = _quality("unverified", "annual issuer identity or source receipt unverified")
        elif facts and any(number(facts.get(k)) is not None for k in ("net_income", "revenue", "diluted_eps")):
            quality["fundamentals"] = _quality("available", "supported reported annual filing facts")
        elif reported["status"] != "missing":
            quality["fundamentals"] = reported
        elif account.startswith("unsupported"):
            quality["fundamentals"] = _quality("unsupported", "sector or share-class accounting policy unavailable")
        else:
            quality["fundamentals"] = _quality("blocked" if providers.get("annual") == "blocked" else "missing", "supported annual evidence unavailable")
        if account.startswith("unsupported"):
            quality["valuation"] = _quality("unsupported", "annual operating-company P/E policy not applicable")
        elif any(r in reasons for r in ("issuer_identity_conflict", "annual_source_time_unverified", "per_share_basis_unverified")):
            quality["valuation"] = _quality("unverified", "annual denominator identity, receipt or per-share comparability unverified")
        elif number(card.get("annual_earnings_pe")) is not None:
            quality["valuation"] = _quality("available", "raw close / supported annual diluted EPS, not TTM/fair value")
        elif number(facts.get("diluted_eps")) is not None and facts["diluted_eps"] <= 0:
            quality["valuation"] = _quality("unsupported", "nonpositive annual EPS cannot support positive P/E")
        else:
            quality["valuation"] = _quality("missing", "no compatible annual denominator; quarter facts not annualized")
        earnings = card.get("earnings")
        if _event_verified(earnings, sources, card["symbol"], now):
            quality["earnings"] = _quality("stale" if day(earnings["date"]) <= now.date() else "available", "source-backed event date; blackout gate separate")
        else:
            quality["earnings"] = _quality("unverified" if earnings else "missing", "verified next earnings evidence unavailable")
        quality["news"] = _news_quality(card.get("news"), now)
        quality["risk"] = _quality("available" if quality["price"]["status"] == "available" and card.get("return_basis") == "adjusted"
                                   and number(card.get("dollar_turnover")) is not None else "unverified",
                                   "price/volume context only; no manipulation inference or independent source corroboration")
        card["data_quality"] = quality
        for field, value in quality.items():
            metrics["fields"][field][value["status"]] += 1
    report["coverage_metrics"] = metrics
    return report
