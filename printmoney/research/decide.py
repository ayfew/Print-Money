"""Turn the numbers into a decision: what to look at today, and what to skip.

``brief.py`` answers "what happened".  That is data, and the honest complaint
about the first version of this project was that data is where it stopped - a
reader got twenty-four rows of percentages and had to do the deciding
themselves, every morning, which is the work they wanted handed off.

This module answers the different question: *what deserves attention today, and
what does not*.  It is built from four rules, in priority order, and every one of
them is a restatement of something already measured rather than a new opinion:

    1  a scheduled release from an official source lands today or tomorrow, and
       the historical record says that kind of day moves particular markets more
       than an ordinary day
    2  a market's volatility has climbed into the top of its own two-year range,
       which is the one forward-looking statement this project can support
    3  funding carry has crossed the threshold where it pays for its own costs
    4  none of the above, which is most days

What is *not* here is any claim about direction.  No market is called a buy, a
sell, a winner or an opportunity, because ten years of testing put the month-to-
month persistence of returns at r = +0.02 while the same test put volatility at
r = +0.76.  Naming what is dangerous is supportable.  Naming what will go up is
not, and dressing the second one up as the first is the single easiest way for a
tool like this to start lying to the person using it.

Every note carries a source id from :mod:`sources`, and nothing renders without
one.  Notes hold i18n keys and pre-formatted numbers rather than sentences, so
Thai and English are two assemblies of the same facts instead of a translation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from . import sources
from .brief import Brief, MarketLine
from .events import Event, Impact

#: Roughly how fast a person reads a short bulleted note, in words per minute.
#: Used only to print an honest estimate at the top - one of the success criteria
#: for this brief was that it can be read in about two minutes, and a number the
#: reader can check beats a promise they cannot.
READING_WPM = 180.0

#: How far ahead an event has to be before it stops being today's problem.
HORIZON_DAYS = 7

#: A market has to move this much more than usual on an event day before the
#: brief will tell anyone to watch it. Below this the effect is real but too
#: small to change what a person should do.
TOUCH_RATIO = 1.15

#: ...and this far below ordinary before the brief will say to ignore it. Naming
#: what an event does *not* move is half the value: it is what stops a rate
#: decision from being treated as a reason to stare at Bitcoin.
UNTOUCHED_RATIO = 1.02

#: How much a market has to have moved before the brief bothers looking for a
#: reason. Below this the move is inside the noise and any explanation offered
#: for it would be a story rather than an attribution.
WORTH_EXPLAINING = 0.010

#: And how unusual the driver's own move has to be before it counts as the
#: reason. A real yield that barely twitched did not move gold, however good the
#: long-run correlation between them is.
DRIVER_PERCENTILE = 0.70


@dataclass(frozen=True)
class Note:
    """One line of the decision brief, with the receipt attached.

    ``params`` holds numbers already formatted for display; market names are
    deliberately absent and are filled in by the renderer from ``symbols``, so a
    Thai reader gets Thai names without this module knowing any Thai.
    """

    kind: str                       # focus | watch | avoid | ignore | changed
    key: str                        # i18n key
    source: str                     # id in sources.REGISTRY
    params: dict[str, str] = field(default_factory=dict)
    symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        sources.get(self.source)    # refuse an uncitable claim at construction

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "key": self.key, "source": self.source,
                "params": self.params, "symbols": list(self.symbols)}


@dataclass
class Decision:
    """The whole verdict, ordered the way it should be read."""

    day: str
    focus: Note | None = None
    watch: list[Note] = field(default_factory=list)
    avoid: list[Note] = field(default_factory=list)
    ignore: list[Note] = field(default_factory=list)
    changed: list[Note] = field(default_factory=list)
    context: list[Note] = field(default_factory=list)
    why: list[Note] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    @property
    def notes(self) -> list[Note]:
        return ([self.focus] if self.focus else []) + \
            self.watch + self.avoid + self.ignore + self.changed + \
            self.context + self.why

    @property
    def source_ids(self) -> list[str]:
        return [n.source for n in self.notes]

    @property
    def quiet(self) -> bool:
        return not self.watch and not self.avoid

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "focus": self.focus.to_dict() if self.focus else None,
            "watch": [n.to_dict() for n in self.watch],
            "avoid": [n.to_dict() for n in self.avoid],
            "ignore": [n.to_dict() for n in self.ignore],
            "changed": [n.to_dict() for n in self.changed],
            "context": [n.to_dict() for n in self.context],
            "why": [n.to_dict() for n in self.why],
            "events": [e.to_dict() for e in self.events],
            "sources": [s.to_dict() for s in sources.cited(self.source_ids)],
            "quiet": self.quiet,
        }


# --------------------------------------------------------------------------- #
def _pct(x: float, digits: int = 1) -> str:
    return f"{100 * x:+.{digits}f}%"


def _ratio(x: float) -> str:
    return f"{x:.2f}x"


def _by_symbol(brief: Brief) -> dict[str, MarketLine]:
    return {l.symbol: l for l in brief.lines}


def _known(symbols: Iterable[str], have: dict[str, MarketLine]) -> tuple[str, ...]:
    """Only name markets that are actually in today's brief.

    The impact table is measured over a fixed universe; if the brief is running a
    smaller one, pointing at a market the reader cannot see in the table below is
    worse than saying nothing.
    """
    return tuple(s for s in symbols if s in have)


# --------------------------------------------------------------------------- #
def decide(
    brief: Brief,
    *,
    events: Sequence[Event] = (),
    impacts: dict[str, Impact] | None = None,
    previous: dict[str, Any] | None = None,
    today: date | None = None,
    feeds: dict[str, Any] | None = None,
    links: Any = None,
) -> Decision:
    """Assemble today's decision from the brief, the calendar and yesterday."""
    impacts = impacts or {}
    today = today or brief.generated_at.date()
    d = Decision(day=today.isoformat())

    if not brief.ok:
        d.focus = Note(
            kind="focus", key="focus_broken", source="yahoo",
            params={"loaded": str(brief.loaded), "requested": str(brief.requested)},
        )
        return d

    have = _by_symbol(brief)
    soon = [e for e in events if today <= e.date <= today + timedelta(days=HORIZON_DAYS)]
    d.events = soon

    _event_notes(d, soon, impacts, have, today)
    _risk_notes(d, brief)
    _carry_note(d, brief)
    _focus(d, brief, soon, impacts, have, today)
    _ignore_notes(d, brief, soon, impacts, have)
    if previous:
        _changes(d, brief, previous, soon, today)
    if feeds:
        _context_notes(d, feeds)
        if links is not None:
            _why_notes(d, brief, feeds, links)
    return d


