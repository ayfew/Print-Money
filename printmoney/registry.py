"""A durable record of what every token we have ever bought actually pays.

Positions outlive markets.  Once an event resolves it drops out of the Gamma
listing, and if the only description of a payoff lived in that listing we would
be holding shares we can no longer settle.  So the moment an order is planned we
write the leg's geometry here, keyed by token id, and settlement reads from this
file rather than from the API.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .data.types import INF, Leg, LegKind, Side, Strip
from .util import parse_iso, read_json, write_json

log = logging.getLogger("printmoney.registry")


@dataclass
class TokenSpec:
    """Enough to settle one token without asking anyone."""

    token_id: str
    side: str          # which outcome this token is
    kind: str          # LegKind of the underlying leg
    label: str
    question: str
    strip_slug: str
    expiry: datetime
    window_start: datetime | None = None
    lo: float | None = None
    hi: float | None = None
    strike: float | None = None
    barrier: float | None = None

    # ------------------------------------------------------------------ #
    def yes_pays(
        self,
        settlement_price: float,
        run_max: float | None = None,
        run_min: float | None = None,
    ) -> bool | None:
        """Does the YES outcome pay? ``None`` when we lack the data to say."""
        k = LegKind(self.kind)
        if k is LegKind.RANGE:
            lo = -INF if self.lo is None else self.lo
            hi = INF if self.hi is None else self.hi
            return lo < settlement_price <= hi
        if k is LegKind.ABOVE:
            if self.strike is None:
                return None
            return settlement_price > self.strike
        if k is LegKind.TOUCH_UP:
            if self.barrier is None or run_max is None:
                return None
            return run_max >= self.barrier
        if k is LegKind.TOUCH_DOWN:
            if self.barrier is None or run_min is None:
                return None
            return run_min <= self.barrier
        return None

    def pays(
        self,
        settlement_price: float,
        run_max: float | None = None,
        run_min: float | None = None,
    ) -> bool | None:
        yes = self.yes_pays(settlement_price, run_max, run_min)
        if yes is None:
            return None
        return yes if self.side == Side.YES.value else not yes

    @property
    def needs_path(self) -> bool:
        return LegKind(self.kind) in (LegKind.TOUCH_UP, LegKind.TOUCH_DOWN)

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "side": self.side,
            "kind": self.kind,
            "label": self.label,
            "question": self.question,
            "strip_slug": self.strip_slug,
            "expiry": self.expiry.isoformat(),
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "lo": self.lo,
            "hi": self.hi,
            "strike": self.strike,
            "barrier": self.barrier,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TokenSpec":
        return cls(
            token_id=str(d["token_id"]),
            side=str(d.get("side", "YES")),
            kind=str(d.get("kind", "RANGE")),
            label=str(d.get("label", "")),
            question=str(d.get("question", "")),
            strip_slug=str(d.get("strip_slug", "")),
            expiry=parse_iso(d["expiry"]),
            window_start=parse_iso(d["window_start"]) if d.get("window_start") else None,
            lo=d.get("lo"),
            hi=d.get("hi"),
            strike=d.get("strike"),
            barrier=d.get("barrier"),
        )


def _finite(x: float) -> float | None:
    import math

    return None if (x is None or math.isnan(x) or math.isinf(x)) else float(x)


class MarketRegistry:
    def __init__(self, path: str | Path = "state/markets.json") -> None:
        self.path = Path(path)
        self.specs: dict[str, TokenSpec] = {}
        raw = read_json(self.path, {})
        if isinstance(raw, dict):
            for tid, d in raw.items():
                try:
                    self.specs[tid] = TokenSpec.from_dict(d)
                except Exception as exc:  # noqa: BLE001
                    log.warning("dropping unreadable registry entry %s: %s", tid[:12], exc)

    # ------------------------------------------------------------------ #
    def record_leg(self, strip: Strip, leg: Leg) -> None:
        for side, token in ((Side.YES, leg.yes_token), (Side.NO, leg.no_token)):
            if not token:
                continue
            self.specs[token] = TokenSpec(
                token_id=token,
                side=side.value,
                kind=leg.kind.value,
                label=leg.label or leg.slug,
                question=leg.question,
                strip_slug=strip.slug,
                expiry=strip.expiry,
                window_start=strip.start,
                lo=_finite(leg.lo),
                hi=_finite(leg.hi),
                strike=_finite(leg.strike),
                barrier=_finite(leg.barrier),
            )

    def record_strip(self, strip: Strip) -> None:
        for leg in strip.legs:
            self.record_leg(strip, leg)

    def record_strips(self, strips: Iterable[Strip]) -> None:
        for s in strips:
            self.record_strip(s)

    def get(self, token_id: str) -> TokenSpec | None:
        return self.specs.get(token_id)

    def save(self) -> None:
        write_json(self.path, {k: v.to_dict() for k, v in self.specs.items()})

    def prune(self, keep_token_ids: Iterable[str], before: datetime) -> int:
        """Forget long-settled markets we no longer hold."""
        keep = set(keep_token_ids)
        gone = [
            tid
            for tid, spec in self.specs.items()
            if tid not in keep and spec.expiry < before
        ]
        for tid in gone:
            del self.specs[tid]
        return len(gone)

    def __len__(self) -> int:
        return len(self.specs)
