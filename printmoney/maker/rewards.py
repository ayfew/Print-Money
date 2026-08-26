"""Polymarket's liquidity reward programme: the published scoring rule, exactly.

    S(v, s) = ((v - s) / v)^2 * size        v = max spread, s = spread from mid

    Q_one = sum of S over  bids on YES  and  asks on NO
    Q_two = sum of S over  asks on YES  and  bids on NO

    midpoint in [0.10, 0.90]:   Q = max( min(Q_one, Q_two), max(Q_one, Q_two) / c )
    midpoint outside that:      Q = min(Q_one, Q_two)          (two-sided or nothing)

    c = 3.0.  Sampled once a minute; the daily pool is split pro-rata on Q, with a
    $1 minimum payout.

Three things follow from the formula and they drive every decision downstream:

* The penalty is **quadratic** in distance from the midpoint. Quoting at half the
  allowed spread does not earn half the score, it earns a quarter less than that.
  Tight quotes earn; lazy quotes at the edge of the band earn almost nothing.
* Two-sided quoting is worth up to 3x one-sided, and near 0 or 1 it is mandatory.
* Score is **pro-rata**. The number that matters is not the size of the pool, it
  is the size of the pool divided by everyone else already standing in it.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from ..data.types import Leg, OrderBook
from ..util import USER_AGENT, retry, safe_float

log = logging.getLogger("printmoney.rewards")

#: The one-sided discount in the published formula.
ONE_SIDED_DIVISOR = 3.0

#: Below and above these midpoints, one-sided liquidity scores nothing at all.
TWO_SIDED_ONLY_BELOW = 0.10
TWO_SIDED_ONLY_ABOVE = 0.90

#: Polymarket does not pay out less than this per market per day.
MIN_PAYOUT_USD = 1.0


# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RewardProgram:
    """One market's reward configuration, as published by the CLOB."""

    condition_id: str
    max_spread: float       # in probability units (the API gives cents)
    min_size: float         # shares; smaller orders do not count at all
    daily_rate: float       # USD per day in the pool

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "RewardProgram | None":
        rate = safe_float(row.get("total_daily_rate"), 0.0) or 0.0
        spread = safe_float(row.get("rewards_max_spread"), 0.0) or 0.0
        if rate <= 0 or spread <= 0:
            return None
        return cls(
            condition_id=str(row.get("condition_id") or ""),
            max_spread=spread / 100.0,
            min_size=safe_float(row.get("rewards_min_size"), 0.0) or 0.0,
            daily_rate=rate,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "max_spread": self.max_spread,
            "min_size": self.min_size,
            "daily_rate": self.daily_rate,
        }


def fetch_programs(
    clob_url: str = "https://clob.polymarket.com", *, page_size: int = 500
) -> dict[str, RewardProgram]:
    """Every market currently paying liquidity rewards, keyed by condition id."""
    out: dict[str, RewardProgram] = {}
    cursor = ""
    with httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}) as client:
        while True:
            params: dict[str, Any] = {"limit": page_size}
            if cursor:
                params["next_cursor"] = cursor

            def call() -> Any:
                r = client.get(f"{clob_url}/rewards/markets/current", params=params)
                r.raise_for_status()
                return r.json()

            payload = retry(call, what="GET /rewards/markets/current")
            rows = payload.get("data") or []
            for row in rows:
                program = RewardProgram.from_api(row)
                if program and program.condition_id:
                    out[program.condition_id] = program
            cursor = payload.get("next_cursor") or ""
            if not rows or not cursor or cursor == "LTE=":
                break
    log.info(
        "reward programmes: %d markets, $%,.0f/day total",
        len(out),
        sum(p.daily_rate for p in out.values()),
    )
    return out


# --------------------------------------------------------------------------- #
def spread_score(program: RewardProgram, spread: float, size: float) -> float:
    """S(v, s) = ((v - s)/v)^2 * size, and zero outside the band or under the minimum."""
    if size < program.min_size or spread < 0 or spread > program.max_spread:
        return 0.0
    ratio = (program.max_spread - spread) / program.max_spread
    return ratio * ratio * size


def adjusted_midpoint(book: OrderBook, min_size: float) -> float | None:
    """Midpoint using only orders large enough to qualify.

    Polymarket scores against a size-cutoff-adjusted midpoint, not the raw one.
    Quoting off the raw midpoint when a one-share order is setting it is a good
    way to sit outside the band while believing you are inside it.
    """
    bid = next((l.price for l in book.bids if l.size >= min_size), None)
    ask = next((l.price for l in book.asks if l.size >= min_size), None)
    if bid is not None and ask is not None:
        return 0.5 * (bid + ask)
    return bid if bid is not None else ask


@dataclass
class MarketScore:
    """Everything scored on one market at one instant."""

    q_one: float = 0.0
    q_two: float = 0.0
    midpoint: float = math.nan

    @property
    def two_sided_only(self) -> bool:
        if math.isnan(self.midpoint):
            return True
        return not (TWO_SIDED_ONLY_BELOW <= self.midpoint <= TWO_SIDED_ONLY_ABOVE)

    @property
    def q(self) -> float:
        lo, hi = min(self.q_one, self.q_two), max(self.q_one, self.q_two)
        if self.two_sided_only:
            return lo
        return max(lo, hi / ONE_SIDED_DIVISOR)

    def to_dict(self) -> dict[str, float | bool]:
        return {
            "q_one": round(self.q_one, 4),
            "q_two": round(self.q_two, 4),
            "q": round(self.q, 4),
            "midpoint": None if math.isnan(self.midpoint) else round(self.midpoint, 4),
            "two_sided_only": self.two_sided_only,
        }