# --------------------------------------------------------------------------- #
#: The readings worth printing every morning whether or not they moved, because
#: their *level* is the context everything else sits in. Ordered as a person
#: reads them: policy, the curve, the number gold trades against, then fear.
CONTEXT_ORDER = ("effr", "ust2y", "ust10y", "curve", "real10y", "vix")


def _context_notes(d: Decision, feeds: dict[str, Any]) -> None:
    """Where the macro backdrop stands today. Levels, not opinions."""
    for key in CONTEXT_ORDER:
        feed = feeds.get(key)
        if feed is None or not feed.lines:
            continue
        latest = feed.latest
        change = feed.change(1)
        pctile = feed.percentile()
        d.context.append(Note(
            kind="context",
            key="context_curve" if key == "curve" else "context_reading",
            source=feed.source,
            params={
                "label": feed.key,
                "value": f"{latest.value:,.2f}{'%' if feed.unit == '%' else ''}",
                "change": _bp(change, feed.unit) if change is not None else "",
                "pct": f"{pctile:.0%}" if pctile is not None else "",
                "state": ("inverted" if latest.value < 0 else "normal")
                         if key == "curve" else "",
            },
        ))


def _bp(change: float, unit: str) -> str:
    """Yields are quoted in basis points by everyone who trades them."""
    if unit == "%":
        return f"{100 * change:+.0f}bp"
    return f"{change:+.2f}"


def _why_notes(d: Decision, brief: Brief, feeds: dict[str, Any], links: Any) -> None:
    """For today's biggest moves, name a driver that also moved - or say nothing.

    Everything here is attribution, never prediction. The correlations behind it
    are contemporaneous by construction: they say two things moved together
    today, which is a fact about today. Reading a forecast into that is the
    mistake this project has spent its whole length refusing to make, so the
    strength of each link is printed next to it rather than left implied.
    """
    for line in brief.movers(4):
        if abs(line.day) < WORTH_EXPLAINING:
            continue
        best = None
        for link in links.for_symbol(line.symbol):
            feed = feeds.get(link.feed)
            if feed is None:
                continue
            change = feed.change(1)
            if change is None or not _driver_moved(feed, change):
                continue
            # Only offer the driver when it moved the way the relationship says
            # it should have. A gold selloff on a day real yields also fell is
            # not explained by real yields, and pretending otherwise is how a
            # brief becomes astrology.
            if (change > 0) != (line.day > 0) and link.r > 0:
                continue
            if (change > 0) == (line.day > 0) and link.r < 0:
                continue
            if best is None or abs(link.r) > abs(best[0].r):
                best = (link, feed, change)
        if best is None:
            continue
        link, feed, change = best
        d.why.append(Note(
            kind="why", key="why_move", source="macro",
            params={
                "move": _pct(line.day, 2),
                "driver": feed.key,
                "change": _bp(change, feed.unit),
                "strength": link.strength,
                "direction": link.direction,
                "r": f"{link.r:+.2f}",
                "n": str(link.n),
            },
            symbols=(line.symbol,),
        ))


