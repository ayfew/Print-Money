"""The indicator sweep: does it stop itself finding things that are not there?

A sweep over 146 rules will produce winners whether or not any exist. The value
of this module is entirely in the four bars it makes a rule clear, so that is
what these tests hold - plus the two alignment mistakes that would each silently
manufacture an edge: reading tomorrow's return, and computing a threshold from
data the rule could not have seen.
"""
from __future__ import annotations

import numpy as np
import pytest

from printmoney.research import indicators as I
from printmoney.research.data import Bar, Series


def _series(symbol: str, rets, start: float = 100.0) -> Series:
    bars, price, ts = [], start, 1_500_000_000
    bars.append(Bar(ts=ts, open=price, high=price, low=price, close=price,
                    volume=1e6, raw_close=price))
    for i, r in enumerate(rets, 1):
        prev = price
        price *= 1.0 + r
        bars.append(Bar(ts=ts + i * 86_400, open=prev, high=max(prev, price),
                        low=min(prev, price), close=price, volume=1e6,
                        raw_close=price))
    return Series(symbol=symbol, name=symbol, bars=bars)


def _rets(series: Series) -> np.ndarray:
    c = np.array(series.closes)
    return np.diff(c) / c[:-1]


# --------------------------------------------------------------------------- #
class TestScoringAlignment:
    # The convention, stated once: rets[i] is the move from bar i into bar i+1,
    # and pos[i] is the position decided on bar i's close. So pos[i] earns
    # rets[i]. The two tests below pin both halves of that down, because an
    # off-by-one either way is invisible in the output and changes everything.

    def test_a_position_earns_the_move_that_comes_after_it(self):
        rets = np.array([0.0, 0.10, 0.0, 0.10, 0.0, 0.10, 0.0, 0.10] * 40)
        # Long exactly on the bars whose *next* move is up. Nothing in _score
        # forbids this - keeping it honest is _signal's job - but it documents
        # which return a position is paired with.
        prescient = np.concatenate([(rets > 0).astype(float), [0.0]])
        gross, _net, _turn, _t, _n = I._score(prescient, rets, 0.0)
        assert gross > 1.0

    def test_reacting_to_a_move_that_already_happened_earns_nothing(self):
        """Yesterday's rise does not pay today, which is the point."""
        rets = np.array([0.0, 0.10, 0.0, 0.10, 0.0, 0.10, 0.0, 0.10] * 40)
        reactive = np.concatenate([[0.0], (rets > 0).astype(float)])
        gross, _net, _turn, _t, _n = I._score(reactive, rets, 0.0)
        assert abs(gross) < 0.01

    def test_costs_are_charged_on_every_change_of_position(self):
        rets = np.zeros(600)
        flip = np.array([float(i % 2) for i in range(601)])
        _g, net, turnover, _t, _n = I._score(flip, rets, 0.0010)
        assert turnover > 200                    # a flip every single day
        assert net < -0.20                       # and it costs a fortune

    def test_holding_costs_exactly_one_entry(self):
        """Getting in is a trade. Staying in is not."""
        rets = np.zeros(600)
        _g, net, turnover, _t, n = I._score(np.ones(601), rets, 0.0010)
        assert turnover == pytest.approx(0.0010 / 0.0010 * 252 / n, rel=0.01)
        assert net == pytest.approx(-0.0010 * 252 / n, rel=0.01)

    def test_a_short_sample_scores_as_nothing_rather_than_as_noise(self):
        assert I._score(np.ones(50), np.zeros(49), 0.001) == (0.0, 0.0, 0.0, 0.0, 49)


class TestPointInTime:
    def test_the_trailing_median_never_sees_its_own_future(self):
        x = np.concatenate([np.zeros(I.WARMUP + 50), np.full(200, 100.0)])
        med = I._trailing_median(x)
        # At the first post-warmup point the future spike must not be in there.
        assert med[I.WARMUP + 1] == 0.0
        assert np.isnan(med[:I.WARMUP]).all()

    def test_the_median_does_move_once_history_accumulates(self):
        rng = np.random.default_rng(1)
        x = rng.normal(size=I.WARMUP + 400).cumsum()
        med = I._trailing_median(x)
        tail = med[np.isfinite(med)]
        assert tail.size > 100 and tail.std() > 0


class TestTheFourBars:
    def _sweep(self, **kw):
        sw = I.Sweep(null_high=0.05, buy_and_hold=0.10, **kw)
        return sw

    def _result(self, net, *, turnover=8.0, significant=True):
        r = I.Result(name="X", group="Momentum Indicators", inverted=False,
                     gross=net, net=net, turnover=turnover, tstat=5.0,
                     n_days=5000)
        r.significant = significant
        return r

    def test_a_rule_below_buy_and_hold_does_not_survive(self):
        sw = self._sweep(results=[self._result(0.09)])
        assert sw.survivors() == []

    def test_a_rule_inside_the_noise_band_does_not_survive(self):
        sw = self._sweep(results=[self._result(0.04)])
        assert sw.survivors() == []

    def test_a_rule_that_never_trades_does_not_survive(self):
        """Regression: the top result was +12.7% on one trade in ten years."""
        sw = self._sweep(results=[self._result(0.30, turnover=0.1)])
        assert sw.survivors() == []

    def test_a_rule_that_failed_the_correction_does_not_survive(self):
        sw = self._sweep(results=[self._result(0.30, significant=False)])
        assert sw.survivors() == []

    def test_a_rule_clearing_all_four_does_survive(self):
        sw = self._sweep(results=[self._result(0.30)])
        assert len(sw.survivors()) == 1

    def test_the_floor_is_the_higher_of_the_two_benchmarks(self):
        sw = I.Sweep(null_high=0.20, buy_and_hold=0.10,
                     results=[self._result(0.15)])
        assert sw.survivors() == []


