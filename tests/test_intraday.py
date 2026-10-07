from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from printmoney.research import intraday as m


def sample(day="2026-10-07"):
    cal = m.calendar()
    sessions = cal.sessions_window(cal.previous_session(day), -200)
    now = datetime.fromisoformat(day + "T14:10:00+00:00")
    def bar(stamp, price, volume=1000):
        return dict(start=day + "T" + stamp + "+00:00", open=price,
                    high=price+.05, low=price-.05, close=price, volume=volume)
    return now, dict(symbol="TEST", currency="USD", instrument_type="EQUITY",
        source_url="https://example.com/prices", retrieved_at=now.isoformat(),
        premarket_complete=True, corporate_actions_verified=True,
        daily=[dict(session=str(d.date()), close=10 if d == sessions[-1] else 9.9, high=10.2) for d in sessions],
        bars=[bar("08:00:00", 10.6, 60000), bar("13:25:00", 10.7),
              *[bar(f"13:{minute}:00", 10.8) for minute in (30,35,40,45,50,55)],
              bar("14:00:00", 10.9), bar("14:05:00", 11)],
        catalyst=dict(status="verified", source="issuer release", url="https://example.com/news",
            published_at=day+"T12:00:00+00:00", observed_at=day+"T12:01:00+00:00",
            verified_at=day+"T12:02:00+00:00", description="Material contract; manually reviewed"))


def test_signal_uses_prior_hod_and_frozen_premarket():
    now, item = sample()
    result = m.scan([item], now)
    row = result["rows"][0]
    assert row["state"] == "experimental_signal"
    assert row["metrics"]["premarket_volume"] == 61000
    assert row["metrics"]["prior_hod"] == pytest.approx(10.95)
    assert result["evaluation"]["status"] == "unvalidated"
    assert result["evaluation"]["sample_count"] == 0


@pytest.mark.parametrize("change,reason", [
    ({"premarket_complete": False}, "premarket_coverage_unverified"),
    ({"corporate_actions_verified": False}, "corporate_actions_unverified"),
    ({"catalyst": {}}, "catalyst_unknown"),
    ({"retrieved_at": "2026-10-07T13:00:00+00:00"}, "source_stale"),
    ({"daily": []}, "daily_history_incomplete"),
])
def test_missing_evidence_abstains(change, reason):
    now, item = sample()
    item.update(change)
    row = m.scan([item], now)["rows"][0]
    assert row["state"] == "cannot_evaluate"
    assert reason in row["reasons"]


def test_future_and_unfinished_bars_cannot_trigger():
    now, item = sample()
    item["bars"][-1]["close"] = 10.9
    item["bars"][-1]["low"] = 10.85
    future = dict(item["bars"][-1], start="2026-10-07T14:10:00+00:00", close=100, high=100)
    item["bars"].append(future)
    row = m.scan([item], now)["rows"][0]
    assert row["state"] == "watch"
    assert "prior_hod_not_broken" in row["reasons"]


def test_hindsight_news_rejected():
    now, item = sample()
    item["catalyst"]["observed_at"] = "2026-10-07T14:11:00+00:00"
    assert "catalyst_unknown" in m.scan([item], now)["rows"][0]["reasons"]


def test_missing_previous_session_rejected():
    now, item = sample()
    item["daily"][-1]["session"] = "2026-10-05"
    assert m.scan([item], now)["rows"][0]["state"] == "cannot_evaluate"


def test_calendar_dst_holiday_and_early_close():
    assert m.session(datetime.fromisoformat("2026-01-05T15:00:00+00:00"))["open"].hour == 14
    assert m.session(datetime.fromisoformat("2026-07-06T14:00:00+00:00"))["open"].hour == 13
    assert m.session(datetime.fromisoformat("2026-07-03T14:00:00+00:00")) is None
    assert m.session(datetime.fromisoformat("2026-11-27T17:00:00+00:00"))["close"].hour == 18


def test_closed_market_and_before_ten():
    now, item = sample()
    assert m.scan([item], now.replace(hour=21))["rows"][0]["state"] == "outside_session"
    assert m.scan([item], now.replace(hour=13, minute=55))["rows"][0]["state"] == "waiting"


