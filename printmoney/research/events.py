"""Scheduled events, and the measured evidence for whether each one matters.

Every other feature in this project had to clear a bar before it shipped, and an
event calendar is no exception.  A calendar that lists things because they sound
important is decoration; this one lists only what moved markets more than an
ordinary day did, and it records *which* markets, because that turns out to be
the interesting half of the answer.

Two families are carried here, for two different reasons:

``payrolls``
    US non-farm payrolls, the first Friday of the month.  Defined by rule, so it
    needs no feed at all and can never go stale.  Measured over ten years and
    twenty-four markets it runs at 1.15x an ordinary day against 0.94x for every
    *other* Friday - so this is a payrolls effect, not a Friday effect.

``fomc``
    The Federal Reserve's rate decision.  Announced years ahead and published by
    the Fed itself, which is the most authoritative source that exists for it,
    so the dates are fetched rather than guessed.

What is deliberately absent matters as much.  CPI was tested as a 10th-to-15th
window and came back at 1.01x with t = +0.50 - indistinguishable from an
ordinary day.  That window is almost certainly too coarse, six days with one
release inside it, but a proxy that cannot be measured is a proxy that cannot be
trusted, so it does not ship.  If a free source of exact BLS release dates turns
up it can be added here and put through :func:`measure` like everything else.

Nothing in this module says which way a market will go.  It says a date is on
the calendar and, for that date, how much bigger a typical move has historically
been - a statement about *risk*, which is the one thing this project has been
able to show is forecastable.
"""
from __future__ import annotations

import logging
import re
import statistics as st
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

from ..util import DATA_DIR, STATE_DIR, USER_AGENT, read_json, retry, write_json

log = logging.getLogger("printmoney.research")

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS_URL = "https://www.bls.gov/schedule/news_release/empsit.htm"

CACHE = STATE_DIR / "events.json"

#: The measured impact table, committed rather than recomputed. Working it out
#: needs ten years of bars for the whole universe, which is minutes of downloads
#: the morning run should not be paying for every day - and the ratios move by
#: fractions of a percent when a single new event is added. Regenerate it
#: deliberately with `pm events --measure --save`, and let code review see the
#: diff, which is the right amount of ceremony for a number the brief quotes.
IMPACTS = DATA_DIR / "impacts.json"

#: The Fed publishes years ahead, so a week-old cache costs nothing and a failed
#: fetch on a workflow run should never take the whole brief down with it.
CACHE_HOURS = 24 * 7

#: Bumped when the *meaning* of a cached row changes rather than its shape.
#: Price bars already learned this the expensive way: a cache written before the
#: switch to adjusted closes read back without complaint and served unadjusted
#: prices to code that assumed otherwise. Nothing failed, which was the problem.
SCHEMA = 1


MONTHS = {
    m: i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"],
        1,
    )
}


# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Event:
    """One dated thing on the calendar.

    ``day`` is the day the *number lands*, not the day the meeting opens - a
    two-day FOMC moves the market on the afternoon of the second day, and dating
    it to the first would smear the effect across a day where nothing happened.
    """

    day: str          # YYYY-MM-DD, UTC
    kind: str         # "payrolls" | "fomc"
    name: str
    source: str       # where a person can go and check it themselves
    note: str = ""

    @property
    def date(self) -> date:
        return datetime.strptime(self.day, "%Y-%m-%d").date()

    def to_dict(self) -> dict[str, Any]:
        return {"day": self.day, "kind": self.kind, "name": self.name,
                "source": self.source, "note": self.note}


# --------------------------------------------------------------------------- #
# payrolls - pure rule, no network, cannot go stale
def first_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7)


def payrolls(start: date, end: date) -> list[Event]:
    """US non-farm payrolls: first Friday of every month, 08:30 New York."""
    out: list[Event] = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        d = first_friday(y, m)
        if start <= d <= end:
            out.append(Event(
                day=d.isoformat(),
                kind="payrolls",
                name="US jobs report (non-farm payrolls)",
                source=BLS_URL,
                note="08:30 New York",
            ))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


# --------------------------------------------------------------------------- #
# FOMC - fetched from the Fed, because inventing dates is not an option
def _expand(name: str) -> str:
    """The Fed abbreviates in straddle rows: Apr/May, Jan/Feb, Sept."""
    for full in MONTHS:
        if full.lower().startswith(name.lower()[:3]):
            return full
    return name


