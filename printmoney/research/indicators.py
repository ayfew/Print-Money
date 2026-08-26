"""Run a whole indicator library through the fee wall, with the correction that
makes the answer mean anything.

TA-Lib ships 161 functions.  The usual way to use a library like that is to try
indicators until one looks good on your data, which is a procedure guaranteed to
succeed whether or not anything is there: test enough rules at the five percent
level and one in twenty passes by construction.  ``study.py`` already tested six
hand-picked rules and found them indistinguishable from random; this module does
the same job at library scale, and the only reason it is worth doing is the
three pieces of discipline around it.

*Mechanical rule construction.*  Every indicator is turned into a signal by the
same two rules, chosen once, applied to all of them.  Overlap studies that live
in the price's own units (moving averages, bands) become "hold while price is
above it".  Everything else becomes "hold while the indicator is above its own
trailing median".  Each rule is tested together with its exact inverse, so a
rule that loses badly cannot be quietly reported as its own mirror image.  No
thresholds are tuned, because a tuned threshold is a seventh degree of freedom
nobody counts.

*Point-in-time everything.*  The trailing median at each date is computed from
data strictly before it.  Getting this wrong is the classic way a sweep like
this comes back full of winners.

*A correction for having looked 300 times.*  Benjamini-Hochberg across the whole
family, plus an empirical null: random signals with the same turnover, so the
comparison already carries the cost drag rather than pretending it away.

What this is not: a search for something to trade.  If a rule survives here it
has survived one in-sample sweep, which is the weakest possible evidence and
still a long way from an edge.  What the sweep is actually for is putting a
number on how thoroughly the fee wall eats the entire genre.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from .data import TRADING_DAYS_PER_YEAR, Series

log = logging.getLogger("printmoney.indicators")

#: Round-trip cost. The same 10bp the rest of the project uses, which is already
#: generous for a retail account paying spread and FX on top.
COST = 0.0010

#: Warm-up before any rule may trade, so an indicator with a long lookback is
#: not judged on the period where it is still filling up.
WARMUP = 250

#: How many random signals to draw for the empirical null.
NULL_DRAWS = 400

#: The false-discovery rate the family is held to.
FDR = 0.05

#: Round trips a year below which a "rule" is not a rule.
#:
#: The sweep's top result was AVGDEV at +12.7% a year with a turnover of 0.1 -
#: roughly one trade in a decade. Its t-statistic is enormous because it is
#: computed on daily returns and a constant position makes those returns
#: massively autocorrelated; the actual sample size is the number of decisions,
#: which is one. A rule that decides once is buy-and-hold with an indicator
#: drawn on top of it, and reporting it as a discovery would be the single most
#: misleading thing this module could do.
MIN_TURNOVER = 2.0

#: Indicator groups whose output is in the price's own units, so the natural
#: rule is "price above it" rather than "above its own median".
PRICE_UNIT_GROUPS = {"Overlap Studies", "Price Transform"}

#: Skipped outright. Pattern recognition returns a signed integer flag rather
#: than a level, and Math Transform functions are trigonometry on price with no
#: trading interpretation at all - including them would pad the count with
#: nonsense and make the correction look more impressive than it is.
SKIP_GROUPS = {"Pattern Recognition", "Math Transform", "Math Operators"}


@dataclass
class Result:
    """One rule, measured over the whole universe."""

    name: str
    group: str
    inverted: bool
    gross: float          # annualised, before costs
    net: float            # annualised, after costs
    turnover: float       # round trips a year
    tstat: float
    n_days: int
    p_value: float = 1.0
    significant: bool = False

    @property
    def label(self) -> str:
        return f"{self.name}{' (inverse)' if self.inverted else ''}"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "group": self.group, "inverted": self.inverted,
                "gross": round(self.gross, 5), "net": round(self.net, 5),
                "turnover": round(self.turnover, 1), "tstat": round(self.tstat, 2),
                "p_value": round(self.p_value, 5), "n_days": self.n_days,
                "significant": self.significant}


@dataclass
class Sweep:
    results: list[Result] = field(default_factory=list)
    null_low: float = 0.0
    null_high: float = 0.0
    null_median: float = 0.0
    buy_and_hold: float = 0.0
    markets: int = 0
    span: str = ""
    cost: float = COST

    def survivors(self) -> list[Result]:
        """Rules that clear all three bars, not any one of them.

        1. Benjamini-Hochberg, because the sweep looked a hundred and forty-six
           times and roughly seven of those would pass at five percent on noise.
        2. Above the empirical null - random signals with matched turnover,
           which come in at +5.4% a year because a long-or-flat rule in markets
           that rose captures beta whether or not it predicts anything.
        3. Above buy-and-hold. This is the bar that does the real work. A rule
           that trades once a decade and is otherwise long has not beaten
           anything; it *is* buy-and-hold, with a moving average drawn on top.
           Several of the best-looking results here are exactly that.
        4. Actually trades. See MIN_TURNOVER: a rule with one decision in ten
           years has a sample size of one, whatever its t-statistic says.
        """
        floor = max(self.null_high, self.buy_and_hold)
        return [r for r in self.results
                if r.significant and r.net > floor
                and r.turnover >= MIN_TURNOVER]

    def best(self, n: int = 8) -> list[Result]:
        return sorted(self.results, key=lambda r: -r.net)[:n]

    def worst(self, n: int = 3) -> list[Result]:
        return sorted(self.results, key=lambda r: r.net)[:n]

    def to_dict(self) -> dict[str, Any]:
        return {"span": self.span, "markets": self.markets, "cost": self.cost,
                "tested": len(self.results),
                "null_low": round(self.null_low, 5),
                "null_high": round(self.null_high, 5),
                "null_median": round(self.null_median, 5),
                "buy_and_hold": round(self.buy_and_hold, 5),
                "survivors": [r.to_dict() for r in self.survivors()],
                "best": [r.to_dict() for r in self.best(10)]}


# --------------------------------------------------------------------------- #
def available() -> bool:
    import importlib.util

    return importlib.util.find_spec("talib") is not None


def _catalogue() -> list[tuple[str, str]]:
    """(function name, group) for every indicator worth turning into a rule."""
    import talib

    out: list[tuple[str, str]] = []
    for group, names in talib.get_function_groups().items():
        if group in SKIP_GROUPS:
            continue
        for name in names:
            out.append((name, group))
    return sorted(out)


def _compute(name: str, inputs: dict[str, np.ndarray]) -> np.ndarray | None:
    """Call one TA-Lib function through the abstract API.

    The abstract interface reads each function's declared inputs and pulls them
    out of the dict itself, which is the difference between one code path and a
    hand-written table of 161 signatures - RSI wants close, ADX wants high/low/
    close, OBV wants close and volume, and none of that has to be written down
    here to stay correct.

    Functions returning several lines (MACD, BBANDS, STOCH) give back a list;
    the first is the one the indicator is named for.
    """
    from talib import abstract

    try:
        out = abstract.Function(name)(inputs)
    except Exception:                              # noqa: BLE001 - 161 shapes
        return None
    if isinstance(out, (list, tuple)):
        if not out:
            return None
        out = out[0]
    arr = np.asarray(out, dtype=float)
    close = inputs["close"]
    return arr if arr.shape == close.shape else None


def _trailing_median(x: np.ndarray) -> np.ndarray:
    """Expanding median, strictly from data before each point.

    Done with a sort per step rather than a clever structure: three hundred
    rules over twenty-four markets is a few seconds either way, and a subtle
    off-by-one in a streaming median would silently leak the future, which is
    the one bug this whole module exists to avoid.
    """
    out = np.full_like(x, np.nan)
    for i in range(WARMUP, len(x)):
        window = x[:i]
        window = window[np.isfinite(window)]
        if window.size >= 30:
            out[i] = np.median(window)
    return out


def _signal(name: str, group: str, series: Series) -> np.ndarray | None:
    """A 0/1 position for each day, decided on information available that day."""
    bars = series.bars
    if len(bars) < WARMUP + 60:
        return None
    # Indicators read the *traded* tape: open, high and low are never adjusted,
    # so pairing them with a dividend-adjusted close would make every range and
    # every crossover slightly fictional. Returns are scored on the adjusted
    # series separately, because that is the holder's return rather than the
    # shape of the chart.
    inputs = {
        "open": np.array([b.open for b in bars], dtype=float),
        "high": np.array([b.high for b in bars], dtype=float),
        "low": np.array([b.low for b in bars], dtype=float),
        "close": np.array([b.raw_close or b.close for b in bars], dtype=float),
        "volume": np.array([b.volume for b in bars], dtype=float),
    }
    c = inputs["close"]
    ind = _compute(name, inputs)
    if ind is None or not np.isfinite(ind[WARMUP:]).any():
        return None

    if group in PRICE_UNIT_GROUPS:
        raw = c > ind
    else:
        raw = ind > _trailing_median(ind)
    pos = np.where(np.isfinite(ind), raw.astype(float), 0.0)
    pos[:WARMUP] = 0.0
    return pos


def _score(pos: np.ndarray, rets: np.ndarray, cost: float) -> tuple[float, float, float, float, int]:
    """Annualised gross and net, turnover, and a t-statistic on daily net."""
    # ``rets[i]`` is the move from bar i into bar i+1, and ``pos[i]`` is decided
    # on bar i's close. So the position at i earns rets[i], and dropping the
    # last position (which has no next day) lines the two up exactly. Slicing
    # the returns as well would shift the whole series by a day and hand every
    # rule tomorrow's answer.
    held = pos[:-1]
    r = rets[:len(held)]
    trades = np.abs(np.diff(np.concatenate([[0.0], held])))
    daily_net = held * r - trades * cost
    n = int(len(r))
    if n < 250:
        return 0.0, 0.0, 0.0, 0.0, n

    gross = float(np.mean(held * r) * TRADING_DAYS_PER_YEAR)
    net = float(np.mean(daily_net) * TRADING_DAYS_PER_YEAR)
    turnover = float(np.sum(trades) / n * TRADING_DAYS_PER_YEAR)
    sd = float(np.std(daily_net, ddof=1))
    tstat = float(np.mean(daily_net) / sd * math.sqrt(n)) if sd > 0 else 0.0
    return gross, net, turnover, tstat, n


def _p_from_t(t: float, n: int) -> float:
    """Two-sided normal approximation. n is in the thousands here."""
    return math.erfc(abs(t) / math.sqrt(2.0))


def _benjamini_hochberg(results: Sequence[Result], q: float = FDR) -> None:
    """Mark the rules that survive the family-wise correction, in place.

    Bonferroni would be the blunter choice; BH is the standard one when the
    question is "how many of these are real" rather than "is this specific one
    real", which is exactly the question a sweep asks.
    """
    ordered = sorted(results, key=lambda r: r.p_value)
    m = len(ordered)
    cutoff = 0
    for i, r in enumerate(ordered, start=1):
        if r.p_value <= q * i / m:
            cutoff = i
    for i, r in enumerate(ordered, start=1):
        r.significant = i <= cutoff and r.net > 0


# --------------------------------------------------------------------------- #
def _empirical_null(series: Sequence[Series], cost: float, *,
                    draws: int = NULL_DRAWS, seed: int = 7) -> tuple[float, float, float]:
    """Random signals with realistic turnover, scored the same way.

    The point is that a rule which is long half the time in a market that rose
    will post a positive number while predicting nothing. This band says how
    positive that number gets by luck alone, at the same cost.
    """
    rng = np.random.default_rng(seed)
    rets = [np.diff(np.array(s.closes)) / np.array(s.closes)[:-1] for s in series]
    rets = [r[np.isfinite(r)] for r in rets if len(r) > WARMUP + 260]
    if not rets:
        return 0.0, 0.0, 0.0

    out = []
    for _ in range(draws):
        totals = []
        for r in rets:
            # A persistent random walk between long and flat, so turnover lands
            # in the same range as a real indicator rather than at every bar.
            flips = rng.random(len(r)) < (1.0 / rng.integers(4, 40))
            pos = np.cumsum(flips) % 2
            _g, net, _t, _ts, _n = _score(
                np.concatenate([pos, [pos[-1]]]).astype(float), r, cost)
            totals.append(net)
        out.append(float(np.mean(totals)))
    lo, mid, hi = np.percentile(out, [2.5, 50, 97.5])
    return float(lo), float(mid), float(hi)


def _buy_and_hold(series: Sequence[Series]) -> float:
    """Equal-weight, always long, annualised, on the same window.

    The bar every rule actually has to clear. Nothing here charges it a cost,
    because holding does not pay one - which is the point of the comparison and
    not a favour being done to it.
    """
    rates = []
    for s in series:
        c = np.array(s.closes, dtype=float)
        c = c[WARMUP:]
        if len(c) < 260 or c[0] <= 0:
            continue
        years = len(c) / TRADING_DAYS_PER_YEAR
        rates.append((c[-1] / c[0]) ** (1.0 / years) - 1.0)
    return float(np.mean(rates)) if rates else 0.0


def sweep(series: Iterable[Series], *, cost: float = COST,
          limit: int | None = None) -> Sweep:
    """Every indicator, both directions, across the whole universe."""
    if not available():
        raise RuntimeError(
            "TA-Lib is not installed. `pip install TA-Lib` - since 0.6.5 it "
            "ships binary wheels, so the old build-the-C-library dance is gone."
        )

    universe = [s for s in series if len(s.bars) > WARMUP + 260]
    if not universe:
        raise RuntimeError("no market has enough history for a sweep")

    catalogue = _catalogue()
    if limit:
        catalogue = catalogue[:limit]

    rets = {s.symbol: np.diff(np.array(s.closes)) / np.array(s.closes)[:-1]
            for s in universe}
    out = Sweep(markets=len(universe), cost=cost)
    days = sorted(b.date.strftime("%Y-%m-%d")
                  for s in universe for b in (s.bars[0], s.bars[-1]))
    out.span = f"{days[0]}..{days[-1]}"

    for name, group in catalogue:
        rows: list[tuple[float, float, float, float, int]] = []
        for s in universe:
            pos = _signal(name, group, s)
            if pos is None:
                continue
            rows.append(_score(pos, rets[s.symbol], cost))
        rows = [r for r in rows if r[4] >= 250]
        if len(rows) < max(3, len(universe) // 3):
            continue                       # too few markets to mean anything

        for inverted in (False, True):
            g, n_, t_, ts, nd = _aggregate(rows, inverted, cost)
            r = Result(name=name, group=group, inverted=inverted, gross=g,
                       net=n_, turnover=t_, tstat=ts, n_days=nd)
            r.p_value = _p_from_t(r.tstat, r.n_days)
            out.results.append(r)

    _benjamini_hochberg(out.results)
    out.null_low, out.null_median, out.null_high = _empirical_null(universe, cost)
    out.buy_and_hold = _buy_and_hold(universe)
    return out


def _aggregate(rows: Sequence[tuple[float, float, float, float, int]],
               inverted: bool, cost: float = COST
               ) -> tuple[float, float, float, float, int]:
    """Average one rule across markets. The inverse flips the position, which
    flips gross and the t-statistic but leaves turnover untouched - you pay the
    same tolls going the other way, which is the whole point of testing it.

    ``cost`` is threaded in rather than read from the module constant. It used
    to be the constant, which meant `--fee` moved the gross column and left the
    net column priced at ten basis points whatever was asked for - a sweep run
    at Thai retail cost would have reported a fee wall a third the real height.
    """
    sign = -1.0 if inverted else 1.0
    gross = float(np.mean([r[0] for r in rows])) * sign
    turnover = float(np.mean([r[2] for r in rows]))
    net = gross - turnover * cost
    tstat = float(np.mean([r[3] for r in rows])) * sign * math.sqrt(len(rows))
    return gross, net, turnover, tstat, int(np.sum([r[4] for r in rows]))
