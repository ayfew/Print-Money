"""Full private universe rendering, literal evidence, and executable control rules."""
import copy
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest


FIELDS = ("price", "fundamentals", "valuation", "earnings", "news", "risk")


def render(report, lang="en"):
    try:
        from printmoney.research.stockview import render_universe_html
    except ImportError:
        pytest.fail("full private universe renderer is missing")
    return render_universe_html(report, lang)


def card(symbol="GOOG", **extra):
    result = {"symbol": symbol, "name": "Alphabet", "indices": ["sp500", "nasdaq100"],
              "sector": "Communication Services", "status": "watch", "last": 100, "currency": "USD",
              "annual_earnings_pe": None, "sources": [],
              "data_quality": {field: {"status": "missing", "reason": "fixture gap"} for field in FIELDS}}
    result.update(extra)
    return result


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows, self.links, self.controls, self.scripts = [], [], {}, []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "data-symbol" in attrs:
            self.rows.append(attrs)
        if tag == "a":
            self.links.append(attrs)
        if "id" in attrs:
            self.controls[attrs["id"]] = attrs
        if tag == "script":
            self.scripts.append(attrs)


class VisibleTable(HTMLParser):
    """Read scan cells while excluding attributes and collapsed evidence content."""
    def __init__(self, html):
        super().__init__()
        self.headings, self.rows = [], []
        self.row, self.cell, self.heading, self.details_depth = None, None, None, 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "data-symbol" in attrs:
            self.row = []
            self.rows.append(self.row)
        if tag == "td" and self.row is not None:
            self.cell = []
            self.row.append(self.cell)
        if tag == "th" and attrs.get("scope") == "col":
            self.heading = []
            self.headings.append(self.heading)
        if tag == "details":
            self.details_depth += 1

    def handle_data(self, text):
        if not self.details_depth and self.cell is not None:
            self.cell.append(text)
        if self.heading is not None:
            self.heading.append(text)

    def handle_endtag(self, tag):
        if tag == "td":
            self.cell = None
        if tag == "tr":
            self.row = None
        if tag == "th":
            self.heading = None
        if tag == "details":
            self.details_depth -= 1

    def cell_containing_heading(self, needle, row=0):
        headings = ["".join(parts) for parts in self.headings]
        matches = [index for index, heading in enumerate(headings) if needle.lower() in heading.lower()]
        assert len(matches) == 1, f"Expected one visible column for {needle!r}, got {headings!r}"
        assert len(self.rows[row]) == len(headings)
        return " ".join(self.rows[row][matches[0]])


def test_scan_columns_show_precise_returns_liquidity_annual_period_basis_earnings_and_reasons():
    attack = '<script>alert("reason")</script>'
    item = card(day_return=0.0123456789012345, month_return=-0.0987654321098765,
                return_basis="adjusted", dollar_turnover=123456789.12345679,
                annual_facts={"net_income": 1000.123456789, "operating_cashflow": -0.0123456789,
                    "annual_start": "2025-07-01", "annual_end": "2026-06-30", "currency": "USD",
                    "form": "10-K", "basis": "US-GAAP"},
                earnings={"date": "2026-10-28", "verification": "verified", "source_id": "issuer"},
                reasons=["earnings_blackout", attack])
    table = VisibleTable(render({"evaluated": [item]}))
    for heading, value in (("1d return", item["day_return"]), ("1m return", item["month_return"]),
                           ("20-session", item["dollar_turnover"]), ("annual net income", 1000.123456789),
                           ("annual OCF", -0.0123456789)):
        assert str(value) in table.cell_containing_heading(heading)
    assert "adjusted" in table.cell_containing_heading("1d return")
    for heading in ("annual net income", "annual OCF"):
        text = table.cell_containing_heading(heading)
        assert all(value in text for value in ("2025-07-01", "2026-06-30", "USD", "10-K", "US-GAAP"))
    assert "2026-10-28" in table.cell_containing_heading("earnings")
    assert "verified" in table.cell_containing_heading("earnings")
    assert "earnings_blackout" in table.cell_containing_heading("reasons")
    assert attack in table.cell_containing_heading("reasons")
    assert "<script>" not in render({"evaluated": [item]})