def _one_meeting(year: int, mtext: str, dtext: str) -> Event | None:
    projections = "*" in dtext
    dtext = dtext.replace("–", "-").replace("*", "").strip()
    parts = [p for p in re.split(r"[-\s]+", dtext) if p.isdigit()]
    if not parts:
        return None
    last_day = int(parts[-1])

    names = [n for n in mtext.split("/") if n]
    # "Apr/May 30-1": the closing day belongs to the second month named, which
    # you can spot because the range counts *down* rather than up.
    straddles = len(names) > 1 and len(parts) > 1 and int(parts[0]) > last_day
    month = MONTHS.get(_expand(names[-1] if straddles else names[0]))
    if month is None:
        return None
    # A December/January meeting closes in the following calendar year.
    if straddles and month == 1 and MONTHS.get(_expand(names[0])) == 12:
        year += 1
    if not 1 <= last_day <= monthrange(year, month)[1]:
        return None

    d = date(year, month, last_day)
    return Event(
        day=d.isoformat(),
        kind="fomc",
        name="Fed rate decision (FOMC)",
        source=FOMC_URL,
        note="14:00 New York" + (", with projections" if projections else ""),
    )


def parse_fomc(html: str) -> list[Event]:
    """Pull the decision dates out of the Fed's own calendar page."""
    events: list[Event] = []
    panels = re.split(r'<a id="\d+">(\d{4}) FOMC Meetings</a>', html)
    for year_s, body in zip(panels[1::2], panels[2::2]):
        months = re.findall(
            r'fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([A-Za-z/]+)', body)
        days = re.findall(
            r'fomc-meeting__date[^>]*>\s*([0-9–\-*/\s]+?)\s*<', body)
        for mtext, dtext in zip(months, days):
            ev = _one_meeting(int(year_s), mtext, dtext)
            if ev is not None:
                events.append(ev)
    # The page repeats nothing, but a defensive dedupe keeps a layout change
    # from quietly doubling every meeting.
    seen: dict[str, Event] = {e.day: e for e in events}
    return sorted(seen.values(), key=lambda e: e.day)


def _fetch_fomc() -> list[Event]:
    def once() -> list[Event]:
        r = httpx.get(FOMC_URL, timeout=30.0,
                      headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        r.raise_for_status()
        events = parse_fomc(r.text)
        # A layout change would parse to nothing and look exactly like a quiet
        # year, so treat a thin result as a failure rather than as news.
        if len(events) < 8:
            raise ValueError(f"FOMC page parsed to only {len(events)} meetings")
        return events

    return retry(once, attempts=3, what="fomc calendar")


def fomc(*, cache_hours: float = CACHE_HOURS, offline: bool = False) -> list[Event]:
    """Every FOMC decision date the Fed currently publishes, cached on disk."""
    cached = read_json(CACHE, default=None)
    if cached and cached.get("schema") != SCHEMA:
        cached = None                   # written by an older parser; refetch
    age_ok = bool(cached) and cached.get("fetched_at", 0.0) > (
        datetime.now(timezone.utc).timestamp() - cache_hours * 3600)
    if cached and (age_ok or offline):
        return [Event(**e) for e in cached["events"]]
    if offline:
        return []
    try:
        events = _fetch_fomc()
    except Exception as exc:                      # noqa: BLE001 - never fatal
        log.warning("FOMC calendar unavailable (%s)", exc)
        return [Event(**e) for e in cached["events"]] if cached else []
    write_json(CACHE, {"schema": SCHEMA,
                       "fetched_at": datetime.now(timezone.utc).timestamp(),
                       "events": [e.to_dict() for e in events]})
    return events


# --------------------------------------------------------------------------- #
def calendar(start: date, end: date, *, offline: bool = False) -> list[Event]:
    """Everything scheduled inside a window, earliest first."""
    out = payrolls(start, end)
    out += [e for e in fomc(offline=offline) if start <= e.date <= end]
    return sorted(out, key=lambda e: (e.day, e.kind))


def upcoming(*, within_days: int = 7, today: date | None = None,
             offline: bool = False) -> list[Event]:
    today = today or datetime.now(timezone.utc).date()
    return calendar(today, today + timedelta(days=within_days), offline=offline)


# --------------------------------------------------------------------------- #
@dataclass
class Impact:
    """How much bigger a typical move was on this kind of day, per market.

    ``ratio`` is mean absolute daily return on event days over the same figure
    on every other day, so 1.30 means "a third bigger than usual".  It is kept
    per market because the answer differs enormously between them and that
    difference is the useful part: payrolls moves the US rates complex and does
    not touch crypto, which is exactly what a jobs number should do.
    """

    kind: str
    ratio: float
    tstat: float
    events: int
    markets_bigger: int
    markets: int
    by_market: dict[str, float] = field(default_factory=dict)

    @property
    def real(self) -> bool:
        """|t| > 2 is the usual not-a-fluke bar; the ratio floor keeps a
        statistically clean 2% effect from being called meaningful."""
        return (abs(self.tstat) > 2.0 and self.ratio > 1.05
                and self.markets_bigger > 0.6 * self.markets)

    def touches(self, n: int = 5) -> list[tuple[str, float]]:
        """The markets this event actually moves, strongest first."""
        return [(s, r) for s, r in
                sorted(self.by_market.items(), key=lambda kv: -kv[1])[:n]
                if r > 1.05]

    def ignores(self, n: int = 3) -> list[tuple[str, float]]:
        """The markets it demonstrably does not move."""
        return [(s, r) for s, r in
                sorted(self.by_market.items(), key=lambda kv: kv[1])[:n]
                if r < 1.0]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ratio": round(self.ratio, 3),
                "tstat": round(self.tstat, 2), "events": self.events,
                "markets_bigger": self.markets_bigger, "markets": self.markets,
                "real": self.real,
                "by_market": {k: round(v, 3) for k, v in self.by_market.items()}}


