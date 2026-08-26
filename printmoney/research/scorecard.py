"""Keep score of the brief's own calls, including the ones it got wrong.

Anyone can write a morning note.  Almost nobody keeps an honest record of
whether the note was right, and that gap is the only thing here worth trusting -
a claim that is never checked is indistinguishable from a claim that is never
true.

The brief makes exactly one kind of forward-looking statement: that a market is
running hot, or is quiet, by its own two-year standard.  That is falsifiable, so
it gets scored.  Twenty-one trading days after a flag is raised, the market's
*realised* volatility over that period is compared against the same history the
flag was drawn from, and the call is a hit if it landed on the side it predicted.

A coin gets 50%.  Anything the brief cannot beat 50% at should be deleted from
the brief rather than explained, and the number printed here is what that
decision would be made on.

Two sources of scored calls, kept apart because they deserve different levels of
trust:

``backtest``
    the same rule replayed over history, strictly point-in-time - the percentile
    at each date is computed from data before that date only, and the forward
    window is data after it.  Available immediately, which matters because a
    live-only scorecard says nothing for its first month.

``live``
    flags this project actually published, appended one line per market per day
    and resolved once they mature.  Slower, smaller, and the one that counts,
    because a backtest cannot accidentally have been fitted to the future in the
    way a live record cannot be.
"""
from __future__ import annotations

import json
import math
import statistics as st
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..util import DATA_DIR
from .brief import RISK_CALM, RISK_ELEVATED, RISK_EXTREME
from .data import TRADING_DAYS_PER_YEAR, Series

CLAIMS = DATA_DIR / "claims.jsonl"

#: The published track record. Recomputing the backtest needs ten years of bars
#: for the whole universe, so the summary is committed and the brief quotes it,
#: the same arrangement as the event impact table. Regenerate with `pm score
#: --save`; the diff is the audit trail.
SUMMARY = DATA_DIR / "scorecard.json"

#: How long a volatility call is given to come true. One trading month: long
#: enough that a single noisy session cannot decide it, short enough that the
#: reader finds out while they still remember the call.
LOOKAHEAD = 21

#: The trailing window the flag itself is computed over, matching brief.py.
WINDOW = 21

#: The brief calls a market hot or quiet; both are bets that the next month
#: lands on one side of that market's own median. This is that median.
MEDIAN = 0.5


@dataclass
class Resolved:
    day: str
    symbol: str
    call: str                 # "elevated" | "extreme" | "calm"
    called_percentile: float  # where vol sat when the call was made
    realised_percentile: float
    realised_vol: float

    @property
    def predicted_high(self) -> bool:
        return self.call in ("elevated", "extreme")

    @property
    def hit(self) -> bool:
        return (self.realised_percentile > MEDIAN) == self.predicted_high

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["hit"] = self.hit
        return d


@dataclass
class Score:
    """Hit rate, split the ways that could hide a bad result."""

    label: str
    resolved: list[Resolved] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.resolved)

    @property
    def hits(self) -> int:
        return sum(1 for r in self.resolved if r.hit)

    @property
    def rate(self) -> float:
        return self.hits / len(self.resolved) if self.resolved else 0.0

    @property
    def stderr(self) -> float:
        """Plain binomial standard error, for display only.

        Deliberately not what :attr:`beats_coin` is built on. At a perfect record
        this collapses to zero, which would let four calls out of four announce
        themselves as skill - exactly the false confidence a scorecard exists to
        prevent, and exactly the bug this docstring is standing on top of.
        """
        n = len(self.resolved)
        if n < 2:
            return 0.0
        return math.sqrt(self.rate * (1 - self.rate) / n)

    @property
    def lower_bound(self) -> float:
        """Wilson lower bound at roughly 95%.

        Wilson is used rather than the textbook normal interval because it stays
        sane at the edges: at four-for-four it returns 0.50, which says "this
        could still be a coin" - the truth - where the naive interval says the
        uncertainty is zero.
        """
        n = len(self.resolved)
        if n == 0:
            return 0.0
        z = 2.0
        p = self.rate
        denom = 1.0 + z * z / n
        centre = (p + z * z / (2 * n)) / denom
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
        return centre - half

    @property
    def beats_coin(self) -> bool:
        """Clear of 50% even at the pessimistic end of the interval."""
        return bool(self.resolved) and self.lower_bound > MEDIAN

    def by_call(self) -> dict[str, "Score"]:
        out: dict[str, Score] = {}
        for r in self.resolved:
            out.setdefault(r.call, Score(label=r.call)).resolved.append(r)
        return dict(sorted(out.items()))

    def by_symbol(self) -> dict[str, "Score"]:
        out: dict[str, Score] = {}
        for r in self.resolved:
            out.setdefault(r.symbol, Score(label=r.symbol)).resolved.append(r)
        return out

    def worst(self, n: int = 5) -> list[tuple[str, "Score"]]:
        """The markets this rule works least well on. Printed, not buried."""
        rows = [(s, sc) for s, sc in self.by_symbol().items() if len(sc) >= 5]
        return sorted(rows, key=lambda kv: kv[1].rate)[:n]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n": len(self.resolved),
            "hits": self.hits,
            "rate": round(self.rate, 4),
            "stderr": round(self.stderr, 4),
            "lower_bound": round(self.lower_bound, 4),
            "beats_coin": self.beats_coin,
            "by_call": {k: {"n": len(v), "rate": round(v.rate, 4)}
                        for k, v in self.by_call().items()},
        }


