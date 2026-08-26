"""The decision layer: does it say something, and is what it says defensible?

The brief used to stop at data, and the fix was a layer that decides what today
is about. That layer can fail in ways a percentage table cannot - it can
contradict itself, overclaim, cite nothing, or quietly compare today against
today and report that the world never changes. Each of those has its own test
here, and most of them are regressions for a bug that actually shipped.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from printmoney.research import scorecard as sc
from printmoney.research import sources
from printmoney.research.brief import Brief, MarketLine
from printmoney.research.data import Bar, Series
from printmoney.research.decide import Note, decide, read_seconds, snapshot
from printmoney.research.events import (
    Event, Impact, first_friday, parse_fomc, payrolls,
)
from printmoney.research.export import brief_to_event
from printmoney.research.i18n import render_note

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def _line(symbol="SPY", name="S&P 500", *, pctile=0.5, vol=0.13, day=0.004):
    return MarketLine(
        symbol=symbol, name=name, last=760.0, day=day, week=0.01, month=0.03,
        year=0.18, vol_annual=vol, zscore=1.0, intraday_share=0.4,
        vol_percentile=pctile, drawdown=-0.02,
    )


def _brief(lines=None, *, carry=None, loaded=None, error=""):
    lines = list(lines) if lines is not None else [_line()]
    return Brief(
        generated_at=NOW, lines=lines, carry=carry, error=error,
        requested=len(lines), loaded=len(lines) if loaded is None else loaded,
    )


def _impact(kind="fomc", *, ratio=1.16, tstat=3.7, by=None):
    return Impact(
        kind=kind, ratio=ratio, tstat=tstat, events=44,
        markets_bigger=21, markets=24,
        by_market=by or {"UUP": 1.58, "GLD": 1.41, "SPY": 1.20,
                         "BTC-USD": 1.00, "XLU": 0.98},
    )


def _event(kind="fomc", day="2026-08-26"):
    return Event(day=day, kind=kind, name="Fed rate decision (FOMC)",
                 source="https://www.federalreserve.gov/", note="14:00 New York")


# --------------------------------------------------------------------------- #
class TestSourceDiscipline:
    def test_a_note_cannot_cite_a_source_that_is_not_on_the_list(self):
        """The whole point of the registry: an uncited claim fails loudly."""
        with pytest.raises(KeyError):
            Note(kind="focus", key="focus_none", source="some-guy-on-twitter")

    def test_every_note_the_decider_produces_is_citable(self):
        d = decide(_brief([_line(pctile=0.95), _line("GLD", "Gold", pctile=0.05),
                           _line("USO", "Oil", pctile=0.08),
                           _line("TLT", "Bonds", pctile=0.10)]),
                   events=[_event()], impacts={"fomc": _impact()}, today=TODAY)
        assert d.notes
        for note in d.notes:
            assert sources.get(note.source).tier in (1, 2, 3, 4)

    def test_cited_sources_come_back_most_authoritative_first(self):
        got = sources.cited(["yahoo", "study", "fed", "yahoo"])
        assert [s.tier for s in got] == sorted(s.tier for s in got)
        assert got[0].id == "fed"          # tier 1 leads
        assert len(got) == 3               # and the duplicate collapsed

    def test_there_is_no_news_tier(self):
        """Summarising headlines would be a bet on return forecastability."""
        assert all(s.tier <= 4 for s in sources.REGISTRY.values())
        assert not any("news" in s.what.lower() for s in sources.REGISTRY.values())


# --------------------------------------------------------------------------- #
class TestFocusIsConsistentWithItself:
    def test_focus_does_not_claim_calm_while_flagging_hot_markets(self):
        """Regression: 'all 24 markets are normal' printed above nine warnings."""
        lines = [_line(f"S{i}", f"M{i}", pctile=0.85) for i in range(9)]
        lines += [_line(f"C{i}", f"N{i}", pctile=0.50) for i in range(15)]
        d = decide(_brief(lines), today=TODAY)
        assert d.focus is not None
        assert d.focus.key == "focus_careful"
        text = render_note(d.focus, "en")
        assert "All 24 markets are inside their normal range" not in text

    def test_focus_and_the_careful_section_report_the_same_count(self):
        """Regression: the focus said nine and the section below it said six."""
        lines = [_line(f"S{i}", f"M{i}", pctile=0.85) for i in range(9)]
        lines += [_line(f"C{i}", f"N{i}", pctile=0.50) for i in range(15)]
        d = decide(_brief(lines), today=TODAY)
        assert d.focus.params["n"] == d.avoid[0].params["n"] == "9"

    def test_the_calendar_title_does_not_say_nothing_today_while_markets_run_hot(self):
        lines = [_line(f"S{i}", f"M{i}", pctile=0.92) for i in range(4)]
        b = _brief(lines)
        d = decide(b, today=TODAY)
        title = brief_to_event(b, "en", decision=d).title
        assert "nothing today" not in title.lower()
        assert "4" in title

    def test_a_genuinely_quiet_day_still_says_so_plainly(self):
        d = decide(_brief([_line(pctile=0.5), _line("GLD", "Gold", pctile=0.45)]),
                   today=TODAY)
        assert d.focus.key == "focus_none"
        assert d.quiet


# --------------------------------------------------------------------------- #
class TestEventsDriveTheFocus:
    def test_a_measured_event_today_becomes_the_focus(self):
        d = decide(_brief([_line("UUP", "Dollar"), _line("GLD", "Gold")]),
                   events=[_event()], impacts={"fomc": _impact()}, today=TODAY)
        assert d.focus.key == "focus_event"
        assert d.focus.params["event_kind"] == "fomc"
        assert d.focus.params["days"] == "0"

    def test_an_unmeasured_event_is_not_mentioned_at_all(self):
        """Official is not the same as important; only measured gets airtime."""
        d = decide(_brief([_line("UUP", "Dollar")]),
                   events=[_event(kind="cpi")], impacts={}, today=TODAY)
        assert d.watch == []
        assert d.focus.key != "focus_event"

    def test_an_event_that_failed_its_own_test_is_dropped(self):
        weak = _impact(ratio=1.01, tstat=0.5)
        assert not weak.real
        d = decide(_brief([_line("UUP", "Dollar")]),
                   events=[_event()], impacts={"fomc": weak}, today=TODAY)
        assert d.watch == []

    def test_the_brief_names_what_the_event_does_not_move(self):
        """The half most notes leave out, and the reason BTC is not FOMC news."""
        b = _brief([_line("UUP", "Dollar"), _line("BTC-USD", "Bitcoin"),
                    _line("XLU", "Utilities")])
        d = decide(b, events=[_event()], impacts={"fomc": _impact()}, today=TODAY)
        untouched = [n for n in d.ignore if n.key == "ignore_untouched"]
        assert untouched
        assert set(untouched[0].symbols) == {"BTC-USD", "XLU"}

    def test_markets_missing_from_todays_universe_are_never_named(self):
        b = _brief([_line("UUP", "Dollar")])          # no GLD, no SPY
        d = decide(b, events=[_event()], impacts={"fomc": _impact()}, today=TODAY)
        for note in d.notes:
            assert set(note.symbols) <= {"UUP"}

    def test_events_beyond_the_horizon_are_not_todays_problem(self):
        d = decide(_brief([_line("UUP", "Dollar")]),
                   events=[_event(day="2026-10-30")],
                   impacts={"fomc": _impact()}, today=TODAY)
        assert d.events == []
        assert d.watch == []


# --------------------------------------------------------------------------- #
class TestWhatChangedSinceYesterday:
    def _yesterday(self, **over):
        base = {"day": "2026-08-25", "risk": {"SPY": "calm", "GLD": "normal"},
                "event_days": [], "carry_net": 0.02}
        base.update(over)
        return base

    def test_a_market_moving_up_the_risk_ladder_is_reported(self):
        b = _brief([_line("SPY", "S&P 500", pctile=0.85)])   # was calm
        d = decide(b, previous=self._yesterday(), today=TODAY)
        keys = [n.key for n in d.changed]
        assert "changed_risk_up" in keys

    def test_a_market_calming_down_is_reported_too(self):
        b = _brief([_line("GLD", "Gold", pctile=0.05)])      # was normal
        d = decide(b, previous=self._yesterday(), today=TODAY)
        assert "changed_risk_down" in [n.key for n in d.changed]

    def test_an_unchanged_market_produces_no_line(self):
        b = _brief([_line("SPY", "S&P 500", pctile=0.10)])   # calm, still calm
        d = decide(b, previous=self._yesterday(), today=TODAY)
        assert [n for n in d.changed if n.key.startswith("changed_risk")] == []

    def test_an_event_entering_the_window_is_news_once(self):
        b = _brief([_line("UUP", "Dollar")])
        prev = self._yesterday(event_days=[])
        d = decide(b, events=[_event()], impacts={"fomc": _impact()},
                   previous=prev, today=TODAY)
        assert "changed_event_entered" in [n.key for n in d.changed]

        prev_now = self._yesterday(event_days=["2026-08-26"])
        d2 = decide(b, events=[_event()], impacts={"fomc": _impact()},
                    previous=prev_now, today=TODAY)
        assert "changed_event_entered" not in [n.key for n in d2.changed]

    def test_carry_is_only_news_when_it_crosses_the_bar(self):
        under = _brief([_line()], carry={"basket_net_annual": 0.05})
        d = decide(under, previous=self._yesterday(), today=TODAY)
        assert "changed_carry" not in [n.key for n in d.changed]

        over = _brief([_line()], carry={"basket_net_annual": 0.20})
        d2 = decide(over, previous=self._yesterday(), today=TODAY)
        assert "changed_carry" in [n.key for n in d2.changed]

    def test_with_no_yesterday_there_is_simply_nothing_to_diff(self):
        d = decide(_brief(), previous=None, today=TODAY)
        assert d.changed == []


# --------------------------------------------------------------------------- #
class TestSnapshotRoundTrip:
    def test_a_snapshot_carries_exactly_what_tomorrow_needs(self):
        b = _brief([_line("SPY", "S&P 500", pctile=0.85)],
                   carry={"basket_net_annual": 0.04})
        d = decide(b, today=TODAY)
        snap = snapshot(b, d)
        assert snap["day"] == TODAY.isoformat()
        assert snap["risk"]["SPY"] == "elevated"
        assert snap["carry_net"] == 0.04
        # and it survives the trip through disk
        assert json.loads(json.dumps(snap)) == snap

    def test_todays_snapshot_can_be_diffed_by_tomorrow(self):
        today_b = _brief([_line("SPY", "S&P 500", pctile=0.05)])
        snap = snapshot(today_b, decide(today_b, today=TODAY))
        tomorrow_b = _brief([_line("SPY", "S&P 500", pctile=0.95)])
        d = decide(tomorrow_b, previous=snap, today=date(2026, 8, 27))
        assert "changed_risk_up" in [n.key for n in d.changed]


# --------------------------------------------------------------------------- #
class TestBrokenDataStillProducesAnHonestAnswer:
    def test_a_thin_universe_refuses_to_have_an_opinion(self):
        b = _brief([_line()], loaded=1)
        b.requested = 24
        d = decide(b, today=TODAY)
        assert d.focus.key == "focus_broken"
        assert d.watch == [] and d.avoid == []

    def test_the_failure_says_how_much_loaded(self):
        b = _brief([_line()], loaded=2)
        b.requested = 24
        text = render_note(decide(b, today=TODAY).focus, "en")
        assert "2" in text and "24" in text


# --------------------------------------------------------------------------- #
class TestRendering:
    def test_thai_and_english_render_the_same_note_differently(self):
        note = Note(kind="avoid", key="avoid_hot", source="vol",
                    params={"n": "3", "total": "24", "worst_vol": "48%",
                            "worst_pct": "91%"},
                    symbols=("USO",))
        th = render_note(note, "th", {"USO": "Oil"})
        en = render_note(note, "en", {"USO": "Oil"})
        assert th != en
        assert "น้ำมัน" in th          # the Thai name, not the ticker
        assert "Oil" in en
        assert "48%" in th and "48%" in en

    def test_an_unknown_language_falls_back_rather_than_crashing(self):
        note = Note(kind="focus", key="focus_none", source="study",
                    params={"n": "24"})
        assert render_note(note, "de")

    def test_relative_dates_are_built_not_translated(self):
        note = Note(kind="watch", key="changed_event_entered", source="fed",
                    params={"event_kind": "fomc", "days": "1"})
        assert "พรุ่งนี้" in render_note(note, "th")
        assert "tomorrow" in render_note(note, "en")

    def test_the_brief_fits_inside_a_two_minute_read(self):
        """One of the stated success criteria, so it gets asserted, not hoped."""
        lines = [_line(f"S{i}", f"Market {i}", pctile=0.85 if i < 6 else 0.5)
                 for i in range(24)]
        b = _brief(lines)
        d = decide(b, events=[_event()], impacts={"fomc": _impact()}, today=TODAY)
        body = brief_to_event(b, "en", decision=d).body
        assert read_seconds(body) <= 120

    def test_every_rendered_note_actually_substituted_its_numbers(self):
        b = _brief([_line("UUP", "Dollar", pctile=0.95), _line("GLD", "Gold")])
        d = decide(b, events=[_event()], impacts={"fomc": _impact()}, today=TODAY)
        for note in d.notes:
            text = render_note(note, "en", {"UUP": "Dollar", "GLD": "Gold"})
            assert "{" not in text and "}" not in text


# --------------------------------------------------------------------------- #
class TestScheduledDates:
    def test_first_friday_is_the_first_friday(self):
        assert first_friday(2026, 9) == date(2026, 9, 4)
        assert first_friday(2026, 5) == date(2026, 5, 1)   # month starts Friday
        assert first_friday(2026, 8) == date(2026, 8, 7)   # month starts Saturday

    def test_payrolls_lands_once_a_month_and_only_inside_the_window(self):
        got = payrolls(date(2026, 1, 1), date(2026, 12, 31))
        assert len(got) == 12
        assert all(e.date.weekday() == 4 and e.date.day <= 7 for e in got)

    def test_fomc_parses_a_two_day_meeting_to_its_closing_day(self):
        html = ('<a id="1">2026 FOMC Meetings</a>'
                '<div class="fomc-meeting__month"><strong>January</strong></div>'
                '<div class="fomc-meeting__date">27-28</div>')
        assert [e.day for e in parse_fomc(html)] == ["2026-01-28"]

    def test_fomc_handles_a_meeting_that_straddles_two_months(self):
        html = ('<a id="1">2024 FOMC Meetings</a>'
                '<div class="fomc-meeting__month"><strong>Apr/May</strong></div>'
                '<div class="fomc-meeting__date">30-1</div>')
        assert [e.day for e in parse_fomc(html)] == ["2024-05-01"]

    def test_fomc_rolls_the_year_over_a_december_january_meeting(self):
        html = ('<a id="1">2023 FOMC Meetings</a>'
                '<div class="fomc-meeting__month"><strong>Dec/Jan</strong></div>'
                '<div class="fomc-meeting__date">31-1</div>')
        assert [e.day for e in parse_fomc(html)] == ["2024-01-01"]

    def test_the_projections_asterisk_is_read_not_parsed_as_a_date(self):
        html = ('<a id="1">2026 FOMC Meetings</a>'
                '<div class="fomc-meeting__month"><strong>March</strong></div>'
                '<div class="fomc-meeting__date">17-18*</div>')
        got = parse_fomc(html)
        assert got[0].day == "2026-03-18"
        assert "projections" in got[0].note

    def test_a_layout_change_parses_to_nothing_rather_than_to_nonsense(self):
        assert parse_fomc("<html><body>redesigned</body></html>") == []


# --------------------------------------------------------------------------- #
class TestImpactBar:
    def test_a_strong_clean_broad_effect_is_real(self):
        assert _impact(ratio=1.16, tstat=3.7).real

    def test_statistical_significance_alone_is_not_enough(self):
        """A 2% effect measured perfectly still changes nobody's morning."""
        assert not _impact(ratio=1.02, tstat=9.0).real

    def test_a_large_effect_that_could_be_noise_is_not_enough(self):
        assert not _impact(ratio=1.40, tstat=1.1).real

    def test_an_effect_carried_by_a_handful_of_markets_is_not_enough(self):
        weak = _impact(ratio=1.20, tstat=4.0)
        weak.markets_bigger = 10          # of 24
        assert not weak.real

    def test_touches_and_ignores_split_at_ordinary(self):
        imp = _impact()
        assert [s for s, _ in imp.touches(5)] == ["UUP", "GLD", "SPY"]
        assert [s for s, _ in imp.ignores(5)] == ["XLU"]


