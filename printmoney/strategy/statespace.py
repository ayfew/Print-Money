"""The common state space, and the tradable instruments defined over it.

Every terminal BTC market on a given settlement time - bracket buckets and
above-ladder digitals alike - is a payoff function of one number.  Cut the price
line at every strike and boundary any of them mentions and you get a finite set
of *states*; inside a state, every leg pays a constant.  From there the whole
portfolio problem is finite and linear.

Instruments are per-order-book-level, not per-leg.  Eating three price levels of
an offer is three instruments with three different costs, which makes the
linear program's cost function exactly right instead of approximately right.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from ..config import Config
from ..data.types import INF, Leg, LegKind, OrderBook, Side, Strip, StripKind
from ..model.paths import ModelEnsemble
from .fees import FeeModel, fee_model

log = logging.getLogger("printmoney.statespace")


@dataclass
class Instrument:
    """One buyable slice: `shares` of one token at one book level."""

    key: str
    strip_slug: str
    leg_index: int
    leg: Leg
    side: Side
    price: float
    fee: float
    max_shares: float
    level: int
    payoff: np.ndarray  # (n_states,) of 0.0/1.0

    @property
    def token_id(self) -> str:
        return self.leg.token(self.side)

    @property
    def cost_per_share(self) -> float:
        return self.price + self.fee

    @property
    def label(self) -> str:
        return f"{self.leg.label or self.leg.slug} {self.side.value}@{self.price:.3f}"

    def expected_value(self, state_probs: np.ndarray) -> float:
        """Per-share EV under one model: fair value minus what it costs."""
        return float(np.dot(self.payoff, state_probs)) - self.cost_per_share

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "strip": self.strip_slug,
            "leg": self.leg.label,
            "side": self.side.value,
            "price": self.price,
            "fee": self.fee,
            "max_shares": self.max_shares,
            "level": self.level,
            "token_id": self.token_id,
        }


@dataclass
class StateSpace:
    """Discretised settlement outcomes plus everything priced against them."""

    edges: list[float]                 # finite cut points, ascending
    reps: np.ndarray                   # representative price inside each state
    probs: np.ndarray                  # (n_models, n_states)
    model_names: list[str]
    instruments: list[Instrument] = field(default_factory=list)
    #: token id -> payoff in each state. Needed to price what we already hold,
    #: not just what we might buy.
    token_payoffs: dict[str, np.ndarray] = field(default_factory=dict)
    strips: list[Strip] = field(default_factory=list)
    expiry_years: float = 0.0
    spot: float = 0.0
    n_paths: int = 0

    @property
    def n_states(self) -> int:
        return len(self.edges) + 1

    @property
    def n_models(self) -> int:
        return self.probs.shape[0]

    def state_bounds(self, s: int) -> tuple[float, float]:
        lo = -INF if s == 0 else self.edges[s - 1]
        hi = INF if s == len(self.edges) else self.edges[s]
        return lo, hi

    def state_label(self, s: int) -> str:
        lo, hi = self.state_bounds(s)
        if lo == -INF:
            return f"<={hi:,.0f}"
        if hi == INF:
            return f">{lo:,.0f}"
        return f"{lo:,.0f}-{hi:,.0f}"

    def base_probs(self) -> np.ndarray:
        return self.probs[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "edges": self.edges,
            "states": [self.state_label(s) for s in range(self.n_states)],
            "probs": self.probs.tolist(),
            "model_names": self.model_names,
            "n_instruments": len(self.instruments),
            "n_paths": self.n_paths,
            "strips": [s.slug for s in self.strips],
        }


# --------------------------------------------------------------------------- #
def collect_edges(strips: Sequence[Strip]) -> list[float]:
    edges: set[float] = set()
    for strip in strips:
        for leg in strip.legs:
            if leg.is_path_dependent:
                continue
            for b in leg.boundaries:
                edges.add(round(float(b), 6))
    return sorted(edges)


def _representatives(edges: Sequence[float], spot: float, ensemble: ModelEnsemble) -> np.ndarray:
    """A price to stand for each state, used only for display and diagnostics."""
    if not edges:
        return np.array([spot], dtype=float)
    reps: list[float] = []
    lo_tail = min(float(edges[0]) * 0.97, ensemble.base.quantile(0.001))
    reps.append(lo_tail)
    for a, b in zip(edges, edges[1:]):
        reps.append(math.sqrt(max(a, 1e-9) * max(b, 1e-9)))
    hi_tail = max(float(edges[-1]) * 1.03, ensemble.base.quantile(0.999))
    reps.append(hi_tail)
    return np.asarray(reps, dtype=float)


def state_probabilities(edges: Sequence[float], ensemble: ModelEnsemble) -> np.ndarray:
    """(n_models, n_states) probability matrix from the simulated paths."""
    n_states = len(edges) + 1
    out = np.zeros((len(ensemble.members), n_states), dtype=float)
    for m, model in enumerate(ensemble.members):
        for s in range(n_states):
            lo = -INF if s == 0 else float(edges[s - 1])
            hi = INF if s == len(edges) else float(edges[s])
            out[m, s] = model.prob_range(lo, hi)
        total = out[m].sum()
        if total <= 0:
            raise ValueError(f"model {model.name} assigned zero probability to every state")
        out[m] /= total  # guard against float drift at the boundaries
    return out


def leg_payoff_vector(leg: Leg, reps: np.ndarray) -> np.ndarray:
    """YES payoff of a terminal leg in each state."""
    if leg.kind is LegKind.RANGE:
        pay = (reps > leg.lo) & (reps <= leg.hi)
    elif leg.kind is LegKind.ABOVE:
        pay = reps > leg.strike
    else:
        raise ValueError(f"{leg.kind} is path dependent and has no terminal payoff vector")
    return pay.astype(float)


# --------------------------------------------------------------------------- #
def _instruments_for_leg(
    strip: Strip,
    leg: Leg,
    leg_index: int,
    yes_payoff: np.ndarray,
    cfg: Config,
) -> list[Instrument]:
    out: list[Instrument] = []
    fm: FeeModel = fee_model(leg, cfg.fees)
    s = cfg.strategy

    for side, book, payoff in (
        (Side.YES, leg.yes_book, yes_payoff),
        (Side.NO, leg.no_book, 1.0 - yes_payoff),
    ):
        if not isinstance(book, OrderBook):
            continue
        for level, lvl in enumerate(book.walk("ask", s.max_book_levels)):
            shares = lvl.size * s.max_depth_fraction
            if shares < max(s.min_order_shares, leg.min_order_shares):
                continue
            if not 0.0 < lvl.price < 1.0:
                continue
            out.append(
                Instrument(
                    key=f"{strip.slug}#{leg_index}#{side.value}#{level}",
                    strip_slug=strip.slug,
                    leg_index=leg_index,
                    leg=leg,
                    side=side,
                    price=float(lvl.price),
                    fee=fm.per_share(float(lvl.price)),
                    max_shares=float(shares),
                    level=level,
                    payoff=payoff.copy(),
                )
            )
    return out


def build_state_space(
    strips: Sequence[Strip],
    ensemble: ModelEnsemble,
    spot: float,
    cfg: Config,
) -> StateSpace:
    """Assemble one portfolio problem from every terminal strip on this expiry."""
    terminal_strips = [s for s in strips if s.kind in (StripKind.BRACKET, StripKind.ABOVE)]
    if not terminal_strips:
        raise ValueError("no terminal (bracket/above) strips to build a state space from")

    expiries = {s.expiry.replace(microsecond=0) for s in terminal_strips}
    if len(expiries) > 1:
        raise ValueError(
            "state space needs a single settlement time, got "
            + ", ".join(sorted(e.isoformat() for e in expiries))
        )

    edges = collect_edges(terminal_strips)
    if not edges:
        raise ValueError("no price boundaries found across the given strips")

    reps = _representatives(edges, spot, ensemble)
    probs = state_probabilities(edges, ensemble)

    space = StateSpace(
        edges=edges,
        reps=reps,
        probs=probs,
        model_names=[m.name for m in ensemble.members],
        strips=list(terminal_strips),
        expiry_years=ensemble.years,
        spot=spot,
        n_paths=ensemble.base.n_paths,
    )

    for strip in terminal_strips:
        for i, leg in enumerate(strip.legs):
            if leg.is_path_dependent or not leg.accepting_orders:
                continue
            try:
                yes_payoff = leg_payoff_vector(leg, reps)
            except ValueError:
                continue
            space.token_payoffs[leg.yes_token] = yes_payoff
            space.token_payoffs[leg.no_token] = 1.0 - yes_payoff
            space.instruments.extend(_instruments_for_leg(strip, leg, i, yes_payoff, cfg))

    log.debug(
        "state space: %d states, %d instruments, %d models",
        space.n_states,
        len(space.instruments),
        space.n_models,
    )
    return space


def group_strips_by_expiry(strips: Iterable[Strip]) -> dict[str, list[Strip]]:
    """Bucket strips so that same-settlement markets are solved together.

    Solving the bracket strip and the above-ladder for the same day jointly is
    what turns two separate mispricings into one hedged position.
    """
    out: dict[str, list[Strip]] = {}
    for s in strips:
        if s.kind is StripKind.TOUCH:
            continue
        key = s.expiry.replace(microsecond=0).isoformat()
        out.setdefault(key, []).append(s)
    return out


# --------------------------------------------------------------------------- #
@dataclass
class Holdings:
    """What we already own on this state space, priced in every state.

    Without this the solver plans each cycle as though the book were empty. Each
    plan then respects the loss floor on its own while the *sum* of them quietly
    walks straight through it - which is exactly what happened the first time
    this engine was left running: three cycles, three individually-compliant
    plans, one position with two and a half times the sanctioned downside.
    """

    payoff_by_state: np.ndarray
    cost: float = 0.0
    tokens: list[str] = field(default_factory=list)

    @property
    def pnl_by_state(self) -> np.ndarray:
        return self.payoff_by_state - self.cost

    @property
    def is_empty(self) -> bool:
        return not self.tokens

    def to_dict(self) -> dict[str, object]:
        return {
            "cost": round(self.cost, 4),
            "tokens": len(self.tokens),
            "pnl_by_state": [round(float(x), 4) for x in self.pnl_by_state],
        }


def holdings_on(space: StateSpace, positions: Sequence[object]) -> Holdings:
    """Map open positions onto this state space.

    Positions in markets the state space does not cover are ignored here - they
    are somebody else's settlement date and cannot affect this one.
    """
    payoff = np.zeros(space.n_states, dtype=float)
    cost = 0.0
    tokens: list[str] = []
    for pos in positions:
        token = getattr(pos, "token_id", None)
        shares = float(getattr(pos, "shares", 0.0) or 0.0)
        if not token or shares <= 0 or getattr(pos, "settled", False):
            continue
        vector = space.token_payoffs.get(token)
        if vector is None:
            continue
        payoff += shares * vector
        cost += float(getattr(pos, "cost_basis", 0.0) or 0.0)
        tokens.append(token)
    return Holdings(payoff_by_state=payoff, cost=cost, tokens=tokens)
