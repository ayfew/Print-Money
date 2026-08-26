"""Polymarket taker fees.

The crypto price markets run ``crypto_fees_v2``: the taker pays

    fee_per_share = rate * min(p, 1 - p) ** exponent

with ``rate`` around 0.07.  At p = 0.50 that is 3.5 cents on a share worth 50
cents - seven per cent of notional, round trip.  Any strategy that ignores this
is not trading an edge, it is paying rent.  Every edge in this codebase is quoted
*after* fees for exactly that reason.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import FeeConfig
from ..data.types import Leg


@dataclass(frozen=True)
class FeeModel:
    rate: float = 0.07
    exponent: float = 1.0
    taker_only: bool = True
    pad: float = 0.002

    def per_share(self, price: float, *, taker: bool = True) -> float:
        """Fee charged on one share bought at ``price``."""
        if self.rate <= 0:
            return 0.0
        if self.taker_only and not taker:
            return 0.0
        p = min(max(price, 0.0), 1.0)
        return self.rate * (min(p, 1.0 - p) ** self.exponent)

    def total_cost_per_share(self, price: float, *, taker: bool = True) -> float:
        """Price + fee + safety pad: the number an edge must beat."""
        return price + self.per_share(price, taker=taker) + self.pad

    def breakeven_probability(self, price: float, *, taker: bool = True) -> float:
        """Minimum true probability that makes buying at ``price`` a fair bet."""
        return self.total_cost_per_share(price, taker=taker)


def fee_model(leg: Leg, cfg: FeeConfig) -> FeeModel:
    """Prefer the market's own advertised schedule, else the configured default."""
    if cfg.prefer_market_schedule and leg.fee_rate:
        return FeeModel(
            rate=float(leg.fee_rate),
            exponent=float(leg.fee_exponent or cfg.exponent),
            taker_only=bool(leg.fee_taker_only),
            pad=cfg.slippage_pad,
        )
    return FeeModel(
        rate=cfg.rate,
        exponent=cfg.exponent,
        taker_only=cfg.taker_only,
        pad=cfg.slippage_pad,
    )
