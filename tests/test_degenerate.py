"""Every public entry point, fed the inputs that should not happen.

A review that runs once evaporates. This is the same sweep written down: empty
series, single bars, constant prices, missing legs, garbage payloads. None of it
should crash, and none of it should invent a number - a constant price series has
no volatility and no correlation, and saying so is different from returning zero
because a division happened to survive.

The parsers get the same treatment. Treasury, Cboe and the Fed can all redesign
a page without telling anyone, and the failure has to be an empty result rather
than a confident wrong one.
"""
from __future__ import annotations

import numpy as np
import pytest

from printmoney.research import brief as B
from printmoney.research import contamination as C
from printmoney.research import decide as D
from printmoney.research import events as E
from printmoney.research import feeds as F
from printmoney.research import graph as G
from printmoney.research import indicators as I
from printmoney.research import macro as M
from printmoney.research import scorecard as SC
from printmoney.research.data import Bar, Series


def series(n: int, *, const: bool = False, start: float = 100.0) -> Series:
    bars, price = [], start
    for i in range(n):
        if not const:
            price *= 1.0 + (0.01 if i % 2 else -0.01)
        bars.append(Bar(ts=1_500_000_000 + i * 86_400, open=price, high=price,
                        low=price, close=price, volume=0.0, raw_close=price))
    return Series(symbol="X", name="X", bars=bars)


def feed(values, *, key="k", source="curve", unit="%") -> F.Feed:
    return F.Feed(key=key, name=key, source=source, unit=unit,
                  lines=[F.Line(day=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                                value=v) for i, v in enumerate(values)])


# --------------------------------------------------------------------------- #
class TestNothingCrashesOnNothing:
    @pytest.mark.parametrize("n", [0, 1, 2, 5, 69])
    def test_a_series_too_short_to_judge_returns_no_line(self, n):
        assert B._line(series(n)) is None

    def test_a_constant_series_produces_a_line_with_no_volatility(self):
        line = B._line(series(400, const=True))
        assert line is not None
        assert line.vol_annual == 0.0
        assert line.day == 0.0

    def test_a_brief_with_no_markets_still_yields_a_verdict(self):
        d = D.decide(B.Brief(lines=[], requested=0, loaded=0))
        assert d.focus is not None

    def test_a_measurement_over_nothing_is_not_real(self):
        assert M.measure({}, {}).links == []
        impact = E.measure("x", [], {}, [])
        assert impact.events == 0 and not impact.real

    def test_a_constant_feed_correlates_with_nothing(self):
        link = M.explains(feed([1.0] * 40), series(400, const=True))
        assert link.r == 0.0 and not link.real

    def test_a_graph_with_no_measured_links_still_builds(self):
        g = G.build(links=M.Table())
        assert g.nodes and g.why("NOT_A_NODE") == []

    def test_a_scorecard_over_nothing_scores_nothing(self):
        assert len(SC.backtest([])) == 0
        assert len(SC.backtest([series(900, const=True)])) == 0

    def test_a_sweep_benchmark_over_nothing_is_zero_not_an_error(self):
        assert I._buy_and_hold([]) == 0.0

    def test_a_position_held_through_a_flat_market_earns_nothing(self):
        _g, net, _t, _ts, _n = I._score(np.ones(300), np.zeros(299), 0.0)
        assert net == 0.0

    def test_an_empty_feed_serialises_without_inventing_a_reading(self):
        d = feed([]).to_dict()
        assert d["value"] is None and d["day"] is None
        assert d["change_1d"] is None and d["percentile"] is None

    def test_the_curve_needs_both_legs_and_says_so_by_returning_nothing(self):
        assert F.curve_spread({"ust10y": feed([4.0])}) is None
        assert F.curve_spread({}) is None