def test_scan_unknowns_and_unverified_bases_are_explicit_without_zero_substitution():
    items = [card("UNKNOWN", day_return=None, month_return=float("nan"), dollar_turnover=float("inf")),
             card("RAW", day_return=0, month_return=0, return_basis="raw_unverified_actions",
                  dollar_turnover=0, annual_facts={"net_income": 0, "operating_cashflow": 0}, reasons=[])]
    table = VisibleTable(render({"evaluated": items}))
    for heading in ("1d return", "1m return", "20-session", "annual net income", "annual OCF", "earnings", "reasons"):
        assert "unknown" in table.cell_containing_heading(heading)
    assert "raw_unverified_actions" in table.cell_containing_heading("1d return", 1)
    assert "adjusted" not in table.cell_containing_heading("1d return", 1)
    assert "0" in table.cell_containing_heading("annual net income", 1)
    assert "unknown" in table.cell_containing_heading("annual net income", 1)
    assert "none supplied" in table.cell_containing_heading("reasons", 1)


def test_annual_numeric_and_earnings_date_sort_keys_preserve_missingness():
    items = [card("KNOWN", annual_facts={"net_income": -0.0123456789012345, "operating_cashflow": 1000},
                  earnings={"date": "2026-10-28"}), card("UNKNOWN")]
    rows = Document(render({"evaluated": items})).rows
    assert rows[0]["data-net-income"] == "-0.0123456789012345"
    assert rows[0]["data-operating-cashflow"] == "1000"
    assert rows[0]["data-earnings-date"] == "2026-10-28"
    assert rows[1]["data-net-income"] == "" and rows[1]["data-operating-cashflow"] == ""
    assert rows[1]["data-earnings-date"] == "unknown"


@pytest.mark.parametrize("currency", ["EUR", None])
def test_usd_turnover_column_does_not_relabel_missing_or_other_currency(currency):
    item = card(currency=currency, dollar_turnover=123456789.12345679)
    html = render({"evaluated": [item]})
    assert Document(html).rows[0]["data-dollar-turnover"] == ""
    assert "unknown" in VisibleTable(html).cell_containing_heading("20-session")
    assert str(item["dollar_turnover"]) in html.split('<details class="evidence">', 1)[1]


def test_renders_all_520_rows_preserving_classes_and_membership_attribution():
    cards = [card(f"T{i:03}") for i in range(517)] + [card("GOOG"), card("GOOGL"), card("BRK.B")]
    report = {"evaluated": cards, "highlights": cards[:5], "membership": {
        "version": "fixture-revision", "captured_at": "2026-10-04T00:00:00Z",
        "verification": "uncertified", "completeness": "unknown", "sources": [{
            "url": "https://example.org/members", "license": "CC BY-SA 4.0", "revision": "oldid=123"}]}}
    before = copy.deepcopy(report)
    html = render(report)
    document = Document(html)
    assert len(document.rows) == 520
    assert [row["data-symbol"] for row in document.rows[-3:]] == ["GOOG", "GOOGL", "BRK.B"]
    assert all(json.loads(row["data-indices"]) == ["sp500", "nasdaq100"] for row in document.rows)
    assert html.count('<details class="evidence">') == 520
    assert "520 / 520" in html and "fixture-revision" in html and "CC BY-SA 4.0" in html
    assert "uncertified" in html and "oldid=123" in html and "completeness" in html
    assert report == before


def test_controls_are_labeled_local_and_counts_live():
    html = render({"evaluated": [card()]})
    document = Document(html)
    for identifier in ("search", "status-filter", "index-filter", "sector-filter", "gap-filter", "sort", "direction"):
        assert identifier in document.controls
        assert f'for="{identifier}"' in html
    assert document.controls["counts"]["aria-live"] == "polite"
    assert "<caption>" in html and 'scope="col"' in html
    assert all("src" not in attrs for attrs in document.scripts)
    assert "fetch(" not in html and "XMLHttpRequest" not in html and "@import" not in html
    assert "connect-src 'none'" in html


