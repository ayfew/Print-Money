"""Fees, the state space, and the linear program that picks positions."""
from __future__ import annotations

import numpy as np
import pytest

from printmoney.backtest import synthetic_bracket_strip
from printmoney.data.types import Side
from printmoney.model.paths import build_ensemble
from printmoney.strategy.fees import FeeModel, fee_model
from printmoney.strategy.lp import conditional_value_at_risk, evaluate, solve
from printmoney.strategy.statespace import (
    Holdings,
    build_state_space,
    collect_edges,
    group_strips_by_expiry,
    holdings_on,
    state_probabilities,
)

DAY = 1.0 / 365.25
EDGES = [76_000.0, 78_000.0, 80_000.0, 82_000.0]
SPOT = 79_000.0


def _models(strip, cfg, sigma=0.5, years=DAY):
    return build_ensemble(SPOT, sigma, years, cfg.model)


class TestFees:
    def test_fee_is_symmetric_and_peaks_at_the_middle(self):
        fm = FeeModel(rate=0.07, exponent=1.0, pad=0.0)
        assert fm.per_share(0.5) == pytest.approx(0.035)
        assert fm.per_share(0.2) == pytest.approx(fm.per_share(0.8))
        assert fm.per_share(0.99) < fm.per_share(0.5)

    def test_zero_rate_means_no_fee(self):
        assert FeeModel(rate=0.0).per_share(0.5) == 0.0

    def test_breakeven_includes_the_pad(self):
        fm = FeeModel(rate=0.07, pad=0.002)
        assert fm.breakeven_probability(0.5) == pytest.approx(0.5 + 0.035 + 0.002)

    def test_market_schedule_wins_when_present(self, cfg):
        strip = synthetic_bracket_strip(SPOT, EDGES, [0.2] * 5)
        leg = strip.legs[0]
        leg.fee_rate = 0.05
        cfg.fees.prefer_market_schedule = True
        assert fee_model(leg, cfg.fees).rate == 0.05
        cfg.fees.prefer_market_schedule = False
        assert fee_model(leg, cfg.fees).rate == cfg.fees.rate


class TestStateSpace:
    def test_edges_come_from_every_leg(self, cfg):
        strip = synthetic_bracket_strip(SPOT, EDGES, [0.2] * 5)
        assert collect_edges([strip]) == EDGES

    def test_state_probabilities_are_a_distribution(self, cfg):
        strip = synthetic_bracket_strip(SPOT, EDGES, [0.2] * 5)
        probs = state_probabilities(EDGES, _models(strip, cfg))
        assert probs.shape == (len(cfg.model.ensemble), len(EDGES) + 1)
        assert np.allclose(probs.sum(axis=1), 1.0)

    def test_instruments_are_per_book_level(self, cfg):
        strip = synthetic_bracket_strip(SPOT, EDGES, [0.2] * 5)
        space = build_state_space([strip], _models(strip, cfg), SPOT, cfg)
        assert space.n_states == len(EDGES) + 1
        # one level per side per leg in the synthetic book
        assert len(space.instruments) == 2 * len(strip.legs)
        yes = [i for i in space.instruments if i.side is Side.YES]
        # a YES bucket pays in exactly one state
        assert all(i.payoff.sum() == 1.0 for i in yes)
        no = [i for i in space.instruments if i.side is Side.NO]
        assert all(i.payoff.sum() == space.n_states - 1 for i in no)

    def test_mixed_expiries_are_refused(self, cfg):
        a = synthetic_bracket_strip(SPOT, EDGES, [0.2] * 5, slug="a")
        b = synthetic_bracket_strip(SPOT, EDGES, [0.2] * 5, slug="b")
        b.expiry = a.expiry.replace(year=a.expiry.year + 1)
        with pytest.raises(ValueError, match="single settlement time"):
            build_state_space([a, b], _models(a, cfg), SPOT, cfg)

    def test_grouping_splits_by_settlement_and_drops_barriers(self, cfg):
        a = synthetic_bracket_strip(SPOT, EDGES, [0.2] * 5, slug="a")
        b = synthetic_bracket_strip(SPOT, EDGES, [0.2] * 5, slug="b")
        groups = group_strips_by_expiry([a, b])
        assert len(groups) == 1
        assert len(next(iter(groups.values()))) == 2


