"""Getting the brief off this machine and onto a phone.

Two formats, because they answer different questions:

* **iCalendar (.ics)** - a subscribable feed. Apple Calendar refreshes it on its
  own, so once the file lives at a URL the phone keeps itself up to date with no
  app, no account and nothing to install.
* **A single HTML page** - for actually reading. The calendar entry carries the
  verdict; this carries the table behind it.

Both are written as plain files. Whatever syncs them (OneDrive, iCloud Drive, a
web server) is somebody else's job, which is the point - nothing here depends on
a service that can disappear.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from ..util import fmt_usd
from . import sources
from .brief import Brief
from .decide import read_seconds
from .i18n import calendar_name as default_calendar_name
from .i18n import event_name, market_name, norm, render_note, t, when_phrase

log = logging.getLogger("printmoney.export")

#: Where the full page lives. Put on the event as a URL property as well as in
#: the body, because Apple Calendar makes the URL tappable and the body is only
#: text - and the whole point of the page is that it is one tap from the entry.
SITE_URL = "https://ayfew.github.io/Print-Money/"

#: RFC 5545 wants CRLF line endings and lines folded at 75 octets.
CRLF = "\r\n"
FOLD_AT = 73


def _fold(line: str) -> str:
    """Fold a long content line the way RFC 5545 requires."""
    out = []
    while len(line.encode("utf-8")) > FOLD_AT:
        cut = FOLD_AT
        while len(line[:cut].encode("utf-8")) > FOLD_AT:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return CRLF.join(out)


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _uid(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20] + "@printmoney"


# --------------------------------------------------------------------------- #
@dataclass
class CalendarEvent:
    day: date
    title: str
    body: str
    uid_seed: str

    def lines(self, stamp: str) -> list[str]:
        return [
            "BEGIN:VEVENT",
            _fold(f"UID:{_uid(self.uid_seed)}"),
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{self.day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{self.day + timedelta(days=1):%Y%m%d}",
            _fold(f"SUMMARY:{_escape(self.title)}"),
            _fold(f"DESCRIPTION:{_escape(self.body)}"),
            _fold(f"URL:{SITE_URL}"),
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]


def _pct(x: float, digits: int = 2) -> str:
    return f"{100 * x:+.{digits}f}%"


def _names_for(brief: Brief) -> dict[str, str]:
    return {l.symbol: l.name for l in brief.lines}


def _title(brief: Brief, decision: Any, lang: str) -> str:
    """What the phone shows on the lock screen, which is all most days get read.

    Twenty-odd characters have to carry the decision, so the title states the one
    thing today is about rather than counting rows - "nothing today" is a real
    answer and gets said plainly.
    """
    if not brief.ok:
        return t("title_failed", lang)
    focus = getattr(decision, "focus", None)
    if focus is not None:
        params = focus.params
        if focus.key == "focus_event":
            return t("title_event", lang,
                     event=event_name(params.get("event_kind", ""), lang),
                     when=when_phrase(int(params.get("days", 0) or 0), lang))
        if focus.key in ("focus_risk", "focus_careful"):
            # "nothing today" over a lock screen while nine markets run hot is
            # the same contradiction the focus line was fixed for.
            return t("title_risk", lang, n=params.get("n", "0"))
    if brief.actions:
        return t("title_actions", lang, n=len(brief.actions))
    return t("title_quiet", lang)


def _decision_lines(decision: Any, brief: Brief, lang: str) -> list[str]:
    """The decision, in reading order: what today is, what changed, then lists.

    "Changed since yesterday" sits second on purpose. For a reader who opens this
    every morning it is the only part that is new, and burying it under sections
    that were identical yesterday is how a daily note becomes wallpaper.
    """
    if decision is None:
        return []
    names = _names_for(brief)
    parts: list[str] = []

    if decision.focus is not None:
        parts.append(render_note(decision.focus, lang, names))
        parts.append("")

    # Reading order: what today is, what is new since yesterday, why the tape
    # did what it did, the backdrop that makes the rest legible, then the lists.
    for header, notes in (
        ("hdr_changed", decision.changed),
        ("hdr_why", decision.why),
        ("hdr_context", getattr(decision, "context", [])),
        ("hdr_watch", decision.watch),
        ("hdr_avoid", decision.avoid),
        ("hdr_ignore", decision.ignore),
    ):
        if not notes:
            continue
        parts.append(t(header, lang) + ":")
        parts.extend(f"  - {render_note(n, lang, names)}" for n in notes)
        if header == "hdr_why":
            # Said once, next to the attributions, because a same-day
            # correlation reads like a forecast if nobody says it is not one.
            parts.append("  " + t("why_note", lang))
        parts.append("")

    if not decision.changed and decision.focus is not None:
        parts.append(t("no_changes", lang))
        parts.append("")
    return parts


def _source_lines(decision: Any, lang: str) -> list[str]:
    if decision is None or not decision.source_ids:
        return []
    parts = [t("hdr_sources", lang) + ":"]
    parts.extend(
        t("source_line", lang, tier=s.tier_name, name=s.name, url=s.url)
        for s in sources.cited(decision.source_ids)
    )
    parts.append("")
    return parts


def _score_lines(score: dict[str, Any] | None, lang: str) -> list[str]:
    if not score or not score.get("n"):
        return []
    return [
        t("hdr_score", lang) + ":",
        "  " + t("score_line", lang, rate=f"{100 * score.get('rate', 0.0):.0f}%",
                 n=str(score.get("n", 0))),
        "",
    ]


def brief_to_event(brief: Brief, lang: str = "th", *,
                   decision: Any = None,
                   score: dict[str, Any] | None = None) -> CalendarEvent:
    """One all-day entry per brief: the decision in the title, the case inside.

    Built from the brief's numbers, not from its English sentences, so a second
    language is a different assembly rather than a translation of prose that has
    already been written.
    """
    lang = norm(lang)
    day = brief.generated_at.date()
    title = _title(brief, decision, lang)

    parts: list[str] = []
    if not brief.ok:
        parts.append(t("verdict_failed", lang, error=brief.verdict))
        parts.append("")
        parts.extend(f"  {o}" for o in brief.observations)
        return CalendarEvent(
            day=day, title=title, body="\n".join(parts), uid_seed=f"brief-{day:%Y-%m-%d}"
        )

    decision_block = _decision_lines(decision, brief, lang)
    if decision_block:
        # The decision replaces the old one-line verdict rather than sitting
        # above it; two summaries disagreeing about the same morning is worse
        # than either one alone.
        parts.extend(decision_block)
    else:
        parts.append(
            t("verdict_actions", lang, n=len(brief.actions)) if brief.actions
            else t("verdict_quiet", lang)
        )
        parts.append("")

    if brief.actions:
        parts.append(t("hdr_actions", lang) + ":")
        parts.extend(f"  - {a}" for a in brief.actions)
        parts.append("")

    movers = brief.movers(5)
    if movers:
        parts.append(t("hdr_movers", lang) + ":")
        for m in movers:
            parts.append(
                t(
                    "mover_line",
                    lang,
                    name=market_name(m.symbol, m.name, lang),
                    day=_pct(m.day),
                    month=_pct(m.month, 1),
                )
            )
        parts.append("")

    stretched = brief.stretched(4)
    if stretched:
        parts.append(t("hdr_stretched", lang) + ":")
        for m in stretched:
            parts.append(
                t(
                    "stretched_line",
                    lang,
                    name=market_name(m.symbol, m.name, lang),
                    z=f"{m.zscore:+.1f}",
                    month=_pct(m.month, 1),
                )
            )
        parts.append("  " + t("stretched_note", lang))
        parts.append("")

    # With a decision attached this is already said, better, under CAREFUL.
    danger = [] if decision is not None else brief.dangerous(5)
    if danger:
        parts.append(t("hdr_danger", lang) + ":")
        for m in danger:
            parts.append(
                t(
                    "danger_line",
                    lang,
                    name=market_name(m.symbol, m.name, lang),
                    vol=f"{m.vol_annual:.0%}",
                    pct=f"{m.vol_percentile:.0%}",
                    dd=f"{m.drawdown:+.1%}",
                )
            )
        parts.append("  " + t("danger_note", lang))
        parts.append("")

    if brief.carry and not brief.carry.get("error"):
        c = brief.carry
        parts.append(t("hdr_carry", lang) + ":")
        parts.append(
            t(
                "carry_line",
                lang,
                rate=_pct(c.get("basket_net_annual", 0.0), 1),
                monthly=fmt_usd(c.get("monthly_usd", 0.0)),
                capital=fmt_usd(c.get("capital", 0.0)),
                # Which exchange this came from. Funding differs between venues
                # and the cloud runner falls back to whichever answers, so a
                # rate with no venue on it cannot be compared with yesterday's -
                # today's +3.7% and yesterday's +3.6% were two different books.
                venue=c.get("venue") or "?",
            )
        )
        net = c.get("basket_net_annual", 0.0)
        threshold = "15%"
        parts.append(
            t("carry_above", lang, threshold=threshold) if net >= 0.15
            else t("carry_below", lang, threshold=threshold)
        )
        parts.append("")

    parts.append(t("site_url_label", lang) + ": " + SITE_URL)
    parts.append("")
    parts.extend(_score_lines(score, lang))
    parts.extend(_source_lines(decision, lang))
    parts.append(t("footer", lang))

    body = "\n".join(parts)
    if decision is not None:
        # Stated, not promised: one of the success criteria for this brief was
        # that it can be read in about two minutes, and a reader can check a
        # printed number against their own clock.
        body = t("read_time", lang, sec=read_seconds(body)) + "\n\n" + body
    return CalendarEvent(
        day=day, title=title, body=body, uid_seed=f"brief-{day:%Y-%m-%d}"
    )


def event_for(item: Any, lang: str = "th") -> CalendarEvent:
    """Build the entry from either a bare ``Brief`` or a whole ``Morning``.

    Taking both keeps every existing caller working while letting the ones that
    have a decision hand it over, rather than forcing a second writer that would
    drift out of step with this one.
    """
    brief = getattr(item, "brief", item)
    return brief_to_event(
        brief, lang,
        decision=getattr(item, "decision", None),
        score=getattr(item, "score", None),
    )


def write_ics(
    briefs: Sequence[Any] | Any,
    path: str | Path,
    *,
    calendar_name: str | None = None,
    lang: str = "th",
    merge_existing: bool = True,
) -> Path:
    """Write (or extend) a subscribable calendar file.

    Existing events are kept so the feed builds a history rather than replacing
    itself every morning - a calendar with one event in it is a notification,
    not a record.
    """
    if isinstance(briefs, Brief) or hasattr(briefs, "brief"):
        briefs = [briefs]
    lang = norm(lang)
    calendar_name = calendar_name or default_calendar_name(lang)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    kept: list[str] = []
    if merge_existing and out.exists():
        text = out.read_text(encoding="utf-8", errors="replace")
        new_uids = {_uid(event_for(b, lang).uid_seed) for b in briefs}
        block: list[str] = []
        inside = False
        for raw in text.splitlines():
            if raw.startswith("BEGIN:VEVENT"):
                inside, block = True, [raw]
            elif raw.startswith("END:VEVENT") and inside:
                block.append(raw)
                if not any(u in "".join(block) for u in new_uids):
                    kept.extend(block)
                inside = False
            elif inside:
                block.append(raw)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//printmoney//daily brief//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _fold(f"X-WR-CALNAME:{_escape(calendar_name)}"),
        "X-PUBLISHED-TTL:PT6H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
    ]
    lines.extend(kept)
    for b in briefs:
        lines.extend(event_for(b, lang).lines(stamp))
    lines.append("END:VCALENDAR")

    # newline="" or the interpreter rewrites our CRLF as CR-CRLF on
    # Windows and the file stops being valid iCalendar.
    out.write_text(CRLF.join(lines) + CRLF, encoding="utf-8", newline="")
    log.info("wrote %s (%d events)", out, len(briefs) + len(kept) // 9)
    return out


# --------------------------------------------------------------------------- #
def write_html(
    item: Any, path: str | Path, *, capital: float = 1_000.0, lang: str = "th"
) -> Path:
    """A single page sized for a phone screen.

    Takes a ``Brief`` or a ``Morning``; with the latter the decision leads and
    the twenty-four-row table becomes the evidence underneath it rather than the
    thing the reader has to interpret for themselves.
    """
    lang = norm(lang)
    brief: Brief = getattr(item, "brief", item)
    decision = getattr(item, "decision", None)
    score = getattr(item, "score", None)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    def esc(x: Any) -> str:
        return (
            str(x)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def signed(v: float) -> str:
        cls = "up" if v > 0 else "down" if v < 0 else "flat"
        return f'<span class="{cls}">{v:+.2%}</span>'

    rows = "".join(
        f"<tr><td>{esc(market_name(l.symbol, l.name, lang))}</td><td>{signed(l.day)}</td>"
        f"<td>{signed(l.week)}</td><td>{signed(l.month)}</td>"
        f"<td>{l.vol_annual:.0%}</td></tr>"
        for l in sorted(brief.lines, key=lambda x: -abs(x.day))
    )
    actions = "".join(f"<li class='act'>{esc(a)}</li>" for a in brief.actions)
    notes = "".join(f"<li>{esc(o)}</li>" for o in brief.observations)

    # --- the decision, which is the part the page exists for ---------------- #
    names = _names_for(brief)

    def note_list(notes_: Sequence[Any], css: str) -> str:
        return "".join(
            f"<li class='{css}'>{esc(render_note(n, lang, names))}"
            f"<span class='src'>{esc(sources.get(n.source).tier_name)}</span></li>"
            for n in notes_
        )

    decision_html = ""
    if decision is not None:
        blocks = []
        if decision.focus is not None:
            blocks.append(
                f'<h2 class="lead">{esc(t("hdr_focus", lang))}</h2>'
                f'<div class="verdict">{esc(render_note(decision.focus, lang, names))}</div>'
            )
        for header, notes_, css in (
            ("hdr_changed", decision.changed, "chg"),
            ("hdr_why", decision.why, "why"),
            ("hdr_context", getattr(decision, "context", []), "ctx"),
            ("hdr_watch", decision.watch, "act"),
            ("hdr_avoid", decision.avoid, "warn"),
            ("hdr_ignore", decision.ignore, "skip"),
        ):
            if notes_:
                blocks.append(
                    f'<h2>{esc(t(header, lang))}</h2>'
                    f'<ul>{note_list(notes_, css)}</ul>'
                    + (f'<p class="muted">{esc(t("why_note", lang))}</p>'
                       if header == "hdr_why" else "")
                )
        if not decision.changed:
            blocks.append(f'<p class="muted">{esc(t("no_changes", lang))}</p>')
        decision_html = "".join(blocks)

    score_html = ""
    if score and score.get("n"):
        score_html = (
            f'<h2>{esc(t("hdr_score", lang))}</h2><p class="muted">'
            + esc(t("score_line", lang,
                    rate=f"{100 * score.get('rate', 0.0):.0f}%",
                    n=str(score.get("n", 0))))
            + "</p>"
        )

    sources_html = ""
    if decision is not None and decision.source_ids:
        items = "".join(
            f'<li><a href="{esc(s.url)}" rel="noopener">{esc(s.name)}</a>'
            f'<span class="src">{esc(s.tier_name)}</span></li>'
            for s in sources.cited(decision.source_ids)
        )
        sources_html = f'<h2>{esc(t("hdr_sources", lang))}</h2><ul class="srcs">{items}</ul>'

    carry_block = ""
    if brief.carry and not brief.carry.get("error"):
        c = brief.carry
        carry_block = (
            f'<h2>{esc(t("hdr_carry", lang))}</h2><p class="muted">'
            f"basket nets <b>{c.get('basket_net_annual', 0):+.1%}</b> a year "
            f"&rarr; {esc(fmt_usd(c.get('monthly_usd', 0)))} a month on "
            f"{esc(fmt_usd(c.get('capital', capital)))}. "
            f"{c.get('scanned', 0)} perps scanned, {c.get('hedgeable', 0)} hedgeable.</p>"
        )

    html = f"""<!doctype html><html lang="{lang}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(t("page_title", lang))} {brief.generated_at:%Y-%m-%d}</title>
