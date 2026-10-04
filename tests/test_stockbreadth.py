"""Index research must keep all securities and frozen provenance, with honest gaps."""
from copy import deepcopy
import json

import pytest

from test_stocks import NOW, observation


def snapshot(count=505):
    members = [{"symbol": f"S{i:04d}", "provider_symbol": f"S{i:04d}",
                "name": f"Generated issuer {i}", "sector": "Industrials",
                "industry": "Generated fixture", "cik": i+1, "indices": ["sp500"]} for i in range(count)]
    return {"schema_version": 1, "name": "sp500-nasdaq100", "version": "generated-v1",
            "captured_at": NOW.isoformat(), "verification": "community_unverified",
            "completeness": "complete_discovery_uncertified", "sources": [], "members": members,
            "pending": [], "warnings": ["Generated fixture, not actual constituents"]}


def test_named_index_request_supports_broad_security_universe_without_relaxing_custom_limit():
    from printmoney.research.stocks import ResearchRequest
    symbols = tuple(m["symbol"] for m in snapshot()["members"])
    req = ResearchRequest(symbols=symbols, universe="sp500-nasdaq100")
    assert len(req.symbols) == 505 and req.universe == "sp500-nasdaq100"
    with pytest.raises(ValueError):
        ResearchRequest(symbols=symbols)
    with pytest.raises(ValueError):
        ResearchRequest(universe="sp500")


def test_security_aliases_deduplicate_without_collapsing_distinct_share_classes():
    from printmoney.research.stocks import ResearchRequest
    req = ResearchRequest(symbols=("BRK-B", "BRK.B", "GOOG", "GOOGL"))
    assert req.symbols == ("BRK.B", "GOOG", "GOOGL")


def test_unsupported_accounting_stays_in_output_and_does_not_get_universal_pe():
    from printmoney.research.stocks import ResearchRequest, screen
    item = observation(accounting_policy="unsupported_financials", indices=["sp500"])
    r = screen(ResearchRequest(symbols=("MSFT",), horizon="months-plus"), [item], NOW)
    card = r["evaluated"][0]
    assert card["status"] == "watch" and "unsupported_accounting_policy" in card["reasons"]
    assert card["annual_earnings_pe"] is None and card["indices"] == ["sp500"]


def test_record_freezes_membership_and_coverage_but_collection_runtime_is_not_new_evidence(tmp_path):
    from printmoney.research.stocks import ResearchRequest, screen
    from printmoney.research.stockhistory import record, load_records
    r = screen(ResearchRequest(symbols=("MSFT",), horizon="months-plus"), [observation()], NOW)
    r["membership"] = {"name": "sp500", "version": "v1", "captured_at": NOW.isoformat(), "members": [{"symbol": "MSFT"}]}
    r["collection"] = {"requested": 1, "cached": 0, "attempted": 1, "completed": 1, "failed": 0}
    first = record(r, tmp_path)
    b = deepcopy(r)
    b["collection"].update(cached=1, attempted=0, completed=0)
    assert record(b, tmp_path) == first
    saved = load_records(tmp_path)[0]["payload"]
    assert saved["membership"]["version"] == "v1" and saved["coverage_metrics"]["total"] == 1
    b["membership"]["version"] = "v2"
    assert record(b, tmp_path) != first