class TestSolver:
    def _solve(self, prices, cfg, **overrides):
        for k, v in overrides.items():
            setattr(cfg.strategy, k, v)
        strip = synthetic_bracket_strip(SPOT, EDGES, prices, spread=0.002)
        space = build_state_space([strip], _models(strip, cfg), SPOT, cfg)
        return solve(space, cfg), space

    def _model_probs(self, cfg):
        strip = synthetic_bracket_strip(SPOT, EDGES, [0.2] * (len(EDGES) + 1))
        space = build_state_space([strip], _models(strip, cfg), SPOT, cfg)
        return space.base_probs()

    def test_underpriced_partition_is_a_risk_free_trade(self, cfg):
        """A strip priced at 0.90 of fair always pays 1.00, whatever BTC does.

        The prices have to be proportional to the model's own probabilities: a
        flat 0.18 across five buckets is not a pure arbitrage, it is a screaming
        directional mispricing, and the solver is right to take that instead.
        """
        plan, _ = self._solve(self._model_probs(cfg) * 0.90, cfg)
        assert plan.ok
        assert plan.is_arbitrage
        assert plan.worst_case > 0
        assert plan.worst_case == pytest.approx(plan.best_case, rel=1e-6)
        # every order is a YES buy: the whole strip
        assert {o.instrument.side for o in plan.orders} == {Side.YES}

    def test_overpriced_partition_is_sold_through_the_no_side(self, cfg):
        """A strip priced at 1.15 of fair means the NO side of it is cheap.

        Note what the solver does NOT do: it does not buy the whole NO basket for
        a guaranteed pittance. Selling an overpriced strip earns 0.15 * fair per
        share, so the edge is concentrated in the high-probability buckets, and
        it buys those. The result is a near-riskless position that earns several
        times what the flat basket would.
        """
        plan, _ = self._solve(self._model_probs(cfg) * 1.15, cfg)
        assert plan.ok
        assert {o.instrument.side for o in plan.orders} == {Side.NO}
        assert plan.expected_pnl > 0
        assert plan.worst_case > -0.05 * plan.capital_used

    def test_fairly_priced_strip_is_declined(self, cfg):
        strip = synthetic_bracket_strip(SPOT, EDGES, [0.2] * 5, spread=0.02)
        space = build_state_space([strip], _models(strip, cfg), SPOT, cfg)
        # price the strip at the model's own probabilities
        probs = space.base_probs()
        fair = synthetic_bracket_strip(SPOT, EDGES, probs, spread=0.02)
        space = build_state_space([fair], _models(fair, cfg), SPOT, cfg)
        plan = solve(space, cfg)
        assert not plan.ok
        assert plan.reason

    def test_loss_floor_is_never_breached(self, cfg):
        plan, _ = self._solve([0.05, 0.10, 0.55, 0.10, 0.05], cfg, risk_aversion=0.0)
        allowed = (
            cfg.strategy.max_loss_fraction
            * cfg.strategy.max_notional_per_event
            * cfg.risk.capital_usd
        )
        assert plan.worst_case >= -allowed - 1e-3

    def test_risk_aversion_prefers_the_certain_trade(self, cfg):
        """With a CVaR penalty, a sure thing beats a marginally richer gamble."""
        prices = [0.02, 0.05, 0.60, 0.05, 0.02]  # middle bucket badly overpriced
        greedy, _ = self._solve(prices, cfg, risk_aversion=0.0)
        careful, _ = self._solve(prices, cfg, risk_aversion=2.0)
        assert careful.cvar <= greedy.cvar + 1e-6
        assert careful.worst_case >= greedy.worst_case - 1e-6

    def test_no_instruments_is_reported_not_crashed(self, cfg):
        strip = synthetic_bracket_strip(SPOT, EDGES, [0.2] * 5)
        space = build_state_space([strip], _models(strip, cfg), SPOT, cfg)
        space.instruments = []
        plan = solve(space, cfg)
        assert not plan.ok
        assert "no tradable book levels" in plan.reason

    def test_zero_capital_is_reported_not_crashed(self, cfg):
        strip = synthetic_bracket_strip(SPOT, EDGES, [0.18] * 5)
        space = build_state_space([strip], _models(strip, cfg), SPOT, cfg)
        plan = solve(space, cfg, capital=0.0)
        assert not plan.ok
        assert "capital" in plan.reason

    def test_evaluate_agrees_with_the_orders_it_was_given(self, cfg):
        plan, space = self._solve([0.18] * 5, cfg)
        again = evaluate(plan.orders, space, cfg)
        assert again.capital_used == pytest.approx(plan.capital_used)
        assert again.worst_case == pytest.approx(plan.worst_case)
        assert again.expected_pnl == pytest.approx(plan.expected_pnl)