def _driver_moved(feed: Any, change: float) -> bool:
    """Did this reading actually do something today, by its own standards?"""
    moves = [abs(b.value - a.value) for a, b in zip(feed.lines, feed.lines[1:])]
    if len(moves) < 30:
        return False
    moves = sorted(moves[-504:])
    cut = moves[int(DRIVER_PERCENTILE * (len(moves) - 1))]
    return abs(change) >= cut


# --------------------------------------------------------------------------- #
def _event_notes(d: Decision, soon: Sequence[Event], impacts: dict[str, Impact],
                 have: dict[str, MarketLine], today: date) -> None:
    for ev in soon:
        imp = impacts.get(ev.kind)
        if imp is None or not imp.real:
            # An event nobody can show matters is not worth a reader's attention,
            # however official the body publishing it.
            continue
        # Five is as many as anyone acts on. A list of eight is a list nobody
        # reads to the end of, which defeats the point of naming any of them.
        touched = _known(
            [s for s, r in imp.touches(5) if r >= TOUCH_RATIO], have)
        if not touched:
            continue
        d.watch.append(Note(
            kind="watch",
            key="watch_event",
            source="fed" if ev.kind == "fomc" else "bls",
            params={
                "event_kind": ev.kind,
                "days": str((ev.date - today).days),
                "note": ev.note,
                "ratio": _ratio(imp.ratio),
                "n": str(imp.events),
                "top": _ratio(max(r for _s, r in imp.touches(1))),
            },
            symbols=touched,
        ))




def _risk_notes(d: Decision, brief: Brief) -> None:
    # Count every flagged market but name only the first few. The count and the
    # list have to come from the same set, or the focus line says nine and the
    # section below it says six and the reader has no idea which to believe.
    hot = sorted((l for l in brief.lines if l.dangerous),
                 key=lambda l: -l.vol_percentile)
    if not hot:
        return
    d.avoid.append(Note(
        kind="avoid",
        key="avoid_hot",
        source="vol",
        params={
            "worst_vol": f"{hot[0].vol_annual:.0%}",
            "worst_pct": f"{hot[0].vol_percentile:.0%}",
            "n": str(len(hot)),
            "total": str(len(brief.lines)),
        },
        symbols=tuple(l.symbol for l in hot[:6]),
    ))


def _carry_note(d: Decision, brief: Brief) -> None:
    c = brief.carry
    if not c or c.get("error"):
        return
    net = c.get("basket_net_annual", 0.0)
    if net < 0.15:
        return
    d.watch.append(Note(
        kind="watch",
        key="watch_carry",
        source="binance",
        params={"rate": _pct(net), "monthly": f"${c.get('monthly_usd', 0.0):,.2f}",
                "capital": f"${c.get('capital', 0.0):,.0f}"},
    ))


def _focus(d: Decision, brief: Brief, soon: Sequence[Event],
           impacts: dict[str, Impact], have: dict[str, MarketLine],
           today: date) -> None:
    """The single sentence at the top. One thing, or an honest nothing."""
    imminent = [e for e in soon if (e.date - today).days <= 1
                and (imp := impacts.get(e.kind)) is not None and imp.real]
    if imminent:
        ev = imminent[0]
        imp = impacts[ev.kind]
        d.focus = Note(
            kind="focus", key="focus_event",
            source="fed" if ev.kind == "fomc" else "bls",
            params={"event_kind": ev.kind,
                    "days": str((ev.date - today).days),
                    "ratio": _ratio(imp.ratio)},
            symbols=_known([s for s, r in imp.touches(3) if r >= TOUCH_RATIO], have),
        )
        return

    extreme = [l for l in brief.lines if l.risk == "extreme"]
    if extreme:
        extreme.sort(key=lambda l: -l.vol_percentile)
        d.focus = Note(
            kind="focus", key="focus_risk", source="vol",
            params={"n": str(len(extreme)),
                    "vol": f"{extreme[0].vol_annual:.0%}",
                    "pct": f"{extreme[0].vol_percentile:.0%}"},
            symbols=tuple(l.symbol for l in extreme[:3]),
        )
        return

    if d.watch:
        d.focus = Note(kind="focus", key="focus_watch", source="study",
                       params={"n": str(len(d.watch))})
        return

    # A quiet day with markets flagged below is still a quiet day, but saying
    # "everything is normal" over the top of a CAREFUL list is a contradiction
    # the reader would be right to stop trusting the brief over.
    hot = [l for l in brief.lines if l.dangerous]
    if hot:
        hot.sort(key=lambda l: -l.vol_percentile)
        d.focus = Note(
            kind="focus", key="focus_careful", source="vol",
            params={"n": str(len(hot)), "total": str(len(brief.lines))},
            symbols=tuple(l.symbol for l in hot[:2]),
        )
        return

    d.focus = Note(kind="focus", key="focus_none", source="study",
                   params={"n": str(len(brief.lines))})