def test_default_cli_resolves_full_union_and_exports_every_evaluated_row(monkeypatch, capsys, tmp_path):
    from printmoney.research import stockcli
    from printmoney.cli import main
    selected = []
    members = snapshot()["members"]
    def resolve(name, now, cache_dir, **kwargs):
        selected.append(name)
        return snapshot()
    def collect(symbols, provider_factory, now, cache_dir, **kwargs):
        return {"observations": [observation(s, earnings=None, fundamentals={}) for s in symbols],
                "collection": {"requested": len(symbols), "cached": 0, "attempted": len(symbols),
                               "completed": len(symbols), "failed": 0, "offline": 0, "workers": 4,
                               "request_interval_seconds": 1}}
    monkeypatch.setattr(stockcli, "resolve_universe", resolve)
    monkeypatch.setattr(stockcli, "collect_many", collect)
    monkeypatch.setattr(stockcli, "PRIVATE_ROOT", tmp_path / "state" / "research")
    html = stockcli.PRIVATE_ROOT / "universe.html"
    assert main(["research", "--json", "--html", str(html), "--no-record"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert selected == ["sp500-nasdaq100"]
    assert len(r["evaluated"]) == r["coverage_metrics"]["total"] == len(members)
    assert r["membership"]["version"] == "generated-v1"
    body = html.read_text(encoding="utf-8")
    assert all(m["symbol"] in body for m in members)
    assert all(sum(counts.values()) == len(members) for counts in r["coverage_metrics"]["fields"].values())


def test_unsafe_evidence_destination_is_rejected_before_discovery(monkeypatch, capsys, tmp_path):
    from printmoney.research import stockcli
    from printmoney.cli import main
    calls = []
    monkeypatch.setattr(stockcli, "resolve_universe", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(stockcli, "PRIVATE_ROOT", tmp_path / "state" / "research")
    assert main(["research", "--json", "--evidence", str(tmp_path / "public.json"), "--no-record"]) == 2
    r = json.loads(capsys.readouterr().out)
    assert r["errors"] and not calls


def test_unavailable_membership_returns_explicit_gap_without_starter_fallback(monkeypatch, capsys):
    from printmoney.research import stockcli
    from printmoney.cli import main
    empty = snapshot(0)
    empty["completeness"] = "unavailable"
    monkeypatch.setattr(stockcli, "resolve_universe", lambda *a, **kw: empty)
    assert main(["research", "--json", "--offline", "--no-record"]) == 2
    r = json.loads(capsys.readouterr().out)
    assert r["evaluated"] == [] and r["membership"]["completeness"] == "unavailable"
    assert r["errors"] and r["abstention"]["active"]


def test_context_failure_retains_provider_price_evidence(monkeypatch):
    from printmoney.research import stockcli
    class Price:
        def __init__(self, **kwargs):
            assert kwargs["sec_enabled"] is False
        def collect(self, symbol, now):
            return observation(symbol)
    class BrokenContext:
        def __init__(self, **kwargs):
            pass
        def collect(self, symbol, now):
            raise RuntimeError("unexpected parser failure")
    monkeypatch.setattr(stockcli, "StockProvider", Price)
    monkeypatch.setattr(stockcli, "NewsProvider", BrokenContext)
    monkeypatch.setattr(stockcli, "FinancialReleaseProvider", BrokenContext)
    provider = stockcli._ResearchProvider(object(), "unused-fixture-cache")
    row = provider.collect("MSFT", NOW)
    assert row["last"] == 100 and row["sources"][0]["id"] == "price"
    assert row["news"]["status"] == row["reported_financials"]["status"] == "unavailable"


@pytest.mark.parametrize("extra", [["--workers", "5"], ["--request-interval", "0"], ["--request-interval", "nan"]])
def test_invalid_collection_settings_reject_before_source_request(monkeypatch, capsys, extra):
    from printmoney.research import stockcli
    from printmoney.cli import main
    calls = []
    monkeypatch.setattr(stockcli, "resolve_universe", lambda *a, **kw: calls.append(a))
    assert main(["research", "--json", "--no-record", *extra]) == 2
    assert json.loads(capsys.readouterr().out)["errors"] and not calls


def test_membership_freshness_is_separate_from_official_certification():
    from datetime import timedelta
    from printmoney.research import stockcli
    current = stockcli.membership_context(snapshot(), NOW)
    assert current["status"] == "fresh_discovery_snapshot" and current["official_current_certification"] == "unverified"
    stale = stockcli.membership_context(snapshot(), NOW + timedelta(hours=7))
    assert stale["status"] == "stale_discovery_snapshot" and stale["snapshot_age_seconds"] == 25200
    assert stale["official_current_certification"] == "unverified"


def test_due_changes_mark_offline_membership_as_frozen_not_current():
    from printmoney.research import stockcli
    data = snapshot()
    data["pending"] = [{"symbol": "TWLO", "action": "addition", "effective_at": "2026-10-03T10:00:00+00:00"}]
    data["captured_at"] = "2026-10-03T09:00:00+00:00"
    context = stockcli.membership_context(data, NOW)
    assert context["status"] == "snapshot_with_due_changes"
    assert context["due_changes"] == data["pending"] and len(data["members"]) == 505


def test_cache_crossing_exchange_close_rechecks_currentness_without_advancing_receipts(tmp_path):
    from datetime import datetime, timezone
    from printmoney.research.stockbatch import _write_cache, _cache_path, collect_many
    from printmoney.research.stocks import ResearchRequest, screen
    at_receipt = datetime(2026, 10, 2, 19, tzinfo=timezone.utc)
    after_close = datetime(2026, 10, 2, 21, tzinfo=timezone.utc)
    item = observation(quote_day="2026-10-01", last_completed_session="2026-10-01", fetched_at=at_receipt.isoformat())
    item["earnings"]["retrieved_at"] = at_receipt.isoformat()
    for source in item["sources"]:
        source["retrieved_at"] = at_receipt.isoformat()
    _write_cache(_cache_path(tmp_path, "MSFT"), item)
    batch = collect_many(["MSFT"], lambda gate: pytest.fail("offline factory called"), after_close, tmp_path, offline=True)
    card = screen(ResearchRequest(symbols=("MSFT",), horizon="months-plus"), batch["observations"], after_close)["evaluated"][0]
    assert batch["collection"]["cached"] == 1 and card["status"] == "watch"
    assert "stale_price" in card["reasons"] and card["data_quality"]["price"]["status"] == "stale"
    assert card["fetched_at"] == at_receipt.isoformat()


def test_intraday_observation_does_not_become_completed_close_after_the_market_closes():
    from datetime import datetime, timezone
    from printmoney.research.stocks import ResearchRequest, screen
    receipt = "2026-10-02T19:00:00+00:00"
    item = observation(quote_day="2026-10-02", last_completed_session="2026-10-01", fetched_at=receipt)
    item["earnings"]["retrieved_at"] = receipt
    for source in item["sources"]:
        source["retrieved_at"] = receipt
    after_close = datetime(2026, 10, 2, 21, tzinfo=timezone.utc)
    card = screen(ResearchRequest(symbols=("MSFT",), horizon="months-plus"), [item], after_close)["evaluated"][0]
    assert card["status"] == "watch" and "uncompleted_session" in card["reasons"]


def test_membership_client_and_collector_share_the_run_gate(monkeypatch, capsys, tmp_path):
    from printmoney.research import stockcli
    from printmoney.cli import main
    gates = []
    class Client:
        def __init__(self, gate):
            gates.append(gate)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def resolve(*args, **kwargs):
        assert kwargs["client"] is not None
        return snapshot(1)
    def collect(symbols, factory, now, cache, **kwargs):
        assert kwargs["request_gate"] is gates[0]
        return {"observations": [observation(symbols[0], fundamentals={}, earnings=None)], "collection": {"requested": 1}}
    monkeypatch.setattr(stockcli, "_MembershipClient", Client)
    monkeypatch.setattr(stockcli, "resolve_universe", resolve)
    monkeypatch.setattr(stockcli, "collect_many", collect)
    monkeypatch.setattr(stockcli, "PRIVATE_ROOT", tmp_path / "state" / "research")
    assert main(["research", "--json", "--no-record"]) == 0
    assert len(json.loads(capsys.readouterr().out)["evaluated"]) == 1


def test_nonfinite_output_has_one_structured_error_even_without_record(monkeypatch, capsys, tmp_path):
    from printmoney.research import stockcli
    from printmoney.cli import main
    data = snapshot(1)
    data["sources"] = [{"revision": float("nan")}]
    monkeypatch.setattr(stockcli, "resolve_universe", lambda *a, **kw: data)
    monkeypatch.setattr(stockcli, "collect_many", lambda *a, **kw: {
        "observations": [observation("S0000", fundamentals={}, earnings=None)], "collection": {"requested": 1}})
    monkeypatch.setattr(stockcli, "PRIVATE_ROOT", tmp_path / "state" / "research")
    assert main(["research", "--json", "--no-record"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["system_status"] == "unavailable" and result["errors"]