def measure_all(*, rng: str = "10y", universe: Sequence[tuple[str, str]] | None = None,
                cache_hours: float = 24.0) -> tuple[dict[str, Impact], str]:
    """Measure every event family against real history. Slow, run deliberately."""
    from .data import UNIVERSE, align, fetch_many

    series = fetch_many(universe or UNIVERSE, rng=rng, cache_hours=cache_hours)
    days, aligned = align(series.values())
    if not days:
        raise RuntimeError("no aligned trading days - market data did not load")

    lo, hi = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    families = {
        "payrolls": payrolls(lo, hi),
        "fomc": [e for e in fomc() if lo <= e.date <= hi],
    }
    impacts = {
        kind: measure(kind, days, aligned, [e.day for e in evs])
        for kind, evs in families.items()
    }
    return impacts, f"{days[0]}..{days[-1]} over {len(aligned)} markets"


def save_impacts(impacts: dict[str, Impact], *, span: str = "") -> Path:
    write_json(IMPACTS, {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "span": span,
        "impacts": {k: v.to_dict() for k, v in impacts.items()},
    })
    return IMPACTS


def load_impacts() -> dict[str, Impact]:
    """The committed impact table, or nothing at all.

    An empty result is handled everywhere downstream by simply not mentioning
    events, which is the correct degradation: a calendar entry with no measured
    backing is the decoration this module exists to keep out.
    """
    blob = read_json(IMPACTS, default=None)
    if not blob:
        return {}
    out: dict[str, Impact] = {}
    for kind, d in blob.get("impacts", {}).items():
        out[kind] = Impact(
            kind=d["kind"], ratio=d["ratio"], tstat=d["tstat"],
            events=d["events"], markets_bigger=d["markets_bigger"],
            markets=d["markets"], by_market=d.get("by_market", {}),
        )
    return out


def measure(kind: str, days: Sequence[str], aligned: dict[str, list],
            flagged: Iterable[str]) -> Impact:
    """Compare flagged days against every other day, market by market."""
    flags = set(flagged)
    rows: list[tuple[str, float, float]] = []
    pooled_on: list[float] = []
    pooled_off: list[float] = []

    for sym, bars in aligned.items():
        on, off = [], []
        for i in range(1, len(bars)):
            r = abs(bars[i].close / bars[i - 1].close - 1.0)
            (on if days[i] in flags else off).append(r)
        if len(on) < 20 or len(off) < 100:
            continue
        rows.append((sym, st.fmean(on), st.fmean(off)))
        pooled_on += on
        pooled_off += off

    if not rows:
        return Impact(kind=kind, ratio=0.0, tstat=0.0, events=0,
                      markets_bigger=0, markets=0)

    m_on, m_off = st.fmean(pooled_on), st.fmean(pooled_off)
    # Welch, by hand rather than pulling in scipy for one number.
    se = (st.variance(pooled_on) / len(pooled_on)
          + st.variance(pooled_off) / len(pooled_off)) ** 0.5
    return Impact(
        kind=kind,
        ratio=m_on / m_off if m_off else 0.0,
        tstat=(m_on - m_off) / se if se else 0.0,
        events=sum(1 for d in days if d in flags),
        markets_bigger=sum(1 for _s, a, b in rows if a > b),
        markets=len(rows),
        by_market={s: (a / b if b else 0.0) for s, a, b in rows},
    )
