"""Prospective scenarios use the original cutoff, exchange sessions and all outcomes."""
from datetime import datetime, timezone

import pytest

from test_stocks import NOW, observation


def api():
    from printmoney.research import stockhistory
    assert hasattr(stockhistory, "evaluate"), "prospective stock evaluation is missing"
    return stockhistory


def evidence(tmp_path, horizon="months-plus", status=None, cutoff=None):
    from printmoney.research.stocks import ResearchRequest, screen
    from printmoney.research.stockhistory import record, load_records
    r = screen(ResearchRequest(symbols=("MSFT",), horizon=horizon),
               [observation(verified_catalyst=True)], NOW)
    if cutoff:
        r["generated_at"] = cutoff
    if status:
        r["evaluated"][0]["status"] = status
        r["evaluated"][0]["entry_condition"]["state"] = "unmet"
    record(r, tmp_path)
    return load_records(tmp_path)


def prices(span=63, stock_end=80, benchmark_end=110):
    import exchange_calendars
    cal = exchange_calendars.get_calendar("XNYS")
    sessions = cal.sessions_in_range("2026-10-05", "2027-02-01")[:span]
    def bars(end):
        return [[int(s.timestamp()), 100, max(100,end), min(100,end),
                 end if i == span-1 else 100, 1_000_000, end if i == span-1 else 100, True]
                for i,s in enumerate(sessions)]
    return {"MSFT": bars(stock_end), "SPY": bars(benchmark_end)}, sessions


MATURE = datetime(2027, 2, 2, tzinfo=timezone.utc)


def test_new_record_remains_pending_without_any_future_data(tmp_path):
    h = api()
    r = h.evaluate(evidence(tmp_path), {}, as_of=NOW)
    assert r["outcomes"][0]["state"] == "pending"
    assert r["summary"]["n"] == 0 and r["counts"]["pending"] == 1
    assert r["policy_validation"] == "unvalidated"


def test_negative_outcomes_costs_cash_and_matched_benchmark_are_retained(tmp_path):
    h = api()
    data, sessions = prices()
    r = h.evaluate(evidence(tmp_path), data, as_of=MATURE)
    out = r["outcomes"][0]
    assert out["state"] == "matured" and r["summary"]["n"] == 1
    assert out["entry_day"] == "2026-10-05" and out["exit_day"] == sessions[-1].date().isoformat()
    assert out["gross_return"] == pytest.approx(-.20)
    assert out["net_return_10bp"] == pytest.approx(-.201)
    assert out["net_return_30bp"] == pytest.approx(-.203)
    assert out["benchmark_return"] == pytest.approx(.10)
    assert out["excess_return"] == pytest.approx(-.30)
    assert out["cash_return"] == 0
    assert r["assumptions"]["round_trip_cost_bps"] == [10,30]


def test_entry_is_next_session_open_after_cutoff_not_after_quote_day(tmp_path):
    h = api()
    data,_ = prices(span=64)
    r = h.evaluate(evidence(tmp_path, cutoff="2026-10-05T15:00:00+00:00"), data, as_of=MATURE)
    assert r["outcomes"][0]["entry_day"] == "2026-10-06"


def test_days_weeks_uses_twenty_one_completed_sessions(tmp_path):
    h = api()
    data,sessions = prices(span=21)
    out = h.evaluate(evidence(tmp_path, horizon="days-weeks"), data, as_of=MATURE)["outcomes"][0]
    assert out["horizon_sessions"] == 21 and out["exit_day"] == sessions[-1].date().isoformat()


def test_missing_expected_entry_is_not_shifted_to_a_later_quote(tmp_path):
    h = api()
    data,_ = prices()
    data["MSFT"] = data["MSFT"][1:]
    r = h.evaluate(evidence(tmp_path), data, as_of=MATURE)
    assert r["outcomes"][0]["state"] == "unscorable" and r["summary"]["n"] == 0
    assert "entry_price_missing" in r["outcomes"][0]["reasons"]


def test_missing_benchmark_preserves_stock_result_without_counting_a_complete_comparison(tmp_path):
    h = api()
    data,_ = prices()
    del data["SPY"]
    r = h.evaluate(evidence(tmp_path), data, as_of=MATURE)
    out = r["outcomes"][0]
    assert out["state"] == "unscorable" and out["gross_return"] == pytest.approx(-.20)
    assert "benchmark_price_missing" in out["reasons"] and r["summary"]["n"] == 0


def test_watch_avoid_and_missing_entry_conditions_are_not_paper_fills(tmp_path):
    h = api()
    data,_ = prices()
    r = h.evaluate(evidence(tmp_path, status="watch"), data, as_of=MATURE)
    assert r["outcomes"][0]["state"] == "not_entered" and r["summary"]["n"] == 0
    assert r["outcomes"][0]["observation"]["gross_return"] == pytest.approx(-.20)


def test_adjusted_return_proxy_handles_raw_split_without_a_fake_loss(tmp_path):
    h = api()
    data,_ = prices(stock_end=50)
    # Entry raw open/close 100 corresponds to adjusted 50; exit raw/adjusted 50.
    for bar in data["MSFT"]:
        bar[4] = 50
    out = h.evaluate(evidence(tmp_path), data, as_of=MATURE)["outcomes"][0]
    assert out["gross_return"] == pytest.approx(0)


@pytest.mark.parametrize("symbol", ["MSFT","SPY"])
def test_unverified_adjusted_endpoints_cannot_become_scored_split_losses(tmp_path,symbol):
    h = api()
    data,_ = prices(stock_end=50)
    for bar in data[symbol]:
        bar[7] = False
    r = h.evaluate(evidence(tmp_path),data,as_of=MATURE)
    assert r["outcomes"][0]["state"] == "unscorable" and r["summary"]["n"] == 0
    assert "adjustment_unverified" in " ".join(r["outcomes"][0]["reasons"])


def test_calendar_absence_and_modified_evidence_do_not_invent_scores(tmp_path):
    h = api()
    records = evidence(tmp_path)
    data,_ = prices()
    r = h.evaluate(records, data, calendar=False, as_of=MATURE)
    assert r["outcomes"][0]["state"] == "unscorable" and r["summary"]["n"] == 0
    records[0]["payload"]["evaluated"][0]["status"] = "avoid"
    with pytest.raises(ValueError, match="integrity"):
        h.evaluate(records, data, as_of=MATURE)


def test_registered_score_cli_keeps_empty_records_explicit(tmp_path, monkeypatch, capsys):
    import json
    from printmoney.research import stockcli
    monkeypatch.setattr(stockcli, "PRIVATE_ROOT", tmp_path/"state"/"research")
    from printmoney.cli import main
    assert main(["research-score", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["summary"]["n"] == 0 and r["counts"]["records"] == 0


def test_unknown_frozen_evaluation_definition_is_not_silently_reinterpreted(tmp_path):
    h=api()
    from printmoney.research.stocks import ResearchRequest,screen
    r=screen(ResearchRequest(symbols=("MSFT",),horizon="months-plus"),[observation()],NOW)
    r["evaluation_definition"]={"version":"some_future_model"}
    h.record(r,tmp_path)
    data,_=prices()
    result=h.evaluate(h.load_records(tmp_path),data,as_of=MATURE)
    assert result["outcomes"][0]["state"]=="unscorable" and result["summary"]["n"]==0
