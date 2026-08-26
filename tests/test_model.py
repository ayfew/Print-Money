"""Volatility estimation, the path engine, and calibrating one to market quotes."""
from __future__ import annotations

import math

import numpy as np
import pytest

from printmoney.model import vol as volmod
from printmoney.model.calibrate import (
    Observation,
    calibrate,
    calibrate_bank,
    lognormal_cdf,
    lognormal_prob_above,
    lognormal_prob_touch,
    blend_vol,
)
from printmoney.data.types import INF, LegKind
from printmoney.model.paths import BGK_BETA, build_ensemble, mix_tail, simulate, simulate_bank

YEAR = 1.0
DAY = 1.0 / 365.25


class TestVolatility:
    def test_estimators_recover_the_generating_volatility(self, hour_candles, cfg):
        """All four estimators should land near the 50% the fixture was built with."""
        for name in ("close_to_close", "parkinson", "garman_klass", "rogers_satchell", "yang_zhang"):
            v = volmod.ESTIMATORS[name](hour_candles)
            assert 0.35 < v < 0.70, f"{name} returned {v:.3f}"

    def test_blend_applies_premium_and_bounds(self, hour_candles, cfg):
        cfg.model.vol_risk_premium = 1.2
        est = volmod.estimate(hour_candles, cfg.model)
        assert est.annual == pytest.approx(est.raw_annual * 1.2, rel=1e-9)
        assert est.bars == len(hour_candles)

        cfg.model.vol_cap_annual = 0.30
        cfg.model.vol_floor_annual = 0.10
        capped = volmod.estimate(hour_candles, cfg.model)
        assert capped.annual == pytest.approx(0.30)
        assert capped.clipped

    def test_too_little_history_is_an_error(self, cfg):
        with pytest.raises(ValueError):
            volmod.estimate([], cfg.model)

    def test_standardised_returns_are_standardised(self, hour_candles):
        r = volmod.standardized_returns(hour_candles)
        assert abs(float(np.mean(r))) < 1e-9
        assert float(np.std(r, ddof=1)) == pytest.approx(1.0, rel=1e-9)


class TestPathBank:
    def test_gbm_matches_the_closed_form_lognormal(self, cfg):
        bank = simulate_bank(DAY, cfg.model, generator="gbm", seed=5, n_paths=60_000)
        spot, sigma = 80_000.0, 0.6
        for strike in (76_000.0, 80_000.0, 84_000.0):
            mc = bank.prob_above(spot, sigma, 0.0, strike)
            exact = lognormal_prob_above(spot, sigma, DAY, strike)
            assert mc == pytest.approx(exact, abs=0.01), f"strike {strike}"

    def test_rescaling_is_exact_not_approximate(self, cfg):
        """Realising the same bank at two volatilities must agree with the bank."""
        bank = simulate_bank(DAY, cfg.model, generator="gbm", seed=6)
        a = bank.realize(80_000.0, 0.40, 0.0, "a")
        b = bank.realize(80_000.0, 0.80, 0.0, "b")
        assert a.prob_above(82_000.0) < b.prob_above(82_000.0)
        assert a.prob_above(82_000.0) == pytest.approx(
            bank.prob_above(80_000.0, 0.40, 0.0, 82_000.0)
        )

    def test_probabilities_over_a_partition_sum_to_one(self, cfg):
        bank = simulate_bank(DAY, cfg.model, generator="gbm", seed=7)
        edges = [70_000.0, 75_000.0, 80_000.0, 85_000.0]
        total = bank.prob_range(80_000.0, 0.5, 0.0, -INF, edges[0])
        for lo, hi in zip(edges, edges[1:]):
            total += bank.prob_range(80_000.0, 0.5, 0.0, lo, hi)
        total += bank.prob_range(80_000.0, 0.5, 0.0, edges[-1], INF)
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_touch_beats_terminal_and_respects_the_barrier(self, cfg):
        bank = simulate_bank(DAY, cfg.model, generator="gbm", seed=8, n_paths=40_000)
        spot, sigma = 80_000.0, 0.6
        touch = bank.prob_touch_up(spot, sigma, 0.0, 82_000.0)
        end_above = bank.prob_above(spot, sigma, 0.0, 82_000.0)
        # Reflection principle: touching is about twice as likely as finishing above.
        assert touch > end_above
        assert touch == pytest.approx(2 * end_above, rel=0.25)
        assert bank.prob_touch_up(spot, sigma, 0.0, 79_000.0) == 1.0
        assert bank.prob_touch_down(spot, sigma, 0.0, 81_000.0) == 1.0

    def test_continuity_correction_raises_touch_probability(self, cfg):
        bank = simulate_bank(DAY, cfg.model, generator="gbm", seed=9)
        raw = bank.prob_touch_up(80_000.0, 0.6, 0.0, 83_000.0, correct=False)
        corrected = bank.prob_touch_up(80_000.0, 0.6, 0.0, 83_000.0, correct=True)
        assert corrected >= raw
        assert BGK_BETA == pytest.approx(0.5826)

    def test_bootstrap_falls_back_when_there_is_no_history(self, cfg):
        bank = simulate_bank(DAY, cfg.model, generator="bootstrap", shock_pool=None, seed=3)
        assert bank.generator == "gbm"

    def test_tail_mixture_fattens_the_tails_only(self, cfg):
        main = simulate_bank(DAY, cfg.model, generator="gbm", seed=11, n_paths=20_000)
        tail = simulate_bank(DAY, cfg.model, generator="gbm", seed=12, n_paths=1_000)
        mixed = mix_tail(main, tail, 0.05, 3.0)
        far = 96_000.0
        assert mixed.prob_above(80_000.0, 0.5, 0.0, far) > main.prob_above(80_000.0, 0.5, 0.0, far)
        # the middle barely moves
        assert mixed.prob_above(80_000.0, 0.5, 0.0, 80_000.0) == pytest.approx(
            main.prob_above(80_000.0, 0.5, 0.0, 80_000.0), abs=0.03
        )