def test_repeated_signal_deduplicates_but_every_run_is_preserved(tmp_path):
    now, item = sample()
    report = m.scan([item], now)
    first = m.save_run(report, tmp_path)
    second = m.save_run(report, tmp_path)
    assert first != second
    assert json.loads(second.read_text())["rows"][0]["new_signal"] is False
    assert json.loads(first.read_text())["rows"][0]["new_signal"] is True
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_premarket_revision_cannot_silently_replace_frozen_value(tmp_path):
    now, item = sample()
    m.save_run(m.scan([item], now), tmp_path)
    item["bars"][0]["volume"] += 100
    path = m.save_run(m.scan([item], now), tmp_path)
    row = json.loads(path.read_text())["rows"][0]
    assert row["state"] == "cannot_evaluate"
    assert "premarket_revised_after_freeze" in row["reasons"]


def test_zero_results_and_html_escapes():
    now, item = sample()
    assert m.scan([], now)["coverage"]["requested"] == 0
    item["symbol"] = "<script>"
    with pytest.raises(ValueError):
        m.scan([item], now)
    assert "unvalidated" in m.render_html(m.scan([], now))


def test_duplicate_bars_and_nonfinite_data_rejected():
    now, item = sample()
    item["bars"].append(deepcopy(item["bars"][-1]))
    assert "invalid_bars" in m.scan([item], now)["rows"][0]["reasons"]
    now, item = sample()
    item["bars"][-1]["volume"] = float("nan")
    assert "invalid_bars" in m.scan([item], now)["rows"][0]["reasons"]


def test_thresholds_configurable_and_strict():
    now, item = sample()
    row = m.scan([item], now, m.Policy(min_premarket_volume=61000))["rows"][0]
    assert "premarket_volume_below_threshold" in row["reasons"]
    with pytest.raises(ValueError):
        m.Policy(gap=float("nan"))


def test_strict_trend_and_no_current_day_daily_lookahead():
    now, item = sample()
    for day in item["daily"]:
        day["close"] = 10
    item["daily"].append(dict(session="2026-10-07", close=1, high=1))
    assert "below_sma200" in m.scan([item], now)["rows"][0]["reasons"]


def test_stale_quote_and_missing_prior_hod_bar():
    now, item = sample()
    item["bars"].pop(-2)
    assert "regular_session_incomplete" in m.scan([item], now)["rows"][0]["reasons"]
    now, item = sample()
    item["retrieved_at"] = (now+timedelta(minutes=15)).isoformat()
    assert "quote_stale" in m.scan([item], now+timedelta(minutes=15))["rows"][0]["reasons"]


def test_lock_collision_and_corrupt_history_fail_closed(tmp_path):
    now, item = sample()
    lock = tmp_path / ".writer.lock"
    lock.touch()
    with pytest.raises(FileExistsError):
        m.save_run(m.scan([item], now), tmp_path)
    lock.unlink()
    (tmp_path / "broken.json").write_text("{")
    with pytest.raises(ValueError):
        m.save_run(m.scan([item], now), tmp_path)
    assert not lock.exists()


def test_cli_creates_private_json_html_and_returns_abstention(tmp_path, monkeypatch, capsys):
    from printmoney.cli import main
    from printmoney.research import intradaycli
    monkeypatch.setattr(intradaycli, "STATE_DIR", tmp_path / "state")
    now, item = sample()
    item["catalyst"] = {}
    source = tmp_path / "input.json"
    source.write_text(json.dumps([item]))
    code = main(["intraday", "--input", str(source), "--as-of", now.isoformat(), "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 2
    assert result["rows"][0]["state"] == "cannot_evaluate"
    assert result["input_mode"] == "imported_attestation"
    from pathlib import Path
    assert Path(result["evidence_path"]).is_file()
    assert "catalyst_unknown" in Path(result["html_path"]).read_text()
    assert result["inputs"] == [item]


def test_html_does_not_execute_news_markup():
    now, item = sample()
    item["catalyst"]["description"] = "<script>alert(1)</script>"
    page = m.render_html(m.scan([item], now))
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_unknown_coverage_does_not_poison_future_verified_freeze(tmp_path):
    now, item = sample()
    unknown = deepcopy(item)
    unknown["premarket_complete"] = False
    unknown["bars"][0]["volume"] = 10
    m.save_run(m.scan([unknown], now), tmp_path)
    result = json.loads(m.save_run(m.scan([item], now), tmp_path).read_text())
    assert result["rows"][0]["state"] == "experimental_signal"
