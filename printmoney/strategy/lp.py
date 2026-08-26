"""The position solver: a linear program over settlement states.

The pitch for this kind of bot is always "a self-hedging grid - if BTC goes up
one side pays, if it goes down the other side pays, if it sits still the middle
pays".  That is a description of a *result*, not a method.  This module produces
that result by asking for it directly:

    maximise   the expected profit under the least favourable model we admit
    subject to the loss in EVERY settlement state being bounded,
               capital, per-leg and per-event limits,
               and never asking for more size than the book actually shows.

Because payoffs are constant inside a state and costs are linear in size, the
whole thing is a linear program - solved exactly, in milliseconds, by HiGHS.
A hedge that looks unprofitable on its own is kept if it buys back worst-case
room cheaply; a juicy-looking leg is dropped if it can only be filled by paying
through three levels of book.  Neither judgement is hand-coded.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from scipy.optimize import linprog

from ..config import Config
from ..util import fmt_usd
from .statespace import Holdings, Instrument, StateSpace

log = logging.getLogger("printmoney.lp")


@dataclass
class Order:
    """A concrete buy: N shares of one token at one price."""

    instrument: Instrument
    shares: float

    @property
    def price(self) -> float:
        return self.instrument.price

    @property
    def fee(self) -> float:
        return self.instrument.fee * self.shares

    @property
    def notional(self) -> float:
        return self.shares * self.instrument.price

    @property
    def cost(self) -> float:
        return self.shares * self.instrument.cost_per_share

    @property
    def token_id(self) -> str:
        return self.instrument.token_id

    def to_dict(self) -> dict[str, object]:
        return {
            "strip": self.instrument.strip_slug,
            "leg": self.instrument.leg.label,
            "question": self.instrument.leg.question,
            "side": self.instrument.side.value,
            "token_id": self.token_id,
            "price": self.price,
            "shares": round(self.shares, 4),
            "notional": round(self.notional, 4),
            "fee": round(self.fee, 4),
            "cost": round(self.cost, 4),
            "level": self.instrument.level,
        }


@dataclass
class Plan:
    """The solver's answer, plus everything needed to audit it."""

    orders: list[Order] = field(default_factory=list)
    status: str = "empty"
    reason: str = ""
    capital_used: float = 0.0        # what THIS plan spends
    expected_pnl: float = 0.0        # what THIS plan adds, worst model
    expected_pnl_base: float = 0.0   # what THIS plan adds, base model
    #: Risk numbers describe the position we would be left holding - existing
    #: holdings included - because that is the thing that can actually lose money.
    worst_case: float = 0.0
    best_case: float = 0.0
    cvar: float = 0.0
    ev_noise: float = 0.0
    existing_cost: float = 0.0
    pnl_by_state: np.ndarray = field(default_factory=lambda: np.zeros(0))
    incremental_pnl_by_state: np.ndarray = field(default_factory=lambda: np.zeros(0))
    payoff_by_state: np.ndarray = field(default_factory=lambda: np.zeros(0))
    state_labels: list[str] = field(default_factory=list)
    solver_status: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "accepted" and bool(self.orders)

    @property
    def is_arbitrage(self) -> bool:
        """Profitable in every settlement state - no model risk at all."""
        return bool(self.orders) and self.worst_case > 0.0

    @property
    def return_on_capital(self) -> float:
        return self.expected_pnl / self.capital_used if self.capital_used > 0 else 0.0

    def summary(self) -> str:
        if not self.orders:
            return f"no position ({self.reason or self.status})"
        tag = "ARB" if self.is_arbitrage else "EV"
        return (
            f"{tag} {len(self.orders)} orders, stake {fmt_usd(self.capital_used)}, "
            f"E[pnl] {fmt_usd(self.expected_pnl)} ({self.return_on_capital:+.2%}), "
            f"CVaR {fmt_usd(self.cvar)}, worst {fmt_usd(self.worst_case)}, "
            f"best {fmt_usd(self.best_case)}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "solver_status": self.solver_status,
            "capital_used": round(self.capital_used, 4),
            "expected_pnl": round(self.expected_pnl, 4),
            "expected_pnl_base": round(self.expected_pnl_base, 4),
            "worst_case": round(self.worst_case, 4),
            "best_case": round(self.best_case, 4),
            "cvar": round(self.cvar, 4),
            "existing_cost": round(self.existing_cost, 4),
            "ev_noise": round(self.ev_noise, 6),
            "return_on_capital": round(self.return_on_capital, 6),
            "is_arbitrage": self.is_arbitrage,
            "orders": [o.to_dict() for o in self.orders],
            "pnl_by_state": [round(float(x), 4) for x in self.pnl_by_state],
            "state_labels": self.state_labels,
        }


