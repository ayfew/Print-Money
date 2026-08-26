"""The daily brief: what happened, and whether anything is worth doing about it.

Most mornings the answer is no, and that is the product rather than a failure of
it.  A daily note that finds a trade every day is not research, it is a
subscription to transaction costs - ``study.py`` has the ten years of arithmetic
showing exactly how that ends.

So this brief is built to say no by default.  It only escalates when a signal
clears the cost of acting on it, and it shows that comparison every time so the
reasoning is visible rather than trusted.
"""
from __future__ import annotations

import logging
import math
import statistics as st
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from ..util import utcnow
from .data import TRADING_DAYS_PER_YEAR, UNIVERSE, Series, fetch_many

log = logging.getLogger("printmoney.brief")

#: A carry basket has to beat this before it is worth opening positions for.
CARRY_ACTION_THRESHOLD = 0.15

#: Below this share of the universe the brief is not a quiet day, it is a broken
#: one. A cloud run once fetched zero markets, printed "Nothing today" and exited
#: zero - a confident verdict computed over no data at all. Anything that reads
#: the exit code, or the calendar entry, would have believed it.
MIN_UNIVERSE_COVERAGE = 0.5

#: How far from its own recent range a market has to be before it is remarkable.
STRETCH_SIGMA = 2.0

#: Where a market's current volatility sits in its own two-year history. These
#: are the only forward-looking numbers the brief will produce, because they are
#: the only ones that survived testing: over ten years and twenty-four markets,
#: this month's volatility predicted next month's with r = +0.76 and was positive
#: in 24 of 24 markets, while this month's return predicted next month's with
#: r = +0.02 and was positive in 7. Danger is forecastable. Direction is not.
RISK_EXTREME = 0.90
RISK_ELEVATED = 0.75
RISK_CALM = 0.20


@dataclass
class MarketLine:
    symbol: str
    name: str
    last: float
    day: float                 # last close vs previous close
    week: float
    month: float
    year: float
    vol_annual: float
    zscore: float              # how stretched vs its 60-day mean, in sigmas
    intraday_share: float      # what fraction of the year's return came intraday
    vol_percentile: float = 0.5   # where today's vol sits in its own 2y history
    drawdown: float = 0.0         # how far below its own 1y high

    @property
    def risk(self) -> str:
        """calm | normal | elevated | extreme, from its own volatility history."""
        if self.vol_percentile >= RISK_EXTREME:
            return "extreme"
        if self.vol_percentile >= RISK_ELEVATED:
            return "elevated"
        if self.vol_percentile <= RISK_CALM:
            return "calm"
        return "normal"

    @property
    def dangerous(self) -> bool:
        return self.risk in ("elevated", "extreme")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "last": round(self.last, 4),
            "day": round(self.day, 5),
            "week": round(self.week, 5),
            "month": round(self.month, 5),
            "year": round(self.year, 5),
            "vol_annual": round(self.vol_annual, 4),
            "zscore": round(self.zscore, 2),
            "intraday_share": round(self.intraday_share, 3),
            "vol_percentile": round(self.vol_percentile, 3),
            "drawdown": round(self.drawdown, 4),
            "risk": self.risk,
        }


@dataclass
class Brief:
    generated_at: datetime = field(default_factory=utcnow)
    lines: list[MarketLine] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    carry: dict[str, Any] | None = None
    error: str = ""
    requested: int = 0
    loaded: int = 0

    @property
    def ok(self) -> bool:
        """Did enough of the universe load for the verdict to mean anything?"""
        if self.error:
            return False
        if not self.requested:
            return bool(self.lines)
        return self.loaded >= self.requested * MIN_UNIVERSE_COVERAGE

    @property
    def coverage(self) -> float:
        return self.loaded / self.requested if self.requested else 0.0

    @property
    def verdict(self) -> str:
        if self.error:
            return f"could not complete: {self.error}"
        if not self.ok:
            return (
                f"could not complete: only {self.loaded} of {self.requested} markets "
                "loaded, so there is no verdict to give"
            )
        if self.actions:
            return f"{len(self.actions)} thing(s) worth acting on today."
        return "Nothing today. No signal clears what it would cost to act on it."

    def movers(self, n: int = 5) -> list[MarketLine]:
        return sorted(self.lines, key=lambda l: -abs(l.day))[:n]

    def stretched(self, n: int = 5) -> list[MarketLine]:
        return [l for l in sorted(self.lines, key=lambda l: -abs(l.zscore))[:n]
                if abs(l.zscore) >= STRETCH_SIGMA]

    def dangerous(self, n: int = 6) -> list[MarketLine]:
        """Markets running hot by their own standards, most extreme first."""
        return sorted(
            (l for l in self.lines if l.dangerous), key=lambda l: -l.vol_percentile
        )[:n]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "verdict": self.verdict,
            "ok": self.ok,
            "requested": self.requested,
            "loaded": self.loaded,
            "coverage": round(self.coverage, 3),
            "actions": self.actions,
            "observations": self.observations,
            "carry": self.carry,
            "error": self.error,
            "lines": [l.to_dict() for l in self.lines],
        }


# --------------------------------------------------------------------------- #
def _pct_change(closes: Sequence[float], back: int) -> float:
    if len(closes) <= back:
        return 0.0
    return closes[-1] / closes[-1 - back] - 1.0