class TestClosedForms:
    def test_touch_is_twice_terminal_for_a_driftless_log_price(self):
        sigma, T = 0.6, DAY
        drift = 0.5 * sigma * sigma  # cancels the -sigma^2/2 in the log drift
        t = lognormal_prob_touch(80_000.0, sigma, T, 81_000.0, up=True, drift=drift)
        a = lognormal_prob_above(80_000.0, sigma, T, 81_000.0, drift=drift)
        assert t == pytest.approx(2 * a, rel=1e-6)

    def test_cdf_is_monotone_in_strike(self):
        prev = -1.0
        for k in range(60_000, 100_000, 5_000):
            v = lognormal_cdf(80_000.0, 0.5, DAY, float(k))
            assert v >= prev
            prev = v

    def test_blend_is_geometric(self):
        assert blend_vol(0.30, 0.60, 0.5) == pytest.approx(math.sqrt(0.30 * 0.60))
        assert blend_vol(0.30, None, 0.5) == 0.30
        assert blend_vol(0.30, 0.60, 1.0) == pytest.approx(0.60)


class TestCalibration:
    def _observations(self, spot, sigma, years, strikes):
        return [
            Observation(
                kind=LegKind.ABOVE,
                market_prob=lognormal_prob_above(spot, sigma, years, k),
                weight=1.0,
                strike=k,
            )
            for k in strikes
        ]

    def test_lognormal_fit_recovers_the_volatility_it_was_given(self):
        spot, sigma, years = 80_000.0, 0.55, DAY
        obs = self._observations(spot, sigma, years, [74_000.0, 78_000.0, 82_000.0, 86_000.0])
        cal = calibrate(obs, spot, years)
        assert cal.ok
        assert cal.sigma == pytest.approx(sigma, rel=0.02)

    def test_fit_needs_enough_informative_quotes(self):
        cal = calibrate(self._observations(80_000.0, 0.5, DAY, [80_000.0]), 80_000.0, DAY)
        assert not cal.ok
        assert "informative quotes" in cal.reason

    def test_bank_fit_recovers_volatility_from_its_own_distribution(self, cfg):
        """Fitting against the simulated distribution, not a lognormal proxy."""
        spot, sigma = 80_000.0, 0.55
        bank = simulate_bank(DAY, cfg.model, generator="gbm", seed=21, n_paths=60_000)
        strikes = [74_000.0, 78_000.0, 82_000.0, 86_000.0]
        obs = [
            Observation(
                kind=LegKind.ABOVE,
                market_prob=bank.prob_above(spot, sigma, 0.0, k),
                weight=1.0,
                strike=k,
            )
            for k in strikes
        ]
        cal = calibrate_bank(bank, obs, spot)
        assert cal.ok
        assert cal.sigma == pytest.approx(sigma, rel=0.03)


def test_ensemble_members_use_the_volatility_source_they_asked_for(cfg):
    cfg.model.ensemble = [
        {"name": "base", "vol_source": "base", "vol_mult": 1.0, "generator": "gbm"},
        {"name": "implied", "vol_source": "implied", "vol_mult": 1.0, "generator": "gbm"},
        {"name": "realized", "vol_source": "realized", "vol_mult": 1.0, "generator": "gbm"},
    ]
    ens = build_ensemble(
        80_000.0, 0.5, DAY, cfg.model, vols={"base": 0.5, "implied": 0.8, "realized": 0.3}
    )
    by_name = {m.name: m.sigma_annual for m in ens}
    assert by_name == pytest.approx({"base": 0.5, "implied": 0.8, "realized": 0.3})
    # one simulation shared across all three
    assert all(m.bank is ens.base.bank for m in ens)


def test_simulate_rejects_nonsense(cfg):
    with pytest.raises(ValueError):
        simulate(0.0, 0.5, DAY, cfg.model)
    with pytest.raises(ValueError):
        simulate(80_000.0, 0.5, 0.0, cfg.model)
    with pytest.raises(ValueError):
        simulate_bank(DAY, cfg.model, generator="wishful-thinking")
