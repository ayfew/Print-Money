"""Which official readings explain which markets, and which explain nothing.

Adding data is easy and almost always a mistake.  A brief that quotes seven new
numbers every morning is not better informed than one that quotes none; it is
just longer.  So every feed in :mod:`feeds` has to pass through here first, and
what comes out the other side is a small table of relationships that survived
measurement, plus an explicit list of the ones that did not.

Two kinds of relationship are measured, and keeping them apart is the whole
point of the module:

``explains``
    Same-day change against same-day change.  Gold fell and the ten-year real
    yield rose, and those two things have moved against each other consistently
    for years - so the brief may say the second is *why* the first happened.
    This is attribution.  It is backward-looking by construction and it is the
    only thing a correlation of contemporaneous moves can honestly support.

``predicts``
    Today's reading against the *next* month's realised volatility, scored the
    same way the brief's own risk flags are scored in :mod:`scorecard`.  This is
    forecasting, and it is held to the harder bar: beat a coin, with the
    pessimistic end of a Wilson interval, or be reported as useless.

The distinction matters because conflating them is the single most common way a
market note starts lying.  "Gold moves inversely to real yields" is measurable
and true.  "Real yields fell, so buy gold" smuggles a forecast in behind it, and
this project already put the forecastability of returns at r = +0.02.
"""
from __future__ import annotations

import logging
import math
import statistics as st
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..util import DATA_DIR, read_json, write_json
from .data import Series
from .feeds import Feed

log = logging.getLogger("printmoney.macro")

#: The measured table, committed like the rest of the evidence in ``data/``.
LINKS = DATA_DIR / "macro.json"

#: A t-statistic on the correlation. With five hundred-odd daily observations
#: this is a low bar in significance terms, which is why the size bar below sits
#: next to it - a relationship can be beyond doubt and still be too faint to be
#: worth a sentence in somebody's morning.
MIN_T = 3.0

#: And the size bar. Below this the feed explains under 2% of the variance, and
#: saying "gold fell because real yields rose" would be dressing up noise.
MIN_ABS_R = 0.15

#: How far ahead the predictive test looks, matching the scorecard.
LOOKAHEAD = 21

#: Pairs where the feed and the market are the *same underlying instrument*, so
#: a high correlation is arithmetic rather than information.
#:
#: The ten-year Treasury yield correlates with TLT at -0.91, and VIX with SPY at
#: -0.82. Both are the strongest numbers this module produces and both are
#: worthless as explanations: TLT holds long-dated Treasuries, so its price
#: *is* the yield expressed backwards, and VIX is computed from options on the
#: index SPY tracks. Telling a reader "bonds fell because yields rose" is not an
#: insight, it is the same fact said twice.
#:
#: They are still measured and still printed by ``pm macro`` - a sudden break in
#: one would mean a data problem worth knowing about - but the brief will not
#: offer them as reasons.
SAME_INSTRUMENT: set[tuple[str, str]] = {
    ("ust10y", "TLT"), ("ust2y", "TLT"), ("real10y", "TLT"), ("curve", "TLT"),
    ("vix", "SPY"), ("vvix", "SPY"), ("skew", "SPY"),
}

#: The volatility indices, and the US equity complex they are a measure *of*.
#:
#: Strictly only VIX-against-SPY is arithmetic. But VIX is computed from S&P 500
#: options, and QQQ, the sector funds and small caps are all slices of the same
#: tape - so "tech fell because VIX rose" has the causation backwards. VIX rose
#: *because* US equities fell; the two are one observation reported twice, and a
#: reader who acts on the second one has learned nothing.
#:
#: Everything outside this set stays eligible, and that is where the value is.
#: Bitcoin against VIX at -0.33 is worth a sentence precisely because so many
#: people hold it believing it is uncorrelated. Gold, oil, the dollar and
#: foreign equity all carry genuine cross-asset information against a US fear
#: gauge; a US sector fund does not.
VOL_FEEDS = {"vix", "vvix", "skew"}
US_EQUITY = {"SPY", "QQQ", "DIA", "IWM",
             "XLK", "XLF", "XLV", "XLU", "XLP", "XLE"}

