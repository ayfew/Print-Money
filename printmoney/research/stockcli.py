"""Free public research, strict JSON and a private full-universe HTML view."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import sys
import httpx

from ..util import STATE_DIR
from .stockbatch import RequestGate, collect_many
from .stockdata import StockProvider
from .stockevidence import add_coverage, enrich_observations, load_evidence
from .stockfinancials import FinancialReleaseProvider
from .stockhistory import evaluate, load_records, private_path, record
from .stocknews import NewsProvider
from .stocks import ResearchRequest, screen
from .stockuniverse import resolve_universe
from .stockview import render_universe_html

PRIVATE_ROOT = STATE_DIR / "research"


def render_html(report: dict, lang="th") -> str:
    return render_universe_html(report, lang)


def error_report(exc):
    report = {"schema_version": 1, "system_status": "unavailable", "actionability": "conditional",
              "coverage": {"thai_equities": "not_connected", "social": "not_connected"},
              "evaluated": [], "highlights": [], "consider": [], "watch": [], "avoid": [], "excluded": [],
              "abstention": {"active": True, "reason": "request_or_output_error"},
              "errors": [{"code": type(exc).__name__, "message": str(exc)}]}
    return add_coverage(report, datetime.now(timezone.utc))


class _ResearchProvider:
    """One worker owns its clients; every actual HTTP call shares the same gate."""
    def __init__(self, gate, cache_dir, news=True, financials=True):
        self.price = StockProvider(request_gate=gate, sec_enabled=False, cache_dir=cache_dir)
        self.news = NewsProvider(request_gate=gate) if news else None
        self.financials = FinancialReleaseProvider(request_gate=gate) if financials else None

    def collect(self, symbol, now):
        result = self.price.collect(symbol, now)
        for field, provider in (("news", self.news), ("reported_financials", self.financials)):
            if provider is None:
                result[field] = {"status": "not_requested", "sources": [], "warnings": ["New collection disabled."]}
                continue
            try:
                result[field] = provider.collect(symbol, now)
            except Exception as exc:
                # A failed optional adapter cannot erase independently collected prices.
                result[field] = {"status": "unavailable", "sources": [],
                                 "warnings": [f"Context unavailable: {type(exc).__name__}."]}
        return result

    def close(self):
        for provider in (self.price, self.news, self.financials):
            if provider is not None and hasattr(provider, "close"):
                provider.close()


class _MembershipClient:
    """Membership and subsequent prices share the same run pacing/circuits."""
    def __init__(self, gate):
        self.gate = gate
        self.client = httpx.Client(timeout=20, follow_redirects=False, headers={
            "User-Agent": "Print-Money research/1.0 (https://github.com/ayfew/Print-Money)"})

    def get(self, url, **kwargs):
        self.gate.acquire(url)
        response = self.client.get(url, follow_redirects=False)
        self.gate.block(url, response.status_code)
        return response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()


def _settings(args):
    workers = getattr(args, "workers", 4)
    interval = getattr(args, "request_interval", 1.0)
    if not 1 <= workers <= 4:
        raise ValueError("Use one to four collection workers.")
    if not math.isfinite(interval) or interval < 1:
        raise ValueError("Public source requests must be paced at least one second apart.")
    return workers, interval


def _request(args, symbols=(), universe=None):
    return ResearchRequest(symbols=tuple(symbols), universe=universe,
                           market=args.market, horizon=args.horizon, risk=args.risk,
                           budget=args.budget, currency=args.currency,
                           loss_limit=args.loss_limit, pe_cap=args.pe_cap)


def _custom_membership(request, now):
    return {"schema_version": 1, "name": "custom", "version": "custom:" + ",".join(request.symbols),
            "captured_at": now.isoformat(), "verification": "user_selected",
            "completeness": "explicit_selection", "sources": [], "pending": [], "warnings": [],
            "members": [{"symbol": s, "provider_symbol": {"BRK.B": "BRK-B", "BF.B": "BF-B"}.get(s, s),
                         "indices": []} for s in request.symbols]}


def membership_context(snapshot, now):
    """Report age/known due changes without modifying a frozen membership list."""
    context = {"evaluated_at": now.isoformat(), "snapshot_age_seconds": None,
               "status": "unknown_snapshot_clock", "due_changes": [],
               "official_current_certification": "unverified"}
    try:
        captured = datetime.fromisoformat(snapshot["captured_at"])
        if captured.tzinfo is None or captured.utcoffset() is None or captured > now:
            return context
        context["snapshot_age_seconds"] = (now-captured).total_seconds()
        for change in snapshot.get("pending", []):
            effective = datetime.fromisoformat(change["effective_at"])
            if effective.tzinfo is not None and effective <= now:
                context["due_changes"].append(dict(change))
    except (ValueError, TypeError, KeyError):
        return context
    if not snapshot.get("members"):
        context["status"] = "membership_unavailable"
    elif snapshot.get("name") in ("custom", "starter"):
        context["status"] = "explicit_local_selection"
    elif context["due_changes"]:
        context["status"] = "snapshot_with_due_changes"
    elif context["snapshot_age_seconds"] >= 21600:
        context["status"] = "stale_discovery_snapshot"
    elif isinstance(snapshot.get("completeness"), dict) and snapshot["completeness"].get("status") == "partial":
        context["status"] = "partial_discovery_snapshot"
    else:
        context["status"] = "fresh_discovery_snapshot"
    return context


def _print_report(result, as_json):
    # The Windows console default cannot encode all issuer names or Thai text.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if as_json:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return
    print(f'Sol · stock research · {result["system_status"]}')
    print("Coverage: " + json.dumps(result.get("coverage_metrics", {}), ensure_ascii=False))
    for card in result["evaluated"]:
        print(f'{card["symbol"]}: {card["status"]} · close {card["last"]} {card["currency"]} '
              f'on {card["quote_day"]} · {", ".join(card["reasons"]) or "screen gates met"}')
    if result["abstention"]["active"]:
        print("No trade: " + str(result["abstention"]["reason"]))
    for error in result["errors"]:
        print(json.dumps(error, ensure_ascii=False))


def cmd_research(args) -> int:
    try:
        # Validate private destinations and user inputs before any source request.
        destination = private_path(args.html, PRIVATE_ROOT) if args.html else None
        evidence_path = private_path(args.evidence, PRIVATE_ROOT) if args.evidence else None
        membership_path = private_path(args.membership_file, PRIVATE_ROOT) if args.membership_file else None
        cache = private_path(PRIVATE_ROOT / "cache", PRIVATE_ROOT)
        symbols = tuple(args.symbols.split(",")) if args.symbols else ()
        if symbols and args.universe:
            raise ValueError("Choose either an index universe or explicit symbols.")
        preliminary = _request(args, symbols=symbols)
        workers, interval = _settings(args)
        started = datetime.now(timezone.utc)
        imports = load_evidence(evidence_path, started) if evidence_path else {}
        if preliminary.market not in (None, "us"):
            result = screen(preliminary, [], started)
        else:
            name = "custom" if symbols else args.universe or "sp500-nasdaq100"
            gate = RequestGate(interval=interval)
            if name == "custom":
                membership = _custom_membership(preliminary, started)
            elif args.offline or membership_path or name == "starter":
                membership = resolve_universe(name, now=None, cache_dir=cache / "membership",
                                              offline=args.offline, import_path=membership_path)
            else:
                with _MembershipClient(gate) as client:
                    membership = resolve_universe(name, now=None, cache_dir=cache / "membership", client=client)
            members = membership.get("members", [])
            if not members:
                result = error_report(ValueError("Requested membership is unavailable; no securities evaluated."))
                result["membership"] = membership
                result["membership_context"] = membership_context(membership, started)
            else:
                request = _request(args, symbols=[m["symbol"] for m in members], universe=name)
                def factory(gate):
                    return _ResearchProvider(gate, cache / "provider", news=not args.no_news,
                                             financials=not args.no_financials)
                batch = collect_many(request.symbols, factory, started, cache / "observations",
                                     workers=workers, interval=interval, offline=args.offline, refresh=args.refresh,
                                     request_gate=gate)
                # Freeze after receipt; explicit historical callers retain their own cutoff.
                cutoff = datetime.now(timezone.utc)
                observations = enrich_observations(batch["observations"], members, cutoff, imports)
                result = screen(request, observations, cutoff)
                result["membership"] = membership
                result["membership_context"] = membership_context(membership, cutoff)
                result["collection"] = batch["collection"]
        # Validate before records/HTML/output, including the --no-record path.
        json.dumps(result, allow_nan=False)
        if not args.no_record and result.get("evaluated"):
            result["report_id"] = record(result, private_path(PRIVATE_ROOT / "records", PRIVATE_ROOT))
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(render_html(result, args.lang), encoding="utf-8")
    except (ValueError, OSError, TypeError, KeyError, OverflowError) as exc:
        result = error_report(exc)
    _print_report(result, args.json)
    return 2 if result["system_status"] == "unavailable" else 0


def register(subparsers):
    parser = subparsers.add_parser("research", help="free public stock research; full universe and private local output")
    parser.add_argument("--universe", choices=("sp500", "nasdaq100", "sp500-nasdaq100", "starter"),
                        help="default: S&P500 + Nasdaq100 securities; discovery membership is uncertified")
    parser.add_argument("--symbols", help="explicit comma-separated US securities, up to fifty")
    parser.add_argument("--market", help="supported market: us; missing value keeps research general")
    parser.add_argument("--horizon", help="intraday, days-weeks or months-plus; intraday is unsupported")
    parser.add_argument("--risk", help="low, moderate or high; no assumed risk appetite")
    parser.add_argument("--budget", help="positive amount in your currency; no automatic allocation")
    parser.add_argument("--currency", help="three-letter budget currency; no assumed FX conversion")
    parser.add_argument("--loss-limit", help="positive review context; no guaranteed loss cap")
    parser.add_argument("--pe-cap", type=float, default=25, help="unvalidated annual-earnings P/E research cap; default 25")
    parser.add_argument("--workers", type=int, default=4, help="one to four collectors sharing one request gate")
    parser.add_argument("--request-interval", type=float, default=1.0, help="seconds between public request starts; minimum 1")
    parser.add_argument("--offline", action="store_true", help="use eligible private cache only; never request a source")
    parser.add_argument("--refresh", action="store_true", help="skip observation cache; source denials are not retried")
    parser.add_argument("--membership-file", help="explicit provenance-bearing snapshot beneath state/research")
    parser.add_argument("--evidence", help="explicit schema1 source-backed context import beneath state/research")
    parser.add_argument("--no-news", action="store_true", help="skip new registered issuer feed requests")
    parser.add_argument("--no-financials", action="store_true", help="skip new registered issuer release requests")
    parser.add_argument("--json", action="store_true", help="one strict JSON document on stdout")
    parser.add_argument("--no-record", action="store_true", help="do not save a prospective decision record")
    parser.add_argument("--html", nargs="?", const=str(PRIVATE_ROOT / "latest.html"), help="export only beneath state/research")
    parser.add_argument("--lang", choices=("th", "en"), default="th")
    parser.set_defaults(func=cmd_research)
    parser = subparsers.add_parser("research-score", help="evaluate private prospective stock research scenarios")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--request-interval", type=float, default=1.0)
    parser.set_defaults(func=cmd_research_score)


def cmd_research_score(args) -> int:
    try:
        workers, interval = _settings(args)
        records = load_records(private_path(PRIVATE_ROOT / "records", PRIVATE_ROOT))
        symbols = {c["symbol"] for r in records for c in r["payload"].get("evaluated", [])}
        data, warnings = {}, []
        if symbols:
            cache = private_path(PRIVATE_ROOT / "cache", PRIVATE_ROOT)
            batch = collect_many(sorted(symbols | {"SPY"}),
                                 lambda gate: StockProvider(request_gate=gate, sec_enabled=False, cache_dir=cache / "provider"),
                                 datetime.now(timezone.utc), cache / "observations", workers=workers,
                                 interval=interval, offline=args.offline, refresh=args.refresh)
            for item in batch["observations"]:
                data[item["symbol"]] = item.get("bars", [])
                warnings.extend({"symbol": item["symbol"], "message": w} for w in item.get("warnings", []))
        result = evaluate(records, data)
        result["provider_warnings"] = warnings
        json.dumps(result, allow_nan=False)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        result = error_report(exc)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False) if args.json
          else json.dumps(result.get("summary", result), ensure_ascii=False, indent=2))
    return 2 if result.get("errors") else 0
