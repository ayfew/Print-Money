"""Positions, fills, cash and the equity curve.

The ledger is the only thing that knows what we actually own.  It is written
atomically after every change so that a crash mid-cycle leaves a readable file,
and it is deliberately dumb: no strategy logic lives here, only bookkeeping that
has to be right.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .util import fmt_usd, parse_iso, read_json, utcnow, write_json

log = logging.getLogger("printmoney.ledger")


@dataclass
class Fill:
    ts: datetime
    token_id: str
    strip_slug: str
    leg_label: str
    question: str
    side: str
    price: float
    shares: float
    fee: float
    mode: str
    order_id: str = ""

    @property
    def cost(self) -> float:
        return self.shares * self.price + self.fee

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "token_id": self.token_id,
            "strip_slug": self.strip_slug,
            "leg_label": self.leg_label,
            "question": self.question,
            "side": self.side,
            "price": self.price,
            "shares": self.shares,
            "fee": self.fee,
            "cost": round(self.cost, 6),
            "mode": self.mode,
            "order_id": self.order_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Fill":
        return cls(
            ts=parse_iso(d["ts"]),
            token_id=str(d["token_id"]),
            strip_slug=str(d.get("strip_slug", "")),
            leg_label=str(d.get("leg_label", "")),
            question=str(d.get("question", "")),
            side=str(d.get("side", "YES")),
            price=float(d["price"]),
            shares=float(d["shares"]),
            fee=float(d.get("fee", 0.0)),
            mode=str(d.get("mode", "paper")),
            order_id=str(d.get("order_id", "")),
        )


@dataclass
class Position:
    token_id: str
    strip_slug: str
    leg_label: str
    question: str
    side: str
    shares: float = 0.0
    cost_basis: float = 0.0     # includes fees paid
    expiry: datetime | None = None
    settled: bool = False

    @property
    def avg_price(self) -> float:
        return self.cost_basis / self.shares if self.shares > 0 else 0.0

    def value_at(self, yes_prob: float) -> float:
        """Mark-to-market using a YES probability for the underlying leg."""
        p = yes_prob if self.side == "YES" else 1.0 - yes_prob
        return self.shares * min(max(p, 0.0), 1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "strip_slug": self.strip_slug,
            "leg_label": self.leg_label,
            "question": self.question,
            "side": self.side,
            "shares": round(self.shares, 6),
            "cost_basis": round(self.cost_basis, 6),
            "avg_price": round(self.avg_price, 6),
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "settled": self.settled,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Position":
        return cls(
            token_id=str(d["token_id"]),
            strip_slug=str(d.get("strip_slug", "")),
            leg_label=str(d.get("leg_label", "")),
            question=str(d.get("question", "")),
            side=str(d.get("side", "YES")),
            shares=float(d.get("shares", 0.0)),
            cost_basis=float(d.get("cost_basis", 0.0)),
            expiry=parse_iso(d["expiry"]) if d.get("expiry") else None,
            settled=bool(d.get("settled", False)),
        )


# --------------------------------------------------------------------------- #
class Ledger:
    def __init__(self, path: str | Path, starting_cash: float) -> None:
        self.path = Path(path)
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.positions: dict[str, Position] = {}
        self.fills: list[Fill] = []
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.equity_curve: list[tuple[datetime, float]] = []
        self.created_at = utcnow()

    # ------------------------------------------------------------------ #
    @classmethod
    def load_or_new(cls, path: str | Path, starting_cash: float) -> "Ledger":
        led = cls(path, starting_cash)
        raw = read_json(path)
        if not isinstance(raw, dict):
            return led
        try:
            led.starting_cash = float(raw.get("starting_cash", starting_cash))
            led.cash = float(raw.get("cash", starting_cash))
            led.realized_pnl = float(raw.get("realized_pnl", 0.0))
            led.fees_paid = float(raw.get("fees_paid", 0.0))
            led.created_at = parse_iso(raw["created_at"]) if raw.get("created_at") else utcnow()
            led.positions = {
                k: Position.from_dict(v) for k, v in (raw.get("positions") or {}).items()
            }
            led.fills = [Fill.from_dict(f) for f in (raw.get("fills") or [])]
            led.equity_curve = [
                (parse_iso(t), float(v)) for t, v in (raw.get("equity_curve") or [])
            ]
            log.info(
                "loaded ledger: cash %s, %d positions, %d fills",
                fmt_usd(led.cash),
                len(led.positions),
                len(led.fills),
            )
        except Exception as exc:  # noqa: BLE001
            log.error("ledger at %s is unreadable (%s); starting fresh", path, exc)
            return cls(path, starting_cash)
        return led

    def save(self) -> None:
        write_json(
            self.path,
            {
                "created_at": self.created_at.isoformat(),
                "updated_at": utcnow().isoformat(),
                "starting_cash": self.starting_cash,
                "cash": round(self.cash, 6),
                "realized_pnl": round(self.realized_pnl, 6),
                "fees_paid": round(self.fees_paid, 6),
                "positions": {k: p.to_dict() for k, p in self.positions.items()},
                # keep the tail only; a long paper run should not grow without bound
                "fills": [f.to_dict() for f in self.fills[-2000:]],
                "equity_curve": [(t.isoformat(), round(v, 6)) for t, v in self.equity_curve[-5000:]],
            },
        )

    # ------------------------------------------------------------------ #
    def record_fill(self, fill: Fill, expiry: datetime | None = None) -> None:
        cost = fill.cost
        if cost > self.cash + 1e-9:
            raise ValueError(
                f"fill costs {fmt_usd(cost)} but only {fmt_usd(self.cash)} cash is available"
            )
        self.cash -= cost
        self.fees_paid += fill.fee
        self.fills.append(fill)

        pos = self.positions.get(fill.token_id)
        if pos is None:
            pos = Position(
                token_id=fill.token_id,
                strip_slug=fill.strip_slug,
                leg_label=fill.leg_label,
                question=fill.question,
                side=fill.side,
                expiry=expiry,
            )
            self.positions[fill.token_id] = pos
        pos.shares += fill.shares
        pos.cost_basis += cost
        if expiry and not pos.expiry:
            pos.expiry = expiry

    # ------------------------------------------------------------------ #
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.shares > 0 and not p.settled]

    def exposure(self) -> float:
        """Cost basis still at risk."""
        return sum(p.cost_basis for p in self.open_positions())

    def mark_to_market(self, yes_probs: dict[str, float]) -> float:
        """Total equity: cash plus the marked value of everything open.

        ``yes_probs`` is keyed by token id and holds that token's own probability
        (already flipped for NO tokens by the caller).
        """
        value = 0.0
        for pos in self.open_positions():
            p = yes_probs.get(pos.token_id)
            if p is None:
                value += pos.cost_basis  # no quote: hold at cost rather than invent one
            else:
                value += pos.shares * min(max(float(p), 0.0), 1.0)
        return self.cash + value

    def record_equity(self, equity: float, ts: datetime | None = None) -> None:
        self.equity_curve.append((ts or utcnow(), float(equity)))

    # ------------------------------------------------------------------ #
    def settle(self, token_id: str, payoff_per_share: float) -> float:
        """Settle one token at 1.0 or 0.0. Returns realised PnL."""
        pos = self.positions.get(token_id)
        if pos is None or pos.settled or pos.shares <= 0:
            return 0.0
        proceeds = pos.shares * float(payoff_per_share)
        pnl = proceeds - pos.cost_basis
        self.cash += proceeds
        self.realized_pnl += pnl
        pos.settled = True
        log.info(
            "settled %s (%s %s) at %.2f: %s",
            pos.leg_label or pos.token_id[:10],
            pos.side,
            f"{pos.shares:g} shares",
            payoff_per_share,
            fmt_usd(pnl),
        )
        return pnl

    def expired_positions(self, now: datetime | None = None) -> list[Position]:
        now = now or utcnow()
        return [
            p
            for p in self.open_positions()
            if p.expiry is not None and p.expiry <= now
        ]

    # ------------------------------------------------------------------ #
    def stats(self, equity: float | None = None) -> dict[str, Any]:
        eq = equity if equity is not None else self.cash + self.exposure()
        peak = max((v for _, v in self.equity_curve), default=eq)
        drawdown = (peak - eq) / peak if peak > 0 else 0.0
        wins = sum(1 for f in self.fills if f.shares > 0)
        return {
            "cash": round(self.cash, 4),
            "equity": round(eq, 4),
            "starting_cash": self.starting_cash,
            "total_return": round((eq / self.starting_cash - 1.0), 6)
            if self.starting_cash
            else 0.0,
            "realized_pnl": round(self.realized_pnl, 4),
            "fees_paid": round(self.fees_paid, 4),
            "exposure": round(self.exposure(), 4),
            "open_positions": len(self.open_positions()),
            "fills": len(self.fills),
            "peak_equity": round(peak, 4),
            "drawdown": round(drawdown, 6),
            "orders_placed": wins,
        }

    def fills_since(self, since: datetime) -> list[Fill]:
        return [f for f in self.fills if f.ts >= since]

    def fills_last_hour(self, now: datetime | None = None) -> int:
        cutoff = (now or utcnow()) - timedelta(hours=1)
        return sum(1 for f in self.fills if f.ts >= cutoff)