# --------------------------------------------------------------------------- #
def _series(symbol, returns, start=100.0):
    """A Series whose daily returns are exactly what was asked for."""
    bars, price, ts = [], start, 1_600_000_000
    bars.append(Bar(ts=ts, open=price, high=price, low=price, close=price, volume=1.0))
    for i, r in enumerate(returns, 1):
        price *= 1.0 + r
        bars.append(Bar(ts=ts + i * 86_400, open=price, high=price, low=price,
                        close=price, volume=1.0))
    return Series(symbol=symbol, name=symbol, bars=bars)


class TestScorecard:
    def test_a_call_is_a_hit_when_the_next_month_lands_where_it_said(self):
        hit = sc.Resolved(day="2026-01-01", symbol="X", call="elevated",
                          called_percentile=0.9, realised_percentile=0.8,
                          realised_vol=0.4)
        miss = sc.Resolved(day="2026-01-01", symbol="X", call="elevated",
                           called_percentile=0.9, realised_percentile=0.2,
                           realised_vol=0.1)
        assert hit.hit and not miss.hit

    def test_a_calm_call_is_scored_the_other_way_round(self):
        calm_right = sc.Resolved(day="d", symbol="X", call="calm",
                                 called_percentile=0.1, realised_percentile=0.2,
                                 realised_vol=0.05)
        calm_wrong = sc.Resolved(day="d", symbol="X", call="calm",
                                 called_percentile=0.1, realised_percentile=0.9,
                                 realised_vol=0.5)
        assert calm_right.hit and not calm_wrong.hit

    def test_a_good_rate_on_a_tiny_sample_does_not_count_as_skill(self):
        small = sc.Score(label="t", resolved=[
            sc.Resolved(day="d", symbol="X", call="elevated",
                        called_percentile=0.9, realised_percentile=0.9,
                        realised_vol=0.4) for _ in range(4)])
        assert small.rate == 1.0
        assert not small.beats_coin        # n=4, the error bar swallows it

    def test_a_coin_flip_result_is_reported_as_a_coin_flip(self):
        rows = []
        for i in range(200):
            rows.append(sc.Resolved(
                day="d", symbol="X", call="elevated", called_percentile=0.9,
                realised_percentile=0.9 if i % 2 else 0.1, realised_vol=0.3))
        assert not sc.Score(label="t", resolved=rows).beats_coin

    def test_the_backtest_never_reads_past_the_date_it_is_calling(self):
        """A rule scored against its own future comes back perfect every time."""
        import random
        rng = random.Random(7)
        calm = [rng.gauss(0, 0.004) for _ in range(400)]
        wild = [rng.gauss(0, 0.040) for _ in range(400)]
        # Volatility that flips halfway: a lookahead bug scores this near 100%.
        score = sc.backtest([_series("FLIP", calm + wild)])
        assert score.resolved
        assert 0.0 < score.rate < 1.0

    def test_recording_the_same_day_twice_does_not_double_the_sample(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        lines = [_line("SPY", "S&P 500", pctile=0.95),
                 _line("GLD", "Gold", pctile=0.05)]
        assert sc.record(lines, "2026-08-26", path) == 2
        assert sc.record(lines, "2026-08-26", path) == 0
        assert sc.pending(path) == 2

    def test_only_flagged_markets_are_recorded(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        n = sc.record([_line("SPY", "S&P 500", pctile=0.50)], "2026-08-26", path)
        assert n == 0                      # normal is not a call

    def test_a_half_written_line_does_not_take_the_scorecard_down(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        sc.record([_line("SPY", "S&P 500", pctile=0.95)], "2026-08-26", path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"day": "2026-08-2\n')       # killed mid-write
        assert sc.pending(path) == 1

    def test_headline_prefers_live_calls_once_there_are_enough(self):
        summary = {"backtest": {"n": 5000, "rate": 0.81},
                   "live": {"n": 40, "rate": 0.62}}
        assert sc.headline(summary)["basis"] == "live"

    def test_headline_falls_back_to_the_backtest_while_live_is_thin(self):
        summary = {"backtest": {"n": 5000, "rate": 0.81},
                   "live": {"n": 3, "rate": 1.0}}
        assert sc.headline(summary)["basis"] == "backtest"

    def test_headline_says_nothing_when_there_is_nothing_to_say(self):
        assert sc.headline(None) is None
        assert sc.headline({"backtest": {"n": 4, "rate": 1.0}}) is None


# --------------------------------------------------------------------------- #
class TestMorningRun:
    """The sequencing that has no visible symptom when it goes wrong."""

    def _patch(self, monkeypatch, tmp_path, lines):
        from printmoney.research import morning as mr

        monkeypatch.setattr(mr, "SNAPSHOT", tmp_path / "snapshot.json")
        monkeypatch.setattr(sc, "CLAIMS", tmp_path / "claims.jsonl")
        monkeypatch.setattr(mr, "build_brief",
                            lambda **kw: _brief(lines))
        monkeypatch.setattr(mr.ev, "load_impacts", lambda: {})
        return mr

    def test_yesterday_is_read_before_today_is_written(self, monkeypatch, tmp_path):
        """Otherwise the diff compares today with itself, forever."""
        mr = self._patch(monkeypatch, tmp_path, [_line("SPY", "S&P 500", pctile=0.05)])
        mr.run(include_carry=False, today=TODAY)

        mr = self._patch(monkeypatch, tmp_path, [_line("SPY", "S&P 500", pctile=0.95)])
        m = mr.run(include_carry=False, today=date(2026, 8, 27))
        assert "changed_risk_up" in [n.key for n in m.decision.changed]

    def test_a_same_day_rerun_still_diffs_against_yesterday(self, monkeypatch,
                                                            tmp_path):
        """The workflow can be re-fired by hand; the second run must not go blind."""
        mr = self._patch(monkeypatch, tmp_path, [_line("SPY", "S&P 500", pctile=0.05)])
        mr.run(include_carry=False, today=TODAY)

        hot = [_line("SPY", "S&P 500", pctile=0.95)]
        mr = self._patch(monkeypatch, tmp_path, hot)
        first = mr.run(include_carry=False, today=date(2026, 8, 27))
        second = mr.run(include_carry=False, today=date(2026, 8, 27))
        assert [n.key for n in first.decision.changed] == \
               [n.key for n in second.decision.changed]

    def test_a_rerun_does_not_record_the_same_call_twice(self, monkeypatch,
                                                         tmp_path):
        mr = self._patch(monkeypatch, tmp_path, [_line("SPY", "S&P 500", pctile=0.95)])
        assert mr.run(include_carry=False, today=TODAY).recorded == 1
        assert mr.run(include_carry=False, today=TODAY).recorded == 0

    def test_missing_impacts_warn_rather_than_inventing_an_event(self, monkeypatch,
                                                                 tmp_path):
        mr = self._patch(monkeypatch, tmp_path, [_line()])
        m = mr.run(include_carry=False, today=TODAY)
        assert m.warnings and "pm events" in m.warnings[0]
        assert m.decision.events == []

    def test_nothing_is_persisted_when_the_data_was_too_thin(self, monkeypatch,
                                                             tmp_path):
        from printmoney.research import morning as mr

        monkeypatch.setattr(mr, "SNAPSHOT", tmp_path / "snapshot.json")
        monkeypatch.setattr(sc, "CLAIMS", tmp_path / "claims.jsonl")
        thin = _brief([_line()], loaded=1)
        thin.requested = 24
        monkeypatch.setattr(mr, "build_brief", lambda **kw: thin)
        monkeypatch.setattr(mr.ev, "load_impacts", lambda: {})

        m = mr.run(include_carry=False, today=TODAY)
        assert not m.ok
        assert not (tmp_path / "snapshot.json").exists()
        assert m.recorded == 0
