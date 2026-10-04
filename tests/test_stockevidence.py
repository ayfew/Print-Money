"""Broad evidence coverage must count gaps and preserve reported-period truth."""
from copy import deepcopy
import json

import pytest

from test_stocks import NOW, observation


def api():
    from printmoney.research import stockevidence
    return stockevidence


def release(**changes):
    result = {
        "status": "available",
        "periods": [{"kind": "quarter", "start": None, "end": "2026-06-27",
                     "label": "issuer-reported fiscal quarter", "currency": "USD",
                     "basis": "reported_unspecified",
                     "metrics": {"revenue": {"value": 109_400_000_000, "unit": "USD",
                                             "basis": "reported_unspecified", "locator": "financial results"},
                                 "diluted_eps": {"value": 2.02, "unit": "USD/share",
                                                 "basis": "reported_unspecified", "locator": "financial results"}}}],
        "sources": [{"id": "financial_release", "kind": "issuer",
                     "url": "https://www.apple.com/newsroom/2026/07/apple-reports-third-quarter-results/",
                     "retrieved_at": NOW.isoformat()}],
        "warnings": ["GAAP designation not stated; annual metrics unavailable"],
    }
    result.update(changes)
    return result


def report(items=None):
    from printmoney.research.stocks import ResearchRequest, screen
    items = items or [observation()]
    r = screen(ResearchRequest(symbols=tuple(item["symbol"] for item in items), horizon="months-plus"), items, NOW)
    for card in r["evaluated"]:
        card["return_basis"] = "adjusted"
    return r


def test_reported_quarter_facts_are_visible_without_synthetic_annual_valuation():
    e = api()
    r = report([observation(fundamentals={}, earnings=None)])
    r["evaluated"][0]["reported_financials"] = release()
    e.add_coverage(r, NOW)
    card = r["evaluated"][0]
    assert card["data_quality"]["fundamentals"]["status"] == "available"
    assert card["data_quality"]["valuation"]["status"] == "missing"
    assert card["annual_earnings_pe"] is None and card["status"] == "watch"
    assert card["reported_financials"]["periods"][0]["basis"] == "reported_unspecified"


def test_each_coverage_bucket_has_the_full_denominator_including_unsupported_rows():
    e = api()
    items = [observation("MSFT"), observation("JPM", fundamentals={}, earnings=None),
             observation("BAD", last=None, fundamentals={}, earnings=None)]
    r = report(items)
    r["evaluated"][1]["accounting_policy"] = "unsupported_financials"
    e.add_coverage(r, NOW)
    metrics = r["coverage_metrics"]
    assert metrics["total"] == 3 and sum(metrics["status_counts"].values()) == 3
    assert set(metrics["fields"]) == {"price", "fundamentals", "valuation", "earnings", "news", "risk"}
    assert all(sum(buckets.values()) == 3 for buckets in metrics["fields"].values())
    assert len(r["evaluated"]) == 3


@pytest.mark.parametrize("changes", [
    {"sources": [{"id": "financial_release", "kind": "issuer", "url": "javascript:buy()", "retrieved_at": NOW.isoformat()}]},
    {"sources": [{"id": "financial_release", "kind": "issuer", "url": "https://www.apple.com/newsroom/", "retrieved_at": "2026-10-04T00:00:00+00:00"}]},
    {"sources": [{"id": "financial_release", "kind": "issuer", "url": "https://www.apple.com/newsroom/", "retrieved_at": NOW.isoformat(), "published_day": "2026-10-04"}]},
    {"sources": [{"id": "financial_release", "kind": "issuer", "url": "https://www.apple.com/newsroom/", "retrieved_at": "2026-10-03T10:00:00+00:00", "published_at": "2026-10-03T11:00:00+00:00"}]},
])
def test_future_or_unsafe_release_sources_cannot_count_as_available(changes):
    r = report([observation(fundamentals={}, earnings=None)])
    r["evaluated"][0]["reported_financials"] = release(**changes)
    api().add_coverage(r, NOW)
    assert r["evaluated"][0]["data_quality"]["fundamentals"]["status"] == "unverified"


