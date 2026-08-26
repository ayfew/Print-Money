"""The calendar feed: valid iCalendar, idempotent on rerun, and bilingual.

The event body is assembled from the brief's numbers rather than from its English
sentences, so the Thai and English calendars are two renderings of one dataset
rather than a translation of prose. These tests hold both to the same standard.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from printmoney.research.brief import Brief, MarketLine
from printmoney.research.export import brief_to_event, write_html, write_ics
from printmoney.research.i18n import MARKET_TH

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)


def _line(symbol="SPY", name="S&P 500", day=0.004, z=1.0, month=0.03):
    return MarketLine(
        symbol=symbol, name=name, last=760.0, day=day, week=0.01, month=month,
        year=0.18, vol_annual=0.13, zscore=z, intraday_share=0.4,
    )


def _brief(*, actions=(), lines=None, at=NOW, error="", carry=None, loaded=None):
    lines = list(lines) if lines is not None else [_line(), _line("GLD", "Gold", -0.011)]
    return Brief(
        generated_at=at,
        lines=lines,
        actions=list(actions),
        observations=["something happened"],
        carry=carry,
        error=error,
        requested=len(lines),
        loaded=len(lines) if loaded is None else loaded,
    )


def unfold(text: str) -> str:
    """Undo RFC 5545 line folding so content can be matched as written."""
    return text.replace("\r\n ", "")


# --------------------------------------------------------------------------- #
class TestCalendarFile:
    def test_line_endings_are_crlf_and_nothing_else(self, tmp_path):
        """Windows text mode turns \\r\\n into \\r\\r\\n and the file stops parsing."""
        raw = write_ics(_brief(), tmp_path / "cal.ics").read_bytes()
        assert b"\r\n" in raw
        stripped = raw.replace(b"\r\n", b"")
        assert b"\r" not in stripped and b"\n" not in stripped

    def test_thai_survives_folding_without_splitting_a_character(self, tmp_path):
        """Folding counts bytes but must cut on character boundaries.

        Thai is three bytes per character; a byte-indexed cut would leave half a
        character on each line and the file would not decode.
        """
        raw = write_ics(_brief(), tmp_path / "cal.ics", lang="th").read_bytes()
        text = raw.decode("utf-8")                      # would raise if split badly
        assert all(len(l.encode()) <= 75 for l in text.split("\r\n"))
        assert "ช้างขาว" in unfold(text)

    def test_it_is_a_complete_calendar(self, tmp_path):
        text = write_ics(_brief(), tmp_path / "cal.ics").read_text(encoding="utf-8")
        assert text.startswith("BEGIN:VCALENDAR")
        assert text.rstrip().endswith("END:VCALENDAR")
        assert text.count("BEGIN:VEVENT") == text.count("END:VEVENT") == 1
        assert "DTSTART;VALUE=DATE:20260826" in text

    def test_rerunning_the_same_day_replaces_rather_than_duplicates(self, tmp_path):
        path = tmp_path / "cal.ics"
        write_ics(_brief(lines=[_line("USO", "Oil", -0.011)]), path)
        write_ics(_brief(lines=[_line("USO", "Oil", -0.099)]), path)
        text = unfold(path.read_text(encoding="utf-8"))
        assert text.count("BEGIN:VEVENT") == 1
        assert "-9.90%" in text, "the rewrite should carry the newer numbers"
        assert "-1.10%" not in text

    def test_a_new_day_is_appended_to_the_history(self, tmp_path):
        path = tmp_path / "cal.ics"
        write_ics(_brief(), path)
        write_ics(_brief(at=NOW + timedelta(days=1)), path)
        assert path.read_text(encoding="utf-8").count("BEGIN:VEVENT") == 2

    def test_special_characters_are_escaped(self, tmp_path):
        """Commas and semicolons are field separators in iCalendar."""
        broken = _brief(error="gold, silver; oil all failed", loaded=0)
        text = unfold(write_ics(broken, tmp_path / "cal.ics").read_text(encoding="utf-8"))
        assert "gold\\, silver\\; oil all failed" in text


class TestLanguages:
    def test_thai_is_the_default(self):
        assert brief_to_event(_brief()).title == "ช้างขาว: วันนี้ไม่มีอะไร"

    def test_english_is_still_available(self):
        assert brief_to_event(_brief(), "en").title == "printmoney: nothing today"

    def test_calendar_is_named_for_the_language(self, tmp_path):
        th = unfold(write_ics(_brief(), tmp_path / "a.ics", lang="th").read_text(encoding="utf-8"))
        en = unfold(write_ics(_brief(), tmp_path / "b.ics", lang="en").read_text(encoding="utf-8"))
        assert "X-WR-CALNAME:ช้างขาว" in th
        assert "X-WR-CALNAME:printmoney" in en

    def test_market_names_are_translated(self):
        body = brief_to_event(_brief(lines=[_line("USO", "Oil", -0.05)])).body
        assert MARKET_TH["USO"] in body
        assert "Oil" not in body

    def test_an_unknown_symbol_keeps_its_english_name(self):
        body = brief_to_event(_brief(lines=[_line("ZZZZ", "Something New", -0.05)])).body
        assert "Something New" in body

    def test_an_unknown_language_falls_back_rather_than_crashing(self):
        assert brief_to_event(_brief(), "de").title == "ช้างขาว: วันนี้ไม่มีอะไร"


class TestEventContent:
    def test_an_actionable_day_says_how_many(self):
        e = brief_to_event(_brief(actions=["carry pays 20%"]))
        assert "1" in e.title and "ควรทำ" in e.title
        assert "carry pays 20%" in e.body

    def test_a_failure_is_not_disguised_as_a_quiet_day(self):
        """The bug this guards: a run that fetched nothing reported 'nothing today'."""
        e = brief_to_event(_brief(error="network down", loaded=0))
        assert "ดึงข้อมูลไม่ได้" in e.title
        assert "ไม่มีอะไร" not in e.title
        assert "network down" in e.body

    def test_zero_coverage_is_a_failure_even_without_an_exception(self):
        e = brief_to_event(Brief(generated_at=NOW, lines=[], requested=24, loaded=0))
        assert "ดึงข้อมูลไม่ได้" in e.title

    def test_the_body_carries_the_numbers(self):
        body = brief_to_event(_brief(lines=[_line("USO", "Oil", -0.0336, month=0.0241)])).body
        assert "-3.36%" in body
        assert "+2.4%" in body

    def test_stretched_markets_are_flagged_with_their_caveat(self):
        body = brief_to_event(_brief(lines=[_line("BTC-USD", "Bitcoin", 0.004, z=3.1)])).body
        assert MARKET_TH["BTC-USD"] in body
        assert "+3.1" in body
        assert "ไม่ใช่สัญญาณ" in body, "stretch must be labelled a fact, not a signal"

    def test_a_calm_market_is_not_flagged_as_stretched(self):
        body = brief_to_event(_brief(lines=[_line("SPY", "S&P 500", 0.004, z=0.4)])).body
        assert "ยืดตัวผิดปกติ" not in body

    def test_carry_is_reported_against_its_threshold(self):
        low = brief_to_event(_brief(carry={"basket_net_annual": 0.032, "monthly_usd": 2.63,
                                           "capital": 1000.0})).body
        assert "ต่ำกว่าเกณฑ์" in low
        high = brief_to_event(_brief(carry={"basket_net_annual": 0.22, "monthly_usd": 18.3,
                                            "capital": 1000.0})).body
        assert "สูงกว่าเกณฑ์" in high


class TestHtmlPage:
    def test_it_is_a_self_contained_page(self, tmp_path):
        html = write_html(_brief(), tmp_path / "b.html").read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>")
        assert "viewport" in html
        assert "http://" not in html and "https://" not in html, "no external assets"

    def test_market_names_are_escaped_and_translated(self, tmp_path):
        html = write_html(
            _brief(lines=[_line("SPY", "S&P 500")]), tmp_path / "b.html", lang="en"
        ).read_text(encoding="utf-8")
        assert "S&amp;P 500" in html

    def test_the_thai_page_declares_its_language(self, tmp_path):
        html = write_html(_brief(), tmp_path / "b.html", lang="th").read_text(encoding="utf-8")
        assert 'lang="th"' in html
        assert "ช้างขาว" in html

    def test_actions_are_rendered_when_present(self, tmp_path):
        html = write_html(
            _brief(actions=["do the thing"]), tmp_path / "b.html"
        ).read_text(encoding="utf-8")
        assert "do the thing" in html


class TestRiskSection:
    """Danger is reported; direction is not."""

    def _hot(self, symbol="USO", name="Oil", pct=0.93, vol=0.48, dd=-0.175):
        line = _line(symbol, name)
        line.vol_percentile = pct
        line.vol_annual = vol
        line.drawdown = dd
        return line

    def test_a_market_hot_by_its_own_history_is_flagged(self):
        body = brief_to_event(_brief(lines=[self._hot()])).body
        assert MARKET_TH["USO"] in body
        assert "48%" in body and "93%" in body and "-17.5%" in body

    def test_the_flag_carries_its_evidence(self):
        body = brief_to_event(_brief(lines=[self._hot()])).body
        assert "r = +0.76" in body
        assert "ไม่ได้บอกว่าจะขึ้นหรือลง" in body, "must refuse to imply a direction"

    def test_a_market_calm_by_its_own_history_is_not_flagged(self):
        body = brief_to_event(_brief(lines=[self._hot(pct=0.10)])).body
        assert "ระวัง" not in body

    def test_high_absolute_volatility_alone_is_not_danger(self):
        """Bitcoin at 38% is ordinary for Bitcoin; bonds at 15% would not be."""
        calm_but_volatile = self._hot("BTC-USD", "Bitcoin", pct=0.15, vol=0.38)
        assert "ระวัง" not in brief_to_event(_brief(lines=[calm_but_volatile])).body

    def test_low_absolute_volatility_can_still_be_danger(self):
        quiet_but_stretched = self._hot("XLP", "Consumer staples", pct=0.86, vol=0.15)
        body = brief_to_event(_brief(lines=[quiet_but_stretched])).body
        assert MARKET_TH["XLP"] in body
