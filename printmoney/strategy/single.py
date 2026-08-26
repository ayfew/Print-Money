"""Single-leg trades for the path-dependent ("will BTC touch X") markets.

These cannot join the linear program: their payoff depends on the whole path, not
on the settlement price, so they do not live in the terminal state space.  They
get the simpler treatment - buy only when the post-fee edge clears a wider bar,
size by fractional Kelly, and never let one barrier bet exceed its own cap.

The wider bar is deliberate.  Barrier probabilities are the most model-sensitive
numbers in the whole system: they depend on the volatility path, not just its
average, and on how finely we sample it.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from ..config import Config
from ..data.types import LegKind, Side, Strip
from ..model.paths import ModelEnsemble
from ..util import fmt_usd
from .fees import fee_model

log = logging.getLogger("printmoney.single")

#: Fraction of full Kelly. Full Kelly on a probability we only know to +/-2 points
#: is a good way to find out what a 60% drawdown feels like.
KELLY_FRACTION = 0.25

#: Same noise gate as the linear program uses.
EV_NOISE_MULTIPLE = 3.0


@dataclass
class SingleTrade:
    strip_slug: str
    leg_index: int
    label: str
    question: str
    token_id: str
    side: Side
    price: float
    fee_per_share: float
    shares: float
    fair: float
    fair_worst: float
    edge: float
    kelly: float
    stderr: float

    @property
    def notional(self) -> float:
        return self.shares * self.price

    @property
    def cost(self) -> float:
        return self.shares * (self.price + self.fee_per_share)

    @property
    def expected_pnl(self) -> float:
        return self.shares * self.edge

    def to_dict(self) -> dict[str, object]:
        return {
            "strip": self.strip_slug,
            "leg": self.label,
            "question": self.question,
            "side": self.side.value,
            "token_id": self.token_id,
            "price": self.price,
            "shares": round(self.shares, 4),
            "cost": round(self.cost, 4),
            "fair": round(self.fair, 4),
            "fair_worst": round(self.fair_worst, 4),
            "edge": round(self.edge, 4),
            "kelly": round(self.kelly, 4),
            "expected_pnl": round(self.expected_pnl, 4),
        }


@dataclass
class TouchPlan:
    trades: list[SingleTrade] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def capital_used(self) -> float:
        return sum(t.cost for t in self.trades)

    @property
    def expected_pnl(self) -> float:
        return sum(t.expected_pnl for t in self.trades)

    @property
    def ok(self) -> bool:
        return bool(self.trades)

    def summary(self) -> str:
        if not self.trades:
            return "no touch trades"
        return (
            f"{len(self.trades)} touch trades, stake {fmt_usd(self.capital_used)}, "
            f"E[pnl] {fmt_usd(self.expected_pnl)}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "trades": [t.to_dict() for t in self.trades],
            "skipped": self.skipped,
            "capital_used": round(self.capital_used, 4),
            "expected_pnl": round(self.expected_pnl, 4),
        }


# --------------------------------------------------------------------------- #
def _fair_touch(leg, model) -> float:
    if leg.kind is LegKind.TOUCH_UP:
        return model.prob_touch_up(leg.barrier)
    if leg.kind is LegKind.TOUCH_DOWN:
        return model.prob_touch_down(leg.barrier)
    raise ValueError(f"{leg.kind} is not a touch leg")


def kelly_fraction(p: float, cost: float) -> float:
    """Kelly stake for a binary paying 1 that costs ``cost``, true chance ``p``."""
    if not 0.0 < cost < 1.0:
        return 0.0
    f = (p - cost) / (1.0 - cost)
    return max(0.0, min(f, 1.0))


def plan_touch_trades(
    strip: Strip,
    ensemble: ModelEnsemble,
    cfg: Config,
    *,
    capital: float | None = None,
    held: dict[str, float] | None = None,
) -> TouchPlan:
    """Buy barrier markets that are clearly mispriced; skip everything else.

    ``held`` maps token id to the money already committed to it, so the per-leg
    budget is a cap on the *position*, not on each individual top-up. A cap that
    resets every cycle is not a cap.
    """
    plan = TouchPlan()
    held = held or {}
    s_cfg = cfg.strategy
    if not s_cfg.enable_touch_markets:
        plan.skipped.append("touch markets disabled in config")
        return plan

    budget = float(capital if capital is not None else cfg.risk.capital_usd)
    per_leg_cap = budget * s_cfg.touch_max_notional
    if per_leg_cap <= 0:
        plan.skipped.append("no capital budgeted for touch markets")
        return plan

    for i, leg in enumerate(strip.legs):
        if not leg.is_path_dependent or not leg.accepting_orders:
            continue

        fairs = [_fair_touch(leg, m) for m in ensemble]
        if not fairs:
            continue
        fair = fairs[0]
        fm = fee_model(leg, cfg.fees)

        best: SingleTrade | None = None
        for side in (Side.YES, Side.NO):
            book = leg.book(side)
            ask = book.best_ask
            if ask is None or not 0.0 < ask < 1.0:
                continue
            # A YES share pays on touch; a NO share pays on no-touch.
            p_side = fair if side is Side.YES else 1.0 - fair
            # Be pessimistic: price the side against its least favourable model.
            p_worst = min(fairs) if side is Side.YES else 1.0 - max(fairs)
            fee = fm.per_share(ask)
            total_cost = ask + fee + cfg.fees.slippage_pad
            edge = p_worst - total_cost
            stderr = ensemble.base.stderr(p_side)

            if edge < s_cfg.touch_min_edge:
                continue
            if edge < EV_NOISE_MULTIPLE * stderr:
                plan.skipped.append(
                    f"{leg.label} {side.value}: edge {edge:.3f} inside MC noise {stderr:.3f}"
                )
                continue

            k = KELLY_FRACTION * kelly_fraction(p_worst, total_cost)
            room = per_leg_cap - held.get(leg.token(side), 0.0)
            if room <= 0:
                plan.skipped.append(
                    f"{leg.label} {side.value}: already at the per-leg budget"
                )
                continue
            stake = min(room, budget * k)
            depth_shares = (book.asks[0].size if book.asks else 0.0) * s_cfg.max_depth_fraction
            shares = min(stake / max(total_cost, 1e-6), depth_shares)
            shares = math.floor(shares * 100.0) / 100.0
            if shares < max(s_cfg.min_order_shares, leg.min_order_shares):
                plan.skipped.append(f"{leg.label} {side.value}: size below venue minimum")
                continue

            candidate = SingleTrade(
                strip_slug=strip.slug,
                leg_index=i,
                label=leg.label or leg.slug,
                question=leg.question,
                token_id=leg.token(side),
                side=side,
                price=float(ask),
                fee_per_share=fee,
                shares=float(shares),
                fair=float(p_side),
                fair_worst=float(p_worst),
                edge=float(edge),
                kelly=float(k),
                stderr=float(stderr),
            )
            if best is None or candidate.expected_pnl > best.expected_pnl:
                best = candidate

        if best is not None:
            plan.trades.append(best)

    # Respect the overall touch budget across the whole strip.
    total = plan.capital_used
    cap = budget * max(s_cfg.touch_max_notional * 3.0, s_cfg.touch_max_notional)
    if total > cap and total > 0:
        scale = cap / total
        for t in plan.trades:
            t.shares = math.floor(t.shares * scale * 100.0) / 100.0
        plan.trades = [t for t in plan.trades if t.shares >= s_cfg.min_order_shares]
        plan.skipped.append(f"touch book scaled by {scale:.2f} to respect the strip budget")

    return plan