# --------------------------------------------------------------------------- #
#: How many Monte-Carlo standard errors of expected profit we demand before
#: believing the edge is real rather than simulation noise.
EV_NOISE_MULTIPLE = 3.0


def _instrument_ev_matrix(space: StateSpace) -> np.ndarray:
    """(n_models, n_instruments) per-share expected value."""
    payoff = np.stack([ins.payoff for ins in space.instruments])          # (J, S)
    cost = np.array([ins.cost_per_share for ins in space.instruments])    # (J,)
    fair = space.probs @ payoff.T                                         # (M, J)
    return fair - cost[None, :]


def solve(
    space: StateSpace,
    cfg: Config,
    *,
    capital: float | None = None,
    holdings: Holdings | None = None,
) -> Plan:
    """Solve the portfolio problem for one settlement time.

    Objective: maximise expected profit under the least favourable model, minus a
    penalty on the conditional value at risk of the loss.  CVaR - the average
    loss across the worst ``1 - cvar_alpha`` of settlement outcomes - is exactly
    linear in the position sizes under the Rockafellar-Uryasev formulation, so
    the whole problem is still one linear program.

    That penalty is what makes the engine prefer a small certain profit to a
    larger uncertain one.  Maximising expectation alone would happily swap a
    risk-free basket for a lottery ticket with a slightly better mean, which is
    how a strategy stays "profitable on paper" right up until the tail arrives.

    ``holdings`` is what we already own on this settlement date.  The risk
    constraints are written against the *combined* position, because a loss floor
    that only looks at the order in front of it is not a loss floor - run the
    engine three times and it will approve three individually-compliant plans
    that add up to something well outside the budget.
    """
    s_cfg = cfg.strategy
    if not space.instruments:
        return Plan(status="rejected", reason="no tradable book levels")

    budget = float(capital if capital is not None else cfg.risk.capital_usd)
    if budget <= 0:
        return Plan(status="rejected", reason="no capital available")

    J = len(space.instruments)
    S = space.n_states
    M = space.n_models

    payoff = np.stack([ins.payoff for ins in space.instruments])        # (J, S)
    cost = np.array([ins.cost_per_share for ins in space.instruments])  # (J,)
    ev = _instrument_ev_matrix(space)                                   # (M, J)
    pnl_coef = payoff - cost[:, None]                                   # (J, S)

    base_pnl = (
        holdings.pnl_by_state
        if holdings is not None and holdings.pnl_by_state.size == S
        else np.zeros(S)
    )

    # Variables: x_0..x_{J-1} (shares), t (robust expected PnL), zeta (the CVaR
    # threshold), u_0..u_{S-1} (loss beyond that threshold in each state).
    T = J
    Z = J + 1
    U0 = J + 2
    n_var = J + 2 + S

    rows: list[np.ndarray] = []
    rhs: list[float] = []

    # (a) t <= expected PnL under every model we admit
    for m in (range(M) if cfg.model.robust else range(1)):
        row = np.zeros(n_var)
        row[:J] = -ev[m]
        row[T] = 1.0
        rows.append(row)
        rhs.append(0.0)

    # (b) hard loss floor: PnL_s >= -max_loss in EVERY state, model-free.
    #
    # The floor is a fixed number of dollars, not a fraction of what the plan
    # happens to spend. Tying it to capital deployed looks more natural and is a
    # trap: buying a leg together with its opposite is a synthetic dollar bond
    # costing a fraction of a cent, and it inflates "capital deployed" without
    # adding any risk - so the solver learns to pad the position to buy itself a
    # bigger loss allowance. Against a fixed floor that trick is just EV-negative.
    risk_budget = budget * s_cfg.max_notional_per_event
    max_loss = s_cfg.max_loss_fraction * risk_budget
    for s in range(S):
        # Never make a state worse than the floor - or, if a state is already
        # past the floor, never make it worse than it already is. Demanding that
        # a new order repair an old position would make the problem infeasible
        # and stop the engine dead instead of simply holding it back.
        allowed = max(max_loss, -float(base_pnl[s]))
        row = np.zeros(n_var)
        row[:J] = -pnl_coef[:, s]
        rows.append(row)
        rhs.append(allowed + float(base_pnl[s]))

    # (c) u_s >= loss in state s beyond zeta, on the COMBINED position
    for s in range(S):
        row = np.zeros(n_var)
        row[:J] = -pnl_coef[:, s]
        row[Z] = -1.0
        row[U0 + s] = -1.0
        rows.append(row)
        rhs.append(float(base_pnl[s]))

    # (d) total capital
    row = np.zeros(n_var)
    row[:J] = cost
    rows.append(row)
    rhs.append(budget)

    # (e) per-leg and (f) per-event notional caps
    leg_groups: dict[tuple[str, int], list[int]] = {}
    strip_groups: dict[str, list[int]] = {}
    for j, ins in enumerate(space.instruments):
        leg_groups.setdefault((ins.strip_slug, ins.leg_index), []).append(j)
        strip_groups.setdefault(ins.strip_slug, []).append(j)

    for idxs in leg_groups.values():
        row = np.zeros(n_var)
        row[idxs] = cost[idxs]
        rows.append(row)
        rhs.append(budget * s_cfg.max_notional_per_leg)

    for idxs in strip_groups.values():
        row = np.zeros(n_var)
        row[idxs] = cost[idxs]
        rows.append(row)
        rhs.append(budget * s_cfg.max_notional_per_event)

    A = np.stack(rows)
    b = np.asarray(rhs, dtype=float)

    # objective: minimise  -t + theta * (zeta + 1/(1-alpha) * sum_s p_s u_s)
    theta = float(s_cfg.risk_aversion)
    alpha = float(s_cfg.cvar_alpha)
    probs = space.base_probs()
    obj = np.zeros(n_var)
    obj[T] = -1.0
    if theta > 0:
        obj[Z] = theta
        obj[U0:] = theta * probs / max(1.0 - alpha, 1e-6)

    minimums = np.array(
        [max(s_cfg.min_order_shares, ins.leg.min_order_shares) for ins in space.instruments]
    )
    caps = np.array([float(ins.max_shares) for ins in space.instruments])

    # Instruments the venue minimum makes unusable at the size the solver wants.
    # Deleting them after the fact would break the hedge the solver just built,
    # so we ban them and solve again.
    banned = np.zeros(J, dtype=bool)
    res = None
    for _attempt in range(MAX_MINIMUM_SIZE_REPAIRS):
        bounds: list[tuple[float, float | None]] = [
            (0.0, 0.0 if banned[j] else float(caps[j])) for j in range(J)
        ]
        # t is floored at zero: this engine only buys positive expectation. Left
        # free, the CVaR term will happily propose paying to hedge an existing
        # book - and on a venue that charges a taker fee on both legs, unwinding
        # usually costs more than the risk it removes. When a date is over its
        # budget the right move is to stop trading it and say so, which is what
        # an empty plan with a stated reason does.
        bounds.append((0.0, None))           # t
        bounds.append((None, None))          # zeta
        bounds.extend([(0.0, None)] * S)     # u_s

        res = linprog(obj, A_ub=A, b_ub=b, bounds=bounds, method="highs")
        if not res.success or res.x is None:
            return Plan(
                status="rejected",
                reason=f"solver failed: {res.message}",
                solver_status=str(res.status),
            )
        raw = np.asarray(res.x[:J], dtype=float)
        too_small = (raw > 1e-9) & (raw < minimums) & (~banned)
        if not too_small.any():
            break
        banned |= too_small

    assert res is not None
    raw = np.asarray(res.x[:J], dtype=float)
    if raw.sum() <= 1e-9:
        # Nothing new to buy - but still report the book we are already holding
        # on this date, because "no trade" and "no position" are different facts.
        idle = evaluate([], space, cfg, holdings=holdings)
        idle.status = "rejected"
        idle.reason = "no combination of the quoted prices is worth its risk after fees"
        idle.solver_status = str(res.message)
        return idle

    orders = _round_orders(raw, space.instruments, s_cfg.min_order_shares)
    plan = evaluate(orders, space, cfg, holdings=holdings)
    plan.solver_status = str(res.message)

    if not orders:
        plan.status = "rejected"
        plan.reason = "the solution needs positions smaller than the venue minimum"
        return plan

    return _apply_gates(plan, space, cfg, max_loss, base_pnl)


