"""The cost arithmetic, and the guards that stop a study fooling itself."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from printmoney.research.data import Bar, Series, align, day_key
from printmoney.research.study import (
    annualise,
    compound,
    evaluate_rule,
    holding_period_sweep,
    max_drawdown,
    noise_band,
    session_split,
    sharpe,
)

DAY = 86400
START = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())


def _series(symbol, n=600, drift=0.0004, intraday_share=0.5, seed=1):
    """A synthetic market with a known split between session and overnight."""
    import random

    rng = random.Random(seed)
    bars = []
    close = 100.0
    for i in range(n):
        overnight = drift * (1 - intraday_share) + rng.gauss(0, 0.004)
        intra = drift * intraday_share + rng.gauss(0, 0.006)
        o = close * (1 + overnight)
        c = o * (1 + intra)
        bars.append(Bar(START + i * DAY, o, max(o, c) * 1.002, min(o, c) * 0.998, c, 1e6))
        close = c
    return Series(symbol=symbol, name=symbol, bars=bars)


class TestMath:
    def test_annualise_matches_compounding(self):
        rets = [0.001] * 252
        assert annualise(rets) == pytest.approx(1.001**252 - 1, rel=1e-9)

    def test_a_wipeout_is_minus_one_hundred_percent(self):
        assert annualise([-1.0, 0.5, 0.5]) == -1.0

    def test_compound_never_goes_negative(self):
        assert compound([-1.5]) == 0.0

    def test_sharpe_of_a_constant_is_zero(self):
        assert sharpe([0.001] * 100) == 0.0

    def test_drawdown_is_peak_to_trough(self):
        assert max_drawdown([0.5, -0.5]) == pytest.approx(-0.5)


class TestAlignment:
    def test_dates_align_across_different_session_stamps(self):
        """Equities stamp the opening bell, crypto stamps midnight UTC.

        Intersecting raw timestamps gives an empty set and a study of nothing,
        which is exactly the bug this guards.
        """
        equity = _series("SPY")
        crypto = Series(
            symbol="BTC",
            name="BTC",
            bars=[Bar(b.ts + 34200, b.open, b.high, b.low, b.close, b.volume) for b in equity.bars],
        )
        raw_overlap = {b.ts for b in equity.bars} & {b.ts for b in crypto.bars}
        assert not raw_overlap

        days, aligned = align([equity, crypto])
        assert len(days) == len(equity.bars)
        assert set(aligned) == {"SPY", "BTC"}

    def test_day_key_is_a_utc_date(self):
        assert day_key(START) == "2024-01-01"


class TestTheFeeWall:
    """The whole thesis of the module, tested on data where the answer is known."""

    def _aligned(self, n=600):
        series = [_series(f"S{i}", n=n, seed=i) for i in range(4)]
        _days, aligned = align(series)
        return aligned

    def test_gross_return_barely_depends_on_holding_period(self, ):
        rows = holding_period_sweep(self._aligned(), periods=(1, 21, 252), fees=(0.0,))
        gross = [r.gross for r in rows]
        assert max(gross) - min(gross) < 0.06, gross

    def test_daily_trading_is_destroyed_and_quarterly_is_not(self):
        rows = {r.days: r for r in holding_period_sweep(self._aligned(), fees=(0.0010,))}
        daily, quarterly = rows[1].net[0.0010], rows[63].net[0.0010]
        assert daily < quarterly
        # 252 round trips at 10bp is a 25% annual toll; whatever the market did,
        # the daily version has to end up well behind and below zero.
        assert daily < 0
        assert rows[1].gross - daily > 0.20, "the toll must show up as ~25%/yr"
        assert quarterly > daily + 0.15

    def test_the_fee_drag_is_frequency_times_cost(self):
        rows = {r.days: r for r in holding_period_sweep(self._aligned(), fees=(0.0010,))}
        for hold, row in rows.items():
            expected = row.gross - (252.0 / hold) * 0.0010
            # geometric vs arithmetic, so allow a loose band; the point is the shape
            assert row.net[0.0010] <= row.gross
            if hold >= 21:
                assert row.net[0.0010] == pytest.approx(expected, abs=0.02)


class TestSessions:
    def test_the_split_recovers_where_the_return_came_from(self):
        """A market built to earn entirely intraday must read as entirely intraday."""
        series = [_series(f"S{i}", intraday_share=1.0, seed=i) for i in range(4)]
        _days, aligned = align(series)
        split = session_split(aligned)
        assert split.intraday > 0
        assert abs(split.overnight) < abs(split.intraday)

    def test_and_the_other_way_round(self):
        series = [_series(f"S{i}", intraday_share=0.0, seed=i) for i in range(4)]
        _days, aligned = align(series)
        split = session_split(aligned)
        assert split.overnight > 0
        assert abs(split.intraday) < abs(split.overnight)


class TestNoiseBand:
    def _aligned(self):
        series = [_series(f"S{i}", seed=i) for i in range(6)]
        _days, aligned = align(series)
        return aligned

    def test_a_random_rule_lands_inside_the_random_band(self):
        aligned = self._aligned()
        band = noise_band(aligned, trials=120, seed=3)
        assert band.low <= band.median <= band.high

        import random

        rng = random.Random(99)
        result = evaluate_rule(
            aligned, "coin flip", lambda i, a: rng.choice(list(a)), band=band, warmup=1
        )
        assert result.inside_noise, (result.gross, band)

    def test_trading_every_day_is_charged_for_every_day(self):
        aligned = self._aligned()
        traded = evaluate_rule(aligned, "always", lambda i, a: "S0", fee=0.0010, warmup=1)
        idle = evaluate_rule(aligned, "never", lambda i, a: None, fee=0.0010, warmup=1)
        assert traded.trades_per_year == pytest.approx(252, rel=0.02)
        assert idle.trades_per_year == 0.0
        assert idle.net == 0.0
        assert traded.net < traded.gross