def score_book(
    program: RewardProgram,
    yes_book: OrderBook,
    no_book: OrderBook,
) -> MarketScore:
    """Score every resting order on the book - ours and everyone else's.

    Q_one is the side that is long the outcome (bids on YES, asks on NO); Q_two is
    the other. They are the same economic side quoted through two tokens, which is
    why the formula adds them together.
    """
    mid = adjusted_midpoint(yes_book, program.min_size)
    score = MarketScore(midpoint=mid if mid is not None else math.nan)
    if mid is None:
        return score

    for level in yes_book.bids:
        score.q_one += spread_score(program, mid - level.price, level.size)
    for level in yes_book.asks:
        score.q_two += spread_score(program, level.price - mid, level.size)
    # A NO order at price p is a YES order at 1 - p, on the opposite side.
    for level in no_book.bids:
        score.q_two += spread_score(program, mid - (1.0 - level.price), level.size)
    for level in no_book.asks:
        score.q_one += spread_score(program, (1.0 - level.price) - mid, level.size)
    return score


def quote_score(program: RewardProgram, size: float, spread: float) -> MarketScore:
    """Score a two-sided quote of ``size`` shares placed ``spread`` from the mid."""
    s = spread_score(program, spread, size)
    return MarketScore(q_one=s, q_two=s, midpoint=0.5)


# --------------------------------------------------------------------------- #
@dataclass
class Opportunity:
    """What one market would pay us, and what it would cost to stand there."""

    program: RewardProgram
    leg: Leg
    strip_slug: str
    label: str
    seconds_to_expiry: float
    midpoint: float
    existing_q: float
    our_q: float
    quote_size: float
    quote_spread: float
    capital: float
    fair: float = math.nan          # our model's probability, when we have one
    book_spread: float = math.nan

    @property
    def share(self) -> float:
        total = self.existing_q + self.our_q
        return self.our_q / total if total > 0 else 0.0

    @property
    def daily_usd(self) -> float:
        earned = self.program.daily_rate * self.share
        # Polymarket does not cut a cheque under a dollar.
        return earned if earned >= MIN_PAYOUT_USD else 0.0

    @property
    def daily_return(self) -> float:
        return self.daily_usd / self.capital if self.capital > 0 else 0.0

    @property
    def monthly_return(self) -> float:
        return self.daily_return * 30.0

    @property
    def mispricing(self) -> float:
        """How far our fair value sits from the midpoint.

        This is the adverse-selection warning light. Quoting both sides of a
        market we think is mispriced means the side that gets hit is the side we
        already believed was wrong.
        """
        if math.isnan(self.fair) or math.isnan(self.midpoint):
            return math.nan
        return self.fair - self.midpoint

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.program.condition_id,
            "strip": self.strip_slug,
            "leg": self.label,
            "daily_pool": self.program.daily_rate,
            "max_spread": self.program.max_spread,
            "min_size": self.program.min_size,
            "midpoint": self.midpoint,
            "existing_q": round(self.existing_q, 2),
            "our_q": round(self.our_q, 2),
            "share": round(self.share, 5),
            "quote_size": self.quote_size,
            "quote_spread": self.quote_spread,
            "capital": round(self.capital, 2),
            "daily_usd": round(self.daily_usd, 3),
            "monthly_return": round(self.monthly_return, 5),
            "fair": None if math.isnan(self.fair) else round(self.fair, 4),
            "mispricing": None if math.isnan(self.mispricing) else round(self.mispricing, 4),
            "seconds_to_expiry": self.seconds_to_expiry,
        }


def evaluate(
    program: RewardProgram,
    leg: Leg,
    *,
    strip_slug: str,
    seconds_to_expiry: float,
    capital: float,
    spread_fraction: float = 0.5,
    fair: float = math.nan,
) -> Opportunity | None:
    """What would happen if we quoted this market with ``capital`` dollars?

    Quoting ``size`` shares on both sides locks exactly ``size`` dollars: the bid
    costs size x p and the matching ask, posted as a bid on the complement token,
    costs size x (1 - p). So capital and share count are the same number.
    """
    existing = score_book(program, leg.yes_book, leg.no_book)
    if math.isnan(existing.midpoint):
        return None

    size = float(capital)
    if size < program.min_size:
        return None

    spread = program.max_spread * float(spread_fraction)
    ours = quote_score(program, size, spread)
    if ours.q <= 0:
        return None

    book_spread = leg.yes_book.spread
    return Opportunity(
        program=program,
        leg=leg,
        strip_slug=strip_slug,
        label=leg.label or leg.slug,
        seconds_to_expiry=seconds_to_expiry,
        midpoint=existing.midpoint,
        existing_q=existing.q,
        our_q=ours.q,
        quote_size=size,
        quote_spread=spread,
        capital=size,
        fair=fair,
        book_spread=book_spread if book_spread is not None else math.nan,
    )


def best_spread_fraction(
    program: RewardProgram,
    leg: Leg,
    *,
    capital: float,
    candidates: Sequence[float] = (0.15, 0.25, 0.35, 0.5, 0.7, 0.9),
) -> float:
    """Pick where inside the band to quote.

    The score is quadratic in distance so tighter is always better for rewards;
    what stops us is that tighter quotes get filled more often. We take the
    tightest spread that still sits at or outside the current best quote, so we
    are joining the queue rather than jumping in front of it.
    """
    mid = adjusted_midpoint(leg.yes_book, program.min_size)
    if mid is None:
        return 0.5
    bid, ask = leg.yes_book.best_bid, leg.yes_book.best_ask
    touch = None
    if bid is not None and ask is not None:
        touch = min(mid - bid, ask - mid)
    for f in sorted(candidates):
        spread = program.max_spread * f
        if touch is None or spread >= touch:
            return f
    return max(candidates)
