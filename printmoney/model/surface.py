"""Implied vs fair probability surfaces, and the coherence checks between them.

Two independent readings of the same event:

* the **implied** surface, read straight off the Polymarket books;
* the **fair** surface, read off the Monte-Carlo paths.

Before comparing them we check the implied surface against *itself*.  A bracket
strip whose mid prices sum to 1.03 or an above-ladder that is not monotone in
strike is mispriced without any model at all, and those are the trades worth
taking first because they do not depend on our volatility being right.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from ..data.types import INF, Leg, LegKind, Strip, StripKind
from .paths import ModelEnsemble, PathResult

log = logging.getLogger("printmoney.surface")


# --------------------------------------------------------------------------- #
@dataclass
class LegQuote:
    """Everything the strategy layer needs to know about one leg, right now."""

    index: int
    leg: Leg
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    implied: float | None            # best available YES probability estimate
    fair: float = math.nan           # base-model fair probability
    fair_lo: float = math.nan        # worst ensemble member
    fair_hi: float = math.nan        # best ensemble member
    mc_stderr: float = 0.0

    @property
    def label(self) -> str:
        return self.leg.label or self.leg.question[:32]

    @property
    def spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    @property
    def buy_yes_edge(self) -> float:
        """Raw (pre-fee) edge from lifting the YES offer."""
        if self.yes_ask is None or math.isnan(self.fair):
            return -math.inf
        return self.fair - self.yes_ask

    @property
    def buy_no_edge(self) -> float:
        if self.no_ask is None or math.isnan(self.fair):
            return -math.inf
        return (1.0 - self.fair) - self.no_ask

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "label": self.label,
            "kind": self.leg.kind.value,
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "no_ask": self.no_ask,
            "implied": self.implied,
            "fair": None if math.isnan(self.fair) else self.fair,
            "fair_lo": None if math.isnan(self.fair_lo) else self.fair_lo,
            "fair_hi": None if math.isnan(self.fair_hi) else self.fair_hi,
            "mc_stderr": self.mc_stderr,
        }


@dataclass
class Incoherence:
    """A pricing inconsistency inside the implied surface itself."""

    kind: str
    detail: str
    size: float          # how far off, in probability points
    legs: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "detail": self.detail, "size": self.size, "legs": self.legs}


@dataclass
class Surface:
    strip: Strip
    quotes: list[LegQuote]
    years_to_expiry: float
    spot: float
    incoherences: list[Incoherence] = field(default_factory=list)
    implied_sum: float | None = None
    normalised_pdf: list[float] = field(default_factory=list)

    def quote(self, index: int) -> LegQuote:
        return self.quotes[index]

    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.strip.slug,
            "kind": self.strip.kind.value,
            "spot": self.spot,
            "years_to_expiry": self.years_to_expiry,
            "implied_sum": self.implied_sum,
            "quotes": [q.to_dict() for q in self.quotes],
            "incoherences": [i.to_dict() for i in self.incoherences],
        }


# --------------------------------------------------------------------------- #
def _side_prices(leg: Leg) -> tuple[float | None, float | None, float | None, float | None]:
    yb, ya = leg.yes_book.best_bid, leg.yes_book.best_ask
    nb, na = leg.no_book.best_bid, leg.no_book.best_ask
    return yb, ya, nb, na


def _implied(leg: Leg, yb: float | None, ya: float | None, nb: float | None, na: float | None) -> float | None:
    """Best single estimate of the market's YES probability.

    Prefer a two-sided YES mid; else infer from the NO book (a NO mid of 0.2
    means YES is 0.8); else fall back to Gamma's last-known price.
    """
    if yb is not None and ya is not None:
        return 0.5 * (yb + ya)
    if nb is not None and na is not None:
        return 1.0 - 0.5 * (nb + na)
    if yb is not None or ya is not None:
        return yb if yb is not None else ya
    if nb is not None or na is not None:
        return 1.0 - (nb if nb is not None else na)  # type: ignore[operator]
    return leg.market_price


def fair_probability(leg: Leg, model: PathResult) -> float:
    if leg.kind is LegKind.RANGE:
        return model.prob_range(leg.lo, leg.hi)
    if leg.kind is LegKind.ABOVE:
        return model.prob_above(leg.strike)
    if leg.kind is LegKind.TOUCH_UP:
        return model.prob_touch_up(leg.barrier)
    if leg.kind is LegKind.TOUCH_DOWN:
        return model.prob_touch_down(leg.barrier)
    raise ValueError(f"unhandled leg kind {leg.kind}")


def build_surface(strip: Strip, ensemble: ModelEnsemble, spot: float, years: float) -> Surface:
    quotes: list[LegQuote] = []
    for i, leg in enumerate(strip.legs):
        yb, ya, nb, na = _side_prices(leg)
        q = LegQuote(
            index=i,
            leg=leg,
            yes_bid=yb,
            yes_ask=ya,
            no_bid=nb,
            no_ask=na,
            implied=_implied(leg, yb, ya, nb, na),
        )
        fairs = [fair_probability(leg, m) for m in ensemble]
        if fairs:
            q.fair = fairs[0]
            q.fair_lo = min(fairs)
            q.fair_hi = max(fairs)
            q.mc_stderr = ensemble.base.stderr(q.fair)
        quotes.append(q)

    surface = Surface(
        strip=strip, quotes=quotes, years_to_expiry=years, spot=spot
    )
    _fill_partition_stats(surface)
    surface.incoherences = find_incoherences(surface)
    return surface


def _fill_partition_stats(surface: Surface) -> None:
    if surface.strip.kind is not StripKind.BRACKET:
        return
    vals = [q.implied for q in surface.quotes]
    if any(v is None for v in vals):
        return
    total = float(sum(v for v in vals if v is not None))
    surface.implied_sum = total
    if total > 0:
        surface.normalised_pdf = [(v or 0.0) / total for v in vals]


# --------------------------------------------------------------------------- #
# coherence
# --------------------------------------------------------------------------- #
def find_incoherences(surface: Surface) -> list[Incoherence]:
    strip = surface.strip
    if strip.kind is StripKind.BRACKET:
        return _bracket_incoherences(surface)
    if strip.kind is StripKind.ABOVE:
        return _ladder_incoherences(surface)
    return _touch_incoherences(surface)


def _bracket_incoherences(surface: Surface) -> list[Incoherence]:
    """A partition must price to exactly 1. Anything else is a free lunch."""
    out: list[Incoherence] = []
    if not surface.strip.is_partition():
        out.append(
            Incoherence(
                kind="not_a_partition",
                detail="bracket legs do not tile the price line; treating strip as untrusted",
                size=0.0,
            )
        )
        return out

    asks = [q.yes_ask for q in surface.quotes]
    bids = [q.yes_bid for q in surface.quotes]

    if all(a is not None for a in asks):
        cost = float(sum(a for a in asks if a is not None))
        if cost < 1.0:
            out.append(
                Incoherence(
                    kind="underpriced_partition",
                    detail=f"buying every YES costs {cost:.4f} and always pays 1.00",
                    size=1.0 - cost,
                    legs=list(range(len(asks))),
                )
            )
    if all(b is not None for b in bids):
        credit = float(sum(b for b in bids if b is not None))
        if credit > 1.0:
            out.append(
                Incoherence(
                    kind="overpriced_partition",
                    detail=f"selling every YES pays {credit:.4f} and costs at most 1.00",
                    size=credit - 1.0,
                    legs=list(range(len(bids))),
                )
            )

    if surface.implied_sum is not None and abs(surface.implied_sum - 1.0) > 0.02:
        out.append(
            Incoherence(
                kind="mid_sum_off",
                detail=f"mid prices sum to {surface.implied_sum:.4f}, not 1.00",
                size=abs(surface.implied_sum - 1.0),
            )
        )
    return out


def _ladder_incoherences(surface: Surface) -> list[Incoherence]:
    """P(S > K) must be non-increasing in K. A crossing is a locked-in spread."""
    out: list[Incoherence] = []
    pts = [
        (q.leg.strike, q)
        for q in surface.quotes
        if q.leg.kind is LegKind.ABOVE and math.isfinite(q.leg.strike)
    ]
    pts.sort(key=lambda t: t[0])
    for (k_lo, q_lo), (k_hi, q_hi) in zip(pts, pts[1:]):
        # Buying "above k_hi" and selling "above k_lo" can never make money, so
        # ask(k_hi) < bid(k_lo) is required. The violation is bid(k_hi) > ask(k_lo).
        if q_hi.yes_bid is not None and q_lo.yes_ask is not None:
            gap = q_hi.yes_bid - q_lo.yes_ask
            if gap > 0:
                out.append(
                    Incoherence(
                        kind="ladder_crossed",
                        detail=(
                            f"above {k_hi:,.0f} bids {q_hi.yes_bid:.3f} while "
                            f"above {k_lo:,.0f} offers {q_lo.yes_ask:.3f}"
                        ),
                        size=gap,
                        legs=[q_lo.index, q_hi.index],
                    )
                )
    return out


def _touch_incoherences(surface: Surface) -> list[Incoherence]:
    """P(touch B) must be non-increasing as B moves away from spot."""
    out: list[Incoherence] = []
    spot = surface.spot
    for kind, key in ((LegKind.TOUCH_UP, 1.0), (LegKind.TOUCH_DOWN, -1.0)):
        pts = [
            (q.leg.barrier, q)
            for q in surface.quotes
            if q.leg.kind is kind and math.isfinite(q.leg.barrier)
        ]
        # order by distance from spot, nearest first
        pts.sort(key=lambda t: abs(t[0] - spot))
        for (b_near, q_near), (b_far, q_far) in zip(pts, pts[1:]):
            if q_far.yes_bid is not None and q_near.yes_ask is not None:
                gap = q_far.yes_bid - q_near.yes_ask
                if gap > 0:
                    out.append(
                        Incoherence(
                            kind="touch_crossed",
                            detail=(
                                f"touch {b_far:,.0f} bids {q_far.yes_bid:.3f} while nearer "
                                f"touch {b_near:,.0f} offers {q_near.yes_ask:.3f}"
                            ),
                            size=gap,
                            legs=[q_near.index, q_far.index],
                        )
                    )
    return out


# --------------------------------------------------------------------------- #
def isotonic_decreasing(y: Sequence[float]) -> list[float]:
    """Pool-adjacent-violators fit of a non-increasing sequence.

    Used to smooth a noisy implied ladder into a valid survival function before
    it is shown to a human; the trading logic works off raw quotes, never this.
    """
    vals = [float(v) for v in y]
    n = len(vals)
    if n <= 1:
        return vals
    level = list(vals)
    weight = [1.0] * n
    idx = list(range(n))
    i = 0
    while i < len(level) - 1:
        if level[i] < level[i + 1] - 1e-15:
            total_w = weight[i] + weight[i + 1]
            merged = (level[i] * weight[i] + level[i + 1] * weight[i + 1]) / total_w
            level[i] = merged
            weight[i] = total_w
            idx[i] = idx[i]
            del level[i + 1]
            del weight[i + 1]
            del idx[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1
    out: list[float] = []
    for lvl, w in zip(level, weight):
        out.extend([lvl] * int(round(w)))
    return out[:n]


def implied_pdf_from_ladder(
    strikes: Sequence[float], survival: Sequence[float]
) -> list[tuple[float, float, float]]:
    """Turn a monotone survival ladder into bucket probabilities.

    Returns ``(lo, hi, prob)`` triples covering (-inf, k0], (k0, k1], ..., (kn, inf).
    """
    order = np.argsort(np.asarray(strikes, dtype=float))
    ks = [float(strikes[i]) for i in order]
    sv = isotonic_decreasing([float(survival[i]) for i in order])
    out: list[tuple[float, float, float]] = []
    out.append((-INF, ks[0], max(0.0, 1.0 - sv[0])))
    for (k0, s0), (k1, s1) in zip(zip(ks, sv), zip(ks[1:], sv[1:])):
        out.append((k0, k1, max(0.0, s0 - s1)))
    out.append((ks[-1], INF, max(0.0, sv[-1])))
    return out