class TestQuizzesRefuseRatherThanCompromise:
    def test_no_universe_means_no_questions(self):
        assert C.build([], n=10) == []

    def test_a_series_too_short_means_no_questions(self):
        assert C.build([series(1)], n=10) == []

    def test_a_market_that_only_ever_rose_yields_no_quiz(self):
        """One-sided is worse than empty: it would score 100% for 'up'."""
        rising = Series(symbol="U", name="U", bars=[
            Bar(ts=1_500_000_000 + i * 86_400, open=100 + i, high=100 + i,
                low=100 + i, close=100 + i, volume=0.0, raw_close=100 + i)
            for i in range(900)])
        assert C.build([rising], n=40) == []

    def test_whatever_comes_back_is_always_balanced(self):
        wave = [0.01 if (i // 60) % 2 == 0 else -0.01 for i in range(900)]
        s = Series(symbol="W", name="W", bars=series(1).bars)
        price, bars = 100.0, []
        for i, r in enumerate(wave):
            price *= 1 + r
            bars.append(Bar(ts=1_500_000_000 + i * 86_400, open=price, high=price,
                            low=price, close=price, volume=0.0, raw_close=price))
        s = Series(symbol="W", name="W", bars=bars)
        for n in (4, 10, 40, 200):
            qs = C.build([s], n=n)
            if qs:
                ups = sum(1 for q in qs if q.truth == "up")
                assert ups == len(qs) - ups, n


class TestParsersFailEmptyNotConfident:
    """A redesigned page must produce nothing, never a plausible wrong number."""

    @pytest.mark.parametrize("junk", ["", "<html><body>we moved</body></html>",
                                      "not,a,csv", "null", "<!doctype html>"])
    def test_treasury(self, junk):
        assert F.parse_treasury(junk, "10 Yr") == []

    @pytest.mark.parametrize("junk", ["", "<html/>", "DATE\n", "garbage"])
    def test_cboe(self, junk):
        assert F.parse_cboe(junk) == []

    @pytest.mark.parametrize("junk", ["[]", '{"soma":{"summary":[]}}', "{}",
                                      '{"soma":[]}', "null", "not json", ""])
    def test_soma(self, junk):
        """Regression: an array where an object was expected raised deep inside
        a comprehension instead of returning nothing."""
        assert F.parse_soma(junk) == []

    @pytest.mark.parametrize("junk", ["{}", "[]", "null", "not json", "",
                                      '{"refRates": {}}'])
    def test_effr_shapes(self, junk):
        assert F.parse_effr(junk) == []

    @pytest.mark.parametrize("junk", ["{}", "null", "not json", "",
                                      '["a string", 42]'])
    def test_auction_shapes(self, junk):
        assert F.parse_auctions(junk, "dealer") == []

    @pytest.mark.parametrize("junk", ["[]", '[{"securityType":"Bill"}]'])
    def test_auctions(self, junk):
        assert F.parse_auctions(junk, "dealer") == []

    @pytest.mark.parametrize("junk", ["", "<html/>", "<a id=\"1\">nope</a>"])
    def test_fomc(self, junk):
        assert E.parse_fomc(junk) == []

    def test_effr_with_no_rates(self):
        assert F.parse_effr('{"refRates":[]}') == []

    def test_a_row_with_a_missing_field_is_skipped_not_defaulted(self):
        csv = 'Date,"10 Yr"\n08/25/2026,\n08/24/2026,not-a-number\n08/21/2026,4.70\n'
        rows = F.parse_treasury(csv, "10 Yr")
        assert [r["value"] for r in rows] == [4.70]


class TestStatisticsDoNotBlowUpAtTheEdges:
    def test_a_wilson_bound_on_no_samples_is_zero_not_a_crash(self):
        assert C.Report(cutoff="x").lower_bound([]) == 0.0
        assert SC.Score(label="x").lower_bound == 0.0

    def test_a_wilson_bound_on_one_sample_stays_inside_zero_and_one(self):
        rows = [SC.Resolved(day="d", symbol="X", call="elevated",
                            called_percentile=0.9, realised_percentile=0.9,
                            realised_vol=0.3)]
        score = SC.Score(label="x", resolved=rows)
        assert 0.0 <= score.lower_bound <= 1.0
        assert not score.beats_coin

    def test_a_correlation_with_too_few_points_is_zero(self):
        assert M._corr([1.0], [2.0]) == 0.0
        assert M._corr([], []) == 0.0

    def test_a_link_with_a_perfect_correlation_does_not_divide_by_zero(self):
        assert M.Link(feed="f", symbol="s", r=1.0, n=100).tstat == 0.0
        assert M.Link(feed="f", symbol="s", r=-1.0, n=100).tstat == 0.0

    def test_a_percentile_against_no_history_is_the_median(self):
        assert SC._percentile(0.5, []) == SC.MEDIAN
        assert SC._percentile(-1.0, [0.1, 0.2]) == SC.MEDIAN