class TestMultipleTesting:
    def _rows(self, ps):
        out = []
        for i, p in enumerate(ps):
            r = I.Result(name=f"R{i}", group="g", inverted=False, gross=0.1,
                         net=0.1, turnover=5.0, tstat=3.0, n_days=5000)
            r.p_value = p
            out.append(r)
        return out

    def test_pure_noise_produces_no_discoveries(self):
        rng = np.random.default_rng(4)
        rows = self._rows(rng.uniform(size=200))
        I._benjamini_hochberg(rows)
        assert sum(r.significant for r in rows) == 0

    def test_a_genuinely_strong_result_still_gets_through(self):
        rows = self._rows([1e-12] + list(np.linspace(0.2, 1.0, 199)))
        I._benjamini_hochberg(rows)
        assert rows[0].significant

    def test_a_negative_rule_is_never_marked_significant(self):
        rows = self._rows([1e-12])
        rows[0].net = -0.4
        I._benjamini_hochberg(rows)
        assert not rows[0].significant

    def test_the_correction_is_stricter_than_a_bare_five_percent(self):
        ps = [0.01] * 3 + [0.9] * 197
        rows = self._rows(ps)
        I._benjamini_hochberg(rows)
        # All three would pass an uncorrected 5% test; BH refuses them.
        assert sum(r.significant for r in rows) == 0


class TestBenchmarks:
    def test_buy_and_hold_is_measured_after_the_warmup(self):
        flat = _series("A", [0.0] * (I.WARMUP + 500))
        assert abs(I._buy_and_hold([flat])) < 1e-9

    def test_buy_and_hold_annualises(self):
        up = _series("A", [0.0004] * (I.WARMUP + 1000))
        rate = I._buy_and_hold([up])
        assert 0.08 < rate < 0.13          # ~4bp a day compounded

    def test_a_series_too_short_to_judge_is_skipped(self):
        assert I._buy_and_hold([_series("A", [0.01] * 50)]) == 0.0

    def test_the_null_band_is_positive_in_a_rising_market(self):
        """The whole reason the band exists: beta looks like skill."""
        rng = np.random.default_rng(11)
        up = _series("A", 0.0005 + rng.normal(0, 0.01, I.WARMUP + 900))
        lo, mid, hi = I._empirical_null([up], 0.0, draws=40, seed=3)
        assert hi > 0 and lo <= mid <= hi


@pytest.mark.skipif(not I.available(), reason="TA-Lib not installed")
class TestAgainstTALib:
    def test_the_catalogue_excludes_the_groups_with_no_trading_meaning(self):
        groups = {g for _n, g in I._catalogue()}
        assert not (groups & I.SKIP_GROUPS)
        assert "Momentum Indicators" in groups

    def test_an_indicator_computes_to_the_length_of_its_input(self):
        rng = np.random.default_rng(2)
        c = 100 + rng.normal(0, 1, 400).cumsum()
        inputs = {"open": c, "high": c + 1, "low": c - 1, "close": c,
                  "volume": np.full(400, 1e6)}
        out = I._compute("RSI", inputs)
        assert out is not None and out.shape == c.shape

    def test_a_multi_line_indicator_returns_its_first_line(self):
        rng = np.random.default_rng(2)
        c = 100 + rng.normal(0, 1, 400).cumsum()
        inputs = {"open": c, "high": c + 1, "low": c - 1, "close": c,
                  "volume": np.full(400, 1e6)}
        assert I._compute("MACD", inputs).shape == c.shape

    def test_an_unknown_function_returns_nothing_rather_than_raising(self):
        c = np.arange(400, dtype=float) + 100
        inputs = {"open": c, "high": c, "low": c, "close": c,
                  "volume": np.full(400, 1e6)}
        assert I._compute("NOT_A_FUNCTION", inputs) is None

    def test_a_signal_is_zero_through_the_warmup(self):
        rng = np.random.default_rng(5)
        s = _series("A", rng.normal(0, 0.01, I.WARMUP + 400))
        pos = I._signal("RSI", "Momentum Indicators", s)
        assert pos is not None
        assert (pos[:I.WARMUP] == 0).all()
        assert set(np.unique(pos)) <= {0.0, 1.0}

    def test_a_sweep_over_a_few_indicators_reports_all_four_bars(self):
        rng = np.random.default_rng(6)
        universe = [_series(f"M{i}", rng.normal(0.0003, 0.012, I.WARMUP + 800))
                    for i in range(4)]
        sw = I.sweep(universe, limit=4)
        assert sw.results and sw.markets == 4
        assert sw.buy_and_hold != 0.0
        assert sw.null_low <= sw.null_median <= sw.null_high
        for r in sw.results:
            assert 0.0 <= r.p_value <= 1.0

    def test_every_rule_is_tested_together_with_its_inverse(self):
        rng = np.random.default_rng(6)
        universe = [_series(f"M{i}", rng.normal(0.0003, 0.012, I.WARMUP + 800))
                    for i in range(4)]
        sw = I.sweep(universe, limit=4)
        names = [r.name for r in sw.results]
        for name in set(names):
            assert names.count(name) == 2
        for name in set(names):
            pair = [r for r in sw.results if r.name == name]
            assert {p.inverted for p in pair} == {False, True}
            assert abs(pair[0].gross + pair[1].gross) < 1e-9
            # ...and both pay the same tolls, which is the point of testing it.
            assert abs(pair[0].turnover - pair[1].turnover) < 1e-9