def _ignore_notes(d: Decision, brief: Brief, soon: Sequence[Event],
                  impacts: dict[str, Impact], have: dict[str, MarketLine]) -> None:
    """What not to spend attention on. The half most briefs leave out."""
    for ev in soon:
        imp = impacts.get(ev.kind)
        if imp is None or not imp.real:
            continue
        untouched = _known(
            [s for s, r in imp.by_market.items() if r < UNTOUCHED_RATIO], have)
        if len(untouched) >= 2:
            worst = min(imp.by_market[s] for s in untouched)
            d.ignore.append(Note(
                kind="ignore", key="ignore_untouched", source="events",
                params={"event_kind": ev.kind, "ratio": _ratio(worst),
                        "n": str(imp.events)},
                symbols=tuple(sorted(untouched, key=lambda s: imp.by_market[s])[:4]),
            ))

    calm = [l for l in brief.lines if l.risk == "calm"]
    if len(calm) >= 3:
        d.ignore.append(Note(
            kind="ignore", key="ignore_calm", source="vol",
            params={"n": str(len(calm)), "total": str(len(brief.lines))},
            symbols=tuple(l.symbol for l in
                          sorted(calm, key=lambda l: l.vol_percentile)[:4]),
        ))


# --------------------------------------------------------------------------- #
def _changes(d: Decision, brief: Brief, previous: dict[str, Any],
             soon: Sequence[Event], today: date) -> None:
    """What is different from yesterday, which is the part worth reading first.

    A brief that looks identical six mornings running has told the reader
    something important, but only if they can see that it is identical. Naming
    the deltas is how a daily note stops being wallpaper.
    """
    prev_risk: dict[str, str] = previous.get("risk", {}) or {}
    if prev_risk:
        ladder = {"calm": 0, "normal": 1, "elevated": 2, "extreme": 3}
        up, down = [], []
        for l in brief.lines:
            was = prev_risk.get(l.symbol)
            if was is None or was == l.risk:
                continue
            (up if ladder.get(l.risk, 1) > ladder.get(was, 1) else down).append(
                (l.symbol, was, l.risk))
        for group, key in ((up, "changed_risk_up"), (down, "changed_risk_down")):
            if group:
                d.changed.append(Note(
                    kind="changed", key=key, source="vol",
                    params={"n": str(len(group)),
                            "was": group[0][1], "now": group[0][2]},
                    symbols=tuple(s for s, _w, _n in group[:4]),
                ))

    prev_days = previous.get("event_days") or []
    new_events = [e for e in soon if e.day not in prev_days]
    for ev in new_events:
        d.changed.append(Note(
            kind="changed", key="changed_event_entered", source="fed"
            if ev.kind == "fomc" else "bls",
            params={"event_kind": ev.kind,
                    "days": str((ev.date - today).days)},
        ))

    was_carry = previous.get("carry_net")
    now_carry = (brief.carry or {}).get("basket_net_annual")
    if was_carry is not None and now_carry is not None:
        crossed = (was_carry < 0.15) != (now_carry < 0.15)
        if crossed:
            d.changed.append(Note(
                kind="changed", key="changed_carry", source="binance",
                params={"was": _pct(was_carry), "now": _pct(now_carry)},
            ))


def snapshot(brief: Brief, decision: Decision) -> dict[str, Any]:
    """The small slice of today that tomorrow needs in order to diff against it."""
    return {
        "day": decision.day,
        "risk": {l.symbol: l.risk for l in brief.lines},
        "event_days": [e.day for e in decision.events],
        "carry_net": (brief.carry or {}).get("basket_net_annual"),
        "quiet": decision.quiet,
    }


# --------------------------------------------------------------------------- #
def read_seconds(text: str) -> int:
    words = max(1, len(text.split()))
    return max(15, round(60.0 * words / READING_WPM))
