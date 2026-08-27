"""One morning, end to end, with every source wired in and none of them real.

The unit tests each hold one module honest. This file holds the *seams* honest,
which is where the bugs in this project have actually lived: a translator
rebound to a table, a snapshot overwritten by a fixture, a count that read nine
in one section and six in the next, a funding spike promoted to a dollar figure
in the brief. None of those were visible from inside the module that caused
them.

Everything here is stubbed. No network, no clock dependence, no cached bars -
so a failure means the wiring broke rather than that an exchange was down.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from printmoney.research import contamination as C
from printmoney.research import feeds as F
from printmoney.research import macro as M
from printmoney.research import morning as mr
from printmoney.research import scorecard as sc
from printmoney.research import sources
from printmoney.research.brief import Brief, MarketLine
from printmoney.research.data import Bar, Series
from printmoney.research.decide import decide
from printmoney.research.events import Event, Impact
from printmoney.research.export import brief_to_event, write_html, write_ics
from printmoney.research.graph import build as build_graph
from printmoney.research.graphview import write_graph_html
from printmoney.research.i18n import render_note

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


# --------------------------------------------------------------------------- #
def _line(symbol, name, *, day=0.004, pctile=0.5, vol=0.15):
    return MarketLine(symbol=symbol, name=name, last=100.0, day=day, week=0.01,
                      month=0.02, year=0.10, vol_annual=vol, zscore=0.8,
                      intraday_share=0.4, vol_percentile=pctile, drawdown=-0.03)


def _brief(**over):
    lines = over.pop("lines", None) or [
        _line("SPY", "S&P 500", day=0.004, pctile=0.55),
        _line("GLD", "Gold", day=-0.021, pctile=0.92, vol=0.29),
        _line("USO", "Oil", day=-0.046, pctile=0.88, vol=0.48),
        _line("UUP", "Dollar", day=0.006, pctile=0.10, vol=0.07),
        _line("BTC-USD", "Bitcoin", day=0.012, pctile=0.81, vol=0.52),
        _line("TLT", "US long bonds", day=0.011, pctile=0.12, vol=0.11),
    ]
    kw = dict(generated_at=NOW, lines=lines, requested=len(lines),
              loaded=len(lines), carry={"basket_net_annual": 0.046,
                                        "monthly_usd": 3.85, "capital": 1000.0})
    kw.update(over)
    return Brief(**kw)


def _feed(key, values, *, source="treasury", unit="%"):
    return F.Feed(key=key, name=key, source=source, unit=unit,
                  lines=[F.Line(day=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                                value=v) for i, v in enumerate(values)])


def _quiet_then(key, move, **kw):
    """Flat for months, then one real move - so the driver gate fires."""
    vals = [4.0 + 0.001 * (i % 3) for i in range(120)]
    vals.append(vals[-1] + move)
    return _feed(key, vals, **kw)


def _feeds():
    return {
        "effr": _quiet_then("effr", 0.0, source="nyfed"),
        "ust2y": _quiet_then("ust2y", -0.07),
        "ust10y": _quiet_then("ust10y", -0.06),
        "real10y": _quiet_then("real10y", 0.08),
        "vix": _quiet_then("vix", -0.40, source="cboe", unit="pts"),
        "curve": _quiet_then("curve", 0.01, source="curve"),
    }


def _links():
    return M.Table(span="test", links=[
        M.Link(feed="real10y", symbol="GLD", r=-0.30, n=659),
        M.Link(feed="ust10y", symbol="USO", r=+0.31, n=659),
        M.Link(feed="ust2y", symbol="UUP", r=+0.43, n=659),
        M.Link(feed="vix", symbol="BTC-USD", r=-0.34, n=750),
        M.Link(feed="ust10y", symbol="TLT", r=-0.91, n=659),      # arithmetic
    ])


def _impacts():
    return {"fomc": Impact(kind="fomc", ratio=1.163, tstat=3.68, events=44,
                           markets_bigger=21, markets=24,
                           by_market={"UUP": 1.58, "GLD": 1.41, "SPY": 1.20,
                                      "BTC-USD": 1.00, "TLT": 0.99})}


class _Spread:
    def __init__(self, symbol, lo, hi, long_v="gate", short_v="binance",
                 settlements=60):
        self.symbol, self.long_venue, self.short_venue = symbol, long_v, short_v
        self.long_annual, self.short_annual = lo, hi
        self.settlements = settlements
        self.snapshot_annual = hi - lo

    @property
    def spread_annual(self):
        return self.short_annual - self.long_annual

    @property
    def verified(self):
        return self.settlements >= 20


class _Venues:
    def __init__(self, spreads):
        self.spreads = spreads
        self.reachable = ['stub']
        self.failed = {}


def _decision(**over):
    # Pop the brief BEFORE merging the rest, or it lands in kw as well and
    # decide() receives it twice. Latent since this file was written; the first
    # test to pass brief= found it.
    brief = over.pop("brief", None) or _brief()
    kw = dict(events=[Event(day=TODAY.isoformat(), kind="fomc",
                            name="Fed rate decision (FOMC)", source="x",
                            note="14:00 New York")],
              impacts=_impacts(), feeds=_feeds(), links=_links(),
              today=TODAY, capital=1000.0)
    kw.update(over)
    return decide(brief, **kw)


# --------------------------------------------------------------------------- #
class TestTheWholeMorning:
    def test_every_section_fills_when_every_source_is_present(self):
        d = _decision(venues=_Venues([_Spread("SOL", 0.013, 0.30)]))
        assert d.focus is not None
        assert d.watch and d.avoid and d.ignore and d.context and d.why

    def test_no_section_contradicts_another_on_a_count(self):
        """Regression: the focus said nine and the section below it said six."""
        d = _decision()
        hot = [n for n in d.avoid if n.key == "avoid_hot"]
        if hot and d.focus.key == "focus_careful":
            assert d.focus.params["n"] == hot[0].params["n"]

    def test_every_note_in_every_section_renders_in_both_languages(self):
        d = _decision(venues=_Venues([_Spread("SOL", 0.013, 0.30)]))
        names = {"SPY": "S&P 500", "GLD": "Gold", "USO": "Oil", "UUP": "Dollar",
                 "BTC-USD": "Bitcoin", "TLT": "US long bonds"}
        for note in d.notes:
            for lang in ("th", "en"):
                text = render_note(note, lang, names)
                assert text.strip(), (note.key, lang)
                assert "{" not in text and "}" not in text, (note.key, lang)

    def test_every_note_cites_a_source_on_the_allowlist(self):
        d = _decision(venues=_Venues([_Spread("SOL", 0.013, 0.30)]))
        for note in d.notes:
            assert sources.get(note.source).tier in (1, 2, 3, 4)

    def test_the_whole_brief_still_reads_inside_two_minutes(self):
        d = _decision(venues=_Venues([_Spread("SOL", 0.013, 0.30)]))
        body = brief_to_event(_brief(), "th", decision=d,
                              score={"n": 5148, "rate": 0.806}).body
        from printmoney.research.decide import read_seconds

        assert read_seconds(body) <= 150

    def test_the_calendar_and_the_page_agree_on_the_verdict(self, tmp_path):
        b, d = _brief(), _decision()
        title = brief_to_event(b, "th", decision=d).title
        html = write_html(_Morning(b, d), tmp_path / "b.html",
                          lang="th").read_text("utf-8")
        focus = render_note(d.focus, "th", {l.symbol: l.name for l in b.lines})
        assert focus[:40] in html
        assert title.startswith("ช้างขาว")


class _Morning:
    """The shape export expects, without running the network."""

    def __init__(self, brief, decision, score=None):
        self.brief, self.decision, self.score = brief, decision, score


class TestVenueSpreadsReachTheBriefHonestly:
    def test_a_verified_spread_becomes_something_to_watch(self):
        d = _decision(venues=_Venues([_Spread("SOL", 0.013, 0.30)]))
        watch = [n for n in d.watch if n.key == "watch_venue_spread"]
        assert watch and watch[0].params["symbol"] == "SOL"

    def test_the_dollar_figure_is_on_half_the_capital_not_all_of_it(self):
        """Only one leg earns, and the account is split across two venues."""
        d = _decision(venues=_Venues([_Spread("SOL", 0.0, 0.24)]))
        note = [n for n in d.watch if n.key == "watch_venue_spread"][0]
        # 1000 / 2 * 0.24 / 12 = 10.00
        assert note.params["monthly"] == "$10.00"

    def test_a_spread_too_small_to_pay_for_two_accounts_is_demoted(self):
        d = _decision(venues=_Venues([_Spread("SOL", 0.0, 0.21)]),
                      capital=100.0)
        assert not [n for n in d.watch if n.key == "watch_venue_spread"]
        assert [n for n in d.ignore if n.key == "ignore_venue_small"]

    def test_a_spread_below_the_bar_is_not_mentioned_at_all(self):
        d = _decision(venues=_Venues([_Spread("BTC", 0.048, 0.073)]))
        assert not [n for n in d.notes if "venue" in n.key]

    def test_no_venues_at_all_costs_nothing_else(self):
        d = _decision(venues=None)
        assert d.focus is not None and d.context


class TestAttributionGuardsSurviveTheWiring:
    """Added after mutation testing: the suite passed with both of these
    disabled, because the fixtures happened to satisfy them. A guard that is
    only exercised by accident is a guard that a refactor can quietly remove."""

    def test_a_driver_that_barely_twitched_explains_nothing(self):
        feeds = _feeds()
        feeds["real10y"] = _quiet_then("real10y", 0.0001)   # a nudge, not a move
        d = _decision(feeds=feeds)
        assert not [n for n in d.why if n.params.get("driver") == "real10y"]

    def test_a_negatively_linked_driver_moving_the_wrong_way_explains_nothing(self):
        # GLD is down on the day and moves *against* real yields, so a real
        # yield that also fell cannot be the reason it fell.
        feeds = _feeds()
        feeds["real10y"] = _quiet_then("real10y", -0.08)
        d = _decision(feeds=feeds)
        assert not [n for n in d.why if n.params.get("driver") == "real10y"]

    def test_a_positively_linked_driver_moving_the_wrong_way_explains_nothing(self):
        """The other half of the sign check, and the one a mutation slipped past.

        Oil is down on the day and moves *with* the ten-year, so a ten-year that
        went up is not why it fell. The previous test only ever exercised the
        negative-correlation branch, which left this one covered by nothing.
        """
        feeds = _feeds()
        feeds["ust10y"] = _quiet_then("ust10y", +0.09)
        d = _decision(feeds=feeds)
        assert not [n for n in d.why
                    if n.params.get("driver") == "ust10y"
                    and n.symbols == ("USO",)]

    def test_the_right_driver_moving_the_right_way_does_explain_it(self):
        """The control, so the two tests above cannot pass by explaining nothing."""
        feeds = _feeds()
        feeds["real10y"] = _quiet_then("real10y", +0.08)
        d = _decision(feeds=feeds)
        assert [n for n in d.why if n.params.get("driver") == "real10y"]


class TestDegradation:
    """Each source can vanish without taking the morning with it."""

    def test_without_feeds_there_is_no_macro_and_no_attribution(self):
        d = _decision(feeds=None)
        assert d.context == [] and d.why == []
        assert d.focus is not None

    def test_without_measured_links_readings_print_but_explain_nothing(self):
        d = _decision(links=M.Table())
        assert d.context and d.why == []

    def test_without_impacts_the_event_is_not_mentioned(self):
        d = _decision(impacts={})
        assert d.watch == [] or all("event" not in n.key for n in d.watch)

    def test_with_nothing_but_prices_it_still_produces_a_verdict(self):
        d = decide(_brief(), today=TODAY)
        assert d.focus is not None
        assert render_note(d.focus, "th").strip()

    def test_broken_data_refuses_to_have_an_opinion_at_all(self):
        b = _brief(loaded=1)
        b.requested = 24
        d = decide(b, feeds=_feeds(), links=_links(), today=TODAY,
                   venues=_Venues([_Spread("SOL", 0.0, 0.9)]))
        assert d.focus.key == "focus_broken"
        assert not d.watch and not d.avoid and not d.context and not d.why


class TestArtefacts:
    def test_the_calendar_is_valid_icalendar_with_everything_attached(self, tmp_path):
        b, d = _brief(), _decision(venues=_Venues([_Spread("SOL", 0.013, 0.30)]))
        raw = write_ics(_Morning(b, d, {"n": 5148, "rate": 0.806}),
                        tmp_path / "c.ics", lang="th").read_bytes()
        assert raw.startswith(b"BEGIN:VCALENDAR")
        assert raw.replace(b"\r\n", b"").count(b"\n") == 0
        assert raw.count(b"BEGIN:VEVENT") == 1

    def test_the_page_leaks_no_template_and_needs_no_network(self, tmp_path):
        b, d = _brief(), _decision()
        html = write_html(_Morning(b, d), tmp_path / "b.html",
                          lang="th").read_text("utf-8")
        assert "<script src" not in html
        for leak in ("{esc(", "{t(", "{rows}", "None</"):
            assert leak not in html

    def test_the_graph_carries_the_measured_and_the_arithmetic_apart(self, tmp_path):
        g = build_graph(links=_links(), impacts=_impacts())
        kinds = {(e.src, e.dst): e.kind for e in g.edges}
        assert kinds[("real10y", "GLD")] == "measured"
        assert kinds[("ust10y", "TLT")] == "arithmetic"
        html = write_graph_html(g, tmp_path / "g.html").read_text("utf-8")
        assert "__DATA__" not in html and '"id": "GLD"' in html

    def test_the_graph_can_explain_a_market_in_the_brief(self):
        g = build_graph(links=_links(), impacts=_impacts())
        chains = g.why("UUP", depth=4)
        assert chains
        assert all(len(c) <= 4 for c in chains)


class TestPublishedEvidenceStaysConsistent:
    """The committed numbers the brief quotes at a reader."""

    def test_the_scorecard_headline_matches_the_committed_summary(self):
        summary = sc.load_summary()
        assert summary is not None
        head = sc.headline(summary)
        assert head and head["n"] >= 1000
        assert 0.5 < head["rate"] < 1.0

    def test_the_indicator_sweep_found_nothing_and_says_so(self):
        from printmoney.util import DATA_DIR

        blob = json.loads((DATA_DIR / "indicators.json").read_text("utf-8"))
        assert blob["tested"] >= 100
        assert blob["survivors"] == []
        assert blob["buy_and_hold"] > blob["null_high"]

    def test_the_contamination_result_still_shows_the_gap(self):
        from printmoney.util import DATA_DIR

        blob = json.loads((DATA_DIR / "contamination.json").read_text("utf-8"))
        assert blob["before_cutoff"]["rate"] - blob["after_cutoff"]["rate"] > 0.4

    def test_every_committed_artefact_is_valid_json(self):
        from printmoney.util import DATA_DIR

        for name in ("impacts.json", "scorecard.json", "macro.json",
                     "indicators.json", "contamination.json", "snapshot.json"):
            path = DATA_DIR / name
            assert path.exists(), name
            assert json.loads(path.read_text("utf-8")), name


class TestNothingWritesWhereItShouldNot:
    def test_a_full_morning_touches_only_the_paths_it_was_given(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(mr, "SNAPSHOT", tmp_path / "snapshot.json")
        monkeypatch.setattr(sc, "CLAIMS", tmp_path / "claims.jsonl")
        monkeypatch.setattr(mr, "build_brief", lambda **kw: _brief())
        monkeypatch.setattr(mr.ev, "load_impacts", _impacts)
        monkeypatch.setattr(mr.ev, "upcoming", lambda **kw: [])
        monkeypatch.setattr(mr.feeds, "load", lambda **kw: _feeds())
        monkeypatch.setattr(mr.feeds, "curve_spread", lambda f: None)
        monkeypatch.setattr(mr.macro, "load", _links)

        m = mr.run(include_carry=False, today=date(2020, 6, 1))
        assert m.ok
        assert (tmp_path / "snapshot.json").exists()
        assert (tmp_path / "claims.jsonl").exists()
        assert m.decision.context and m.decision.why

    def test_a_rerun_changes_neither_the_record_nor_the_diff(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(mr, "SNAPSHOT", tmp_path / "snapshot.json")
        monkeypatch.setattr(sc, "CLAIMS", tmp_path / "claims.jsonl")
        monkeypatch.setattr(mr, "build_brief", lambda **kw: _brief())
        monkeypatch.setattr(mr.ev, "load_impacts", dict)
        monkeypatch.setattr(mr.feeds, "load", lambda **kw: {})
        monkeypatch.setattr(mr.macro, "load", M.Table)

        first = mr.run(include_carry=False, today=date(2020, 6, 1))
        second = mr.run(include_carry=False, today=date(2020, 6, 1))
        assert first.recorded > 0 and second.recorded == 0
        assert [n.key for n in first.decision.changed] == \
               [n.key for n in second.decision.changed]


class TestContaminationHarnessStillGuardsItself:
    def test_the_quiz_cannot_be_beaten_by_answering_one_way(self):
        s = Series(symbol="M", name="M", bars=[
            Bar(ts=1_500_000_000 + i * 86_400, open=100 + (i % 40),
                high=101 + (i % 40), low=99 + (i % 40),
                close=100 + (i % 40) * (1 if (i // 80) % 2 else -1),
                volume=1e6, raw_close=100.0) for i in range(900)])
        qs = C.build([s], n=20)
        if qs:
            rep = C.grade(qs, {q.qid: "up" for q in qs}, cutoff="1900-01-01")
            assert C.Report.rate(rep.answered()) == pytest.approx(0.5)


class TestTheOnePage:
    """Three views, one file, and every name in the brief a link into the map."""

    def _page(self, tmp_path, lang="th"):
        from printmoney.research.site import write_site

        b = _brief()
        d = _decision(venues=_Venues([_Spread("SOL", 0.013, 0.30)]))
        g = build_graph(links=_links(), impacts=_impacts())
        return write_site(_Morning(b, d, {"n": 5148, "rate": 0.806}), g,
                          tmp_path / "index.html", lang=lang).read_text("utf-8")

    def test_it_is_one_self_contained_file(self, tmp_path):
        html = self._page(tmp_path)
        assert "<script src" not in html
        assert '<link rel="stylesheet"' not in html
        assert "__PAYLOAD__" not in html and "__TITLE__" not in html

    def test_all_three_views_are_present(self, tmp_path):
        html = self._page(tmp_path)
        for view in ('id="v-today"', 'id="v-map"', 'id="v-evidence"'):
            assert view in html

    def test_the_graph_index_is_built_before_the_brief_renders(self, tmp_path):
        """Regression: renderToday ran first and read an undefined index."""
        html = self._page(tmp_path)
        assert html.index("const byId") < html.index("function renderToday")

    def test_the_brief_carries_node_ids_so_names_become_links(self, tmp_path):
        html = self._page(tmp_path)
        assert '"nodes"' in html
        # the chip handler and the table both need a jump target
        assert 'data-go="' in html or "data-go" in html

    def test_the_evidence_view_reads_the_committed_artefacts(self, tmp_path):
        html = self._page(tmp_path)
        for key in ("scorecard", "indicators", "contamination", "impacts", "macro"):
            assert f'"{key}"' in html

    def test_both_languages_build_and_differ(self, tmp_path):
        th = self._page(tmp_path, "th")
        en = self._page(tmp_path / "en", "en")
        assert th != en
        assert "วันนี้" in th and "today" in en

    def test_a_morning_with_no_decision_still_produces_a_page(self, tmp_path):
        from printmoney.research.site import write_site

        g = build_graph(links=_links())
        html = write_site(_Morning(_brief(), None), g,
                          tmp_path / "i.html").read_text("utf-8")
        assert "__PAYLOAD__" not in html and '"focus": null' in html

    def test_the_calendar_points_at_the_page(self, tmp_path):
        from printmoney.research.export import SITE_URL

        raw = write_ics(_Morning(_brief(), _decision()), tmp_path / "c.ics",
                        lang="th").read_bytes().decode("utf-8")
        assert f"URL:{SITE_URL}" in raw.replace("\r\n ", "")
        assert SITE_URL in raw.replace("\r\n ", "")


class TestALostSectionIsNeverSilent:
    """A green run that quietly published a smaller brief is the failure mode
    this project keeps having to design against - most recently when the cloud
    runner had no ccxt and dropped the venue section without a word."""

    def _run(self, monkeypatch, tmp_path, carry):
        monkeypatch.setattr(mr, "SNAPSHOT", tmp_path / "s.json")
        monkeypatch.setattr(sc, "CLAIMS", tmp_path / "c.jsonl")
        monkeypatch.setattr(mr, "build_brief", lambda **kw: _brief(carry=carry))
        monkeypatch.setattr(mr.ev, "load_impacts", dict)
        monkeypatch.setattr(mr.feeds, "load", lambda **kw: {})
        monkeypatch.setattr(mr.macro, "load", M.Table)
        monkeypatch.setattr(mr, "decide", lambda b, **kw: decide(b, today=TODAY))
        # include_carry=True also reaches for the exchanges. Stub the whole
        # module out: this file promises no network, and a test that quietly
        # calls five venues takes four minutes and fails on a train.
        import printmoney.carry.venues as venue_mod

        monkeypatch.setattr(venue_mod, "available", lambda: True)
        monkeypatch.setattr(venue_mod, "scan",
                            lambda *a, **k: _Venues([]))
        return mr.run(include_carry=True, today=date(2020, 6, 1), persist=False)

    def test_a_blocked_exchange_is_reported_not_swallowed(self, monkeypatch,
                                                          tmp_path):
        m = self._run(monkeypatch, tmp_path,
                      {"error": "HTTPStatusError: 451 restricted location"})
        assert m.ok                                   # the brief still ships
        assert any("carry" in w for w in m.warnings)  # but the loss is stated

    def test_a_healthy_carry_produces_no_warning(self, monkeypatch, tmp_path):
        m = self._run(monkeypatch, tmp_path,
                      {"basket_net_annual": 0.046, "monthly_usd": 3.85,
                       "capital": 1000.0})
        assert not [w for w in m.warnings if "carry" in w]


class TestACarryNumberAlwaysNamesItsExchange:
    """Funding differs between venues and the cloud runner falls back to
    whichever answers, so a rate with no venue on it cannot be compared with
    yesterday's. The venue was being recorded and not printed."""

    def _body(self, carry, lang="th"):
        b = _brief(carry=carry)
        return brief_to_event(b, lang, decision=_decision(brief=b)).body

    def test_the_calendar_names_the_venue(self):
        body = self._body({"basket_net_annual": 0.037, "monthly_usd": 3.09,
                           "capital": 1000.0, "venue": "bybit"})
        assert "bybit" in body

    def test_english_names_it_too(self):
        body = self._body({"basket_net_annual": 0.037, "monthly_usd": 3.09,
                           "capital": 1000.0, "venue": "gate"}, lang="en")
        assert "gate" in body

    def test_a_missing_venue_is_marked_rather_than_left_blank(self):
        body = self._body({"basket_net_annual": 0.037, "monthly_usd": 3.09,
                           "capital": 1000.0})
        assert "{venue}" not in body and "?" in body

    def test_the_page_carries_it_as_well(self, tmp_path):
        from printmoney.research.site import write_site

        b = _brief(carry={"basket_net_annual": 0.037, "monthly_usd": 3.09,
                          "capital": 1000.0, "venue": "bitget"})
        g = build_graph(links=_links())
        html = write_site(_Morning(b, _decision(brief=b)), g,
                          tmp_path / "i.html").read_text("utf-8")
        assert "bitget" in html
