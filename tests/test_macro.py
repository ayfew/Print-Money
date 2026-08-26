"""Official daily readings: parsed correctly, and only quoted when they mean something.

Adding data sources is the easiest way to make a brief longer and the easiest way
to make it worse. The tests here hold the line in both directions - the parsers
have to survive the real file layouts, and the relationships have to survive
being measured before the brief is allowed to call any of them a reason.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from printmoney.research import feeds as F
from printmoney.research import macro as M
from printmoney.research.brief import Brief, MarketLine
from printmoney.research.data import Bar, Series
from printmoney.research.decide import decide
from printmoney.research.i18n import render_note

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)

# Real layouts, trimmed. The column names and the MM/DD/YYYY dates are exactly
# what the two sites serve, because that is the part a redesign would break.
TREASURY_CSV = (
    'Date,"1 Mo","2 Mo","3 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr",'
    '"10 Yr","20 Yr","30 Yr"\n'
    "08/25/2026,3.79,3.80,3.86,3.95,4.01,4.17,4.25,4.38,4.51,4.64,4.95,5.12\n"
    "08/24/2026,3.80,3.81,3.87,3.97,4.05,4.24,4.32,4.45,4.58,4.70,5.01,5.18\n"
    "08/21/2026,3.81,3.82,3.88,3.98,4.06,4.26,4.34,4.47,4.60,4.72,5.03,5.20\n"
)
REAL_CSV = (
    'Date,"5 YR","7 YR","10 YR","20 YR","30 YR"\n'
    "08/25/2026,2.04,2.16,2.32,2.70,2.92\n"
    "08/24/2026,2.09,2.22,2.38,2.75,2.97\n"
)
VIX_CSV = (
    "DATE,OPEN,HIGH,LOW,CLOSE\n"
    "08/24/2026,15.90,16.40,15.60,15.85\n"
    "08/25/2026,15.80,16.10,15.20,15.45\n"
)
SKEW_CSV = "DATE,SKEW\n08/24/2026,145.64\n08/25/2026,143.27\n"
EFFR_JSON = json.dumps({"refRates": [
    {"effectiveDate": "2026-08-24", "type": "EFFR", "percentRate": 3.63},
    {"effectiveDate": "2026-08-21", "type": "EFFR", "percentRate": 3.63},
]})


def _feed(key, values, *, source="treasury", unit="%", start=1):
    return F.Feed(key=key, name=key, source=source, unit=unit,
                  lines=[F.Line(day=f"2026-01-{start + i:02d}", value=v)
                         for i, v in enumerate(values)])


# --------------------------------------------------------------------------- #
class TestParsers:
    def test_treasury_picks_the_named_column(self):
        rows = F.parse_treasury(TREASURY_CSV, "10 Yr")
        assert [r["value"] for r in rows] == [4.64, 4.70, 4.72]
        assert rows[0]["day"] == "2026-08-25"      # ISO, not MM/DD/YYYY

    def test_treasury_real_curve_has_its_own_column_names(self):
        """The real curve says "10 YR"; the nominal one says "10 Yr"."""
        assert F.parse_treasury(REAL_CSV, "10 YR")[0]["value"] == 2.32
        assert F.parse_treasury(REAL_CSV, "10 Yr") == []

    def test_a_missing_column_yields_nothing_rather_than_zeros(self):
        """A silent zero would read as a rate of 0%, which is a real number."""
        assert F.parse_treasury(TREASURY_CSV, "50 Yr") == []

    def test_blank_cells_are_skipped_not_read_as_zero(self):
        csv = 'Date,"10 Yr"\n08/25/2026,\n08/24/2026,4.70\n'
        rows = F.parse_treasury(csv, "10 Yr")
        assert [r["value"] for r in rows] == [4.70]

    def test_cboe_takes_the_close_from_a_four_column_file(self):
        rows = F.parse_cboe(VIX_CSV)
        assert [r["value"] for r in rows] == [15.85, 15.45]

    def test_cboe_takes_the_only_column_from_a_two_column_file(self):
        assert [r["value"] for r in F.parse_cboe(SKEW_CSV)] == [145.64, 143.27]

    def test_a_redesigned_page_parses_to_nothing_rather_than_to_nonsense(self):
        assert F.parse_cboe("<html>we moved</html>") == []
        assert F.parse_treasury("<html>we moved</html>", "10 Yr") == []

    def test_effr_reads_the_new_york_fed_json(self):
        rows = F.parse_effr(EFFR_JSON)
        assert rows[0] == {"day": "2026-08-24", "value": 3.63}

    def test_rows_are_sorted_oldest_first_whatever_order_they_arrive_in(self):
        lines = F._to_lines(F.parse_treasury(TREASURY_CSV, "10 Yr"))
        assert [l.day for l in lines] == sorted(l.day for l in lines)
        assert lines[-1].value == 4.64          # newest last


# --------------------------------------------------------------------------- #
class TestFeedArithmetic:
    def test_change_is_in_the_series_own_units(self):
        f = _feed("ust10y", [4.70, 4.64])
        assert abs(f.change(1) - (-0.06)) < 1e-9

    def test_change_is_none_when_there_is_no_previous_reading(self):
        assert _feed("ust10y", [4.64]).change(1) is None

    def test_percentile_needs_a_real_history_before_it_answers(self):
        assert _feed("vix", [15.0, 16.0]).percentile() is None
        assert _feed("vix", list(range(40))).percentile() == 1.0

    def test_the_curve_spread_is_ten_minus_two_on_shared_days(self):
        feeds = {"ust10y": _feed("ust10y", [4.64, 4.70]),
                 "ust2y": _feed("ust2y", [4.17, 4.24])}
        spread = F.curve_spread(feeds)
        assert [round(l.value, 2) for l in spread.lines] == [0.47, 0.46]

    def test_the_curve_needs_both_legs(self):
        assert F.curve_spread({"ust10y": _feed("ust10y", [4.64])}) is None

    def test_days_present_in_only_one_leg_are_dropped(self):
        feeds = {"ust10y": _feed("ust10y", [4.64, 4.70, 4.72]),
                 "ust2y": _feed("ust2y", [4.17, 4.24])}
        assert len(F.curve_spread(feeds).lines) == 2


# --------------------------------------------------------------------------- #
def _series(symbol, returns, start=100.0, day0=1):
    bars, price, ts = [], start, 1_600_000_000
    bars.append(Bar(ts=ts, open=price, high=price, low=price, close=price, volume=1.0))
    for i, r in enumerate(returns, 1):
        price *= 1.0 + r
        bars.append(Bar(ts=ts + i * 86_400, open=price, high=price, low=price,
                        close=price, volume=1.0))
    return Series(symbol=symbol, name=symbol, bars=bars)


class TestMeasurement:
    def test_correlation_is_taken_on_changes_not_on_levels(self):
        """Two series that both trend correlate at ~1 while explaining nothing."""
        import random
        rng = random.Random(3)
        # Levels march upward together; the daily changes are independent.
        feed = F.Feed(key="ust10y", name="10y", source="treasury", unit="%", lines=[])
        bars = [Bar(ts=1_600_000_000, open=100, high=100, low=100, close=100, volume=1)]
        level, price = 1.0, 100.0
        for i in range(1, 400):
            level += 0.01 + rng.gauss(0, 0.02)
            price *= 1.0 + 0.001 + rng.gauss(0, 0.01)
            day = datetime.fromtimestamp(1_600_000_000 + i * 86_400,
                                         tz=timezone.utc).strftime("%Y-%m-%d")
            feed.lines.append(F.Line(day=day, value=level))
            bars.append(Bar(ts=1_600_000_000 + i * 86_400, open=price, high=price,
                            low=price, close=price, volume=1))
        link = M.explains(feed, Series(symbol="X", name="X", bars=bars))
        assert abs(link.r) < 0.25          # levels would have given ~+0.99

    def test_a_link_needs_both_significance_and_size(self):
        assert not M.Link(feed="vix", symbol="GLD", r=0.05, n=5000).real
        assert not M.Link(feed="vix", symbol="GLD", r=0.40, n=12).real
        assert M.Link(feed="vix", symbol="GLD", r=0.30, n=600).real

    def test_a_treasury_yield_against_a_treasury_fund_is_arithmetic_not_a_reason(self):
        link = M.Link(feed="ust10y", symbol="TLT", r=-0.91, n=659)
        assert link.mechanical
        assert not link.real

    def test_vix_against_us_equity_is_the_same_fact_said_twice(self):
        for symbol in ("SPY", "QQQ", "XLK", "IWM"):
            link = M.Link(feed="vix", symbol=symbol, r=-0.70, n=750)
            assert link.mechanical, symbol
            assert not link.real

    def test_vix_against_everything_else_still_counts(self):
        """Bitcoin at -0.33 is worth saying precisely because people expect 0."""
        for symbol in ("BTC-USD", "GLD", "USO", "EEM", "THD"):
            link = M.Link(feed="vix", symbol=symbol, r=-0.34, n=750)
            assert not link.mechanical, symbol
            assert link.real, symbol

    def test_strength_is_named_honestly_at_the_low_end(self):
        assert M.Link(feed="real10y", symbol="GLD", r=-0.18, n=659).strength == "weak"
        assert M.Link(feed="ust2y", symbol="UUP", r=+0.43, n=659).strength == "strong"
        assert M.Link(feed="ust2y", symbol="GLD", r=-0.26, n=659).strength == "moderate"

    def test_direction_matches_the_sign(self):
        assert M.Link(feed="ust2y", symbol="UUP", r=+0.43, n=9).direction == "with"
        assert M.Link(feed="real10y", symbol="GLD", r=-0.18, n=9).direction == "against"

    def test_the_table_keeps_what_it_discarded(self):
        t = M.Table(links=[M.Link(feed="vix", symbol="GLD", r=0.30, n=600),
                           M.Link(feed="skew", symbol="GLD", r=0.02, n=600)])
        assert len(t.real()) == 1 and len(t.dead()) == 1

    def test_a_saved_table_survives_the_round_trip(self, tmp_path):
        path = tmp_path / "macro.json"
        t = M.Table(span="x", links=[M.Link(feed="vix", symbol="GLD", r=0.3, n=600)])
        M.save(t, path=path)
        back = M.load(path)
        assert [l.to_dict() for l in back.links] == [l.to_dict() for l in t.links]

    def test_a_missing_table_loads_as_empty_rather_than_raising(self, tmp_path):
        assert M.load(tmp_path / "nope.json").links == []


# --------------------------------------------------------------------------- #
def _line(symbol="GLD", name="Gold", day=-0.02):
    return MarketLine(symbol=symbol, name=name, last=400.0, day=day, week=0.0,
                      month=0.0, year=0.0, vol_annual=0.2, zscore=0.5,
                      intraday_share=0.4, vol_percentile=0.5, drawdown=-0.01)


def _brief(lines):
    return Brief(generated_at=NOW, lines=list(lines), requested=len(lines),
                 loaded=len(lines))


def _moving_feed(key, latest_change, *, unit="%", source="treasury"):
    """A feed that has sat still for months and then moved by ``latest_change``.

    The flat history matters: the driver gate asks whether today's move is large
    against this reading's *own* recent moves, so a quiet series makes any real
    move count and a tiny one still fail.
    """
    vals = [1.0 + 0.0005 * (i % 3) for i in range(120)]
    vals.append(vals[-1] + latest_change)
    return F.Feed(
        key=key, name=key, source=source, unit=unit,
        lines=[F.Line(day=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", value=v)
               for i, v in enumerate(vals)])


class TestAttribution:
    LINKS = M.Table(links=[M.Link(feed="real10y", symbol="GLD", r=-0.30, n=659)])

    def test_a_driver_that_moved_the_right_way_is_named(self):
        feeds = {"real10y": _moving_feed("real10y", +0.08)}
        d = decide(_brief([_line(day=-0.02)]), feeds=feeds, links=self.LINKS,
                   today=NOW.date())
        assert [n.key for n in d.why] == ["why_move"]
        assert d.why[0].symbols == ("GLD",)

    def test_a_driver_that_moved_the_wrong_way_explains_nothing(self):
        """Gold down while real yields also fell is not explained by real yields."""
        feeds = {"real10y": _moving_feed("real10y", -0.08)}
        d = decide(_brief([_line(day=-0.02)]), feeds=feeds, links=self.LINKS,
                   today=NOW.date())
        assert d.why == []

    def test_a_driver_that_barely_twitched_explains_nothing(self):
        feeds = {"real10y": _moving_feed("real10y", +0.0001)}
        d = decide(_brief([_line(day=-0.02)]), feeds=feeds, links=self.LINKS,
                   today=NOW.date())
        assert d.why == []

    def test_a_move_inside_the_noise_is_not_worth_a_reason(self):
        feeds = {"real10y": _moving_feed("real10y", +0.08)}
        d = decide(_brief([_line(day=-0.002)]), feeds=feeds, links=self.LINKS,
                   today=NOW.date())
        assert d.why == []

    def test_with_no_measured_links_nothing_is_offered_as_a_reason(self):
        feeds = {"real10y": _moving_feed("real10y", +0.08)}
        d = decide(_brief([_line(day=-0.02)]), feeds=feeds, links=M.Table(),
                   today=NOW.date())
        assert d.why == []
        assert d.context                      # but the readings still print

    def test_the_attribution_says_it_is_not_a_forecast(self):
        feeds = {"real10y": _moving_feed("real10y", +0.08)}
        d = decide(_brief([_line(day=-0.02)]), feeds=feeds, links=self.LINKS,
                   today=NOW.date())
        text = render_note(d.why[0], "en", {"GLD": "Gold"})
        assert "r = -0.30" in text and "659" in text
        assert "moderate" in text


class TestContext:
    def test_readings_are_printed_in_reading_order(self):
        feeds = {k: _moving_feed(k, +0.01) for k in
                 ("effr", "ust2y", "ust10y", "real10y", "vix")}
        d = decide(_brief([_line()]), feeds=feeds, links=M.Table(), today=NOW.date())
        assert [n.params["label"] for n in d.context] == \
            ["effr", "ust2y", "ust10y", "real10y", "vix"]

    def test_yields_are_quoted_in_basis_points(self):
        feeds = {"ust10y": _moving_feed("ust10y", -0.06)}
        d = decide(_brief([_line()]), feeds=feeds, links=M.Table(), today=NOW.date())
        assert "-6bp" in render_note(d.context[0], "en")

    def test_an_index_is_not_quoted_in_basis_points(self):
        feeds = {"vix": _moving_feed("vix", -0.40, unit="pts", source="cboe")}
        d = decide(_brief([_line()]), feeds=feeds, links=M.Table(), today=NOW.date())
        text = render_note(d.context[0], "en")
        assert "bp" not in text and "-0.40" in text

    def test_the_curve_says_whether_it_is_inverted(self):
        feeds = {"curve": F.Feed(key="curve", name="2s10s", source="curve", unit="%",
                                 lines=[F.Line(day="2026-08-24", value=0.10),
                                        F.Line(day="2026-08-25", value=-0.05)])}
        d = decide(_brief([_line()]), feeds=feeds, links=M.Table(), today=NOW.date())
        assert "inverted" in render_note(d.context[0], "en")

    def test_context_is_absent_entirely_when_no_feed_could_be_read(self):
        d = decide(_brief([_line()]), feeds={}, links=M.Table(), today=NOW.date())
        assert d.context == [] and d.why == []

    def test_every_context_note_is_citable(self):
        from printmoney.research import sources
        feeds = {k: _moving_feed(k, +0.01, source=s) for k, s in
                 (("effr", "nyfed"), ("ust10y", "treasury"), ("vix", "cboe"))}
        d = decide(_brief([_line()]), feeds=feeds, links=M.Table(), today=NOW.date())
        tiers = {sources.get(n.source).tier for n in d.context}
        assert tiers <= {1, 2}                # official or the exchange itself

    def test_thai_and_english_both_render_every_reading(self):
        feeds = {k: _moving_feed(k, +0.01) for k in ("ust2y", "ust10y", "real10y")}
        d = decide(_brief([_line()]), feeds=feeds, links=M.Table(), today=NOW.date())
        for note in d.context:
            for lang in ("th", "en"):
                text = render_note(note, lang)
                assert "{" not in text and text.strip()