#: How many times we will ban sub-minimum positions and re-solve.
MAX_MINIMUM_SIZE_REPAIRS = 4


def conditional_value_at_risk(
    pnl_by_state: np.ndarray, probs: np.ndarray, alpha: float
) -> float:
    """Average loss over the worst ``1 - alpha`` of the probability mass.

    Positive means "in the bad cases, this is what it costs". Negative means the
    position still makes money even in its bad cases.
    """
    if pnl_by_state.size == 0:
        return 0.0
    tail = max(1.0 - float(alpha), 1e-9)
    order = np.argsort(pnl_by_state)          # worst outcomes first
    losses = -pnl_by_state[order]
    weights = probs[order]
    cum = np.cumsum(weights)
    take = np.minimum(weights, np.maximum(tail - (cum - weights), 0.0))
    total = take.sum()
    if total <= 0:
        return float(losses[0])
    return float(np.dot(losses, take) / total)


# --------------------------------------------------------------------------- #
def _round_orders(
    raw: np.ndarray, instruments: Sequence[Instrument], min_shares: float
) -> list[Order]:
    """Round down to whole-ish sizes and drop anything under the venue minimum.

    Rounding *down* only: the LP's constraints are all upper bounds, so shrinking
    a position can never violate one.
    """
    orders: list[Order] = []
    for x, ins in zip(raw, instruments):
        if not math.isfinite(x) or x <= 0:
            continue
        shares = math.floor(x * 100.0) / 100.0
        shares = min(shares, ins.max_shares)
        if shares < max(min_shares, ins.leg.min_order_shares):
            continue
        orders.append(Order(instrument=ins, shares=shares))
    return orders


