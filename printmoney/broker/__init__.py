"""Order routing.

``ExecOrder`` is the single shape both planners emit and both brokers consume, so
the paper broker and the live broker are interchangeable and the engine never
learns which one it is talking to.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

from ..data.types import Side
from ..ledger import Fill, Ledger
from ..strategy.lp import Plan
from ..strategy.single import TouchPlan


@dataclass
class ExecOrder:
    """A buy of ``shares`` of ``token_id`` at a limit of ``price``."""

    token_id: str
    side: str                 # YES or NO, for reporting only
    price: float
    shares: float
    fee_per_share: float
    strip_slug: str
    leg_label: str
    question: str
    expiry: datetime | None = None
    tick_size: float = 0.001
    min_shares: float = 5.0
    source: str = "lp"

    @property
    def notional(self) -> float:
        return self.price * self.shares

    @property
    def cost(self) -> float:
        return self.shares * (self.price + self.fee_per_share)

    def to_dict(self) -> dict[str, object]:
        return {
            "token_id": self.token_id,
            "side": self.side,
            "price": self.price,
            "shares": round(self.shares, 4),
            "cost": round(self.cost, 4),
            "strip": self.strip_slug,
            "leg": self.leg_label,
            "source": self.source,
        }


def orders_from_plan(plan: Plan, expiry: datetime | None = None) -> list[ExecOrder]:
    out: list[ExecOrder] = []
    for o in plan.orders:
        ins = o.instrument
        out.append(
            ExecOrder(
                token_id=ins.token_id,
                side=ins.side.value,
                price=ins.price,
                shares=o.shares,
                fee_per_share=ins.fee,
                strip_slug=ins.strip_slug,
                leg_label=ins.leg.label or ins.leg.slug,
                question=ins.leg.question,
                expiry=expiry,
                tick_size=ins.leg.tick_size,
                min_shares=ins.leg.min_order_shares,
                source="lp",
            )
        )
    return out


def orders_from_touch_plan(plan: TouchPlan, expiry: datetime | None = None) -> list[ExecOrder]:
    return [
        ExecOrder(
            token_id=t.token_id,
            side=t.side.value,
            price=t.price,
            shares=t.shares,
            fee_per_share=t.fee_per_share,
            strip_slug=t.strip_slug,
            leg_label=t.label,
            question=t.question,
            expiry=expiry,
            source="touch",
        )
        for t in plan.trades
    ]


def scale_orders(orders: Sequence[ExecOrder], scale: float, min_shares: float) -> list[ExecOrder]:
    """Shrink every order by the same factor, dropping anything that goes sub-minimum."""
    if scale >= 0.999:
        return list(orders)
    out: list[ExecOrder] = []
    for o in orders:
        shares = int(o.shares * scale * 100) / 100.0
        if shares < max(min_shares, o.min_shares):
            continue
        out.append(
            ExecOrder(
                token_id=o.token_id,
                side=o.side,
                price=o.price,
                shares=shares,
                fee_per_share=o.fee_per_share,
                strip_slug=o.strip_slug,
                leg_label=o.leg_label,
                question=o.question,
                expiry=o.expiry,
                tick_size=o.tick_size,
                min_shares=o.min_shares,
                source=o.source,
            )
        )
    return out


class Broker(Protocol):
    mode: str

    def execute(self, orders: Sequence[ExecOrder], ledger: Ledger) -> list[Fill]:
        ...

    def describe(self) -> str:
        ...


__all__ = [
    "ExecOrder",
    "Broker",
    "orders_from_plan",
    "orders_from_touch_plan",
    "scale_orders",
    "Side",
    "Fill",
]