#: How strong a link has to be before the brief will describe it in those terms.
#: Daily macro relationships are genuinely weak - gold against real yields comes
#: in near -0.18, which is real and reproducible and still explains about three
#: percent of a day's move. Saying "strong" about that would be the overclaim
#: this whole module exists to prevent.
STRENGTH = ((0.40, "strong"), (0.25, "moderate"), (MIN_ABS_R, "weak"))


@dataclass
class Link:
    """One measured relationship between a feed and a market."""

    feed: str
    symbol: str
    r: float
    n: int

    @property
    def tstat(self) -> float:
        if self.n < 5 or abs(self.r) >= 1.0:
            return 0.0
        return self.r * math.sqrt(self.n - 2) / math.sqrt(1.0 - self.r * self.r)

    @property
    def mechanical(self) -> bool:
        """Is this link a restatement rather than a reason?

        See SAME_INSTRUMENT and the VOL_FEEDS/US_EQUITY note above.
        """
        if (self.feed, self.symbol) in SAME_INSTRUMENT:
            return True
        return self.feed in VOL_FEEDS and self.symbol in US_EQUITY

    @property
    def real(self) -> bool:
        return (abs(self.tstat) >= MIN_T and abs(self.r) >= MIN_ABS_R
                and not self.mechanical)

    @property
    def direction(self) -> str:
        return "with" if self.r > 0 else "against"

    @property
    def strength(self) -> str:
        for floor, word in STRENGTH:
            if abs(self.r) >= floor:
                return word
        return "none"

    def to_dict(self) -> dict[str, Any]:
        return {"feed": self.feed, "symbol": self.symbol, "r": round(self.r, 4),
                "n": self.n, "tstat": round(self.tstat, 2), "real": self.real,
                "mechanical": self.mechanical, "direction": self.direction,
                "strength": self.strength}


@dataclass
class Table:
    """Everything measured, including what failed."""

    links: list[Link] = field(default_factory=list)
    span: str = ""

    def real(self) -> list[Link]:
        return sorted((l for l in self.links if l.real), key=lambda l: -abs(l.r))

    def dead(self) -> list[Link]:
        return sorted((l for l in self.links if not l.real), key=lambda l: -abs(l.r))

    def for_symbol(self, symbol: str) -> list[Link]:
        return [l for l in self.real() if l.symbol == symbol]

    def for_feed(self, feed: str) -> list[Link]:
        return [l for l in self.real() if l.feed == feed]

    def to_dict(self) -> dict[str, Any]:
        return {"span": self.span, "links": [l.to_dict() for l in self.links]}