def evaluate(
    orders: Sequence[Order],
    space: StateSpace,
    cfg: Config,
    *,
    holdings: Holdings | None = None,
) -> Plan:
    """Recompute every headline number from the *rounded* order list.

    Profit numbers describe what these orders add; risk numbers describe the book
    we would be left holding.
    """
    S = space.n_states
    base_payoff = (
        holdings.payoff_by_state
        if holdings is not None and holdings.payoff_by_state.size == S
        else np.zeros(S)
    )
    base_cost = float(holdings.cost) if holdings is not None else 0.0

    plan = Plan(orders=list(orders))
    plan.state_labels = [space.state_label(s) for s in range(S)]
    plan.existing_cost = base_cost
    if not orders:
        combined = base_payoff - base_cost
        plan.incremental_pnl_by_state = np.zeros(S)
        plan.payoff_by_state = base_payoff.copy()
        plan.pnl_by_state = combined
        # An empty plan still has risk if we are holding something.
        plan.worst_case = float(np.min(combined)) if combined.size else 0.0
        plan.best_case = float(np.max(combined)) if combined.size else 0.0
        plan.cvar = (
            conditional_value_at_risk(combined, space.base_probs(), cfg.strategy.cvar_alpha)
            if combined.size
            else 0.0
        )
        return plan

    shares = np.array([o.shares for o in orders], dtype=float)
    payoff = np.stack([o.instrument.payoff for o in orders])           # (K, S)
    cost = float(sum(o.cost for o in orders))

    new_payoff = shares @ payoff                                        # (S,)
    incremental = new_payoff - cost
    combined_payoff = base_payoff + new_payoff
    combined = combined_payoff - (base_cost + cost)

    ev_per_model = space.probs @ incremental                            # (M,)

    plan.capital_used = cost
    plan.payoff_by_state = combined_payoff
    plan.incremental_pnl_by_state = incremental
    plan.pnl_by_state = combined
    plan.expected_pnl_base = float(ev_per_model[0])
    plan.expected_pnl = float(np.min(ev_per_model)) if cfg.model.robust else float(ev_per_model[0])
    plan.worst_case = float(np.min(combined))
    plan.best_case = float(np.max(combined))
    plan.cvar = conditional_value_at_risk(combined, space.base_probs(), cfg.strategy.cvar_alpha)
    plan.ev_noise = _ev_standard_error(new_payoff, space)
    plan.status = "evaluated"
    return plan


