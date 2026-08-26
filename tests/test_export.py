"""The calendar feed: valid iCalendar, and idempotent when the job reruns."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from printmoney.research.brief import Brief, MarketLine
from printmoney.research.export import brief_to_event, write_html, write_ics

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)


def _line(symbol="SPY", name="S&P 500", day=0.004):
    return MarketLine(
        symbol=symbol, name=name, last=760.0, day=day, week=0.01, month=0.03,
        year=0.18, vol_annual=0.13, zscore=1.0, intraday_share=0.4,
    )


def _brief(*, actions=(), notes=("something happened",), at=NOW, error=""):
    return Brief(
        generated_at=at,
        lines=[_line(), _line("GLD", "Gold", -0.011)],
        actions=list(actions),
        observations=list(notes),
        error=error,
    )


class TestCalendarFile:
    def test_line_endings_are_crlf_and_nothing_else(self, tmp_path):
        """Windows text mode turns \\r\\n into \\r\\r\\n and the file stops parsing."""
        path = write_ics(_brief(), tmp_path / "cal.ics")
        raw = path.read_bytes()
        assert b"\r\n" in raw
        stripped = raw.replace(b"\r\n", b"")
        assert b"\r" not in stripped and b"\n" not in stripped

    def test_lines_are_folded_within_the_spec(self, tmp_path):
        long_note = "x" * 400
        path = write_ics(_brief(notes=[long_note]), tmp_path / "cal.ics")
        text = path.read_bytes().decode("utf-8")
        assert all(len(l.encode()) <= 75 for l in text.split("\r\n"))

    def test_it_is_a_complete_calendar(self, tmp_path):
        text = write_ics(_brief(), tmp_path / "cal.ics").read_text(encoding="utf-8")
        assert text.startswith("BEGIN:VCALENDAR")
        assert text.rstrip().endswith("END:VCALENDAR")
        assert text.count("BEGIN:VEVENT") == text.count("END:VEVENT") == 1
        assert "DTSTART;VALUE=DATE:20260826" in text

    def test_rerunning_the_same_day_replaces_rather_than_duplicates(self, tmp_path):
        path = tmp_path / "cal.ics"
        write_ics(_brief(notes=["first run"]), path)
        write_ics(_brief(notes=["second run"]), path)
        text = path.read_text(encoding="utf-8")
        assert text.count("BEGIN:VEVENT") == 1
        assert "second run" in text.replace("\r\n ", "")

    def test_a_new_day_is_appended_to_the_history(self, tmp_path):
        path = tmp_path / "cal.ics"
        write_ics(_brief(), path)
        write_ics(_brief(at=NOW + timedelta(days=1)), path)
        text = path.read_text(encoding="utf-8")
        assert text.count("BEGIN:VEVENT") == 2

    def test_special_characters_are_escaped(self, tmp_path):
        note = "gold, silver; oil"
        text = write_ics(_brief(notes=[note]), tmp_path / "cal.ics").read_text(encoding="utf-8")
        unfolded = text.replace("\r\n ", "")
        assert "gold\\, silver\\; oil" in unfolded


class TestEventContent:
    def test_a_quiet_day_says_so_in_the_title(self):
        assert brief_to_event(_brief()).title == "printmoney: nothing today"

    def test_an_actionable_day_says_how_many(self):
        e = brief_to_event(_brief(actions=["carry pays 20%"]))
        assert "1 to act on" in e.title
        assert "carry pays 20%" in e.body

    def test_a_failure_is_not_disguised_as_a_quiet_day(self):
        e = brief_to_event(_brief(error="network down"))
        assert "failed" in e.title

    def test_the_body_carries_the_reasoning(self):
        body = brief_to_event(_brief(notes=["oil fell hard"])).body
        assert "oil fell hard" in body
        assert "S&P 500" in body


class TestHtmlPage:
    def test_it_is_a_self_contained_page(self, tmp_path):
        html = write_html(_brief(), tmp_path / "b.html").read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>")
        assert "viewport" in html
        assert "S&amp;P 500" in html, "market names must be HTML-escaped"
        assert "http://" not in html and "https://" not in html, "no external assets"

    def test_actions_are_rendered_when_present(self, tmp_path):
        html = write_html(
            _brief(actions=["do the thing"]), tmp_path / "b.html"
        ).read_text(encoding="utf-8")
        assert "do the thing" in html
