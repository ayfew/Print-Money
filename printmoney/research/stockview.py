"""Self-contained private full-universe view; source text is always literal."""
from __future__ import annotations

import json
import math
from datetime import date
from html import escape

from .stocks import safe_url


FIELDS = ("price", "fundamentals", "valuation", "earnings", "news", "risk")
QUALITY_STATES = ("available", "missing", "stale", "unsupported", "blocked", "unverified", "not_requested")


def _literal(value):
    return escape(str(value if value is not None else "unknown"), quote=True)


def _display(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "unknown"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value if value is not None else "unknown"


def _numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    try:
        return str(value) if math.isfinite(value) else ""
    except OverflowError:
        return ""


def _quality(card):
    supplied = card.get("data_quality") or {}
    result = {}
    for field in FIELDS:
        item = supplied.get(field) if isinstance(supplied, dict) else None
        if not isinstance(item, dict):
            item = {"status": "unverified", "reason": "Field coverage not supplied by report; no availability inferred."}
        state = item.get("status") or "unverified"
        result[field] = {"status": state, "reason": item.get("reason") or "unknown"}
    return result


def _sources(sources):
    pieces = ['<ul class="sources">']
    for source in sources or []:
        if not isinstance(source, dict):
            pieces.append("<li>" + _literal(_display(source)) + "</li>")
            continue
        url = safe_url(source.get("url"))
        label = _literal(source.get("id") or source.get("name") or "source")
        link = f'<a href="{_literal(url)}" rel="noopener noreferrer">{label}</a>' if url else label
        pieces.append("<li>" + link + " — " + _literal(_display(source)) + "</li>")
    if not sources:
        pieces.append("<li>unknown — source evidence not supplied</li>")
    pieces.append("</ul>")
    return "".join(pieces)


def _definition(values):
    return "<dl>" + "".join(f"<dt>{_literal(key)}</dt><dd>{_literal(_display(value))}</dd>"
                            for key, value in values.items()) + "</dl>"


def _annual_cell(facts, key):
    return ('<td class="annual-cell"><span>' + _literal(_numeric(facts.get(key)) or "unknown") + '</span><small>currency: '
            + _literal(facts.get("currency")) + '</small><small>period: ' + _literal(facts.get("annual_start"))
            + ' → ' + _literal(facts.get("annual_end")) + '</small><small>form: ' + _literal(facts.get("form"))
            + ' · basis: ' + _literal(facts.get("basis")) + '</small></td>')


def _earnings_date(event):
    value = event.get("date")
    try:
        return value if date.fromisoformat(value).isoformat() == value else "unknown"
    except (TypeError, ValueError):
        return "unknown"


def _details(card, quality, label):
    # Retain the established decision fields even when older reports omit them.
    keys = ("provider_symbol", "industry", "accounting_policy", "last", "currency", "quote_day", "price_basis",
            "fetched_at", "annual_facts", "annual_earnings_pe", "valuation_basis", "day_return", "month_return",
            "dollar_turnover", "return_basis", "earnings", "catalyst", "reasons", "warnings", "entry_condition", "review_conditions",
            "invalidation", "manipulation_risk", "uncertainty", "reported_financials", "news")
    values = {key: card.get(key) for key in keys}
    values.update({key: value for key, value in card.items()
                   if key not in values and key not in ("sources", "data_quality", "symbol", "name", "indices", "sector", "status")})
    pieces = ['<details class="evidence"><summary>' + _literal(label) + "</summary>",
              "<h3>Field coverage / สถานะข้อมูล</h3>", _definition(quality), _definition(values),
              "<h3>Sources and lineage / แหล่งข้อมูล</h3>", _sources(card.get("sources"))]
    for key in ("reported_financials", "news"):
        evidence = card.get(key)
        if isinstance(evidence, dict) and evidence.get("sources"):
            pieces.extend(["<h4>" + _literal(key) + " sources</h4>", _sources(evidence["sources"])])
    news = card.get("news")
    if isinstance(news, dict) and news.get("items"):
        pieces.append("<h4>Headline context — does not verify earnings or catalysts</h4><ul>")
        for item in news["items"]:
            if not isinstance(item, dict):
                continue
            title = _literal(item.get("title"))
            url = safe_url(item.get("url"))
            title = f'<a href="{_literal(url)}" rel="noopener noreferrer">{title}</a>' if url else title
            pieces.append("<li>" + title + " — " + _literal(item.get("published_at")) + "</li>")
        pieces.append("</ul>")
    pieces.append("</details>")
    return "".join(pieces)


_STYLE = """
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#101722;color:#e7edf5;font:15px/1.55 system-ui,sans-serif}
main{max-width:1600px;margin:auto;padding:28px}h1,h2,h3,h4{line-height:1.3}h1{margin-bottom:8px;color:#cde5ff}
.muted{color:#b2bed0}.context,.controls{border:1px solid #405169;border-radius:12px;background:#182334;padding:18px;margin:18px 0}
.controls{display:flex;flex-wrap:wrap;gap:14px;align-items:end}.control{display:flex;flex-direction:column;gap:4px}
input,select{font:inherit;color:inherit;background:#101722;border:1px solid #70849c;border-radius:5px;padding:7px;max-width:280px}
input:focus,select:focus,summary:focus,a:focus{outline:3px solid #77c5ff;outline-offset:3px}
.table-wrap{overflow:auto;border:1px solid #405169;border-radius:8px}table{border-collapse:collapse;width:100%;min-width:2400px}
caption{text-align:left;padding:14px;color:#b2bed0}th,td{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #334155}
thead th{background:#213249;position:sticky;top:0}tbody tr:nth-child(even){background:#151f2d}td.number{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
td.identity{min-width:170px}td.identity strong{color:#cde5ff}small{display:block;color:#b2bed0}td.details-cell{min-width:280px;max-width:450px}
td.annual-cell{min-width:190px}td.reasons-cell{min-width:180px;max-width:280px;overflow-wrap:anywhere}td.number small{white-space:normal;min-width:120px;max-width:220px;text-align:left}
summary{cursor:pointer;color:#9bceff}details[open]>summary{margin-bottom:16px}.evidence{overflow-wrap:anywhere}
.evidence dl{margin:14px 0}.evidence dt{font-weight:600;color:#cde5ff}.evidence dd{margin:0 0 12px;white-space:pre-wrap}
.sources{padding-left:20px}.sources li{margin-bottom:10px}a{color:#9bceff}.tag{display:inline-block;background:#233953;border-radius:4px;padding:2px 5px;margin:0 3px 3px 0}
.gap{color:#ffd5a3}.context dd{overflow-wrap:anywhere;margin-bottom:10px}#counts{font-weight:600}#empty{padding:20px}
[hidden]{display:none!important}@media(max-width:600px){main{padding:16px}.controls{gap:10px}.control{flex:1 1 130px}input,select{width:100%}}
"""


_SCRIPT = r"""
(() => {
  'use strict';
  const numericKeys = new Set(['last','annual_earnings_pe','day_return','month_return','dollar_turnover','net_income','operating_cashflow']);
  function matches(row, filters) {
    const query = (filters.search || '').trim().toLocaleLowerCase();
    if (query && !(row.symbol + ' ' + row.name).toLocaleLowerCase().includes(query)) return false;
    if (filters.status && row.status !== filters.status) return false;
    if (filters.index && !row.indices.includes(filters.index)) return false;
    if (filters.sector && row.sector !== filters.sector) return false;
    if (filters.gap === 'none' && row.gaps.length) return false;
    if (filters.gap === 'any' && !row.gaps.length) return false;
    if (filters.gap && !['any','none'].includes(filters.gap) && !row.gaps.includes(filters.gap)) return false;
    return true;
  }
  function compare(a, b, key, direction) {
    const left = a[key], right = b[key];
    const numeric = numericKeys.has(key);
    const unknown = value => value == null || value === '' || value === 'unknown' || (numeric && !Number.isFinite(Number(value)));
    const au = unknown(left), bu = unknown(right);
    if (au !== bu) return au ? 1 : -1;
    if (au) return a.order - b.order;
    const difference = numeric ? Number(left) - Number(right) : String(left).localeCompare(String(right));
    return difference ? difference * (direction === 'desc' ? -1 : 1) : a.order - b.order;
  }
  globalThis.PrintMoneyUniverse = Object.freeze({matches, compare});
  if (typeof document === 'undefined') return;
  const tbody = document.getElementById('universe-body');
  if (!tbody) return;
  const rows = Array.from(tbody.querySelectorAll('tr[data-symbol]')).map((element, order) => ({
    element, order, symbol:element.dataset.symbol, name:element.dataset.name, status:element.dataset.status,
    indices:JSON.parse(element.dataset.indices), sector:element.dataset.sector, gaps:JSON.parse(element.dataset.gaps),
    last:element.dataset.last, annual_earnings_pe:element.dataset.annualEarningsPe,
    day_return:element.dataset.dayReturn, month_return:element.dataset.monthReturn, dollar_turnover:element.dataset.dollarTurnover,
    net_income:element.dataset.netIncome, operating_cashflow:element.dataset.operatingCashflow,
    earnings_date:element.dataset.earningsDate, quote_day:element.dataset.quoteDay
  }));
  const controls = ['search','status-filter','index-filter','sector-filter','gap-filter','sort','direction'].map(id=>document.getElementById(id));
  function update() {
    const values = controls.map(element=>element.value);
    const filters = {search:values[0],status:values[1],index:values[2],sector:values[3],gap:values[4]};
    const sorted = rows.slice().sort((a,b)=>compare(a,b,values[5],values[6]));
    const fragment = document.createDocumentFragment();
    let visible = 0;
    for (const row of sorted) {
      row.element.hidden = !matches(row,filters);
      if (!row.element.hidden) visible++;
      fragment.appendChild(row.element);
    }
    tbody.appendChild(fragment);
    document.getElementById('counts').textContent = `${visible} / ${rows.length}`;
    document.getElementById('empty').hidden = visible !== 0;
  }
  controls.forEach(element=>element.addEventListener(element.id === 'search' ? 'input' : 'change',update));
  update();
})();
"""


def render_universe_html(report: dict, lang="th") -> str:
    """Render all evaluated cards without fetching, writing, or qualifying evidence."""
    thai = lang == "th"
    title = "Sol · งานวิจัยหุ้นทั้งชุด" if thai else "Sol · Full stock research universe"
    cards = report.get("evaluated")
    if cards is None:
        cards = report.get("highlights") or []
    cards = list(cards)
    details_label = "ข้อมูลและหลักฐาน" if thai else "Data and evidence"
    introduction = ("งานวิจัยแบบมีเงื่อนไข การไม่ซื้อขายเป็นทางเลือกได้ ข้อมูลที่ขาดไม่ถือว่าผ่านเกณฑ์"
                    if thai else "Conditional research. No trade is a valid outcome; missing evidence blocks consideration.")
    pieces = ['<!doctype html>', f'<html lang="{_literal(lang)}"><head><meta charset="utf-8">',
              '<meta name="viewport" content="width=device-width,initial-scale=1">',
              '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; '
              'script-src \'unsafe-inline\'; connect-src \'none\'; img-src \'none\'; base-uri \'none\'; form-action \'none\'">',
              '<title>' + _literal(title) + '</title><style>' + _STYLE + '</style></head><body><main>',
              '<h1>' + _literal(title) + '</h1><p>' + _literal(introduction) + '</p>',
              '<p class="muted">As of ' + _literal(report.get("generated_at")) + ' · ' + _literal(report.get("system_status")) + '</p>',
              '<details class="context"><summary>Report coverage, provenance and constraints / ที่มาและข้อจำกัด</summary>',
              _definition({key: report.get(key, "unknown — unverified; not supplied") for key in (
                  "membership", "membership_context", "collection", "coverage_metrics", "coverage", "data_quality", "news", "abstention", "policy_validation",
                  "policy", "personalization", "required_inputs", "errors")})]
    membership = report.get("membership")
    if isinstance(membership, dict):
        pieces.append(_sources(membership.get("sources")))
    pieces.extend(['</details><p class="muted">Field coverage uses the report denominator and mutually exclusive states. '
                   'Missing report coverage is unverified; headline context does not verify events. '
                   'This policy has no validated stock return track record. Source text remains untrusted literal text.</p>',
                   '<noscript><p>JavaScript is disabled: all rows and evidence remain available; browser find can search this page.</p></noscript>',
                   '<div class="controls" aria-label="Universe controls">'])

    def select(identifier, label, options):
        pieces.append(f'<div class="control"><label for="{identifier}">{_literal(label)}</label><select id="{identifier}">')
        pieces.extend(f'<option value="{_literal(value)}">{_literal(text)}</option>' for value, text in options)
        pieces.append('</select></div>')

    def distinct(key):
        return sorted({str(card.get(key) or "unknown") for card in cards})

    pieces.append('<div class="control"><label for="search">' + ("ค้นหาชื่อหรือสัญลักษณ์" if thai else "Search ticker or name")
                  + '</label><input id="search" type="search" autocomplete="off"></div>')
    select("status-filter", "Status / สถานะ", [("", "All statuses")] + [(value, value) for value in distinct("status")])
    indices = sorted({str(index) for card in cards for index in (card.get("indices") or [])})
    select("index-filter", "Index / ดัชนี", [("", "All indices")] + [(value, value) for value in indices] + [("unknown", "unknown")])
    select("sector-filter", "Sector / กลุ่มธุรกิจ", [("", "All sectors")] + [(value, value) for value in distinct("sector")])
    select("gap-filter", "Data gaps / ข้อมูลที่ขาด", [("", "All coverage"), ("any", "Any gap"), ("none", "All fields available")]
           + [(value, value) for value in QUALITY_STATES if value != "available"])
    select("sort", "Sort / เรียงตาม", [("symbol", "Ticker"), ("name", "Name"), ("status", "Status"), ("sector", "Sector"),
           ("last", "Price"), ("annual_earnings_pe", "Annual earnings P/E"), ("day_return", "Day return"),
           ("month_return", "Month return"), ("dollar_turnover", "20-session USD turnover"),
           ("net_income", "Annual net income"), ("operating_cashflow", "Annual OCF"),
           ("earnings_date", "Earnings date"), ("quote_day", "Quote day")])
    select("direction", "Direction / ลำดับ", [("asc", "Ascending"), ("desc", "Descending")])
    pieces.extend(['</div><p>Filtered / total · แสดง / ทั้งหมด: <span id="counts" aria-live="polite" aria-atomic="true">'
                   + f'{len(cards)} / {len(cards)}</span></p>',
                   '<div class="table-wrap" role="region" aria-label="Full security universe" tabindex="0"><table>',
                   '<caption>Every evaluated security · Unknown numeric values sort after known values in either direction.</caption><thead><tr>'])
    pieces.extend('<th scope="col">' + heading + '</th>' for heading in (
        "Ticker / Name", "Index", "Sector", "Status", "Price", "Quote day", "1d return (fraction)",
        "1m return (fraction)", "20-session USD turnover", "Annual net income", "Annual OCF", "Annual P/E",
        "Earnings date", "Reasons", "Field coverage", "Evidence"))
    pieces.append('</tr></thead><tbody id="universe-body">')
    for card in cards:
        quality = _quality(card)
        gaps = sorted({item["status"] for item in quality.values() if item["status"] != "available"})
        tags = list(card.get("indices") or ["unknown"])
        facts = card.get("annual_facts") if isinstance(card.get("annual_facts"), dict) else {}
        event = card.get("earnings") if isinstance(card.get("earnings"), dict) else {}
        turnover = _numeric(card.get("dollar_turnover")) if card.get("currency") == "USD" else ""
        attrs = {"symbol": card.get("symbol", "unknown"), "name": card.get("name") or "unknown",
                 "status": card.get("status") or "unknown", "sector": card.get("sector") or "unknown",
                 "indices": json.dumps(tags, ensure_ascii=False), "gaps": json.dumps(gaps, ensure_ascii=False),
                 "quote-day": card.get("quote_day") or "unknown", "earnings-date": _earnings_date(event),
                 "net-income": _numeric(facts.get("net_income")), "operating-cashflow": _numeric(facts.get("operating_cashflow")),
                 "dollar-turnover": turnover}
        attrs.update({key.replace("_", "-"): _numeric(card.get(key)) for key in
                      ("last", "annual_earnings_pe", "day_return", "month_return")})
        pieces.append('<tr ' + ' '.join(f'data-{key}="{_literal(value)}"' for key, value in attrs.items()) + '>')
        pieces.append('<td class="identity"><strong>' + _literal(card.get("symbol")) + '</strong><small>' + _literal(card.get("name")) + '</small></td>')
        pieces.append('<td>' + ''.join('<span class="tag">' + _literal(tag) + '</span>' for tag in tags) + '</td>')
        pieces.extend('<td>' + _literal(card.get(key)) + '</td>' for key in ("sector", "status"))
        pieces.append('<td class="number">' + _literal(_numeric(card.get("last")) or "unknown")
                      + '<small>' + _literal(card.get("currency")) + '</small><small>basis: '
                      + _literal(card.get("price_basis")) + '</small></td>')
        pieces.append('<td>' + _literal(card.get("quote_day")) + '</td>')
        for key in ("day_return", "month_return"):
            pieces.append('<td class="number">' + _literal(_numeric(card.get(key)) or "unknown")
                          + '<small>basis: ' + _literal(card.get("return_basis")) + '</small></td>')
        pieces.append('<td class="number">' + _literal(turnover or "unknown") + '<small>currency: '
                      + _literal(card.get("currency")) + '</small></td>')
        pieces.extend(_annual_cell(facts, key) for key in ("net_income", "operating_cashflow"))
        pieces.append('<td class="number">' + _literal(_numeric(card.get("annual_earnings_pe")) or "unknown")
                      + '<small>basis: ' + _literal(card.get("valuation_basis")) + '</small></td>')
        pieces.append('<td>' + _literal(_earnings_date(event)) + '<small>verification: '
                      + _literal(event.get("verification")) + '</small></td>')
        reasons = card.get("reasons")
        if isinstance(reasons, (list, tuple)):
            reasons = "; ".join(str(reason) for reason in reasons) if reasons else "none supplied"
        pieces.append('<td class="reasons-cell">' + _literal(_display(reasons)) + '</td><td>')
        pieces.extend('<small' + (' class="gap"' if item["status"] != "available" else '') + '>'
                      + _literal(field) + ': ' + _literal(item["status"]) + '</small>' for field, item in quality.items())
        pieces.append('</td><td class="details-cell">' + _details(card, quality, details_label) + '</td></tr>')
    pieces.extend(['</tbody></table></div><p id="empty"' + (' hidden' if cards else '') + '>No matching securities / ไม่พบรายการที่ตรงตัวกรอง</p>',
                   '</main><script type="text/javascript">' + _SCRIPT + '</script></body></html>'])
    return "\n".join(pieces)
