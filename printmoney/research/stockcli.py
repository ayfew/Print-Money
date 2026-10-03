"""Strict JSON and escaped, private HTML for the stock research workflow."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

from ..util import STATE_DIR
from .stockdata import StockProvider
from .stockhistory import evaluate, load_records, private_path, record
from .stocks import ResearchRequest, run_stock_research, safe_url

PRIVATE_ROOT = STATE_DIR / "research"


def render_html(report: dict, lang="th") -> str:
    def literal(value):
        return escape(str(value if value is not None else "unknown"), quote=True)

    thai = lang == "th"
    title = "Sol · หุ้นที่ควรศึกษา ติดตาม และหลีกเลี่ยง" if thai else "Sol · Daily stock research"
    introduction = ("ผลคัดกรองเพื่อศึกษาต่อ เลือกไม่ซื้อได้ ข้อมูลไม่ครบจะไม่ผ่านการพิจารณา"
                    if thai else "Conditional research. No trade is a valid outcome; missing evidence blocks consideration.")
    pieces = [f'<!doctype html><html lang="{literal(lang)}"><meta charset="utf-8">',
              '<meta name="viewport" content="width=device-width,initial-scale=1"><title>' + literal(title) + '</title>',
              '<style>body{max-width:1000px;margin:auto;padding:28px;font:16px/1.6 system-ui;background:#111827;color:#e5e7eb}'
              'article{border:1px solid #4b5563;border-radius:12px;padding:20px;margin:20px 0}a{color:#93c5fd}'
              'dt{font-weight:bold}dd{margin:0 0 12px;overflow-wrap:anywhere}h2{color:#bfdbfe}</style><body>',
              f'<h1>{literal(title)}</h1><p>{literal(introduction)}</p>',
              f'<p>As of {literal(report.get("generated_at"))} · {literal(report.get("system_status"))} · '
              f'{literal(report.get("policy_validation"))}</p>',
              '<p>Coverage: ' + literal(json.dumps(report.get("coverage", {}), ensure_ascii=False)) + '</p>',
              '<p>No-trade context: ' + literal(json.dumps(report.get("abstention", {}), ensure_ascii=False)) + '</p>',
              '<p>Required profile inputs: ' + literal(", ".join(report.get("required_inputs", []))) + '</p>']
    pieces.append('<p>Policy: ' + literal(json.dumps(report.get("policy", {}))) + '</p>')
    pieces.append('<p>Personalization: ' + literal(json.dumps(report.get("personalization", {}))) + '</p>')
    for card in report.get("highlights", []):
        pieces.append(f'<article><h2>{literal(card["symbol"])} · {literal(card["status"])}</h2>'
                      f'<p>{literal(card.get("name"))}</p><dl>')
        for key in ("last", "currency", "quote_day", "price_basis", "fetched_at", "annual_facts",
                    "annual_earnings_pe", "valuation_basis", "day_return", "month_return", "dollar_turnover",
                    "earnings", "reasons", "warnings", "entry_condition", "review_conditions", "invalidation",
                    "manipulation_risk", "uncertainty"):
            value = card.get(key)
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            pieces.append(f'<dt>{literal(key)}</dt><dd>{literal(value)}</dd>')
        pieces.append('</dl><h3>Sources and lineage</h3><ul>')
        for source in card.get("sources", []):
            url = safe_url(source.get("url"))
            label = literal(source.get("id") or "source")
            link = f'<a href="{literal(url)}" rel="noopener noreferrer">{label}</a>' if url else label
            pieces.append('<li>' + link + ' · ' + literal(json.dumps(source, ensure_ascii=False)) + '</li>')
        pieces.append('</ul></article>')
    pieces.append('<p>At most five highlights. JSON contains every evaluated symbol. Sources remain untrusted literal text. '
                  'This policy has no validated stock return track record.</p></body></html>')
    return "\n".join(pieces)


def _error(exc):
    return {"schema_version": 1, "system_status": "unavailable", "actionability": "conditional",
            "coverage": {"thai_equities": "not_connected", "social": "not_connected"},
            "evaluated": [], "highlights": [], "consider": [], "watch": [], "avoid": [], "excluded": [],
            "abstention": {"active": True, "reason": "request_or_output_error"},
            "errors": [{"code": type(exc).__name__, "message": str(exc)}]}


def cmd_research(args) -> int:
    provider = None
    try:
        destination = private_path(args.html, PRIVATE_ROOT) if args.html else None
        request = ResearchRequest(symbols=tuple(args.symbols.split(",")) if args.symbols else (),
                                  market=args.market, horizon=args.horizon, risk=args.risk,
                                  budget=args.budget, currency=args.currency, loss_limit=args.loss_limit, pe_cap=args.pe_cap)
        provider = StockProvider()
        result = run_stock_research(request, provider=provider)
        if not args.no_record:
            directory = private_path(PRIVATE_ROOT / "records", PRIVATE_ROOT)
            result["report_id"] = record(result, directory)
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(render_html(result, args.lang), encoding="utf-8")
    except (ValueError, OSError, TypeError, KeyError) as exc:
        result = _error(exc)
    finally:
        if provider is not None and hasattr(provider, "close"):
            provider.close()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    else:
        print(f'Sol · stock research · {result["system_status"]}')
        for card in result["highlights"]:
            print(f'{card["symbol"]}: {card["status"]} · close {card["last"]} {card["currency"]} '
                  f'on {card["quote_day"]} · {", ".join(card["reasons"]) or "screen gates met"}')
        if result["abstention"]["active"]:
            print("No trade: " + str(result["abstention"]["reason"]))
        for error in result["errors"]:
            print(json.dumps(error, ensure_ascii=False))
    return 2 if result["system_status"] == "unavailable" else 0


def register(subparsers):
    parser = subparsers.add_parser("research", help="on-demand conditional US stock research; private local output")
    parser.add_argument("--symbols", help="comma-separated US tickers; default is twelve documented starter issuers")
    parser.add_argument("--market", help="supported market: us; missing value keeps research general")
    parser.add_argument("--horizon", help="intraday, days-weeks or months-plus; intraday is unsupported")
    parser.add_argument("--risk", help="low, moderate or high; no assumed risk appetite")
    parser.add_argument("--budget", help="positive amount in your currency; no automatic allocation")
    parser.add_argument("--currency", help="three-letter budget currency; no assumed FX conversion")
    parser.add_argument("--loss-limit", help="positive review context; no guaranteed loss cap")
    parser.add_argument("--pe-cap", type=float, default=25, help="unvalidated annual-earnings P/E research cap; default 25")
    parser.add_argument("--json", action="store_true", help="one strict JSON document on stdout")
    parser.add_argument("--no-record", action="store_true", help="do not save a prospective decision record")
    parser.add_argument("--html", nargs="?", const=str(PRIVATE_ROOT / "latest.html"), help="export only beneath state/research")
    parser.add_argument("--lang", choices=("th", "en"), default="th")
    parser.set_defaults(func=cmd_research)
    parser = subparsers.add_parser("research-score", help="evaluate private prospective stock research scenarios")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=cmd_research_score)


def cmd_research_score(args) -> int:
    provider = None
    try:
        from datetime import datetime, timezone
        records = load_records(private_path(PRIVATE_ROOT / "records", PRIVATE_ROOT))
        symbols = {c["symbol"] for r in records for c in r["payload"].get("evaluated", [])}
        data, warnings = {}, []
        if symbols:
            now = datetime.now(timezone.utc)
            provider = StockProvider()
            for symbol in sorted(symbols | {"SPY"}):
                item = provider.collect(symbol, now)
                data[symbol] = item.get("bars", [])
                warnings.extend({"symbol":symbol, "message":w} for w in item.get("warnings", []))
        result = evaluate(records, data)
        result["provider_warnings"] = warnings
    except (ValueError, OSError, TypeError, KeyError) as exc:
        result = _error(exc)
    finally:
        if provider is not None:
            provider.close()
    print(json.dumps(result, ensure_ascii=False, allow_nan=False) if args.json
          else json.dumps(result.get("summary", result), ensure_ascii=False, indent=2))
    return 2 if result.get("errors") else 0
