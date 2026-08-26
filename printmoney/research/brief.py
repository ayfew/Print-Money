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

#: How far from its own recent range a market has to be before it is remarkable.
STRETCH_SIGMA = 2.0


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
        }


@dataclass
class Brief:
    generated_at: datetime = field(default_factory=utcnow)
    lines: list[MarketLine] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    carry: dict[str, Any] | None = None
    error: str = ""

    @property
    def verdict(self) -> str:
        if self.error:
            return f"could not complete: {self.error}"
        if self.actions:
            return f"{len(self.actions)} thing(s) worth acting on today."
        return "Nothing today. No signal clears what it would cost to act on it."

    def movers(self, n: int = 5) -> list[MarketLine]:
        return sorted(self.lines, key=lambda l: -abs(l.day))[:n]

    def stretched(self, n: int = 5) -> list[MarketLine]:
        return [l for l in sorted(self.lines, key=lambda l: -abs(l.zscore))[:n]
                if abs(l.zscore) >= STRETCH_SIGMA]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "verdict": self.verdict,
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
    )


def build_brief(
    *,
    capital: float = 1_000.0,
    include_carry: bool = True,
    universe: Sequence[tuple[str, str]] = UNIVERSE,
    cache_hours: float = 6.0,
) -> Brief:
    """Assemble the morning note."""
    brief = Brief()
    try:
        series = fetch_many(universe, rng="2y", cache_hours=cache_hours)
        for s in series.values():
            line = _line(s)
            if line is not None:
                brief.lines.append(line)
        brief.lines.sort(key=lambda l: l.symbol)
    except Exception as exc:  # noqa: BLE001
        log.exception("market data failed")
        brief.error = f"{type(exc).__name__}: {exc}"
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

    calm = [l for l in brief.lines if l.vol_annual < 0.10]
    wild = [l for l in brief.lines if l.vol_annual > 0.45]
    if wild:
        brief.observations.append(
            "Running hot: " + ", ".join(f"{l.name} {l.vol_annual:.0%} vol" for l in wild[:3])
        )
    if calm:
        brief.observations.append(
            f"{len(calm)} markets under 10% annualised volatility - a quiet tape is the "
            "worst environment for anything that pays a toll per trade."
        )
