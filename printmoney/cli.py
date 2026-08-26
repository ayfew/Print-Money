"""Command line.

    pm doctor      check connectivity, config and dependencies
    pm scan        list the BTC strips that pass the filters
    pm surface     implied vs fair probabilities, leg by leg
    pm plan        solve the position but place nothing
    pm run         the loop (paper by default)
    pm ui          browser dashboard, live or as a single HTML file
    pm carry       rank delta-neutral funding carry across every perpetual
    pm study       does a trading idea survive its own costs? ten years of evidence
    pm daily       the morning brief: what moved, and whether to do anything
    pm validate    end-to-end self-test against a market with known truth
    pm replay      re-solve recorded snapshots and settle them for real
    pm report      ledger, equity curve, open positions
    pm settle      settle expired positions against the Binance print
    pm reset       clear paper state
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .util import ROOT, fmt_usd, human_dt, setup_console, setup_logging, utcnow

log = logging.getLogger("printmoney.cli")


# --------------------------------------------------------------------------- #
def _load(args: argparse.Namespace):
    from .config import Config

    cfg = Config.load(args.config)
    if getattr(args, "mode", None):
        cfg.execution.mode = args.mode
        cfg.validate()
    if getattr(args, "capital", None):
        cfg.risk.capital_usd = float(args.capital)
    if getattr(args, "paths", None):
        cfg.model.n_paths = int(args.paths)
    if getattr(args, "min_edge", None) is not None:
        cfg.strategy.min_edge = float(args.min_edge)
    setup_logging(args.log_level or cfg.log_level)
    return cfg


def _print_json(obj: Any) -> None:
    setup_console()
    print(json.dumps(obj, indent=2, default=str))


# --------------------------------------------------------------------------- #
def cmd_doctor(args: argparse.Namespace) -> int:
    from .config import Config

    setup_console()
    ok = True
    print(f"printmoney {__version__}")
    print(f"python     {sys.version.split()[0]}")
    print(f"project    {ROOT}")

    for name in ("numpy", "scipy", "httpx", "rich", "yaml"):
        try:
            __import__(name)
            print(f"  [ok]   {name}")
        except ImportError:
            ok = False
            print(f"  [MISS] {name}  -> pip install -r requirements.txt")

    try:
        import importlib.util

        if importlib.util.find_spec("py_clob_client") is None:
            raise ImportError

        print("  [ok]   py-clob-client (live trading available)")
    except ImportError:
        print("  [--]   py-clob-client not installed (paper trading only)")

    try:
        cfg = Config.load(args.config)
        print(f"  [ok]   config loaded, mode={cfg.execution.mode}, capital={fmt_usd(cfg.risk.capital_usd)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] config: {exc}")
        return 1

    armed, why = cfg.live_armed()
    print(f"  live trading: {'ARMED' if armed else 'disarmed'} ({why})")

    from .data.polymarket import PolymarketClient
    from .data.spot import SpotFeed

    try:
        with SpotFeed(cfg) as feed:
            q = feed.spot()
            print(f"  [ok]   spot {q.price:,.2f} from {q.source}")
            candles = feed.klines("1h", 48)
            print(f"  [ok]   {len(candles)} hourly candles")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  [FAIL] spot feed: {exc}")

    try:
        with PolymarketClient(cfg) as pm:
            events = pm.fetch_events(limit=50)
            print(f"  [ok]   gamma api reachable ({len(events)} events)")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  [FAIL] polymarket: {exc}")

    print("\nall good" if ok else "\nsome checks failed")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
def cmd_scan(args: argparse.Namespace) -> int:
    from rich.console import Console
    from rich.table import Table

    from .data.polymarket import PolymarketClient

    cfg = _load(args)
    setup_console()
    console = Console()
    with PolymarketClient(cfg) as pm:
        strips = pm.scan_strips()

    if args.json:
        _print_json([s.to_dict() for s in strips])
        return 0

    t = Table(title="BTC strips on Polymarket", header_style="bold cyan")
    t.add_column("slug", overflow="fold")
    t.add_column("kind")
    t.add_column("legs", justify="right")
    t.add_column("settles in", justify="right")
    t.add_column("liquidity", justify="right")
    t.add_column("24h volume", justify="right")
    t.add_column("mid sum", justify="right")
    for s in strips:
        total = s.sum_yes_mid()
        t.add_row(
            s.slug,
            s.kind.value,
            str(len(s.legs)),
            human_dt(s.seconds_to_expiry()),
            fmt_usd(s.liquidity_usd),
            fmt_usd(s.volume_24h),
            f"{total:.4f}" if total is not None else "-",
        )
    console.print(t)
    if not strips:
        console.print("[yellow]nothing matched. widen filters.* in config.yaml[/yellow]")
    return 0


# --------------------------------------------------------------------------- #
def _build_context(cfg, slug: str | None):
    """Shared setup for `surface` and `plan`: data in, models built."""
    from .data.polymarket import PolymarketClient
    from .data.spot import SpotFeed
    from .model import vol as volmod

    pm = PolymarketClient(cfg)
    feed = SpotFeed(cfg)
    spot = feed.spot()
    candles = feed.klines("1h", cfg.model.vol_lookback_hours)
    vol = volmod.estimate(candles, cfg.model)
    try:
        pool = volmod.standardized_returns(candles)
    except ValueError:
        pool = None

    if slug:
        strip = pm.strip_from_slug(slug)
        strips = [strip] if strip else []
    else:
        strips = pm.scan_strips()
    return pm, feed, spot, vol, pool, strips


def cmd_surface(args: argparse.Namespace) -> int:
    from rich.console import Console

    from .dashboard import incoherence_panel, surface_table
    from .model.build import prepare_models
    from .model.surface import build_surface
    from .util import years_between

    cfg = _load(args)
    setup_console()
    console = Console()
    pm, feed, spot, vol, pool, strips = _build_context(cfg, args.slug)
    try:
        if not strips:
            console.print("[yellow]no strips found[/yellow]")
            return 1
        console.print(
            f"BTC {spot.price:,.2f} ({spot.source})   "
            f"vol {100 * vol.annual:.1f}% annual   "
            f"[dim]raw {100 * vol.raw_annual:.1f}%, "
            + ", ".join(f"{k}={100 * v:.1f}%" for k, v in vol.components.items())
            + "[/dim]"
        )
        payload = []
        for strip in strips:
            years = years_between(utcnow(), strip.expiry)
            if years <= 0:
                continue
            vols, ensemble, _ = prepare_models(
                spot.price, vol.annual, years, [strip], cfg, shock_pool=pool
            )
            surface = build_surface(strip, ensemble, spot.price, years)
            payload.append({**surface.to_dict(), "vols": vols.to_dict()})
            if not args.json:
                console.print(f"[dim]{strip.slug}: {vols.describe()}[/dim]")
                console.print(surface_table(surface))
                panel = incoherence_panel(surface)
                if panel:
                    console.print(panel)
        if args.json:
            _print_json(payload)
    finally:
        pm.close()
        feed.close()
    return 0


# --------------------------------------------------------------------------- #
def cmd_plan(args: argparse.Namespace) -> int:
    from rich.console import Console

    from .dashboard import plan_panel, touch_table
    from .data.types import StripKind
    from .engine import TouchResult
    from .model.build import prepare_models
    from .strategy.lp import solve
    from .strategy.single import plan_touch_trades
    from .strategy.statespace import build_state_space, group_strips_by_expiry
    from .util import years_between

    cfg = _load(args)
    setup_console()
    console = Console()
    pm, feed, spot, vol, pool, strips = _build_context(cfg, args.slug)
    out: list[dict[str, Any]] = []
    try:
        if not strips:
            console.print("[yellow]no strips found[/yellow]")
            return 1
        console.print(f"BTC {spot.price:,.2f}   vol {100 * vol.annual:.1f}%   capital {fmt_usd(cfg.risk.capital_usd)}")

        for key, group in sorted(group_strips_by_expiry(strips).items()):
            years = years_between(utcnow(), group[0].expiry)
            if years <= 0:
                continue
            vols, ensemble, _ = prepare_models(
                spot.price, vol.annual, years, group, cfg, shock_pool=pool
            )
            space = build_state_space(group, ensemble, spot.price, cfg)
            plan = solve(space, cfg)
            out.append(
                {
                    "expiry": key,
                    "strips": [s.slug for s in group],
                    "vols": vols.to_dict(),
                    "plan": plan.to_dict(),
                }
            )
            if not args.json:
                console.rule(f"[bold]{key}[/bold]  {', '.join(s.slug for s in group)}")
                console.print(f"[dim]{vols.describe()}[/dim]")
                console.print(plan_panel(plan, space))

        for strip in strips:
            if strip.kind is not StripKind.TOUCH:
                continue
            years = years_between(utcnow(), strip.expiry)
            if years <= 0:
                continue
            vols, ensemble, _ = prepare_models(
                spot.price, vol.annual, years, [strip], cfg, shock_pool=pool
            )
            tplan = plan_touch_trades(strip, ensemble, cfg)
            out.append({"strip": strip.slug, "vols": vols.to_dict(), "touch_plan": tplan.to_dict()})
            if not args.json:
                console.print(f"[dim]{strip.slug}: {vols.describe()}[/dim]")
                console.print(touch_table(TouchResult(strip=strip, plan=tplan, vols=vols)))

        if args.json:
            _print_json(out)
    finally:
        pm.close()
        feed.close()
    return 0


# --------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace) -> int:
    from .dashboard import render_cycle
    from .engine import Engine

    cfg = _load(args)
    setup_console()
    engine = Engine(cfg)
    try:
        engine.run(
            max_cycles=args.cycles,
            on_cycle=(lambda r: render_cycle(r, verbose=not args.quiet)),
        )
    finally:
        engine.close()
    return 0


# --------------------------------------------------------------------------- #
def cmd_ui(args: argparse.Namespace) -> int:
    from .web import serve, write_report

    cfg = _load(args)
    setup_console()
    if args.serve:
        serve(
            cfg,
            host=args.host,
            port=args.port,
            slug=args.slug,
            ttl=args.ttl,
            open_browser=not args.no_open,
        )
        return 0

    out = write_report(cfg, args.out, slug=args.slug)
    size_kb = out.stat().st_size / 1024
    print(f"wrote {out} ({size_kb:,.0f} KB)")
    if not args.no_open:
        import webbrowser

        webbrowser.open(out.resolve().as_uri())
    return 0


# --------------------------------------------------------------------------- #
def cmd_carry(args: argparse.Namespace) -> int:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    from .carry.scanner import ROUND_TRIP_COST, scan

    cfg = _load(args)
    setup_console()
    console = Console()
    capital = args.capital or cfg.risk.capital_usd

    report = scan(
        capital=capital,
        holding_days=args.hold,
        deep_scan=args.depth,
        min_volume=args.min_volume,
    )
    if args.json:
        _print_json(report.to_dict())
        return 0

    console.print(
        f"[dim]{report.scanned} USDT perpetuals scanned, {report.hedgeable} have a spot "
        f"market to hedge against. Round trip costs {100 * ROUND_TRIP_COST:.2f}% of notional; "
        f"held {args.hold:.0f} days that is a "
        f"{100 * ROUND_TRIP_COST * 365.25 / args.hold:.1f}%/yr drag.[/dim]"
    )

    t = Table(title="delta-neutral funding carry", header_style="bold cyan", title_justify="left")
    t.add_column("symbol", overflow="fold")
    t.add_column("now", justify="right")
    t.add_column("30d avg", justify="right")
    t.add_column(f"net @{args.hold:.0f}d", justify="right")
    t.add_column("paid", justify="right")
    t.add_column("swing", justify="right")
    t.add_column("volume", justify="right")
    t.add_column("warnings", overflow="fold")

    for c in report.candidates[: args.top]:
        net = c.net_annual(args.hold)
        t.add_row(
            c.symbol,
            f"{100 * c.annual_now:+.1f}%",
            f"{100 * c.annual_mean:+.1f}%",
            Text(f"{100 * net:+.1f}%", style="green" if net > 0 else "red"),
            f"{c.positive_fraction:.0%}",
            "-" if c.volatility == float("inf") else f"{100 * c.volatility:.0f}%",
            f"${c.perp_volume_24h / 1e6:,.0f}M",
            Text("; ".join(c.risks) or "-", style="yellow" if c.risks else "dim"),
        )
    console.print(t)

    n = min(args.basket, len(report.candidates))
    if n:
        net = report.basket_net_annual(n)
        monthly = capital * net / 12.0
        console.print(
            f"\nEqual-weight basket of the top {n}: "
            f"[bold]{100 * net:+.1f}%/yr[/bold] net of fees.\n"
            f"On {fmt_usd(capital)} that is [bold]{fmt_usd(monthly)}/month[/bold] "
            f"({fmt_usd(monthly * 36)} a month in Thai baht terms at 36/USD).",
        )
        console.print(
            "[dim]Gross of borrow costs, slippage, rebalancing when the ranking changes, "
            "and exchange risk. Funding is a contractual payment, not a forecast - but it "
            "can and does turn negative, and the position still has to be managed.[/dim]"
        )
    return 0


# --------------------------------------------------------------------------- #
def cmd_study(args: argparse.Namespace) -> int:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    from .research.data import UNIVERSE, fetch_many
    from .research.study import FEE_SCENARIOS, run_study

    _load(args)
    setup_console()
    console = Console()

    console.print(f"[dim]loading {len(UNIVERSE)} markets, {args.range} of daily bars...[/dim]")
    series = fetch_many(UNIVERSE, rng=args.range)
    if len(series) < 5:
        print("not enough market data came back")
        return 1
    report = run_study(list(series.values()), fee=args.fee)

    if args.json:
        _print_json(report.to_dict())
        return 0

    console.print(
        f"[dim]{len(report.symbols)} markets, {report.days} common trading days "
        f"({report.years:.1f} years)[/dim]\n"
    )

    # ---- the central table
    t = Table(title="the only table that matters: same markets, only the frequency changes",
              title_justify="left", header_style="bold cyan")
    t.add_column("holding period", justify="right")
    t.add_column("round trips/yr", justify="right")
    t.add_column("gross", justify="right")
    t.add_column("net @10bp", justify="right")
    t.add_column("net @30bp", justify="right")
    for row in report.holding:
        n10, n30 = row.net.get(0.0010, 0.0), row.net.get(0.0030, 0.0)
        t.add_row(
            f"{row.days} day" + ("s" if row.days > 1 else ""),
            f"{row.trips_per_year:.0f}",
            f"{row.gross:+.1%}",
            Text(f"{n10:+.1%}", style="green" if n10 > 0 else "red"),
            Text(f"{n30:+.1%}", style="green" if n30 > 0 else "red"),
        )
    console.print(t)
    console.print(
        "[dim]The gross column barely moves. Everything that happens to the net column "
        "is the toll.[/dim]\n"
    )

    # ---- session split
    sp = report.split
    t2 = Table(title="buy at the open and sell at the close, every day",
               title_justify="left", header_style="bold cyan")
    t2.add_column("")
    t2.add_column("annualised", justify="right")
    t2.add_row("buy and hold", f"{sp.buy_hold:+.2%}")
    t2.add_row("intraday only (open -> close)", f"{sp.intraday:+.2%}")
    t2.add_row("overnight only (close -> open)", f"{sp.overnight:+.2%}")
    for fee, label in FEE_SCENARIOS:
        v = sp.intraday_net.get(fee, 0.0)
        t2.add_row(f"   intraday after {label}", Text(f"{v:+.2%}", style="green" if v > 0 else "red"))
    console.print(t2)
    console.print(
        f"[dim]Break-even for a daily strategy at 10bp: it must gross "
        f"{report.breakeven_gross():.1%} a year. It grosses {sp.intraday:.1%}. "
        f"Winning days: {sp.win_rate:.1%}.[/dim]\n"
    )

    # ---- rules vs luck
    band = report.band
    t3 = Table(title="does choosing what to buy rescue it?", title_justify="left",
               header_style="bold cyan")
    t3.add_column("rule", overflow="fold")
    t3.add_column("gross", justify="right")
    t3.add_column(f"net @{args.fee*10000:.0f}bp", justify="right")
    t3.add_column("verdict")
    for r in report.rules:
        t3.add_row(
            r.name,
            f"{r.gross:+.2%}",
            Text(f"{r.net:+.2%}", style="green" if r.net > 0 else "red"),
            Text("indistinguishable from random", style="yellow")
            if r.inside_noise else Text("outside the noise band", style="green"),
        )
    console.print(t3)
    console.print(
        f"[dim]{band.samples} strategies that pick a ticker at random return between "
        f"{band.low:+.2%} and {band.high:+.2%} a year, median {band.median:+.2%}. "
        f"A rule inside that range is a coin that charges admission.[/dim]"
    )

    # ---- what IS forecastable
    pers = report.persistence
    if pers is not None:
        t4 = Table(title="so is anything forecastable? same markets, same windows",
                   title_justify="left", header_style="bold cyan")
        t4.add_column("this month predicts next month's...")
        t4.add_column("correlation", justify="right")
        t4.add_column("positive in", justify="right")
        t4.add_row(
            "volatility",
            Text(f"{pers.vol_r:+.3f}", style="green"),
            f"{pers.vol_positive}/{pers.markets} markets",
        )
        t4.add_row(
            "return",
            Text(f"{pers.return_r:+.3f}", style="red"),
            f"{pers.return_positive}/{pers.markets} markets",
        )
        console.print(t4)

        if pers.buckets:
            t5 = Table(title="sorted by today's volatility", title_justify="left",
                       header_style="bold cyan")
            t5.add_column("bucket")
            t5.add_column("vol now", justify="right")
            t5.add_column("vol next month", justify="right")
            t5.add_column("return next month", justify="right")
            for label, now, nxt, fwd in pers.buckets:
                t5.add_row(label, f"{now:.1%}", f"{nxt:.1%}", f"{fwd:+.2%}")
            console.print(t5)

        console.print(
            "[dim]Danger is forecastable and direction is not. That asymmetry is why "
            "the daily brief flags markets running hot by their own standards and "
            "refuses to name anything to buy.[/dim]"
        )
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from .research.brief import build_brief

    cfg = _load(args)
    setup_console()
    console = Console()
    capital = args.capital or cfg.risk.capital_usd

    brief = build_brief(capital=capital, include_carry=not args.no_carry)

    if not brief.ok:
        # Do not write the calendar or the page. Replacing yesterday's real brief
        # with an empty one is worse than leaving yesterday's in place.
        console.print(f"[bold red]{brief.verdict}[/bold red]")
        for o in brief.observations:
            console.print(f"[dim]  {o}[/dim]")
        if args.json:
            _print_json(brief.to_dict())
        return 1

    # Written before anything is printed, so a scheduled run still produces the
    # files even if the terminal it was launched from has gone away.
    if args.ics:
        from .research.export import write_ics

        path = write_ics(brief, args.ics, lang=args.lang)
        if not args.json:
            console.print(f"[dim]calendar -> {path}[/dim]")
    if args.html:
        from .research.export import write_html

        path = write_html(brief, args.html, capital=capital, lang=args.lang)
        if not args.json:
            console.print(f"[dim]page -> {path}[/dim]")

    if args.json:
        _print_json(brief.to_dict())
        return 0

    console.print(
        Panel(
            Text(brief.verdict, style="bold green" if brief.actions else "bold"),
            title=f"morning brief {brief.generated_at:%Y-%m-%d %H:%M UTC}",
            border_style="green" if brief.actions else "dim",
        )
    )

    for a in brief.actions:
        console.print(Text(f"  -> {a}", style="green"))
    for o in brief.observations:
        console.print(Text(f"   . {o}", style="dim"))

    if brief.lines and not args.quiet:
        t = Table(title="\nwhere things stand", title_justify="left", header_style="bold cyan")
        t.add_column("market", overflow="fold")
        t.add_column("last", justify="right")
        t.add_column("day", justify="right")
        t.add_column("week", justify="right")
        t.add_column("month", justify="right")
        t.add_column("year", justify="right")
        t.add_column("vol", justify="right")
        t.add_column("z", justify="right")
        for l in sorted(brief.lines, key=lambda x: -abs(x.day)):
            col = lambda v: Text(f"{v:+.2%}", style="green" if v > 0 else "red" if v < 0 else "dim")
            t.add_row(
                l.name, f"{l.last:,.2f}", col(l.day), col(l.week), col(l.month), col(l.year),
                f"{l.vol_annual:.0%}",
                Text(f"{l.zscore:+.1f}", style="yellow" if abs(l.zscore) >= 2 else "dim"),
            )
        console.print(t)

    console.print(
        "\n[dim]This brief will say 'nothing today' most days. That is the correct "
        "answer most days - run `pm study` for the ten years of arithmetic behind it.[/dim]"
    )
    return 0


# --------------------------------------------------------------------------- #
def cmd_validate(args: argparse.Namespace) -> int:
    from .backtest import validate

    cfg = _load(args)
    setup_console()
    report = validate(cfg)
    if args.json:
        _print_json(report.to_dict())
    else:
        for case in report.cases:
            print(case.line())
        print(f"\n{report.summary()}")
    return 0 if report.passed else 1


def cmd_replay(args: argparse.Namespace) -> int:
    from .backtest import replay
    from .data.spot import SpotFeed

    cfg = _load(args)
    setup_console()
    feed = SpotFeed(cfg) if not args.offline else None
    try:
        report = replay(cfg, directory=args.dir, settlement_feed=feed, limit=args.limit)
    finally:
        if feed:
            feed.close()
    if args.json:
        _print_json(report.to_dict())
    else:
        print(report.summary())
        for note in report.notes[:20]:
            print(f"  - {note}")
    return 0


# --------------------------------------------------------------------------- #
def cmd_report(args: argparse.Namespace) -> int:
    from rich.console import Console
    from rich.table import Table

    from .dashboard import render_equity_sparkline
    from .ledger import Ledger

    cfg = _load(args)
    setup_console()
    console = Console()
    led = Ledger.load_or_new(cfg.execution.ledger_path, cfg.risk.capital_usd)
    stats = led.stats()
    if args.json:
        _print_json(stats)
        return 0

    money = {
        "cash", "equity", "starting_cash", "realized_pnl", "fees_paid",
        "exposure", "peak_equity",
    }
    ratios = {"total_return", "drawdown"}
    t = Table(title="account", header_style="bold cyan", show_header=False)
    t.add_column("k", style="dim")
    t.add_column("v", justify="right")
    for k, v in stats.items():
        if k in money:
            t.add_row(k, fmt_usd(float(v)))
        elif k in ratios:
            t.add_row(k, f"{100 * float(v):+.2f}%")
        else:
            t.add_row(k, str(v))
    console.print(t)
    console.print(render_equity_sparkline(led.equity_curve))

    positions = led.open_positions()
    if positions:
        p = Table(title="open positions", header_style="bold cyan")
        p.add_column("market", overflow="fold")
        p.add_column("leg")
        p.add_column("side")
        p.add_column("shares", justify="right")
        p.add_column("avg px", justify="right")
        p.add_column("cost", justify="right")
        p.add_column("expires", justify="right")
        for pos in positions:
            p.add_row(
                pos.strip_slug,
                pos.leg_label,
                pos.side,
                f"{pos.shares:,.2f}",
                f"{pos.avg_price:.3f}",
                fmt_usd(pos.cost_basis),
                human_dt((pos.expiry - utcnow()).total_seconds()) if pos.expiry else "-",
            )
        console.print(p)

    fills = led.fills[-args.fills :] if args.fills else []
    if fills:
        f = Table(title=f"last {len(fills)} fills", header_style="bold cyan")
        f.add_column("time")
        f.add_column("leg", overflow="fold")
        f.add_column("side")
        f.add_column("px", justify="right")
        f.add_column("shares", justify="right")
        f.add_column("cost", justify="right")
        for fill in fills:
            f.add_row(
                fill.ts.strftime("%m-%d %H:%M:%S"),
                fill.leg_label,
                fill.side,
                f"{fill.price:.3f}",
                f"{fill.shares:,.2f}",
                fmt_usd(fill.cost),
            )
        console.print(f)
    return 0


# --------------------------------------------------------------------------- #
def cmd_settle(args: argparse.Namespace) -> int:
    from .data.spot import SpotFeed
    from .ledger import Ledger
    from .registry import MarketRegistry
    from .settlement import settle_expired

    cfg = _load(args)
    setup_console()
    led = Ledger.load_or_new(cfg.execution.ledger_path, cfg.risk.capital_usd)
    reg = MarketRegistry()
    with SpotFeed(cfg) as feed:
        report = settle_expired(led, reg, feed, grace_seconds=0.0)
    led.save()
    if args.json:
        _print_json(report.to_dict())
    else:
        print(report.summary())
        for d in report.details:
            print(f"  {d}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    cfg = _load(args)
    setup_console()
    targets = [
        Path(cfg.execution.ledger_path),
        Path("state/risk.json"),
        Path("state/markets.json"),
    ]
    if args.snapshots:
        targets.append(Path(cfg.execution.snapshot_dir))
    print("about to delete:")
    for t in targets:
        print(f"  {t}{' (directory)' if t.is_dir() else ''}{'' if t.exists() else '  [missing]'}")
    if not args.yes:
        print("\nre-run with --yes to actually delete")
        return 0
    import shutil

    for t in targets:
        if t.is_dir():
            shutil.rmtree(t, ignore_errors=True)
        elif t.exists():
            t.unlink()
    print("state cleared")
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pm",
        description="Polymarket BTC probability-surface arbitrage engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--version", action="version", version=f"printmoney {__version__}")
    p.add_argument("-c", "--config", default=None, help="path to config.yaml")
    p.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, *, json_flag: bool = True) -> None:
        sp.add_argument("--capital", type=float, default=None, help="override risk.capital_usd")
        sp.add_argument("--paths", type=int, default=None, help="override model.n_paths")
        sp.add_argument("--min-edge", type=float, default=None, help="override strategy.min_edge")
        if json_flag:
            sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("doctor", help="check connectivity, config and dependencies")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("scan", help="list BTC strips that pass the filters")
    common(sp)
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("surface", help="implied vs fair probability, leg by leg")
    sp.add_argument("--slug", default=None, help="a single event slug instead of a full scan")
    common(sp)
    sp.set_defaults(func=cmd_surface)

    sp = sub.add_parser("plan", help="solve the position without placing anything")
    sp.add_argument("--slug", default=None)
    common(sp)
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("run", help="run the engine loop")
    sp.add_argument("--cycles", type=int, default=None, help="stop after N cycles")
    sp.add_argument("--mode", choices=("paper", "dry", "live"), default=None)
    sp.add_argument("--quiet", action="store_true", help="skip the per-leg tables")
    common(sp, json_flag=False)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("ui", help="browser dashboard, live or as a single HTML file")
    sp.add_argument("--serve", action="store_true", help="run a local server that refreshes itself")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8787)
    sp.add_argument("--ttl", type=float, default=15.0, help="seconds to cache one analysis")
    sp.add_argument("--out", default="state/dashboard.html", help="file to write when not serving")
    sp.add_argument("--slug", default=None, help="a single event slug instead of a full scan")
    sp.add_argument("--no-open", action="store_true", help="do not open a browser")
    common(sp, json_flag=False)
    sp.set_defaults(func=cmd_ui)

    sp = sub.add_parser("carry", help="rank delta-neutral funding carry across every perpetual")
    sp.add_argument("--hold", type=float, default=30.0, help="assumed holding period in days")
    sp.add_argument("--top", type=int, default=15, help="rows to show")
    sp.add_argument("--basket", type=int, default=10, help="how many to average for the basket")
    sp.add_argument("--depth", type=int, default=25, help="how many to pull history for")
    sp.add_argument("--min-volume", type=float, default=5e6, help="minimum 24h perp volume")
    common(sp)
    sp.set_defaults(func=cmd_carry)

    sp = sub.add_parser("study", help="does a trading idea survive its own costs?")
    sp.add_argument("--range", default="10y", help="how much history (yahoo range string)")
    sp.add_argument("--fee", type=float, default=0.0010, help="round-trip cost as a fraction")
    common(sp)
    sp.set_defaults(func=cmd_study)

    sp = sub.add_parser("daily", help="the morning brief")
    sp.add_argument("--no-carry", action="store_true", help="skip the funding scan")
    sp.add_argument("--quiet", action="store_true", help="verdict and notes only")
    sp.add_argument("--ics", nargs="?", const="reports/printmoney.ics", default=None,
                    help="append today to a subscribable .ics calendar")
    sp.add_argument("--html", nargs="?", const="reports/brief.html", default=None,
                    help="write a phone-sized HTML page")
    sp.add_argument("--lang", choices=("th", "en"), default="th",
                    help="language for the calendar entry and the page")
    common(sp)
    sp.set_defaults(func=cmd_daily)

    sp = sub.add_parser("validate", help="end-to-end self-test on a market with known truth")
    common(sp)
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("replay", help="re-solve recorded snapshots and settle them")
    sp.add_argument("--dir", default=None, help="snapshot directory")
    sp.add_argument("--limit", type=int, default=None, help="only the last N snapshots")
    sp.add_argument("--offline", action="store_true", help="do not fetch settlement prices")
    common(sp)
    sp.set_defaults(func=cmd_replay)

    sp = sub.add_parser("report", help="ledger, equity curve and open positions")
    sp.add_argument("--fills", type=int, default=15, help="how many recent fills to show")
    common(sp)
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("settle", help="settle expired positions against the Binance print")
    common(sp)
    sp.set_defaults(func=cmd_settle)

    sp = sub.add_parser("reset", help="clear paper trading state")
    sp.add_argument("--yes", action="store_true", help="actually delete")
    sp.add_argument("--snapshots", action="store_true", help="also delete recorded snapshots")
    common(sp, json_flag=False)
    sp.set_defaults(func=cmd_reset)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except Exception as exc:  # noqa: BLE001
        log.exception("command failed")
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