def test_import_rejects_price_override_before_it_can_change_a_screen(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"schema_version": 1, "observations": [{"symbol": "MSFT", "last": 1}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="field"):
        api().load_evidence(path, NOW)


def test_import_rejects_conflicting_duplicate_securities(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"schema_version": 1, "observations": [
        {"symbol": "MSFT", "reported_financials": release()},
        {"symbol": "MSFT", "reported_financials": release(status="missing")},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        api().load_evidence(path, NOW)


@pytest.mark.parametrize("stamp", ["2026-10-04T00:00:00+00:00", "2026-10-03T11:00:00"])
def test_import_rejects_future_or_naive_evidence_clock(tmp_path, stamp):
    data = release()
    data["sources"][0]["retrieved_at"] = stamp
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"schema_version": 1, "observations": [{"symbol": "MSFT", "reported_financials": data}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence"):
        api().load_evidence(path, NOW)


def test_member_metadata_keeps_share_classes_and_unsupported_accounting_visible():
    members = [
        {"symbol": "GOOG", "provider_symbol": "GOOG", "name": "Alphabet C", "sector": "Communication Services", "cik": 1652044, "indices": ["sp500", "nasdaq100"]},
        {"symbol": "GOOGL", "provider_symbol": "GOOGL", "name": "Alphabet A", "sector": "Communication Services", "cik": 1652044, "indices": ["sp500", "nasdaq100"]},
        {"symbol": "JPM", "provider_symbol": "JPM", "name": "Bank", "sector": "Financials", "cik": 19617, "indices": ["sp500"]},
    ]
    results = api().enrich_observations([observation(m["symbol"]) for m in members], members, NOW)
    assert [item["symbol"] for item in results] == ["GOOG", "GOOGL", "JPM"]
    assert results[0]["indices"] == ["sp500", "nasdaq100"]
    assert results[0]["accounting_policy"] == results[1]["accounting_policy"] == "unsupported_share_class"
    assert results[2]["accounting_policy"] == "unsupported_financials"


def test_import_keeps_reported_units_and_does_not_mutate_provider_observation(tmp_path):
    data = {"schema_version": 1, "observations": [{"symbol": "MSFT", "reported_financials": release()}]}
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    extra = api().load_evidence(path, NOW)
    item = observation(fundamentals={}, earnings=None)
    original = deepcopy(item)
    result = api().enrich_observations([item], [{"symbol": "MSFT", "indices": ["sp500"]}], NOW, extra)
    assert item == original
    assert result[0]["last"] == 100 and result[0]["reported_financials"]["periods"][0]["metrics"]["diluted_eps"]["unit"] == "USD/share"


def test_stale_price_is_separate_from_blocked_annual_and_unsupported_news():
    r = report([observation(quote_day="2026-09-30", fundamentals={}, earnings=None)])
    card = r["evaluated"][0]
    card["provider_status"] = {"annual": "blocked"}
    card["news"] = {"status": "unsupported", "items": [], "sources": [], "warnings": []}
    api().add_coverage(r, NOW)
    assert card["data_quality"]["price"]["status"] == "stale"
    assert card["data_quality"]["fundamentals"]["status"] == "blocked"
    assert card["data_quality"]["news"]["status"] == "unsupported"


@pytest.mark.parametrize("last", [0, -1])
def test_nonpositive_quote_never_counts_as_available_price(last):
    r = report([observation(last=last)])
    assert r["evaluated"][0]["data_quality"]["price"]["status"] == "missing"


def test_missing_exchange_calendar_marks_price_currentness_unverified():
    r = report([observation(last_completed_session=None)])
    assert r["evaluated"][0]["data_quality"]["price"]["status"] == "unverified"


def test_reported_annual_release_with_unknown_start_keeps_facts_without_filing_gate():
    data = release()
    data["periods"][0].update(kind="annual", label="Twelve months ended June 30, 2026", end="2026-06-30")
    r = report([observation(fundamentals={}, earnings=None, reported_financials=data)])
    card = r["evaluated"][0]
    assert card["data_quality"]["fundamentals"]["status"] == "available"
    assert not card["annual_facts"] and card["annual_earnings_pe"] is None and card["status"] == "watch"


def test_import_rejects_future_day_only_publication(tmp_path):
    data = release()
    data["sources"][0].update(published_day="2026-10-04", publication_time_precision="day")
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"schema_version": 1, "observations": [{"symbol": "MSFT", "reported_financials": data}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="publication"):
        api().load_evidence(path, NOW)


def test_standalone_share_class_is_not_assumed_to_have_ordinary_share_eps():
    result = api().enrich_observations([observation("BRK.B")], [{"symbol": "BRK.B", "indices": []}], NOW)
    assert result[0]["accounting_policy"] == "unsupported_share_class"


@pytest.mark.parametrize("member_cik", [999999, "0000999999"])
def test_conflicting_member_cik_keeps_price_row_without_annual_qualification(member_cik):
    from printmoney.research.stocks import ResearchRequest, screen
    rows = api().enrich_observations([observation()], [{"symbol": "MSFT", "cik": member_cik, "sector": "Information Technology"}], NOW)
    card = screen(ResearchRequest(symbols=("MSFT",), horizon="months-plus"), rows, NOW)["evaluated"][0]
    assert card["last"] == 100 and card["status"] == "watch"
    assert "issuer_identity_conflict" in card["reasons"] and not card["annual_facts"]
    assert card["data_quality"]["fundamentals"]["status"] == "unverified"


def test_imported_issuer_identity_cannot_replace_known_provider_identity():
    with pytest.raises(ValueError, match="identity"):
        api().enrich_observations([observation()], [{"symbol": "MSFT", "cik": 789019}], NOW,
                                 {"MSFT": {"symbol": "MSFT", "issuer_cik": 999999}})


def test_imported_identity_is_bound_to_digit_string_member_cik_when_provider_unknown():
    with pytest.raises(ValueError, match="identity"):
        api().enrich_observations([observation(issuer_cik=None)], [{"symbol": "MSFT", "cik": "0000999999"}], NOW,
                                 {"MSFT": {"symbol": "MSFT", "issuer_cik": 789019}})


def test_mixed_cik_representations_identify_share_classes_without_collapsing_rows():
    members = [{"symbol": "GOOG", "cik": "0001652044", "sector": "Communication Services"},
               {"symbol": "GOOGL", "cik": 1652044, "sector": "Communication Services"}]
    rows = api().enrich_observations([observation(m["symbol"], issuer_cik=1652044) for m in members], members, NOW)
    assert [r["symbol"] for r in rows] == ["GOOG", "GOOGL"]
    assert all(r["accounting_policy"] == "unsupported_share_class" for r in rows)


def test_matching_digit_string_cik_import_uses_canonical_identity():
    rows = api().enrich_observations([observation(issuer_cik=None)], [{"symbol": "MSFT", "cik": "0000789019"}], NOW,
                                 {"MSFT": {"symbol": "MSFT", "issuer_cik": "0000789019"}})
    assert rows[0]["issuer_cik"] == 789019 and not rows[0].get("identity_conflict")


@pytest.mark.parametrize("changes", [{"split_dates": ["2026-09-01"]}, {"annual_clock": "2026-10-04T00:00:00+00:00"}])
def test_annual_valuation_availability_requires_comparable_eps_and_known_source_clock(changes):
    item = observation()
    if "annual_clock" in changes:
        item["sources"][1]["retrieved_at"] = changes["annual_clock"]
    else:
        item.update(changes)
    card = report([item])["evaluated"][0]
    assert card["annual_earnings_pe"] is None
    assert card["data_quality"]["valuation"]["status"] == "unverified"


def test_future_annual_source_cannot_count_as_available_fundamentals():
    item = observation()
    item["sources"][1]["retrieved_at"] = "2026-10-04T00:00:00+00:00"
    card = report([item])["evaluated"][0]
    assert card["data_quality"]["fundamentals"]["status"] == "unverified" and not card["annual_facts"]


def imported_news(**item_changes):
    item = {"title": "Generated context", "url": "https://www.microsoft.com/news/",
            "published_at": "2026-10-03T09:00:00+00:00", "retrieved_at": "2026-10-03T10:00:00+00:00"}
    item.update(item_changes)
    return {"status": "available", "items": [item], "sources": [], "warnings": []}


@pytest.mark.parametrize("changes", [
    {"published_at": "2026-10-03T11:00:00+00:00"},
    {"updated_at": "2027-01-01T00:00:00+00:00"},
    {"as_of": "2026-10-04"},
    {"updated_at": "2026-10-03T09:30:00"},
])
def test_imported_context_clocks_are_bounded_by_receipt_and_cutoff(tmp_path, changes):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"schema_version": 1, "observations": [{"symbol": "MSFT", "news": imported_news(**changes)}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="clock"):
        api().load_evidence(path, NOW)


def test_unknown_original_publication_can_be_imported_as_updated_only_context(tmp_path):
    data = imported_news(published_at=None, updated_at="2026-10-03T09:00:00+00:00")
    data["status"] = "partial"
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"schema_version": 1, "observations": [{"symbol": "MSFT", "news": data}]}), encoding="utf-8")
    loaded = api().load_evidence(path, NOW)
    assert loaded["MSFT"]["news"]["items"][0]["published_at"] is None


def test_raw_news_coverage_also_rejects_publication_after_receipt():
    card = report([observation(news=imported_news(published_at="2026-10-03T11:00:00+00:00"))])["evaluated"][0]
    assert card["data_quality"]["news"]["status"] == "unverified"


@pytest.mark.parametrize("item", [observation("JPM", sector="Financials"), observation(accounting_policy="unsupported_share_class")])
def test_custom_membership_preserves_known_provider_accounting_restrictions(item):
    rows = api().enrich_observations([item], [{"symbol": item["symbol"], "indices": []}], NOW)
    assert rows[0]["accounting_policy"].startswith("unsupported")


def test_unclassified_custom_accounting_is_unknown_not_operating_company_policy():
    rows = api().enrich_observations([observation("UNKNOWN", sector=None)], [{"symbol": "UNKNOWN", "indices": []}], NOW)
    assert rows[0]["accounting_policy"] == "unsupported_unknown_classification"


@pytest.mark.parametrize("source_changes", [
    {"retrieved_at": "2026-01-01T12:00:00+00:00"},
    {"published_at": "2026-01-01T12:00:00+00:00"},
    {"published_day": "2026-01-01", "publication_time_precision": "day"},
])
def test_reported_actual_period_cannot_postdate_supporting_source(source_changes):
    data = release()
    data["sources"][0].update(source_changes)
    assert api().reported_quality(data, NOW)["status"] == "unverified"


def test_reported_publication_day_remains_day_precision():
    data = release()
    data["sources"][0].update(published_day="2026-06-27", publication_time_precision="day")
    assert api().reported_quality(data, NOW)["status"] == "available"
    assert "published_at" not in data["sources"][0]


def test_import_rejects_reported_actuals_received_before_period_end(tmp_path):
    data = release()
    data["sources"][0]["retrieved_at"] = "2026-01-01T12:00:00+00:00"
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"schema_version": 1, "observations": [
        {"symbol": "MSFT", "reported_financials": data}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="financial evidence"):
        api().load_evidence(path, NOW)


@pytest.mark.parametrize("receipt", ["2026-01-01T12:00:00+00:00", "2026-07-01T12:00:00+00:00",
                                     "2026-08-01T23:59:59+00:00"])
def test_annual_actuals_need_receipt_after_period_and_date_only_filing(receipt):
    item = observation()
    item["sources"][1]["retrieved_at"] = receipt
    card = report([item])["evaluated"][0]
    assert card["status"] == "watch" and not card["annual_facts"]
    assert card["annual_earnings_pe"] is None and "annual_source_time_unverified" in card["reasons"]
    assert card["data_quality"]["fundamentals"]["status"] == "unverified"


def test_annual_source_publication_cannot_predate_filing():
    item = observation()
    item["sources"][1]["published_day"] = "2026-07-01"
    card = report([item])["evaluated"][0]
    assert card["status"] == "watch" and card["annual_earnings_pe"] is None
    assert "annual_source_time_unverified" in card["reasons"]


def test_import_rejects_annual_actuals_received_before_filing(tmp_path):
    item = observation()
    annual = deepcopy(item["sources"][1])
    annual["retrieved_at"] = "2026-07-01T12:00:00+00:00"
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"schema_version": 1, "observations": [
        {"symbol": "MSFT", "issuer_cik": item["issuer_cik"], "fundamentals": item["fundamentals"],
         "sources": [annual]}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="financial evidence clock"):
        api().load_evidence(path, NOW)
