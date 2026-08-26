"""Domain types.

The whole engine is built around one idea: a *strip* is a set of binary markets
on the same underlying with the same settlement time, whose payoffs are all
functions of a single number (the settlement price of BTC).  Once we can express
every leg as a payoff function over settlement price, brackets, above-ladders and
barrier markets all become instruments in one portfolio problem.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Sequence

from ..util import parse_iso, safe_float, utcnow

INF = float("inf")


class Side(str, Enum):
    YES = "YES"
    NO = "NO"


class StripKind(str, Enum):
    #: mutually exclusive price buckets, e.g. "Bitcoin price on Aug 25?"
    BRACKET = "BRACKET"
    #: a ladder of "above K" digitals, i.e. a sampled survival function
    ABOVE = "ABOVE"
    #: path-dependent "will BTC touch K" markets
    TOUCH = "TOUCH"


class LegKind(str, Enum):
    RANGE = "RANGE"          # payoff 1 if lo < S_T <= hi
    ABOVE = "ABOVE"          # payoff 1 if S_T > strike
    TOUCH_UP = "TOUCH_UP"    # payoff 1 if max(S_t) >= barrier
    TOUCH_DOWN = "TOUCH_DOWN"  # payoff 1 if min(S_t) <= barrier


# --------------------------------------------------------------------------- #
# order books
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float

    def to_dict(self) -> dict[str, float]:
        return {"price": self.price, "size": self.size}


@dataclass
class OrderBook:
    """One side of one binary market.

    ``bids`` are sorted best (highest) first, ``asks`` best (lowest) first, which
    is the opposite of what the CLOB returns, so normalisation happens here once.
    """

    token_id: str
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)
    #: When the venue last changed this book. On a quiet market this can be
    #: hours ago and mean nothing at all - it is not a measure of data freshness.
    timestamp: datetime = field(default_factory=utcnow)
    #: When *we* read it. This is the number staleness checks care about.
    fetched_at: datetime = field(default_factory=utcnow)
    tick_size: float = 0.001
    min_order_size: float = 5.0
    neg_risk: bool = False

    # -- construction --------------------------------------------------- #
    @classmethod
    def from_clob(cls, payload: dict[str, Any]) -> "OrderBook":
        def levels(raw: Iterable[dict[str, Any]] | None) -> list[BookLevel]:
            out: list[BookLevel] = []
            for lvl in raw or []:
                p = safe_float(lvl.get("price"))
                s = safe_float(lvl.get("size"))
                if p is None or s is None or s <= 0:
                    continue
                if not 0.0 < p < 1.0:
                    continue
                out.append(BookLevel(p, s))
            return out

        bids = sorted(levels(payload.get("bids")), key=lambda l: -l.price)
        asks = sorted(levels(payload.get("asks")), key=lambda l: l.price)
        ts_raw = payload.get("timestamp")
        try:
            # CLOB sends epoch milliseconds as a string
            ts = datetime.fromtimestamp(int(ts_raw) / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError):
            ts = utcnow()
        return cls(
            token_id=str(payload.get("asset_id") or payload.get("token_id") or ""),
            bids=bids,
            asks=asks,
            timestamp=ts,
            tick_size=safe_float(payload.get("tick_size"), 0.001) or 0.001,
            min_order_size=safe_float(payload.get("min_order_size"), 5.0) or 5.0,
            neg_risk=bool(payload.get("neg_risk", False)),
        )

    # -- observation ---------------------------------------------------- #
    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        b, a = self.best_bid, self.best_ask
        if b is not None and a is not None:
            return 0.5 * (a + b)
        return b if b is not None else a

    @property
    def spread(self) -> float | None:
        b, a = self.best_bid, self.best_ask
        return (a - b) if (b is not None and a is not None) else None

    @property
    def is_empty(self) -> bool:
        return not self.bids and not self.asks

    def depth_usd(self, side: str = "ask", levels: int = 5) -> float:
        book = self.asks if side == "ask" else self.bids
        return sum(l.price * l.size for l in book[:levels])

    def age_seconds(self, now: datetime | None = None) -> float:
        """How long ago we fetched this book."""
        return max(0.0, ((now or utcnow()) - self.fetched_at).total_seconds())

    def quote_age_seconds(self, now: datetime | None = None) -> float:
        """How long ago the venue last moved this book. Informational only."""
        return max(0.0, ((now or utcnow()) - self.timestamp).total_seconds())

    def walk(self, side: str, levels: int) -> list[BookLevel]:
        book = self.asks if side == "ask" else self.bids
        return list(book[:levels])

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "bids": [l.to_dict() for l in self.bids[:10]],
            "asks": [l.to_dict() for l in self.asks[:10]],
            "timestamp": self.timestamp.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "tick_size": self.tick_size,
            "min_order_size": self.min_order_size,
            "neg_risk": self.neg_risk,
        }


EMPTY_BOOK = OrderBook(token_id="")


# --------------------------------------------------------------------------- #
# legs
# --------------------------------------------------------------------------- #
@dataclass
class Leg:
    """A single binary market inside a strip, with its payoff geometry parsed."""

    market_id: str
    condition_id: str
    slug: str
    question: str
    label: str                       # groupItemTitle, e.g. "56,000-58,000"
    kind: LegKind
    yes_token: str
    no_token: str
    lo: float = -INF                 # RANGE
    hi: float = INF                  # RANGE
    strike: float = math.nan         # ABOVE
    barrier: float = math.nan        # TOUCH_*
    liquidity_usd: float = 0.0
    volume_24h: float = 0.0
    fee_rate: float = 0.0
    fee_exponent: float = 1.0
    fee_taker_only: bool = True
    tick_size: float = 0.001
    min_order_shares: float = 5.0
    accepting_orders: bool = True
    neg_risk: bool = False
    market_price: float | None = None  # gamma's own YES mid, used as a fallback

    yes_book: OrderBook = field(default_factory=lambda: OrderBook(token_id=""))
    no_book: OrderBook = field(default_factory=lambda: OrderBook(token_id=""))

    # -- payoff --------------------------------------------------------- #
    def pays_terminal(self, s_t: float) -> bool:
        """YES payoff as a function of settlement price (terminal legs only)."""
        if self.kind is LegKind.RANGE:
            return self.lo < s_t <= self.hi
        if self.kind is LegKind.ABOVE:
            return s_t > self.strike
        raise ValueError(f"{self.kind} is path-dependent; use pays_path()")

    def pays_path(self, s_t: float, run_max: float, run_min: float) -> bool:
        if self.kind is LegKind.TOUCH_UP:
            return run_max >= self.barrier
        if self.kind is LegKind.TOUCH_DOWN:
            return run_min <= self.barrier
        return self.pays_terminal(s_t)

    @property
    def is_path_dependent(self) -> bool:
        return self.kind in (LegKind.TOUCH_UP, LegKind.TOUCH_DOWN)

    @property
    def boundaries(self) -> list[float]:
        """Finite settlement-price boundaries this leg introduces."""
        out: list[float] = []
        if self.kind is LegKind.RANGE:
            for x in (self.lo, self.hi):
                if math.isfinite(x):
                    out.append(x)
        elif self.kind is LegKind.ABOVE and math.isfinite(self.strike):
            out.append(self.strike)
        return out

    def book(self, side: Side) -> OrderBook:
        return self.yes_book if side is Side.YES else self.no_book

    def token(self, side: Side) -> str:
        return self.yes_token if side is Side.YES else self.no_token

    @property
    def yes_mid(self) -> float | None:
        m = self.yes_book.mid
        if m is not None:
            return m
        n = self.no_book.mid
        if n is not None:
            return 1.0 - n
        return self.market_price

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "label": self.label,
            "kind": self.kind.value,
            "lo": None if self.lo == -INF else self.lo,
            "hi": None if self.hi == INF else self.hi,
            "strike": None if math.isnan(self.strike) else self.strike,
            "barrier": None if math.isnan(self.barrier) else self.barrier,
            "yes_token": self.yes_token,
            "no_token": self.no_token,
            "liquidity_usd": self.liquidity_usd,
            "fee_rate": self.fee_rate,
            "yes_book": self.yes_book.to_dict(),
            "no_book": self.no_book.to_dict(),
            "market_price": self.market_price,
        }


# --------------------------------------------------------------------------- #
# strips
# --------------------------------------------------------------------------- #
@dataclass
class Strip:
    event_id: str
    slug: str
    title: str
    kind: StripKind
    expiry: datetime
    start: datetime | None = None
    legs: list[Leg] = field(default_factory=list)
    neg_risk: bool = False
    liquidity_usd: float = 0.0
    volume_24h: float = 0.0
    fetched_at: datetime = field(default_factory=utcnow)

    def seconds_to_expiry(self, now: datetime | None = None) -> float:
        return max(0.0, (self.expiry - (now or utcnow())).total_seconds())

    @property
    def tradable_legs(self) -> list[Leg]:
        return [l for l in self.legs if l.accepting_orders and not (l.yes_book.is_empty and l.no_book.is_empty)]

    def boundaries(self) -> list[float]:
        out: set[float] = set()
        for leg in self.legs:
            out.update(leg.boundaries)
        return sorted(out)

    def sum_yes_mid(self) -> float | None:
        vals = [l.yes_mid for l in self.legs]
        if any(v is None for v in vals):
            return None
        return float(sum(v for v in vals if v is not None))

    def is_partition(self) -> bool:
        """True when the RANGE legs tile the real line without gap or overlap."""
        if self.kind is not StripKind.BRACKET:
            return False
        rs = sorted(
            (l for l in self.legs if l.kind is LegKind.RANGE), key=lambda l: (l.lo, l.hi)
        )
        if len(rs) < 2:
            return False
        if rs[0].lo != -INF or rs[-1].hi != INF:
            return False
        for a, b in zip(rs, rs[1:]):
            if not math.isclose(a.hi, b.lo, rel_tol=1e-9, abs_tol=1e-6):
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "slug": self.slug,
            "title": self.title,
            "kind": self.kind.value,
            "expiry": self.expiry.isoformat(),
            "start": self.start.isoformat() if self.start else None,
            "neg_risk": self.neg_risk,
            "liquidity_usd": self.liquidity_usd,
            "volume_24h": self.volume_24h,
            "fetched_at": self.fetched_at.isoformat(),
            "legs": [l.to_dict() for l in self.legs],
        }


# --------------------------------------------------------------------------- #
# label / question parsing
# --------------------------------------------------------------------------- #
_NUM = r"\$?\s*([0-9][0-9,\.]*)\s*([kKmM])?"


def _to_number(digits: str, suffix: str | None) -> float:
    v = float(digits.replace(",", ""))
    if suffix:
        v *= {"k": 1e3, "m": 1e6}[suffix.lower()]
    return v


#: Anything below this is calendar noise ("on August 25?"), not a BTC price.
MIN_PLAUSIBLE_PRICE = 1_000.0


def parse_numbers(text: str) -> list[float]:
    """Pull every money-ish number out of a market question or label."""
    out: list[float] = []
    for m in re.finditer(_NUM, text or ""):
        digits, suffix = m.group(1), m.group(2)
        if not any(ch.isdigit() for ch in digits):
            continue
        try:
            out.append(_to_number(digits, suffix))
        except ValueError:
            continue
    return out


def parse_prices(text: str, min_value: float = MIN_PLAUSIBLE_PRICE) -> list[float]:
    """parse_numbers, minus the day-of-month and other small integers."""
    return [v for v in parse_numbers(text) if v >= min_value]


_BETWEEN = re.compile(r"between\b", re.I)
_LESS = re.compile(r"\b(less than|below|under)\b", re.I)
_GREATER = re.compile(r"\b(greater than|above|over|at least)\b", re.I)
_REACH = re.compile(r"\b(reach|hit|touch)\b", re.I)
_DIP = re.compile(r"\b(dip|fall|drop|down to)\b", re.I)

_ARROW_UP = "↑"
_ARROW_DOWN = "↓"


def classify_leg(question: str, label: str) -> tuple[LegKind, dict[str, float]]:
    """Work out a leg payoff from its question text, falling back to the label.

    Polymarket writes these questions consistently, but the label is the tiebreak
    for the touch markets where the up/down arrow is the only signal.
    """
    q = question or ""
    lab = label or ""
    q_nums = parse_prices(q)
    lab_nums = parse_prices(lab)
    nums = q_nums or lab_nums

    # Path-dependent markets first: "reach"/"dip to" plus the arrow glyph.
    if _ARROW_UP in lab or (_REACH.search(q) and not _DIP.search(q)):
        if _ARROW_DOWN in lab or _DIP.search(q):
            pass  # ambiguous, fall through to the down branch below
        elif nums:
            return LegKind.TOUCH_UP, {"barrier": nums[0]}
    if _ARROW_DOWN in lab or _DIP.search(q):
        if nums:
            return LegKind.TOUCH_DOWN, {"barrier": nums[0]}

    if _BETWEEN.search(q) and len(nums) >= 2:
        lo, hi = sorted(nums[:2])
        return LegKind.RANGE, {"lo": lo, "hi": hi}

    if _LESS.search(q) or lab.strip().startswith("<"):
        if nums:
            return LegKind.RANGE, {"lo": -INF, "hi": nums[0]}

    if _GREATER.search(q) or lab.strip().startswith(">"):
        if nums:
            # In a bracket strip this is the open top bucket; in an above-ladder
            # it is a digital.  The caller disambiguates using the strip kind.
            return LegKind.ABOVE, {"strike": nums[0]}

    # Bare "56,000-58,000" style labels.
    if "-" in lab and len(lab_nums) >= 2:
        lo, hi = sorted(lab_nums[:2])
        return LegKind.RANGE, {"lo": lo, "hi": hi}

    if len(nums) == 1:
        return LegKind.ABOVE, {"strike": nums[0]}

    raise ValueError(f"cannot classify leg: question={question!r} label={label!r}")


def infer_strip_kind(slug: str, title: str) -> StripKind:
    s = (slug or "").lower()
    t = (title or "").lower()
    if "hit" in s or "reach" in t or "hit" in t:
        return StripKind.TOUCH
    if "-above-" in s or "above" in t:
        return StripKind.ABOVE
    return StripKind.BRACKET


@dataclass
class Quote:
    """A spot observation with provenance, so staleness can be judged."""

    price: float
    source: str
    timestamp: datetime = field(default_factory=utcnow)

    def age_seconds(self, now: datetime | None = None) -> float:
        return max(0.0, ((now or utcnow()) - self.timestamp).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        return {"price": self.price, "source": self.source, "timestamp": self.timestamp.isoformat()}


@dataclass
class Candle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def candles_from_binance(rows: Sequence[Sequence[Any]]) -> list[Candle]:
    out: list[Candle] = []
    for r in rows:
        try:
            out.append(
                Candle(
                    open_time=datetime.fromtimestamp(int(r[0]) / 1000.0, tz=timezone.utc),
                    open=float(r[1]),
                    high=float(r[2]),
                    low=float(r[3]),
                    close=float(r[4]),
                    volume=float(r[5]),
                )
            )
        except (TypeError, ValueError, IndexError):
            continue
    return out


__all__ = [
    "INF",
    "Side",
    "StripKind",
    "LegKind",
    "BookLevel",
    "OrderBook",
    "EMPTY_BOOK",
    "Leg",
    "Strip",
    "Quote",
    "Candle",
    "candles_from_binance",
    "classify_leg",
    "infer_strip_kind",
    "parse_numbers",
    "parse_prices",
    "parse_iso",
]
