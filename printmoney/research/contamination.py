"""Does the language model already know what happened?

Every LLM-agent trading framework has the same unexamined assumption underneath
it: that when the model is asked what to do on 3 March 2023, it is *reasoning*
rather than *remembering*.  If the model was trained on text written after that
date - and it was, because training corpora contain the retrospectives as well
as the news - then a backtest over that period is not a test.  It is an open-book
exam being marked as though it were closed-book.

This is measurable, and the measurement is simple enough that the absence of it
from most projects is the finding.  The standard probe in the literature is to
ask the model about a historical outcome with no context supplied and see how
often it is right; a model with no memory of the period scores a coin flip.

The harness here does that, split by date so the two halves can be compared:

    before the model's cutoff   any skill shown here may be recall
    after the model's cutoff    the only half that can be a forecast

A model that scores well before its cutoff and at chance after it has told you
exactly what its "predictions" were made of.  A model at chance in both halves
has at least not been caught, which is the most a test like this can establish.

The questions are deliberately the easiest possible form - direction over the
following month, two choices, no magnitude - because the point is not to be hard.
A model that cannot beat a coin on the easy version is not going to do better on
a harder one, and one that *can* beat it has demonstrated memory rather than
insight.

Nothing here says LLM agents are useless. It says that any backtest of one over
its own training period measures memory, and that the number such a backtest
produces cannot be spent.
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
from .data import Series

QUIZ = DATA_DIR / "contamination.json"

#: How far forward the question looks. One trading month, matching the horizon
#: the scorecard already grades its own risk calls on.
HORIZON = 21

#: Trading days of run-up quoted in the question, so a model with genuine
#: context - rather than memory - has something to reason from.
CONTEXT_DAYS = 5

#: A coin. Everything here is scored against it.
CHANCE = 0.5


@dataclass
class Question:
    """One (market, date) probe. ``answer`` is filled in by whoever is sitting
    the test; ``truth`` is only attached at scoring time."""

    qid: int
    symbol: str
    day: str
    run_up: float            # the previous week's move, given as context
    answer: str = ""         # "up" | "down"
    truth: str = ""
    forward: float = 0.0

    @property
    def correct(self) -> bool | None:
        if not self.answer or not self.truth:
            return None
        return self.answer.strip().lower() == self.truth

    def prompt(self) -> str:
        return (f"{self.qid:>3}. {self.symbol} on {self.day} "
                f"(previous week {self.run_up:+.1%}): over the NEXT "
                f"{HORIZON} trading days, up or down?")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["correct"] = self.correct
        return d


@dataclass
class Report:
    cutoff: str
    questions: list[Question] = field(default_factory=list)
    model: str = ""

    def answered(self) -> list[Question]:
        return [q for q in self.questions if q.correct is not None]

    def split(self) -> tuple[list[Question], list[Question]]:
        """Before the cutoff, and after it."""
        before = [q for q in self.answered() if q.day < self.cutoff]
        after = [q for q in self.answered() if q.day >= self.cutoff]
        return before, after

    @staticmethod
    def rate(rows: Sequence[Question]) -> float:
        return sum(1 for q in rows if q.correct) / len(rows) if rows else 0.0

    @staticmethod
    def lower_bound(rows: Sequence[Question]) -> float:
        """Wilson, the same one the scorecard uses, for the same reason."""
        n = len(rows)
        if not n:
            return 0.0
        z, p = 2.0, Report.rate(rows)
        denom = 1.0 + z * z / n
        centre = (p + z * z / (2 * n)) / denom
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
        return centre - half

    @property
    def contaminated(self) -> bool:
        """Beating a coin on the half the model may have memorised.

        Deliberately not symmetric with a claim of skill. Scoring above chance
        before the cutoff proves memory or luck and nothing else; scoring above
        chance *after* it would be the interesting result, and is not what this
        flag is for.
        """
        before, _after = self.split()
        return len(before) >= 20 and self.lower_bound(before) > CHANCE

    def to_dict(self) -> dict[str, Any]:
        before, after = self.split()
        return {
            "model": self.model,
            "cutoff": self.cutoff,
            "asked": len(self.questions),
            "answered": len(self.answered()),
            "before_cutoff": {"n": len(before), "rate": round(self.rate(before), 4),
                              "lower_bound": round(self.lower_bound(before), 4)},
            "after_cutoff": {"n": len(after), "rate": round(self.rate(after), 4),
                             "lower_bound": round(self.lower_bound(after), 4)},
            "contaminated": self.contaminated,
            "questions": [q.to_dict() for q in self.questions],
        }


# --------------------------------------------------------------------------- #
def build(series: Iterable[Series], *, n: int = 40, seed: int = 20260826,
          horizon: int = HORIZON) -> list[Question]:
    """Draw a quiz: random markets, random dates, spread across the history.

    Balanced by construction - half the drawn dates are followed by a rise and
    half by a fall - so a subject that simply answers "up" every time, which is
    the winning strategy on real markets, scores fifty percent rather than
    seventy. Without this the test would reward a bias instead of detecting a
    memory.
    """
    import random

    rng = random.Random(seed)
    pool_up: list[Question] = []
    pool_down: list[Question] = []

    for s in series:
        bars = s.bars
        if len(bars) < CONTEXT_DAYS + horizon + 40:
            continue
        for i in range(CONTEXT_DAYS + 10, len(bars) - horizon, 7):
            prev = bars[i - CONTEXT_DAYS].close
            here = bars[i].close
            fwd = bars[i + horizon].close / here - 1.0
            if prev <= 0 or here <= 0 or abs(fwd) < 0.005:
                continue           # a flat month is not a question worth asking
            q = Question(qid=0, symbol=s.symbol,
                         day=bars[i].date.strftime("%Y-%m-%d"),
                         run_up=here / prev - 1.0,
                         truth="up" if fwd > 0 else "down",
                         forward=round(fwd, 4))
            (pool_up if fwd > 0 else pool_down).append(q)

    rng.shuffle(pool_up)
    rng.shuffle(pool_down)
    # Take the same number from each side, capped by the smaller pool. Slicing
    # both at n//2 quietly returned an unbalanced quiz whenever one side ran
    # short - and an unbalanced quiz hands anyone who always answers "up" the
    # market's real base rate of 58.6%, which this harness would then report as
    # memory. Balance is the single property the measurement rests on.
    half = min(n // 2, len(pool_up), len(pool_down))
    picked = pool_up[:half] + pool_down[:half]
    rng.shuffle(picked)
    for i, q in enumerate(picked, start=1):
        q.qid = i
    return picked


def blank(questions: Sequence[Question]) -> list[dict[str, Any]]:
    """The quiz as handed to the subject: no truth, no forward return."""
    return [{"qid": q.qid, "symbol": q.symbol, "day": q.day,
             "run_up": round(q.run_up, 4)} for q in questions]


def sheet(questions: Sequence[Question]) -> str:
    """The printable question paper."""
    head = (f"Answer 'up' or 'down' for each: over the {HORIZON} trading days "
            f"AFTER the date shown, did it rise or fall?\n"
            f"No tools, no lookups - memory only. Half of these rose and half "
            f"fell, so guessing one way scores 50%.\n")
    return head + "\n".join(q.prompt() for q in questions)


def grade(questions: Sequence[Question], answers: dict[int, str], *,
          cutoff: str, model: str = "") -> Report:
    """Attach the answers and score the two halves."""
    for q in questions:
        if q.qid in answers:
            q.answer = answers[q.qid]
    return Report(cutoff=cutoff, questions=list(questions), model=model)


def save(report: Report, path: Path | None = None) -> Path:
    target = path or QUIZ
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    payload["measured_at"] = datetime.now(timezone.utc).isoformat()
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8", newline="\n")
    return target


def load(path: Path | None = None) -> Report | None:
    target = path or QUIZ
    if not target.exists():
        return None
    blob = json.loads(target.read_text(encoding="utf-8"))
    qs = [Question(qid=d["qid"], symbol=d["symbol"], day=d["day"],
                   run_up=d["run_up"], answer=d.get("answer", ""),
                   truth=d.get("truth", ""), forward=d.get("forward", 0.0))
          for d in blob.get("questions", [])]
    return Report(cutoff=blob["cutoff"], questions=qs, model=blob.get("model", ""))


# --------------------------------------------------------------------------- #
def base_rate(series: Iterable[Series], *, horizon: int = HORIZON) -> float:
    """How often the next month was simply up, across the whole sample.

    The number a lazy subject scores by always answering "up" on an unbalanced
    quiz - which is why :func:`build` balances it. Printed alongside the result
    so the balancing can be checked rather than trusted.
    """
    ups, total = 0, 0
    for s in series:
        closes = s.closes
        for i in range(0, len(closes) - horizon, 7):
            if closes[i] <= 0:
                continue
            total += 1
            ups += closes[i + horizon] > closes[i]
    return ups / total if total else 0.0


def summarise(report: Report) -> str:
    before, after = report.split()
    lines = [
        f"model            {report.model or 'unnamed'}",
        f"stated cutoff    {report.cutoff}",
        f"answered         {len(report.answered())} of {len(report.questions)}",
        "",
        f"before cutoff    {len(before):>3} questions, "
        f"{Report.rate(before):.1%} correct "
        f"(Wilson floor {Report.lower_bound(before):.1%})",
        f"after cutoff     {len(after):>3} questions, "
        f"{Report.rate(after):.1%} correct "
        f"(Wilson floor {Report.lower_bound(after):.1%})",
        "",
        f"verdict          {'CONTAMINATED' if report.contaminated else 'no memory detected'}",
    ]
    return "\n".join(lines)


def spread(questions: Sequence[Question]) -> dict[str, Any]:
    """Sanity: is the quiz actually balanced, and how wide are the outcomes?"""
    fwd = [q.forward for q in questions]
    return {
        "n": len(questions),
        "share_up": round(sum(1 for q in questions if q.truth == "up") / len(questions), 3)
        if questions else 0.0,
        "median_abs_move": round(st.median([abs(x) for x in fwd]), 4) if fwd else 0.0,
        "first": min((q.day for q in questions), default=""),
        "last": max((q.day for q in questions), default=""),
    }