# --------------------------------------------------------------------------- #
def _corr(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 5 or len(ys) != len(xs):
        return 0.0
    try:
        return st.correlation(xs, ys)
    except st.StatisticsError:
        return 0.0            # a series that never moved has no correlation


def _daily_changes(feed: Feed) -> dict[str, float]:
    """Day-over-day change, keyed by the later day.

    Levels are compared as changes rather than as levels on purpose: two series
    that both trend will correlate at 0.9 while telling you nothing at all about
    each other, and a brief built on that would be confidently wrong every day.
    """
    out: dict[str, float] = {}
    for prev, cur in zip(feed.lines, feed.lines[1:]):
        out[cur.day] = cur.value - prev.value
    return out


def _market_returns(series: Series) -> dict[str, float]:
    bars = series.bars
    return {
        bars[i].date.strftime("%Y-%m-%d"): bars[i].close / bars[i - 1].close - 1.0
        for i in range(1, len(bars))
    }


def explains(feed: Feed, series: Series) -> Link:
    """How consistently a feed's daily move lines up with a market's."""
    f, m = _daily_changes(feed), _market_returns(series)
    shared = sorted(set(f) & set(m))
    xs = [f[d] for d in shared]
    ys = [m[d] for d in shared]
    return Link(feed=feed.key, symbol=series.symbol, r=_corr(xs, ys), n=len(shared))


def measure(feeds: dict[str, Feed], series: dict[str, Series],
            *, pairs: Iterable[tuple[str, str]] | None = None) -> Table:
    """Every feed against every market, or a named subset."""
    table = Table()
    combos = pairs if pairs is not None else [
        (fk, sk) for fk in feeds for sk in series
    ]
    days: list[str] = []
    for fk, sk in combos:
        feed, s = feeds.get(fk), series.get(sk)
        if feed is None or s is None:
            continue
        link = explains(feed, s)
        if link.n >= 60:
            table.links.append(link)
            days += [feed.lines[0].day, feed.lines[-1].day]
    if days:
        table.span = f"{min(days)}..{max(days)} over {len(table.links)} pairs"
    return table


# --------------------------------------------------------------------------- #
def predicts_volatility(feed: Feed, series: Series, *,
                        lookahead: int = LOOKAHEAD) -> "Forecast":
    """Does a high reading on this feed precede a volatile month?

    Scored exactly like the brief's own flags: split the feed's history at its
    median, and ask whether the market's realised volatility over the following
    month landed on the side the reading implied.
    """
    levels = {l.day: l.value for l in feed.lines}
    bars = series.bars
    days = [b.date.strftime("%Y-%m-%d") for b in bars]
    daily = series.daily_returns()

    hits = 0
    total = 0
    # The median is computed from data strictly before each call, so a feed that
    # drifted over the sample cannot borrow the future to classify its own past.
    for k in range(120, len(daily) - lookahead, 5):
        history = [levels[d] for d in days[:k] if d in levels]
        if len(history) < 100 or days[k] not in levels:
            continue
        mid = st.median(history)
        high_now = levels[days[k]] > mid

        forward = st.stdev(daily[k:k + lookahead]) if lookahead > 2 else 0.0
        past = [st.stdev(daily[i - 21:i]) for i in range(21, k, 5)]
        if len(past) < 20 or forward <= 0:
            continue
        high_next = forward > st.median(past)

        total += 1
        hits += int(high_now == high_next)

    return Forecast(feed=feed.key, symbol=series.symbol, hits=hits, n=total)


@dataclass
class Forecast:
    feed: str
    symbol: str
    hits: int
    n: int

    @property
    def rate(self) -> float:
        return self.hits / self.n if self.n else 0.0

    @property
    def lower_bound(self) -> float:
        """Wilson, for the same reason the scorecard uses it."""
        if not self.n:
            return 0.0
        z, p, n = 2.0, self.rate, self.n
        denom = 1.0 + z * z / n
        centre = (p + z * z / (2 * n)) / denom
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
        return centre - half

    @property
    def real(self) -> bool:
        return self.n >= 30 and self.lower_bound > 0.5

    def to_dict(self) -> dict[str, Any]:
        return {"feed": self.feed, "symbol": self.symbol, "n": self.n,
                "rate": round(self.rate, 4),
                "lower_bound": round(self.lower_bound, 4), "real": self.real}


# --------------------------------------------------------------------------- #
def save(table: Table, forecasts: Sequence[Forecast] = (),
         *, path: Path | None = None) -> Path:
    write_json(path or LINKS, {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "span": table.span,
        "links": [l.to_dict() for l in table.links],
        "forecasts": [f.to_dict() for f in forecasts],
    })
    return path or LINKS


def load(path: Path | None = None) -> Table:
    """The committed table, or an empty one.

    An empty table means the brief simply says nothing about macro drivers,
    which is the correct degradation: an unmeasured explanation is exactly the
    kind of confident-sounding filler this module exists to keep out.
    """
    blob = read_json(path or LINKS, default=None)
    if not blob:
        return Table()
    return Table(
        span=blob.get("span", ""),
        links=[Link(feed=d["feed"], symbol=d["symbol"], r=d["r"], n=d["n"])
               for d in blob.get("links", [])],
    )