def test_details_keep_source_clocks_catalyst_proof_and_explicit_gap_reasons():
    item = card(catalyst={"date": "2026-10-20", "source_id": "issuer",
                         "verification": "verified", "description": "confirmed event"},
                reported_financials={"status": "available", "periods": [{"kind": "quarter",
                    "end": "2026-06-30", "currency": "USD", "metrics": {"revenue": {"value": 7, "unit": "million", "basis": "GAAP"}}}]},
                news={"status": "available", "context_only": True, "items": [{"title": "context headline",
                    "url": "https://example.org/headline", "published_at": "2026-10-02T12:00:00Z"}]},
                sources=[{"id": "issuer", "url": "https://example.org/release", "retrieved_at": "2026-10-03T12:00:00Z"}])
    html = render({"evaluated": [item], "collection": {"failed": 3}, "coverage_metrics": {
        "total": 1, "fields": {field: {"missing": 1} for field in FIELDS}}, "coverage": {"thai_equities": "not_connected"}})
    for expected in ("<dt>catalyst</dt>", "2026-10-20", "source_id", "verification", "retrieved_at",
                     "2026-10-03T12:00:00Z", "fixture gap", "fundamentals", "quarter", "GAAP",
                     "context headline", "context_only", "not_connected", "failed"):
        assert expected in html
    assert "annualized" not in html.lower()
    assert len(Document(html).rows) == 1


def test_all_provider_text_is_literal_and_only_safe_https_links_are_clickable():
    attack = '<script>alert("BUY")</script><img src=x onerror=alert(1)>'
    item = card(name=attack, symbol='X" onmouseover="alert(1)', sector=attack,
                indices=[attack], catalyst={"description": attack}, news={"items": [{"title": attack,
                    "url": "javascript:alert(1)"}]}, sources=[
                    {"id": attack, "url": "javascript:alert(1)", "description": attack},
                    {"id": "safe", "url": "https://example.org/report?a=1&b=2"},
                    {"id": "credential", "url": "https://user:password@example.org/"},
                    {"id": "http", "url": "http://example.org/"}])
    html = render({"evaluated": [item], "membership": {"caveats": [attack]}})
    document = Document(html)
    assert "<script>" not in html and "<img" not in html and "&lt;script&gt;" in html
    assert document.rows[0]["data-symbol"] == item["symbol"]
    assert "onmouseover" not in document.rows[0]
    assert [link["href"] for link in document.links] == ["https://example.org/report?a=1&b=2"]
    assert all(link["rel"] == "noopener noreferrer" for link in document.links)


@pytest.mark.parametrize("report", [{}, {"evaluated": []}, {"highlights": [card("OLD")]},
                                      {"evaluated": [{"symbol": "OLD", "status": "watch", "last": None}]}])
def test_earlier_or_empty_reports_remain_inspectable_without_claiming_coverage(report):
    html = render(report, "th")
    assert 'lang="th"' in html and "Sol" in html
    assert "unknown" in html and "unverified" in html
    expected = report.get("evaluated", report.get("highlights", []))
    assert len(Document(html).rows) == len(expected)


def test_numeric_unknowns_are_empty_sort_keys_and_not_zero():
    items = [card("NONE", last=None), card("NAN", last=float("nan")),
             card("INF", last=float("inf")), card("BOOL", last=True), card("ZERO", last=0)]
    rows = Document(render({"evaluated": items})).rows
    assert [row["data-last"] for row in rows] == ["", "", "", "", "0"]
    assert "NaN" not in render({"evaluated": items})


def test_optional_report_news_collection_limits_remain_visible():
    html = render({"evaluated": [card()], "news": {
        "status": "partial", "context_only": True, "warnings": ["unsupported_feed_fixture"]}})
    assert "unsupported_feed_fixture" in html
    assert "context_only" in html


