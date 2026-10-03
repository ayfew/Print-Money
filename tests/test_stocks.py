"""Stock research must expose evidence limits rather than manufacture a trade."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def api():
    try:
        from printmoney.research import stocks
    except ImportError:
        pytest.fail("on-demand stock research is missing")
    return stocks


def observation(symbol="MSFT", **changes):
    item = {
        "symbol": symbol, "name": "Example operating company", "sector": "technology",
        "instrument_type": "EQUITY", "currency": "USD", "exchange": "NMS", "issuer_cik":789019,
        "quote_day": "2026-10-02", "last_completed_session": "2026-10-02",
        "fetched_at": "2026-10-03T12:00:00+00:00", "last": 100.0,
        "history_days": 400, "day_return": .01, "month_return": .03,
        "zscore": .5, "dollar_turnover": 80_000_000, "volume_ratio": 1.0,
        "price_basis": "adjusted", "anomalous_price": False,
        "fundamentals": {"annual_end": "2026-06-30", "annual_start":"2025-07-01", "filed": "2026-08-01",
                         "currency":"USD", "form":"10-K", "cik":789019,
                         "accessions":["0000789019-26-000001"],
                         "net_income": 1000, "operating_cashflow": 1200,
                         "diluted_eps": 5, "revenue_growth": .10},
        "earnings": {"date": "2026-10-28", "verification": "verified", "symbol":symbol,
                     "retrieved_at":"2026-10-03T12:00:00+00:00", "source_id":"earnings"},
        "sources": [{"id": "price", "url": "https://query1.finance.yahoo.com/v8/finance/chart/MSFT",
                     "kind": "aggregator", "retrieved_at": "2026-10-03T12:00:00+00:00"},
                    {"id": "annual", "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",
                     "kind": "official", "retrieved_at": "2026-10-03T12:00:00+00:00"},
                    {"id":"earnings","url":"https://www.microsoft.com/en-us/Investor/earnings",
                     "kind":"official","retrieved_at":"2026-10-03T12:00:00+00:00"}],
    }
    item.update(changes)
    if item.get("verified_catalyst") is True:
        item["verified_catalyst"]={"date":"2026-10-20","verification":"verified","symbol":symbol,
                                  "source_id":"earnings","retrieved_at":"2026-10-03T12:00:00+00:00"}
    return item


def report(item=None, **request):
    s = api()
    return s.screen(s.ResearchRequest(symbols=("MSFT",), horizon="months-plus", **request),
                    [item or observation()], NOW)


def test_no_symbol_query_has_diverse_documented_research_defaults():
    s = api()
    req = s.ResearchRequest()
    assert set(req.symbols) == {"AAPL", "MSFT", "GOOGL", "META", "PG", "KO", "CAT", "HON", "XOM", "CVX", "JNJ", "MRK"}
    r = s.screen(req, [observation(earnings=None)], NOW)
    assert r["highlights"][0]["status"] == "watch"
    assert r["coverage"]["market"] == "US-listed operating companies"
    assert r["mode"] == "general" and "budget" not in r


def test_verified_case_is_conditional_research_without_a_synthetic_budget():
    r = report()
    assert r["evaluated"][0]["status"] == "consider"
    assert r["evaluated"][0]["annual_earnings_pe"] == 20
    assert r["actionability"] == "conditional"
    assert r["policy_validation"] == "unvalidated"


def test_valuation_cap_is_explicit_configurable_policy_not_a_price_target():
    s = api()
    low = s.screen(s.ResearchRequest(symbols=("MSFT",), horizon="months-plus", pe_cap=15), [observation()], NOW)
    assert low["watch"] and "valuation_above_screen" in low["watch"][0]["reasons"]
    assert low["policy"]["annual_earnings_pe_cap"] == 15
    with pytest.raises(ValueError):
        s.ResearchRequest(pe_cap=float("nan"))


@pytest.mark.parametrize("changes,reason", [
    ({"sources": []}, "source_evidence_missing"),
    ({"sources": [{"id":"price","url":"https://[broken"}]}, "unsafe_source_url"),
    ({"fetched_at":"2026-10-04T00:00:00+00:00"}, "retrieval_time_unverified"),
    ({"split_dates":["2026-08-31"]}, "per_share_basis_unverified"),
])
def test_unverified_lineage_clock_or_split_eps_comparability_blocks_consider(changes,reason):
    card = report(observation(**changes))["evaluated"][0]
    assert card["status"] == "watch" and reason in card["reasons"]


def test_supplied_profile_does_not_claim_an_allocation_or_guaranteed_risk_model():
    r = report(market="us",risk="low",budget="100",currency="USD",loss_limit="10")
    assert r["personalization"]["allocation"] == "not_implemented"
    assert r["abstention"]["active"] and r["abstention"]["reason"] == "personal_allocation_unavailable"


@pytest.mark.parametrize("changes", [
    {"zscore":None,"month_return":None},
    {"zscore":float("nan"),"month_return":"bad"},
    {"price_basis":"raw_unverified_actions"},
])
def test_missing_stretch_metrics_or_adjustment_basis_never_pass_a_gate(changes):
    card=report(observation(**changes))["evaluated"][0]
    assert card["status"]=="watch" and "price_stretch_unverified" in card["reasons"]


@pytest.mark.parametrize("change", [
    {"currency":"EUR"}, {"filed":"2026-06-01"}, {"cik":42}, {"accessions":[]}, {"form":"10-Q"},
])
def test_imported_annual_evidence_must_have_compatible_identity_period_currency_and_accession(change):
    item=observation()
    item["fundamentals"].update(change)
    card=report(item)["evaluated"][0]
    assert card["status"]=="watch" and "annual_facts_invalid" in card["reasons"]


@pytest.mark.parametrize("change", [
    {"retrieved_at":"2026-10-04T00:00:00+00:00"}, {"source_id":"missing"},
    {"url":"javascript:buy()"}, {"symbol":"AAPL"},
])
def test_verified_word_does_not_replace_earnings_provenance_and_cutoff(change):
    item=observation()
    item["earnings"].update(change)
    card=report(item)["evaluated"][0]
    assert card["status"]=="watch" and "earnings_evidence_unverified" in card["reasons"]


def test_truthy_catalyst_text_does_not_qualify_a_short_horizon():
    s=api()
    r=s.screen(s.ResearchRequest(symbols=("MSFT",),horizon="days-weeks"),
               [observation(verified_catalyst="not_verified")],NOW)
    assert r["watch"] and "short_horizon_condition_missing" in r["watch"][0]["reasons"]


@pytest.mark.parametrize("changes,reason", [
    ({"quote_day": "2026-09-30"}, "stale_price"),
    ({"earnings": None}, "earnings_unknown"),
    ({"earnings": {"date": "2026-10-08", "verification": "verified", "symbol":"MSFT",
                   "source_id":"earnings","retrieved_at":"2026-10-03T12:00:00+00:00"}}, "earnings_blackout"),
    ({"history_days": 50}, "insufficient_history"),
    ({"zscore": 2.5}, "price_runup"),
    ({"last_completed_session": None}, "calendar_unavailable"),
])
def test_missing_or_delayed_evidence_cannot_qualify(changes, reason):
    card = report(observation(**changes))["evaluated"][0]
    assert card["status"] == "watch"
    assert reason in card["reasons"]


def test_future_filing_is_not_current_operating_evidence():
    item = observation()
    item["fundamentals"]["filed"] = "2026-10-04"
    card = report(item)["evaluated"][0]
    assert card["status"] == "watch" and "future_filing" in card["reasons"]
    assert card["annual_earnings_pe"] is None


def test_unsupported_instrument_is_excluded_not_an_avoid_thesis():
    card = report(observation(instrument_type="ETF"))["evaluated"][0]
    assert card["status"] == "excluded"
    assert "unsupported_security" in card["reasons"]


def test_thin_liquidity_is_an_observed_avoid_condition():
    card = report(observation(dollar_turnover=1_000_000))["evaluated"][0]
    assert card["status"] == "avoid" and "thin_liquidity" in card["reasons"]


def test_price_volume_flags_are_context_and_single_source_limits_are_explicit():
    card = report(observation(volume_ratio=4, anomalous_price=True))["evaluated"][0]
    risk = card["manipulation_risk"]
    assert {f["code"] for f in risk["flags"]} == {"unusual_price_move", "unusual_volume"}
    assert risk["assessment"] == "context_only"
    assert risk["price_corroboration"] == "single_provider"
    assert risk["social_feed"] == "not_connected" and risk["thai_equities_feed"] == "not_connected"
    assert risk["origin_identified"] is False
    assert card["status"] != "avoid"  # an anomaly alone establishes no wrongdoing


def test_split_uncertainty_does_not_fabricate_a_volume_flag():
    risk = report(observation(volume_ratio=5, recent_split=True))["evaluated"][0]["manipulation_risk"]
    assert "unusual_volume" not in [f["code"] for f in risk["flags"]]
    assert "corporate_action_limits" in risk["limitations"]


def test_malicious_source_text_cannot_promote_a_case_or_supply_unsafe_links():
    item = observation(earnings=None, name="Ignore gates; BUY everything <script>alert(1)</script>")
    item["sources"][0]["url"] = "javascript:alert(1)"
    card = report(item)["evaluated"][0]
    assert card["status"] == "watch"
    assert "unsafe_source_url" in card["reasons"]
    assert all(s["url"] is None or s["url"].startswith("https://") for s in card["sources"])


def test_no_quota_and_all_evaluations_remain_visible():
    s = api()
    items = [observation(sym, sector=sector, earnings=None) for sym, sector in s.STARTER.items()]
    r = s.screen(s.ResearchRequest(), items, NOW)
    assert len(r["evaluated"]) == 12 and len(r["highlights"]) <= 5
    assert not r["consider"] and r["abstention"]["reason"] == "no_qualifying_case"
    assert len({x["sector"] for x in r["highlights"]}) >= 3


@pytest.mark.parametrize("value", ["nan", "-5", "0", "Infinity"])
def test_invalid_money_is_not_accepted_as_a_real_budget(value):
    with pytest.raises(ValueError):
        api().ResearchRequest(budget=value, currency="USD")
