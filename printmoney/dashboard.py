"""Terminal rendering.

Everything the engine decided, shown in a form where a human can disagree with
it: implied versus fair for every leg, the payoff of the proposed position in
every settlement state, and - when nothing is traded - the specific reason.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Sequence

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .engine import CycleResult, TouchResult
from .model.surface import Surface
from .strategy.lp import Plan
from .strategy.statespace import StateSpace
from .util import fmt_pct, fmt_usd, human_dt, setup_console

console = Console()


def _pct(x: float | None, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{100.0 * x:.{digits}f}"


def _signed(x: float, digits: int = 3) -> Text:
    txt = f"{x:+.{digits}f}"
    return Text(txt, style="green" if x > 0 else "red" if x < 0 else "dim")


def _money(x: float) -> Text:
    return Text(fmt_usd(x), style="green" if x > 0 else "red" if x < 0 else "dim")


# --------------------------------------------------------------------------- #
def surface_table(surface: Surface) -> Table:
    strip = surface.strip
    tte = strip.seconds_to_expiry()
    title = (
        f"{strip.title}  [dim]({strip.slug})[/dim]\n"
        f"spot {surface.spot:,.0f}  |  settles in {human_dt(tte)}  |  {strip.kind.value}"
    )
    t = Table(title=title, title_justify="left", header_style="bold cyan", expand=False)
    t.add_column("leg", overflow="fold")
    t.add_column("bid", justify="right")
    t.add_column("ask", justify="right")
    t.add_column("impl %", justify="right")
    t.add_column("fair %", justify="right")
    t.add_column("range %", justify="right")
    t.add_column("buy YES", justify="right")
    t.add_column("buy NO", justify="right")

    for q in surface.quotes:
        by = q.buy_yes_edge
        bn = q.buy_no_edge
        rng = (
            f"{_pct(q.fair_lo)}-{_pct(q.fair_hi)}"
            if not math.isnan(q.fair_lo) and not math.isnan(q.fair_hi)
            else "-"
        )
        t.add_row(
            q.label,
            f"{q.yes_bid:.3f}" if q.yes_bid is not None else "-",
            f"{q.yes_ask:.3f}" if q.yes_ask is not None else "-",
            _pct(q.implied),
            _pct(q.fair),
            rng,
            _signed(by) if math.isfinite(by) else Text("-", style="dim"),
            _signed(bn) if math.isfinite(bn) else Text("-", style="dim"),
        )

    if surface.implied_sum is not None:
        off = surface.implied_sum - 1.0
        style = "yellow" if abs(off) > 0.01 else "dim"
        t.caption = f"[{style}]bracket mids sum to {surface.implied_sum:.4f} ({off:+.4f})[/{style}]"
    return t


def incoherence_panel(surface: Surface) -> Panel | None:
    if not surface.incoherences:
        return None
    lines = [
        Text(f"{i.kind}: {i.detail}  [{i.size:+.4f}]", style="yellow")
        for i in surface.incoherences
    ]
    return Panel(Group(*lines), title="pricing inconsistencies", border_style="yellow")


# --------------------------------------------------------------------------- #
def plan_table(plan: Plan) -> Table:
    t = Table(title="orders", title_justify="left", header_style="bold cyan")
    t.add_column("market", overflow="fold")
    t.add_column("leg")
    t.add_column("side")
    t.add_column("px", justify="right")
    t.add_column("shares", justify="right")
    t.add_column("cost", justify="right")
    for o in plan.orders:
        t.add_row(
            o.instrument.strip_slug,
            o.instrument.leg.label,
            o.instrument.side.value,
            f"{o.price:.3f}",
            f"{o.shares:,.2f}",
            fmt_usd(o.cost),
        )
    return t


def payoff_table(plan: Plan, space: StateSpace, max_rows: int = 24) -> Table:
    t = Table(title="payoff by settlement state", title_justify="left", header_style="bold cyan")
    t.add_column("BTC settles", justify="right")
    t.add_column("model p", justify="right")
    t.add_column("payout", justify="right")
    t.add_column("PnL", justify="right")

    probs = space.base_probs()
    rows = list(range(space.n_states))
    if len(rows) > max_rows:
        # keep the extremes and the most likely middle
        keep = set(rows[:2] + rows[-2:])
        keep |= set(sorted(rows, key=lambda s: -probs[s])[: max_rows - 4])
        rows = sorted(keep)

    for s in rows:
        pnl = float(plan.pnl_by_state[s]) if plan.pnl_by_state.size else 0.0
        payout = float(plan.payoff_by_state[s]) if plan.payoff_by_state.size else 0.0
        t.add_row(
            space.state_label(s),
            f"{100 * probs[s]:.2f}",
            fmt_usd(payout),
            _money(pnl),
        )
    return t


def plan_panel(plan: Plan, space: StateSpace | None) -> Panel:
    if not plan.orders:
        return Panel(
            Text(plan.reason or plan.status, style="dim"),
            title="no position",
            border_style="dim",
        )
    header = Text.assemble(
        ("ARBITRAGE  " if plan.is_arbitrage else "EXPECTED VALUE  ",
         "bold green" if plan.is_arbitrage else "bold cyan"),
        (f"stake {fmt_usd(plan.capital_used)}   ", ""),
        (f"E[PnL] {fmt_usd(plan.expected_pnl)} ({plan.return_on_capital:+.2%})   ",
         "green" if plan.expected_pnl > 0 else "red"),
        (f"CVaR {fmt_usd(plan.cvar)}   ", "yellow" if plan.cvar > 0 else "green"),
        (f"worst {fmt_usd(plan.worst_case)}   best {fmt_usd(plan.best_case)}", ""),
    )
    parts: list[Any] = [header, plan_table(plan)]
    if space is not None:
        parts.append(payoff_table(plan, space))
    parts.append(Text(f"noise floor: {fmt_usd(plan.ev_noise)} per Monte-Carlo std error", style="dim"))
    return Panel(
        Group(*parts),
        title=plan.reason or plan.status,
        border_style="green" if plan.is_arbitrage else "cyan",
    )


# --------------------------------------------------------------------------- #
def touch_table(result: TouchResult) -> Table:
    t = Table(
        title=f"{result.strip.title}  [dim]({result.strip.slug})[/dim]",
        title_justify="left",
        header_style="bold cyan",
    )
    t.add_column("barrier")
    t.add_column("side")
    t.add_column("px", justify="right")
    t.add_column("fair %", justify="right")
    t.add_column("edge", justify="right")
    t.add_column("shares", justify="right")
    t.add_column("cost", justify="right")
    for tr in result.plan.trades:
        t.add_row(
            tr.label,
            tr.side.value,
            f"{tr.price:.3f}",
            _pct(tr.fair),
            _signed(tr.edge),
            f"{tr.shares:,.2f}",
            fmt_usd(tr.cost),
        )
    if not result.plan.trades:
        t.add_row("-", "-", "-", "-", "-", "-", "-")
        t.caption = "[dim]no barrier market cleared the edge threshold[/dim]"
    return t


# --------------------------------------------------------------------------- #
def stats_panel(result: CycleResult) -> Panel:
    s = result.stats or {}
    rows = Table.grid(padding=(0, 2))
    rows.add_column(style="dim")
    rows.add_column()

    spot = f"{result.spot.price:,.2f} ({result.spot.source})" if result.spot else "-"
    vol = f"{100 * result.vol.annual:.1f}% annual" if result.vol else "-"
    rows.add_row("time", result.ts.strftime("%Y-%m-%d %H:%M:%S UTC"))
    rows.add_row("BTC", spot)
    rows.add_row("volatility", vol)
    rows.add_row("equity", fmt_usd(result.equity))
    rows.add_row("cash", fmt_usd(float(s.get("cash", 0.0))))
    rows.add_row("exposure", fmt_usd(float(s.get("exposure", 0.0))))
    rows.add_row("realised", fmt_usd(float(s.get("realized_pnl", 0.0))))
    rows.add_row("fees paid", fmt_usd(float(s.get("fees_paid", 0.0))))
    rows.add_row("return", fmt_pct(float(s.get("total_return", 0.0)), 2))
    rows.add_row("drawdown", fmt_pct(float(s.get("drawdown", 0.0)), 2))
    rows.add_row("open positions", str(s.get("open_positions", 0)))
    rows.add_row("fills", str(s.get("fills", 0)))
    rows.add_row("cycle", f"{result.duration_s:.2f}s")
    return Panel(rows, title="account", border_style="blue")


def notes_panel(result: CycleResult) -> Panel | None:
    lines: list[Text] = []
    if result.error:
        lines.append(Text(f"error: {result.error}", style="bold red"))
    if result.settlement and (
        result.settlement.settled or result.settlement.deferred or result.settlement.unknown
    ):
        lines.append(Text(f"settlement: {result.settlement.summary()}", style="cyan"))
        for d in result.settlement.details[:10]:
            lines.append(Text(f"  {d}", style="dim"))
    for n in result.notes[:20]:
        lines.append(Text(f"- {n}", style="yellow"))
    if not lines:
        return None
    return Panel(Group(*lines), title="notes", border_style="yellow")


def render_cycle(result: CycleResult, *, verbose: bool = True) -> None:
    setup_console()
    console.rule(f"[bold]cycle {result.ts:%H:%M:%S}[/bold]")
    console.print(stats_panel(result))

    for group in result.groups:
        if group.error:
            console.print(
                Panel(Text(group.error, style="red"),
                      title=f"{', '.join(group.slugs)}", border_style="red")
            )
            continue
        if verbose:
            for surface in group.surfaces:
                console.print(surface_table(surface))
                panel = incoherence_panel(surface)
                if panel:
                    console.print(panel)
        if group.plan is not None:
            console.print(plan_panel(group.plan, group.space))
        if group.fills:
            console.print(
                Text(f"filled {len(group.fills)} orders", style="bold green")
            )

    for touch in result.touches:
        if verbose or touch.plan.trades:
            console.print(touch_table(touch))

    panel = notes_panel(result)
    if panel:
        console.print(panel)


def render_equity_sparkline(curve: Sequence[tuple[datetime, float]], width: int = 60) -> Text:
    """A one-line equity curve. Cheap, and enough to spot a bleed."""
    if len(curve) < 2:
        return Text("(not enough history)", style="dim")
    vals = [v for _, v in curve][-width:]
    lo, hi = min(vals), max(vals)
    blocks = "▁▂▃▄▅▆▇█"
    if hi - lo < 1e-9:
        return Text(blocks[0] * len(vals), style="dim")
    out = "".join(blocks[min(int((v - lo) / (hi - lo) * (len(blocks) - 1)), len(blocks) - 1)] for v in vals)
    style = "green" if vals[-1] >= vals[0] else "red"
    return Text(f"{out}  {fmt_usd(vals[0])} -> {fmt_usd(vals[-1])}", style=style)