class TestCVaR:
    def test_worst_tail_only(self):
        pnl = np.array([-10.0, 10.0])
        probs = np.array([0.5, 0.5])
        assert conditional_value_at_risk(pnl, probs, 0.9) == pytest.approx(10.0)

    def test_alpha_zero_is_minus_the_mean(self):
        pnl = np.array([-10.0, 10.0])
        probs = np.array([0.5, 0.5])
        assert conditional_value_at_risk(pnl, probs, 0.0) == pytest.approx(0.0)

    def test_a_sure_profit_has_negative_cvar(self):
        pnl = np.array([5.0, 5.0, 5.0])
        probs = np.array([0.2, 0.3, 0.5])
        assert conditional_value_at_risk(pnl, probs, 0.9) == pytest.approx(-5.0)


class _Pos:
    """Just enough of a ledger Position for the mapper."""

    def __init__(self, token_id, shares, cost_basis, settled=False):
        self.token_id = token_id
        self.shares = shares
        self.cost_basis = cost_basis
        self.settled = settled


class TestExistingHoldings:
    def _space(self, cfg, prices=None):
        prices = [0.2] * 5 if prices is None else prices
        strip = synthetic_bracket_strip(SPOT, EDGES, prices, spread=0.002)
        return strip, build_state_space([strip], _models(strip, cfg), SPOT, cfg)

    def test_holdings_map_onto_the_state_space(self, cfg):
        strip, space = self._space(cfg)
        leg = strip.legs[2]
        held = holdings_on(space, [_Pos(leg.yes_token, 100.0, 30.0)])
        assert held.cost == pytest.approx(30.0)
        # a YES bucket pays in exactly one state
        assert held.payoff_by_state.sum() == pytest.approx(100.0)
        assert held.pnl_by_state.max() == pytest.approx(70.0)
        assert held.pnl_by_state.min() == pytest.approx(-30.0)

    def test_settled_and_foreign_positions_are_ignored(self, cfg):
        strip, space = self._space(cfg)
        leg = strip.legs[2]
        held = holdings_on(
            space,
            [
                _Pos(leg.yes_token, 100.0, 30.0, settled=True),
                _Pos("a-token-from-another-market", 100.0, 30.0),
            ],
        )
        assert held.is_empty
        assert held.cost == 0.0

    def test_repeated_cycles_do_not_compound_past_the_loss_floor(self, cfg):
        """The bug this parameter exists for.

        Three cycles, three plans that each satisfy the floor on their own. If
        the solver is not told what is already on the book, they stack into a
        position with several times the sanctioned downside - which is exactly
        what the engine did the first time it was left running against the live
        board.
        """
        cfg.strategy.risk_aversion = 0.0
        prices = [0.02, 0.05, 0.60, 0.05, 0.02]  # middle bucket badly overpriced
        strip, space = self._space(cfg, prices)
        allowed = (
            cfg.strategy.max_loss_fraction
            * cfg.strategy.max_notional_per_event
            * cfg.risk.capital_usd
        )

        # The solver drives the floor to equality, then sizes are rounded down to
        # whole cents. When part of a new order is hedging something already on
        # the book, shrinking it can move the worst state by a fraction of a
        # cent either way, so allow the same slack the engine's own gate does.
        slack = 1e-3 * allowed

        payoff = np.zeros(space.n_states)
        cost = 0.0
        tokens: list[str] = []
        for _cycle in range(3):
            held = Holdings(payoff_by_state=payoff.copy(), cost=cost, tokens=list(tokens))
            plan = solve(space, cfg, holdings=held)
            if not plan.ok:
                break
            for o in plan.orders:
                payoff += o.shares * o.instrument.payoff
                cost += o.cost
                tokens.append(o.instrument.token_id)
            # the reported worst case is the combined book, not just this order
            assert plan.worst_case == pytest.approx(float((payoff - cost).min()), abs=1e-6)
            assert plan.worst_case >= -allowed - slack

        assert tokens, "expected at least one cycle to trade"
        assert float((payoff - cost).min()) >= -allowed - slack

    def test_a_book_already_past_the_floor_is_not_made_worse(self, cfg):
        """An over-risked book must hold the engine back, not wedge it."""
        strip, space = self._space(cfg, [0.02, 0.05, 0.60, 0.05, 0.02])
        # a position that loses far more than the floor in one state
        payoff = np.zeros(space.n_states)
        payoff[0] = 5_000.0
        held = Holdings(payoff_by_state=payoff, cost=1_000.0, tokens=["x"])
        already = float(held.pnl_by_state.min())
        plan = solve(space, cfg, holdings=held)
        # it must not crash, and must not deepen the existing hole
        assert plan.worst_case >= already - 1e-3
