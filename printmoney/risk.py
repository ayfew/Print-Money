"""Risk limits and kill switches.

The linear program already bounds the loss on any *single* plan.  This module
bounds everything else: how much can be at risk at once, how fast we are allowed
to trade, how stale the data may be, and when to stop entirely.

Every refusal returns a sentence explaining itself.  A bot that silently declines
to trade is indistinguishable from a bot that is broken.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from .config import Config
from .data.types import Quote, Strip
from .ledger import Ledger
from .strategy.lp import Plan
from .strategy.single import TouchPlan
from .util import fmt_usd, human_dt, read_json, utcnow, write_json

log = logging.getLogger("printmoney.risk")


@dataclass
class RiskState:
    day: str = ""
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    halted_at: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "day": self.day,
            "day_start_equity": round(self.day_start_equity, 4),
            "peak_equity": round(self.peak_equity, 4),
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "halted_at": self.halted_at.isoformat() if self.halted_at else None,
        }


@dataclass
class Verdict:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    scale: float = 1.0

    def __bool__(self) -> bool:
        return self.approved

    @property
    def why(self) -> str:
        return "; ".join(self.reasons) if self.reasons else ("approved" if self.approved else "rejected")


class RiskManager:
    def __init__(self, cfg: Config, state_path: str = "state/risk.json") -> None:
        self.cfg = cfg
        self.state_path = state_path
        self.state = RiskState()
        raw = read_json(state_path)
        if isinstance(raw, dict):
            self.state.day = str(raw.get("day", ""))
            self.state.day_start_equity = float(raw.get("day_start_equity", 0.0))
            self.state.peak_equity = float(raw.get("peak_equity", 0.0))
            self.state.halted = bool(raw.get("halted", False))
            self.state.halt_reason = str(raw.get("halt_reason", ""))

    def save(self) -> None:
        write_json(self.state_path, self.state.to_dict())

    # ------------------------------------------------------------------ #
    def start_cycle(self, equity: float, now: datetime | None = None) -> None:
        """Roll the daily window and refresh the high-water mark."""
        now = now or utcnow()
        today = now.strftime("%Y-%m-%d")
        if self.state.day != today:
            self.state.day = today
            self.state.day_start_equity = equity
            # A new day clears a daily-loss halt but not a drawdown halt.
            if self.state.halted and self.state.halt_reason.startswith("daily loss"):
                log.info("new trading day: clearing the daily-loss halt")
                self.state.halted = False
                self.state.halt_reason = ""
        if equity > self.state.peak_equity:
            self.state.peak_equity = equity
        if self.state.day_start_equity <= 0:
            self.state.day_start_equity = equity

    def check_kill_switches(self, equity: float) -> Verdict:
        r = self.cfg.risk
        reasons: list[str] = []

        if self.state.halted:
            return Verdict(False, [f"halted: {self.state.halt_reason}"])

        if self.state.day_start_equity > 0:
            daily = (equity - self.state.day_start_equity) / self.state.day_start_equity
            if daily <= -r.max_daily_loss:
                self._halt(f"daily loss {daily:.2%} exceeded the {r.max_daily_loss:.0%} limit")
                reasons.append(self.state.halt_reason)

        if self.state.peak_equity > 0:
            dd = (self.state.peak_equity - equity) / self.state.peak_equity
            if dd >= r.max_drawdown:
                self._halt(f"drawdown {dd:.2%} reached the {r.max_drawdown:.0%} limit")
                reasons.append(self.state.halt_reason)

        return Verdict(not reasons, reasons)

    def _halt(self, reason: str) -> None:
        if not self.state.halted:
            log.error("KILL SWITCH: %s - no further orders will be sent", reason)
        self.state.halted = True
        self.state.halt_reason = reason
        self.state.halted_at = utcnow()
        self.save()

    def resume(self) -> None:
        log.warning("risk halt cleared by operator")
        self.state.halted = False
        self.state.halt_reason = ""
        self.state.halted_at = None
        self.save()

    # ------------------------------------------------------------------ #
    def check_data_freshness(self, spot: Quote, strips: Sequence[Strip]) -> Verdict:
        r = self.cfg.risk
        reasons: list[str] = []
        now = utcnow()

        age = spot.age_seconds(now)
        if age > r.stale_spot_seconds:
            reasons.append(f"spot quote is {human_dt(age)} old (limit {r.stale_spot_seconds:.0f}s)")

        for strip in strips:
            # How long ago we *read* the books, not how long ago the venue last
            # moved them. A book nobody has touched for an hour is an illiquid
            # market, not bad data, and refusing to trade it would be wrong.
            worst = 0.0
            for leg in strip.legs:
                for book in (leg.yes_book, leg.no_book):
                    if not book.is_empty:
                        worst = max(worst, book.age_seconds(now))
            if worst > r.stale_book_seconds:
                reasons.append(
                    f"{strip.slug}: order books were read {human_dt(worst)} ago "
                    f"(limit {r.stale_book_seconds:.0f}s)"
                )
        return Verdict(not reasons, reasons)

    def check_strip(self, strip: Strip) -> Verdict:
        """Structural sanity of one strip before we price anything off it."""
        reasons: list[str] = []
        f = self.cfg.filters

        tte = strip.seconds_to_expiry()
        if tte < f.min_seconds_to_expiry:
            reasons.append(f"expires in {human_dt(tte)}, under the {human_dt(f.min_seconds_to_expiry)} floor")

        tradable = strip.tradable_legs
        if len(tradable) < 2:
            reasons.append(f"only {len(tradable)} legs have a live book")

        if self.cfg.risk.require_complete_strip and strip.kind.value == "BRACKET":
            if not strip.is_partition():
                reasons.append("bracket legs do not tile the price line (missing or overlapping buckets)")

        if strip.liquidity_usd < f.min_event_liquidity_usd:
            reasons.append(
                f"event liquidity {fmt_usd(strip.liquidity_usd)} below the "
                f"{fmt_usd(f.min_event_liquidity_usd)} floor"
            )
        return Verdict(not reasons, reasons)

    # ------------------------------------------------------------------ #
    def approve(
        self,
        plan: Plan | TouchPlan,
        ledger: Ledger,
        equity: float,
        *,
        open_events: int = 0,
    ) -> Verdict:
        """Portfolio-level check on a plan the strategy already accepted."""
        r = self.cfg.risk
        reasons: list[str] = []
        scale = 1.0

        if self.state.halted:
            return Verdict(False, [f"halted: {self.state.halt_reason}"])

        stake = plan.capital_used
        if stake <= 0:
            return Verdict(False, ["plan deploys no capital"])

        if stake > ledger.cash:
            reasons.append(
                f"plan needs {fmt_usd(stake)} but only {fmt_usd(ledger.cash)} cash is free"
            )

        gross_cap = equity * r.max_gross_exposure
        projected = ledger.exposure() + stake
        if projected > gross_cap:
            room = gross_cap - ledger.exposure()
            if room <= 0:
                reasons.append(
                    f"gross exposure {fmt_usd(ledger.exposure())} already at the "
                    f"{r.max_gross_exposure:.0%} cap"
                )
            else:
                scale = min(scale, room / stake)

        if open_events >= r.max_open_events:
            reasons.append(f"already holding {open_events} events (cap {r.max_open_events})")

        recent = ledger.fills_last_hour()
        if recent >= r.max_trades_per_hour:
            reasons.append(f"{recent} fills in the last hour (cap {r.max_trades_per_hour})")

        n_orders = len(getattr(plan, "orders", None) or getattr(plan, "trades", []))
        if n_orders > r.max_orders_per_cycle:
            reasons.append(
                f"{n_orders} orders in one cycle exceeds the {r.max_orders_per_cycle} cap"
            )

        if reasons:
            return Verdict(False, reasons)
        if scale < 0.999:
            return Verdict(True, [f"scaled to {scale:.0%} to stay inside the exposure cap"], scale)
        return Verdict(True, [], 1.0)