<style>
:root {{ --bg:#fbfbfb; --panel:#fff; --ink:#0a0a0a; --dim:#6b7280; --line:#e8e8e8;
         --up:#0f7a4d; --down:#c0392f; }}
@media (prefers-color-scheme:dark) {{ :root {{ --bg:#0a0a0a; --panel:#151515; --ink:#fafafa;
         --dim:#9aa0ab; --line:#282828; --up:#4ade80; --down:#f87171; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:18px 16px 60px; background:var(--bg); color:var(--ink);
  font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; -webkit-text-size-adjust:100%; }}
.wrap {{ max-width:640px; margin:0 auto; }}
h1 {{ font-size:17px; margin:0 0 2px; letter-spacing:-.01em; }}
h2 {{ font-size:11px; text-transform:uppercase; letter-spacing:.12em; color:var(--dim);
  margin:26px 0 8px; }}
h2.lead {{ margin-top:0; }}
.stamp {{ color:var(--dim); font-size:12.5px; margin-bottom:18px; }}
.verdict {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
  padding:16px 18px; font-size:16px; font-weight:600; line-height:1.5; }}
ul {{ margin:0; padding-left:20px; }} li {{ margin:7px 0; color:var(--dim); }}
li.act {{ color:var(--up); font-weight:600; }}
li.warn {{ color:var(--down); font-weight:600; }}
li.chg {{ color:var(--ink); font-weight:600; }}
li.skip {{ opacity:.72; }}
li.why {{ color:var(--ink); }}
li.ctx {{ color:var(--dim); font-variant-numeric:tabular-nums; }}
.src {{ display:inline-block; margin-left:7px; padding:1px 6px; border-radius:999px;
  border:1px solid var(--line); font-size:10px; letter-spacing:.06em;
  text-transform:uppercase; color:var(--dim); font-weight:500; vertical-align:1px; }}
ul.srcs {{ list-style:none; padding-left:0; }}
ul.srcs a {{ color:var(--ink); text-decoration:none; border-bottom:1px solid var(--line); }}
table {{ width:100%; border-collapse:collapse; font-size:13.5px;
  font-variant-numeric:tabular-nums; }}
th {{ text-align:right; font-size:10px; text-transform:uppercase; letter-spacing:.1em;
  color:var(--dim); padding:6px 4px; border-bottom:1px solid var(--line); }}
th:first-child,td:first-child {{ text-align:left; }}
td {{ text-align:right; padding:7px 4px; border-bottom:1px solid var(--line); }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }} .flat {{ color:var(--dim); }}
.muted {{ color:var(--dim); font-size:13.5px; }}
footer {{ margin-top:30px; padding-top:14px; border-top:1px solid var(--line);
  color:var(--dim); font-size:12px; }}
</style></head><body><div class="wrap">
<h1>{esc(t("calendar_name", lang))}</h1>
<div class="stamp">{brief.generated_at:%A %d %B %Y &middot; %H:%M UTC}</div>
{decision_html or f'<div class="verdict">{esc(brief.verdict)}</div>'}
{f'<h2>{esc(t("hdr_actions", lang))}</h2><ul>{actions}</ul>' if actions else ""}
{f'<h2>{esc(t("hdr_notes", lang))}</h2><ul>{notes}</ul>' if notes else ""}
{carry_block}
{score_html}
<h2>{esc(t("th_where", lang))}</h2>
<table><tr><th>{esc(t("th_market", lang))}</th><th>{esc(t("th_day", lang))}</th><th>{esc(t("th_week", lang))}</th><th>{esc(t("th_month", lang))}</th><th>{esc(t("th_vol", lang))}</th></tr>
{rows}</table>
{sources_html}
<p class="muted"><a href="graph.html">{esc(t("graph_link", lang))}</a></p>
<footer>{t("page_footer", lang)}</footer>
</div></body></html>"""
    out.write_text(html, encoding="utf-8")
    return out