def test_membership_context_retains_discovery_state_and_original_snapshot_clock():
    report = {"evaluated": [card()], "membership": {"captured_at": "2026-10-01T00:00:00Z", "verification": "uncertified"},
              "membership_context": {"status": "stale_discovery_snapshot", "official_current_certification": "unverified",
                  "evaluated_at": "2026-10-04T12:00:00Z", "snapshot_age_seconds": 302400,
                  "due_changes": [{"symbol": "FIX", "effective_date": "2026-10-02"}]}}
    before = copy.deepcopy(report)
    html = render(report)
    for expected in ("stale_discovery_snapshot", "official_current_certification", "evaluated_at", "snapshot_age_seconds",
                     "due_changes", "2026-10-01T00:00:00Z", "2026-10-04T12:00:00Z", "2026-10-02", "FIX"):
        assert expected in html
    assert report == before


def test_javascript_filter_and_numeric_sort_rules_execute_in_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable; browser-rule execution requires a local JS runtime")
    html = render({"evaluated": [card()]})
    scripts = re.findall(r'<script type="text/javascript">(.*?)</script>', html, re.S)
    assert len(scripts) == 1
    program = scripts[0] + r'''
const assert = require('node:assert/strict');
const {matches, compare} = globalThis.PrintMoneyUniverse;
const a = {symbol:'GOOG', name:'Alphabet', status:'watch', indices:['sp500','nasdaq100'], sector:'Technology', gaps:['missing','blocked'], last:'10', order:0};
const b = {...a, symbol:'GOOGL', name:'Alphabet Class A', last:'2', order:1};
const u = {...a, symbol:'EMPTY', last:'', order:2};
assert(matches(a,{search:'  alph ',status:'watch',index:'nasdaq100',sector:'Technology',gap:'blocked'}));
assert(!matches(a,{search:'BRK'}));
assert(!matches(a,{status:'avoid'}));
assert(!matches(a,{index:'nasdaq-composite'}));
assert(!matches(a,{sector:'Banks'}));
assert(!matches(a,{gap:'stale'}));
assert(matches(a,{gap:'any'}));
assert(!matches(a,{gap:'none'}));
assert(matches({...a,gaps:[]},{gap:'none'}));
assert.deepEqual([u,a,b].sort((x,y)=>compare(x,y,'last','asc')).map(x=>x.symbol),['GOOGL','GOOG','EMPTY']);
assert.deepEqual([u,a,b].sort((x,y)=>compare(x,y,'last','desc')).map(x=>x.symbol),['GOOG','GOOGL','EMPTY']);
assert.deepEqual([a,u,b].sort((x,y)=>compare(x,y,'symbol','asc')).map(x=>x.symbol),['EMPTY','GOOG','GOOGL']);
for (const key of ['day_return','month_return','dollar_turnover','net_income','operating_cashflow']) {
  const small={...a,[key]:'2.123456789012345',order:0}, large={...b,[key]:'10.123456789012345',order:1}, unknown={...u,[key]:''};
  assert.deepEqual([unknown,large,small].sort((x,y)=>compare(x,y,key,'asc')).map(x=>x.symbol),['GOOG','GOOGL','EMPTY']);
  assert.deepEqual([unknown,small,large].sort((x,y)=>compare(x,y,key,'desc')).map(x=>x.symbol),['GOOGL','GOOG','EMPTY']);
}
const early={...a,earnings_date:'2026-10-10'}, late={...b,earnings_date:'2026-10-28'}, undated={...u,earnings_date:'unknown'};
assert.deepEqual([undated,late,early].sort((x,y)=>compare(x,y,'earnings_date','asc')).map(x=>x.symbol),['GOOG','GOOGL','EMPTY']);
assert.deepEqual([undated,early,late].sort((x,y)=>compare(x,y,'earnings_date','desc')).map(x=>x.symbol),['GOOGL','GOOG','EMPTY']);
console.log('universe controls passed');
'''
    result = subprocess.run([node, "-e", program], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "universe controls passed" in result.stdout
