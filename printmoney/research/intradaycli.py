"""On-demand scanner, bounded public-source probe and private artifacts."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from ..util import STATE_DIR
from . import intraday
from .stockbatch import RequestGate
from .stockdata import StockProvider, YAHOO, CHART_ALIASES
from .stockhistory import private_path
from .stocknews import NewsProvider


def collect(symbols):
    """Existing free endpoints only; no retries, credentials or source bypass.

    A successful chart response does not attest complete extended-hours coverage.
    Imported, reviewed evidence is required to lift these explicit unknowns.
    """
    gate = RequestGate()
    price = StockProvider(request_gate=gate, sec_enabled=False)
    news = NewsProvider(request_gate=gate)
    items = []
    try:
        for symbol in symbols:
            now = datetime.now(timezone.utc)
            daily = price.collect(symbol, now)
            provider_symbol = CHART_ALIASES.get(symbol, symbol)
            url = f"{YAHOO}/{provider_symbol}?range=1d&interval=5m&includePrePost=true"
            item = dict(symbol=symbol, currency=daily.get("currency"), instrument_type=daily.get("instrument_type"),
                        source_url=url, retrieved_at=now.isoformat(), premarket_complete=False,
                        corporate_actions_verified=False, daily=[], bars=[], catalyst={"status": "unknown"},
                        source_status="unavailable", warnings=list(daily.get("warnings", [])))
            item["daily"] = [dict(session=datetime.fromtimestamp(b[0], timezone.utc).date().isoformat(),
                                  close=b[6], high=b[2]) for b in daily.get("bars", [])]
            try:
                payload, fetched = price._json(url, "", now)
                item["retrieved_at"] = fetched.isoformat()
                data = payload["chart"]["result"][0]
                if data["meta"]["symbol"] != provider_symbol:
                    raise ValueError("Symbol mismatch")
                quote = data["indicators"]["quote"][0]
                for index, ts in enumerate(data.get("timestamp", [])):
                    item["bars"].append(dict(start=datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                        **{key: quote[key][index] for key in ("open", "high", "low", "close", "volume")}))
                item["source_status"] = "available_unverified_coverage"
            except Exception as exc:
                item["source_status"] = price._status(url, exc)
                item["warnings"].append(f"Intraday source unavailable: {type(exc).__name__}; no retry.")
            item["news_context"] = news.collect(symbol, datetime.now(timezone.utc))
            item["warnings"].append("Public chart coverage and corporate-action basis are unverified. Issuer headlines are context, not verified catalysts.")
            items.append(item)
    finally:
        price.close()
        news.close()
    return items


def command(args):
    policy = intraday.Policy(gap=args.gap, min_price=args.min_price, min_premarket_volume=args.min_premarket_volume,
                            stop_fraction=args.stop_fraction, target_fraction=args.target_fraction,
                            round_trip_cost_bps=args.cost_bps)
    if args.input:
        path = Path(args.input)
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Input exceeds 10 MiB limit.")
        items = json.loads(path.read_text(encoding="utf-8"))
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
        if len(symbols) > 5 or len(set(symbols)) != len(symbols) or any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) for s in symbols):
            raise ValueError("Live probe accepts up to five unique plain symbols.")
        if args.as_of:
            raise ValueError("Historical cutoff requires imported point-in-time evidence; live fetch cannot backfill knowledge.")
        items = collect(symbols)
    now = intraday.stamp(args.as_of) if args.as_of else datetime.now(timezone.utc)
    report = intraday.scan(items, now, policy)
    # Preserve original source evidence, including exclusions, even when no signals.
    report["inputs"] = items
    report["input_mode"] = "imported_attestation" if args.input else "public_source_probe"
    root = STATE_DIR / "research"
    directory = private_path(root / "intraday" / "runs", root)
    path = intraday.save_run(report, directory)
    saved = json.loads(path.read_text(encoding="utf-8"))
    html_path = path.with_suffix(".html")
    with html_path.open("x", encoding="utf-8") as handle:
        handle.write(intraday.render_html(saved))
    output = dict(saved, evidence_path=str(path), html_path=str(html_path))
    print(json.dumps(output, ensure_ascii=False, allow_nan=False) if args.json else
          f"Experimental intraday: {saved['coverage']['signals']} signals; validation unvalidated.\nEvidence: {path}\nReport: {html_path}")
    # Data failures remain visible to callers; ordinary waiting/zero matches succeed.
    return 2 if any(row["state"] == "cannot_evaluate" for row in saved["rows"]) else 0


def register(sub):
    parser = sub.add_parser("intraday", help="experimental momentum scanner; on-demand, no trades")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", help="JSON list of timestamped reviewed observations")
    source.add_argument("--symbols", default="AAPL,NVDA", help="up to five symbols for free public source probe")
    parser.add_argument("--as-of", help="aware ISO timestamp; imported point-in-time evidence only")
    parser.add_argument("--gap", type=float, default=.05)
    parser.add_argument("--min-price", type=float, default=3)
    parser.add_argument("--min-premarket-volume", type=float, default=50000)
    parser.add_argument("--stop-fraction", type=float, default=.02)
    parser.add_argument("--target-fraction", type=float, default=.04)
    parser.add_argument("--cost-bps", type=float, default=30)
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=command)
