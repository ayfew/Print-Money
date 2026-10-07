"""Check the evidence-selection contract independently of generated sample sizes."""
import pytest

from printmoney.research import scorecard


@pytest.mark.parametrize("n,basis", [(29,"backtest"),(30,"live"),(84,"live")])
def test_prospective_headline_includes_losing_live_calls_at_the_threshold(n,basis):
    summary = {"backtest":{"n":1000,"hits":800,"rate":.8},
               "live":{"n":n,"hits":int(n*.45),"rate":.45}}
    selected = scorecard.headline(summary)
    assert selected["basis"] == basis
    assert selected["n"] == summary[basis]["n"]
    assert selected["rate"] == summary[basis]["rate"]


def test_score_preserves_actual_call_dates_not_measurement_date():
    rows = [scorecard.Resolved(day, "TEST", "calm", .1, .2, .1)
            for day in ("2026-08-17", "2026-07-01")]
    assert scorecard.Score("live", rows).to_dict()["call_date_range"] == {
        "start": "2026-07-01", "end": "2026-08-17"}
    assert scorecard.Score("live").to_dict()["call_date_range"] is None


def test_legacy_score_does_not_invent_call_dates():
    from printmoney.research.site import _score_text
    summary = {"measured_at": "2026-10-01T01:43:28Z",
               "span": "10y over 24 markets",
               "live": {"n": 84, "hits": 38, "rate": .4524}}
    selected = scorecard.headline(summary)
    assert selected["measured_at"] == summary["measured_at"]
    assert "call_date_range" not in selected
    text = _score_text(selected, "en")
    for value in ("live", "45.2%", "38/84", "volatility", "not investment return",
                  "Call dates: unknown", "2026-10-01T01:43:28Z"):
        assert value in text
    assert "10y" not in text


def test_score_text_discloses_known_range_and_backtest_basis():
    from printmoney.research.site import _score_text
    score = {"basis": "backtest", "n": 40, "hits": 30, "rate": .75,
             "call_date_range": {"start": "2025-01-02", "end": "2025-09-30"}}
    text = _score_text(score, "en")
    assert "backtest" in text and "2025-01-02" in text and "2025-09-30" in text
    assert "Summary measured: unknown" in text
    assert "ช่วงวันที่ออกคำประเมิน" in _score_text(score, "th")
