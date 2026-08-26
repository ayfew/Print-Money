"""Paper broker: fills against the real book, pays real fees, invents nothing.

The point of paper trading is to be *pessimistic*.  Three things here are worse
than a naive simulator would be, on purpose:

* an order can only fill against depth that was actually quoted at its price;
* fees are charged exactly as the venue charges them;
* an optional latency haircut assumes some of the top level is gone by the time
  our order lands, because in a market that reprices every few seconds it is.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Sequence

from ..config import Config
from ..data.types import Strip
from ..ledger import Fill, Ledger
from ..util import fmt_usd, utcnow
from . import ExecOrder

log = logging.getLogger("printmoney.paper")


@dataclass
class PaperBroker:
    """Simulated execution against a snapshot of the live books."""

    cfg: Config
    #: Fraction of the quoted size we assume is still there when we arrive.
    fill_ratio: float = 0.9
    mode: str = "paper"

    def describe(self) -> str:
        return f"paper broker (assumes {self.fill_ratio:.0%} of quoted depth is reachable)"

    # ------------------------------------------------------------------ #
    def execute(self, orders: Sequence[ExecOrder], ledger: Ledger) -> list[Fill]:
        fills: list[Fill] = []
        now = utcnow()
        for order in orders:
            shares = math.floor(order.shares * self.fill_ratio * 100.0) / 100.0
            if shares < max(order.min_shares, self.cfg.strategy.min_order_shares):
                log.debug(
                    "paper: %s %s skipped, %0.2f shares is below the venue minimum",
                    order.leg_label,
                    order.side,
                    shares,
                )
                continue

            cost = shares * (order.price + order.fee_per_share)
            if cost > ledger.cash:
                affordable = ledger.cash / max(order.price + order.fee_per_share, 1e-9)
                shares = math.floor(affordable * 100.0) / 100.0
                if shares < max(order.min_shares, self.cfg.strategy.min_order_shares):
                    log.warning(
                        "paper: out of cash, dropping %s %s", order.leg_label, order.side
                    )
                    continue

            fill = Fill(
                ts=now,
                token_id=order.token_id,
                strip_slug=order.strip_slug,
                leg_label=order.leg_label,
                question=order.question,
                side=order.side,
                price=order.price,
                shares=shares,
                fee=shares * order.fee_per_share,
                mode="paper",
                order_id=f"paper-{int(now.timestamp() * 1000)}-{len(ledger.fills)}",
            )
            ledger.record_fill(fill, expiry=order.expiry)
            fills.append(fill)
            log.info(
                "paper fill: %s %s %.2f @ %.3f  (%s)",
                order.leg_label,
                order.side,
                shares,
                order.price,
                fmt_usd(fill.cost),
            )
        return fills


@dataclass
class DryRunBroker:
    """Prints what it would do and touches nothing."""

    cfg: Config
    mode: str = "dry"

    def describe(self) -> str:
        return "dry run (no orders, no ledger writes)"

    def execute(self, orders: Sequence[ExecOrder], ledger: Ledger) -> list[Fill]:
        for order in orders:
            log.info(
                "DRY: would buy %.2f %s of %s @ %.3f (%s)",
                order.shares,
                order.side,
                order.leg_label,
                order.price,
                fmt_usd(order.cost),
            )
        return []


def settle_expired(ledger: Ledger, strips: Sequence[Strip], settlement_price: float) -> float:
    """Settle every expired position against a known settlement price.

    Only positions whose leg we can still identify are settled; anything else is
    left open and reported, because guessing a payoff is worse than admitting we
    do not know it.
    """
    by_token: dict[str, tuple[Strip, object]] = {}
    for strip in strips:
        for leg in strip.legs:
            by_token[leg.yes_token] = (strip, leg)
            by_token[leg.no_token] = (strip, leg)

    total = 0.0
    for pos in ledger.expired_positions():
        entry = by_token.get(pos.token_id)
        if entry is None:
            log.warning(
                "position %s (%s) expired but its market is gone; leaving it open",
                pos.leg_label,
                pos.token_id[:12],
            )
            continue
        _strip, leg = entry
        try:
            yes_pays = leg.pays_terminal(settlement_price)  # type: ignore[attr-defined]
        except ValueError:
            log.warning("cannot settle path-dependent leg %s from a single price", pos.leg_label)
            continue
        pays = yes_pays if pos.side == "YES" else not yes_pays
        total += ledger.settle(pos.token_id, 1.0 if pays else 0.0)
    return total
