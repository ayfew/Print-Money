"""Settling expired positions against the real settlement print.

Polymarket resolves these markets to the Binance BTC/USDT 1-minute close at the
stated time.  We look that exact bar up rather than using the last price we
happened to see, and for barrier markets we pull the whole 1-minute window and
take its true high and low.

If the data is not there yet the position stays open and says so.  A position
settled against a guess is worse than one that is simply still open.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from .ledger import Ledger
from .registry import MarketRegistry, TokenSpec
from .util import fmt_usd, utcnow

log = logging.getLogger("printmoney.settlement")


class SettlementFeed(Protocol):
    def settlement_close(self, when: datetime) -> float | None: ...
    def path_extremes(self, start: datetime, end: datetime) -> tuple[float, float] | None: ...


@dataclass
class SettlementReport:
    settled: int = 0
    deferred: int = 0
    unknown: int = 0
    realized_pnl: float = 0.0
    details: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not (self.settled or self.deferred or self.unknown):
            return "nothing to settle"
        bits = [f"{self.settled} settled ({fmt_usd(self.realized_pnl)})"]
        if self.deferred:
            bits.append(f"{self.deferred} waiting for the settlement print")
        if self.unknown:
            bits.append(f"{self.unknown} unidentifiable")
        return ", ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "settled": self.settled,
            "deferred": self.deferred,
            "unknown": self.unknown,
            "realized_pnl": round(self.realized_pnl, 4),
            "details": self.details,
        }


def _window_start(spec: TokenSpec) -> datetime:
    if spec.window_start is not None:
        return spec.window_start
    # Fall back to a day before expiry: these barrier markets are daily.
    return spec.expiry - timedelta(days=1)


def settle_expired(
    ledger: Ledger,
    registry: MarketRegistry,
    feed: SettlementFeed,
    *,
    now: datetime | None = None,
    grace_seconds: float = 120.0,
) -> SettlementReport:
    """Settle everything that has expired and whose settlement print exists."""
    now = now or utcnow()
    report = SettlementReport()

    positions = [
        p
        for p in ledger.expired_positions(now)
        if p.expiry is not None and (now - p.expiry).total_seconds() >= grace_seconds
    ]
    if not positions:
        return report

    # One settlement lookup per distinct expiry, not per position.
    price_cache: dict[datetime, float | None] = {}
    extremes_cache: dict[tuple[datetime, datetime], tuple[float, float] | None] = {}

    for pos in positions:
        spec = registry.get(pos.token_id)
        if spec is None:
            report.unknown += 1
            report.details.append(
                f"{pos.leg_label or pos.token_id[:12]}: no registry entry, cannot settle"
            )
            continue

        expiry = spec.expiry
        if expiry not in price_cache:
            try:
                price_cache[expiry] = feed.settlement_close(expiry)
            except Exception as exc:  # noqa: BLE001
                log.warning("settlement price lookup failed for %s: %s", expiry, exc)
                price_cache[expiry] = None
        price = price_cache[expiry]
        if price is None:
            report.deferred += 1
            report.details.append(f"{spec.label}: settlement print for {expiry:%Y-%m-%d %H:%M} not available yet")
            continue

        run_max = run_min = None
        if spec.needs_path:
            key = (_window_start(spec), expiry)
            if key not in extremes_cache:
                try:
                    extremes_cache[key] = feed.path_extremes(*key)
                except Exception as exc:  # noqa: BLE001
                    log.warning("path lookup failed for %s: %s", spec.label, exc)
                    extremes_cache[key] = None
            ext = extremes_cache[key]
            if ext is None:
                report.deferred += 1
                report.details.append(f"{spec.label}: barrier window data unavailable")
                continue
            run_max, run_min = ext

        pays = spec.pays(price, run_max, run_min)
        if pays is None:
            report.unknown += 1
            report.details.append(f"{spec.label}: payoff undetermined from the available data")
            continue

        pnl = ledger.settle(pos.token_id, 1.0 if pays else 0.0)
        report.settled += 1
        report.realized_pnl += pnl
        report.details.append(
            f"{spec.label} {pos.side}: BTC settled {price:,.2f} -> "
            f"{'PAID' if pays else 'expired worthless'} ({fmt_usd(pnl)})"
        )

    if report.settled:
        log.info("settlement: %s", report.summary())
    return report


def mark_prices(strips: list[Any]) -> dict[str, float]:
    """Current per-token mark prices, keyed by token id.

    Each token is marked at its own book's mid, and when only the other side of
    the pair is quoted we mark at one minus that side.  Positions with no live
    quote are held at cost by the ledger rather than marked to a guess.
    """
    out: dict[str, float] = {}
    for strip in strips:
        for leg in strip.legs:
            yes_mid = leg.yes_book.mid
            no_mid = leg.no_book.mid
            if yes_mid is None and no_mid is not None:
                yes_mid = 1.0 - no_mid
            if no_mid is None and yes_mid is not None:
                no_mid = 1.0 - yes_mid
            if yes_mid is not None:
                out[leg.yes_token] = float(min(max(yes_mid, 0.0), 1.0))
            if no_mid is not None:
                out[leg.no_token] = float(min(max(no_mid, 0.0), 1.0))
    return out