# --------------------------------------------------------------------------- #
def _stdev(xs: Sequence[float]) -> float:
    return st.stdev(xs) if len(xs) > 2 else 0.0


def _percentile(value: float, history: Sequence[float]) -> float:
    if not history or value <= 0:
        return MEDIAN
    return sum(1 for h in history if h <= value) / len(history)


def _call_for(pctile: float) -> str | None:
    if pctile >= RISK_EXTREME:
        return "extreme"
    if pctile >= RISK_ELEVATED:
        return "elevated"
    if pctile <= RISK_CALM:
        return "calm"
    return None


def backtest(series: Iterable[Series], *, lookahead: int = LOOKAHEAD,
             step: int = 5) -> Score:
    """Replay the flag over history, point-in-time, and score every call.

    The percentile at each date is built only from windows that ended before it,
    and the outcome only from days after it. Getting this wrong is the classic
    way a rule like this appears to work: the same data that raised the flag also
    settles it, and every call comes back a winner.
    """
    score = Score(label="backtest")
    for s in series:
        daily = s.daily_returns()
        if len(daily) < 3 * WINDOW + lookahead:
            continue
        # Rolling windows of trailing volatility, indexed by their end position.
        ends = list(range(WINDOW, len(daily) - lookahead, step))
        for k in ends:
            history = [_stdev(daily[i - WINDOW:i])
                       for i in range(WINDOW, k, step)]
            if len(history) < 20:
                continue
            recent = _stdev(daily[k - WINDOW:k])
            pctile = _percentile(recent, history)
            call = _call_for(pctile)
            if call is None:
                continue
            forward = _stdev(daily[k:k + lookahead])
            score.resolved.append(Resolved(
                day=s.bars[k].date.strftime("%Y-%m-%d"),
                symbol=s.symbol,
                call=call,
                called_percentile=round(pctile, 4),
                realised_percentile=round(_percentile(forward, history), 4),
                realised_vol=round(forward * math.sqrt(TRADING_DAYS_PER_YEAR), 4),
            ))
    return score


# --------------------------------------------------------------------------- #
def record(brief_lines: Iterable[Any], day: str, path: Path | None = None) -> int:
    """Append today's flags so they can be graded a month from now.

    Idempotent by day: a workflow that runs twice, or is re-run by hand, must not
    turn one call into two and quietly double its own sample size.

    ``path`` defaults to :data:`CLAIMS` but is resolved on each call rather than
    bound at import, so redirecting the constant actually redirects the writes -
    a default captured at definition time silently ignores every override.
    """
    path = path or CLAIMS
    existing = _load(path)
    if any(c.get("day") == day for c in existing):
        return 0
    rows = [
        {"day": day, "symbol": l.symbol, "call": l.risk,
         "called_percentile": round(l.vol_percentile, 4),
         "recorded_at": datetime.now(timezone.utc).isoformat()}
        for l in brief_lines
        if _call_for(l.vol_percentile) is not None
    ]
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def _load(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or CLAIMS
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a half-written line from a killed run, not a crash
    return out


def resolve(series: dict[str, Series], *, lookahead: int = LOOKAHEAD,
            path: Path | None = None) -> Score:
    """Grade every recorded flag that has had its month, using current data."""
    score = Score(label="live")
    claims = _load(path)
    if not claims:
        return score

    for c in claims:
        s = series.get(c["symbol"])
        if s is None:
            continue
        days = [b.date.strftime("%Y-%m-%d") for b in s.bars]
        try:
            k = days.index(c["day"])
        except ValueError:
            continue                      # flagged on a day this market did not trade
        daily = s.daily_returns()
        # daily[i] is the return into bars[i+1], so the flag date sits at k-1.
        k = min(max(k - 1, WINDOW), len(daily))
        if k + lookahead > len(daily):
            continue                      # not matured yet
        history = [_stdev(daily[i - WINDOW:i]) for i in range(WINDOW, k, 5)]
        if len(history) < 20:
            continue
        forward = _stdev(daily[k:k + lookahead])
        score.resolved.append(Resolved(
            day=c["day"], symbol=c["symbol"], call=c["call"],
            called_percentile=float(c.get("called_percentile", 0.0)),
            realised_percentile=round(_percentile(forward, history), 4),
            realised_vol=round(forward * math.sqrt(TRADING_DAYS_PER_YEAR), 4),
        ))
    return score


def pending(path: Path | None = None) -> int:
    """How many flags are still waiting out their month."""
    return len(_load(path))


# --------------------------------------------------------------------------- #
def save_summary(backtested: Score, live: Score | None = None,
                 *, span: str = "", path: Path | None = None) -> Path:
    from ..util import write_json

    path = path or SUMMARY
    write_json(path, {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "span": span,
        "backtest": backtested.to_dict(),
        "live": live.to_dict() if live is not None else None,
        "worst_markets": [{"symbol": s, "n": len(v), "rate": round(v.rate, 4)}
                          for s, v in backtested.worst(5)],
    })
    return path


def load_summary(path: Path | None = None) -> dict[str, Any] | None:
    from ..util import read_json

    return read_json(path or SUMMARY, default=None)


def headline(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    """The one number the brief quotes, preferring live calls over the backtest.

    Live is the honest headline the moment there is enough of it: a backtest can
    only ever show that the rule would have worked, while the live log shows what
    this project actually published and how it turned out.
    """
    if not summary:
        return None
    live = summary.get("live")
    if live and live.get("n", 0) >= 30:
        return {**live, "basis": "live"}
    back = summary.get("backtest")
    if back and back.get("n", 0) >= 30:
        return {**back, "basis": "backtest"}
    return None