def _line(series: Series) -> MarketLine | None:
    bars = series.bars
    if len(bars) < 70:
        return None
    closes = [b.close for b in bars]
    daily = series.daily_returns()
    recent = daily[-252:] if len(daily) >= 252 else daily
    vol = st.stdev(recent) * math.sqrt(TRADING_DAYS_PER_YEAR) if len(recent) > 2 else 0.0

    window = closes[-60:]
    mean, sd = st.fmean(window), (st.stdev(window) if len(window) > 2 else 0.0)
    z = (closes[-1] - mean) / sd if sd > 0 else 0.0

    intraday = series.intraday_returns()[-252:]
    total = sum(daily[-252:]) or 0.0
    share = (sum(intraday) / total) if abs(total) > 1e-9 else 0.0

    # Where today's volatility sits inside this market's own history. Comparing a
    # market to itself is the only comparison that means anything here - 15% vol
    # is calm for Bitcoin and a crisis for long bonds.
    pctile = 0.5
    if len(daily) >= 130:
        window = 21
        recent = st.stdev(daily[-window:]) if len(daily) >= window else 0.0
        history = [
            st.stdev(daily[i - window:i])
            for i in range(window, len(daily), 5)
        ]
        if history and recent > 0:
            pctile = sum(1 for h in history if h <= recent) / len(history)

    high = max(closes[-252:]) if len(closes) >= 60 else max(closes)
    drawdown = closes[-1] / high - 1.0 if high > 0 else 0.0

    return MarketLine(
        symbol=series.symbol,
        name=series.name,
        last=closes[-1],
        day=_pct_change(closes, 1),
        week=_pct_change(closes, 5),
        month=_pct_change(closes, 21),
        year=_pct_change(closes, 252),
        vol_annual=vol,
        zscore=z,
        intraday_share=share,
        vol_percentile=pctile,
        drawdown=drawdown,
    )


def build_brief(
    *,
    capital: float = 1_000.0,
    include_carry: bool = True,
    universe: Sequence[tuple[str, str]] = UNIVERSE,
    cache_hours: float = 6.0,
) -> Brief:
    """Assemble the morning note."""
    brief = Brief(requested=len(universe))
    try:
        series = fetch_many(universe, rng="2y", cache_hours=cache_hours)
        for s in series.values():
            line = _line(s)
            if line is not None:
                brief.lines.append(line)
        brief.lines.sort(key=lambda l: l.symbol)
        brief.loaded = len(brief.lines)
    except Exception as exc:  # noqa: BLE001
        log.exception("market data failed")
        brief.error = f"{type(exc).__name__}: {exc}"
        return brief

    if not brief.ok:
        # Stop here rather than pricing a carry basket and calling the result a
        # quiet day. A brief with no markets in it is a failure, not a verdict.
        log.error(
            "only %d of %d markets loaded (%.0f%%); refusing to issue a verdict",
            brief.loaded,
            brief.requested,
            100 * brief.coverage,
        )
        brief.observations.append(
            f"Market data incomplete: {brief.loaded} of {brief.requested} loaded. "
            "Check network access to query1.finance.yahoo.com."
        )
        return brief

    _observe(brief)

    if include_carry:
        try:
            from ..carry.scanner import scan

            report = scan(capital=capital, holding_days=30.0)
            brief.carry = report.to_dict()
            net = report.basket_net_annual()
            monthly = report.monthly_usd()
            if net >= CARRY_ACTION_THRESHOLD:
                brief.actions.append(
                    f"Funding carry is paying {net:.1%} a year net - above the "
                    f"{CARRY_ACTION_THRESHOLD:.0%} bar. On {capital:,.0f} that is "
                    f"${monthly:,.2f} a month. Worth opening."
                )
            else:
                brief.observations.append(
                    f"Funding carry basket nets {net:.1%} a year (${monthly:,.2f} a month "
                    f"on ${capital:,.0f}) - below the {CARRY_ACTION_THRESHOLD:.0%} bar, so no."
                )
        except Exception as exc:  # noqa: BLE001
            # Recorded as an error on the carry block, not only as a note. A
            # cloud runner sits on a US address and Binance answers 451 to
            # those, so this path is not hypothetical - and a green run that
            # quietly published a brief with one fewer section is the exact
            # failure mode this project keeps having to design against.
            brief.carry = {"error": f"{type(exc).__name__}: {exc}"}
            brief.observations.append(f"carry scan unavailable: {exc}")

    return brief


def _observe(brief: Brief) -> None:
    """Notes about the state of things. Deliberately not trade instructions."""
    if not brief.lines:
        return

    movers = brief.movers(3)
    if movers:
        brief.observations.append(
            "Biggest moves: "
            + ", ".join(f"{m.name} {m.day:+.2%}" for m in movers)
        )

    stretched = brief.stretched(3)
    for m in stretched:
        where = "above" if m.zscore > 0 else "below"
        brief.observations.append(
            f"{m.name} is {abs(m.zscore):.1f} standard deviations {where} its 60-day mean "
            f"({m.month:+.1%} on the month). Stretched, which is a fact, not a signal - "
            "the study found mean-reversion rules indistinguishable from random."
        )

    intraday_heavy = [l for l in brief.lines if l.intraday_share > 0.9 and abs(l.year) > 0.05]
    if intraday_heavy:
        brief.observations.append(
            f"{len(intraday_heavy)} of {len(brief.lines)} markets made most of the last year's "
            "return during the session rather than overnight - the exception to the usual pattern."
        )

    danger = brief.dangerous(4)
    if danger:
        brief.observations.append(
            "Running hot by their own standards: "
            + ", ".join(
                f"{l.name} ({l.vol_annual:.0%} vol, {l.vol_percentile:.0%} of its own history)"
                for l in danger
            )
            + ". Volatility is the one thing here that is forecastable - this month's "
            "predicted next month's with r = +0.76 across 24 markets, while return "
            "managed +0.02. Treat this as where the risk is, never as a direction."
        )

    calm = [l for l in brief.lines if l.risk == "calm"]
    if calm:
        brief.observations.append(
            f"{len(calm)} markets are quiet by their own standards - a still tape is the "
            "worst environment for anything that pays a toll per trade."
        )