def _ev_standard_error(payoff_by_state: np.ndarray, space: StateSpace) -> float:
    """Monte-Carlo standard error of the portfolio's expected payoff.

    The state probabilities are sample proportions from ``n_paths`` draws, so the
    expected payoff inherits their sampling error.  If the edge is not several
    times this number, we are reading noise.
    """
    p = space.base_probs()
    n_paths = space.n_paths or 40_000
    mean = float(np.dot(p, payoff_by_state))
    second = float(np.dot(p, payoff_by_state**2))
    var = max(second - mean * mean, 0.0)
    return math.sqrt(var / max(n_paths, 1))


def _apply_gates(
    plan: Plan,
    space: StateSpace,
    cfg: Config,
    max_loss: float,
    base_pnl: np.ndarray | None = None,
) -> Plan:
    """Final accept/reject. Every rejection reason is recorded, never silent."""
    s_cfg = cfg.strategy

    if plan.capital_used <= 0:
        plan.status = "rejected"
        plan.reason = "zero capital deployed"
        return plan

    # The floor applies to the combined book. Where an existing position is
    # already past it, the bar becomes "do not make it worse" rather than
    # "repair it", which the new order has no way to do.
    already = float(np.min(base_pnl)) if base_pnl is not None and base_pnl.size else 0.0
    floor = -max(abs(max_loss), -already)
    tolerance = max(1e-6, 1e-3 * max(plan.capital_used, abs(max_loss)))
    if plan.worst_case < floor - tolerance:
        plan.status = "rejected"
        plan.reason = (
            f"combined worst case {fmt_usd(plan.worst_case)} breaches the "
            f"{fmt_usd(floor)} loss floor after rounding"
        )
        return plan

    if plan.return_on_capital < s_cfg.min_edge:
        plan.status = "rejected"
        plan.reason = (
            f"expected return {plan.return_on_capital:+.2%} below the "
            f"{s_cfg.min_edge:.2%} threshold"
        )
        return plan

    if plan.expected_pnl < EV_NOISE_MULTIPLE * plan.ev_noise:
        plan.status = "rejected"
        plan.reason = (
            f"edge {fmt_usd(plan.expected_pnl)} is within {EV_NOISE_MULTIPLE:.0f}x "
            f"Monte-Carlo noise ({fmt_usd(plan.ev_noise)})"
        )
        return plan

    plan.status = "accepted"
    plan.reason = "arbitrage: profitable in every state" if plan.is_arbitrage else "positive robust EV"
    return plan


# --------------------------------------------------------------------------- #
def describe_payoff(plan: Plan, space: StateSpace, top: int = 12) -> list[tuple[str, float, float]]:
    """(state label, probability, PnL) rows for display, worst PnL first."""
    p = space.base_probs()
    rows = [
        (space.state_label(s), float(p[s]), float(plan.pnl_by_state[s]))
        for s in range(space.n_states)
    ]
    rows.sort(key=lambda r: r[2])
    return rows[:top]
