"""One morning run: the numbers, the decision, and the record kept of both.

Split out from ``cli.py`` because this sequence has to be identical everywhere -
the terminal, the cloud workflow, and the tests - and because two of its steps
are easy to get subtly wrong in ways nothing complains about:

*Ordering.*  Yesterday's snapshot has to be read *before* today's is written, or
the diff compares today against itself and reports that nothing ever changes.

*Idempotence.*  The workflow can be re-run by hand on the same day, and a
scorecard that counts one call twice inflates its own sample without inflating
its own evidence.  Both the claims log and the snapshot are keyed by date.

Persistence lives under ``data/`` rather than ``state/`` on purpose.  The cloud
runner checks out a clean tree every morning, so anything written to ``state/``
is gone by the next run; and a track record that is committed is a track record
the reader can audit commit by commit, which is the only version of a track
record worth publishing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from ..util import DATA_DIR, read_json, write_json
from . import events as ev
from . import feeds
from . import macro
from . import scorecard as sc
from .brief import Brief, build_brief
from .data import UNIVERSE
from .decide import Decision, decide, snapshot

log = logging.getLogger("printmoney.morning")

SNAPSHOT = DATA_DIR / "snapshot.json"


@dataclass
class Morning:
    brief: Brief
    decision: Decision
    previous: dict[str, Any] | None = None
    recorded: int = 0
    score: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.brief.ok

    def to_dict(self) -> dict[str, Any]:
        d = self.brief.to_dict()
        d["decision"] = self.decision.to_dict()
        d["recorded_claims"] = self.recorded
        d["score"] = self.score
        d["compared_with"] = (self.previous or {}).get("day")
        if self.warnings:
            d["warnings"] = self.warnings
        return d


def load_snapshot(path: Path | None = None) -> dict[str, Any] | None:
    return read_json(path or SNAPSHOT, default=None)


def save_snapshot(data: dict[str, Any], path: Path | None = None) -> Path:
    """Write yesterday's-state-for-tomorrow.

    ``path`` is resolved on each call rather than captured in the signature. A
    default bound at import time cannot be redirected, which is not a stylistic
    point: the test suite pointed :data:`SNAPSHOT` at a temp directory, the
    default ignored it, and a test run quietly overwrote the real committed
    snapshot with a two-line fixture. The published claims log lost a row the
    same way.
    """
    path = path or SNAPSHOT
    write_json(path, data)
    return path


def run(
    *,
    capital: float = 1_000.0,
    include_carry: bool = True,
    universe: Sequence[tuple[str, str]] = UNIVERSE,
    cache_hours: float = 6.0,
    today: date | None = None,
    persist: bool = True,
    offline_events: bool = False,
) -> Morning:
    """Build today's brief and decide what it means. Never raises on bad data."""
    brief = build_brief(capital=capital, include_carry=include_carry,
                        universe=universe, cache_hours=cache_hours)
    today = today or brief.generated_at.date()

    previous = load_snapshot()
    if previous and previous.get("day") == today.isoformat():
        # A same-day re-run must still diff against *yesterday*, not against the
        # snapshot this morning already wrote, or the second run always reports a
        # day with nothing in it.
        previous = previous.get("previous") or None

    impacts = ev.load_impacts()
    warnings: list[str] = []
    if not impacts:
        warnings.append(
            "no measured event impacts on disk - run `pm events --measure --save`; "
            "scheduled events are omitted rather than asserted without evidence"
        )

    # Official daily readings, and the measured table saying which of them move
    # with which markets. Both degrade to nothing rather than to guesswork: with
    # no feeds the brief drops its macro section, and with no table it will
    # print the readings but refuse to call any of them a reason.
    macro_feeds: dict[str, Any] = {}
    links = macro.Table()
    try:
        macro_feeds = feeds.load(offline=offline_events)
        spread = feeds.curve_spread(macro_feeds)
        if spread is not None:
            macro_feeds["curve"] = spread
    except Exception as exc:                      # noqa: BLE001 - never fatal
        warnings.append(f"macro feeds unavailable: {exc}")
    if macro_feeds:
        links = macro.load()
        if not links.links:
            warnings.append(
                "no measured macro links on disk - run `pm macro --save`; "
                "readings are printed but nothing is offered as a reason"
            )

    upcoming: list[ev.Event] = []
    if impacts:
        try:
            upcoming = ev.upcoming(today=today, offline=offline_events)
        except Exception as exc:                  # noqa: BLE001 - never fatal
            warnings.append(f"event calendar unavailable: {exc}")

    decision = decide(brief, events=upcoming, impacts=impacts,
                      previous=previous, today=today,
                      feeds=macro_feeds, links=links)

    m = Morning(brief=brief, decision=decision, previous=previous,
                warnings=warnings)

    if brief.ok and persist:
        try:
            m.recorded = sc.record(brief.lines, today.isoformat())
        except ValueError as exc:
            # A clock running ahead should cost the reader the scorecard entry,
            # never the brief - and never a fabricated row in a published record.
            warnings.append(f"not recorded: {exc}")
        snap = snapshot(brief, decision)
        # Carry one generation back, so a same-day re-run still has something to
        # compare against after it overwrites today.
        snap["previous"] = {k: v for k, v in (previous or {}).items()
                            if k != "previous"} or None
        save_snapshot(snap)

    return m
