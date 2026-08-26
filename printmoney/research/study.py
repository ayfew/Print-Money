"""The cost test every idea has to pass, and the evidence behind it.

Findings, from ten years of daily bars on twenty-four broad markets. They are
reproduced by ``pm study`` rather than asserted, and the numbers below are what
that command printed the day it was written.

**The gross return of holding a market does not depend on how often you touch
it.** Holding for one day, one week, one quarter or a year all grossed about
7.5% a year over the sample. Only the toll changed:

    holding period   round trips/yr   gross     net @10bp   net @30bp
         1 day             252        +7.7%      -100.0%     -100.0%
         5 days             50        +7.8%        +2.5%       -7.4%
        21 days             12        +7.5%        +6.3%       +3.7%
        63 days              4        +7.5%        +7.1%       +6.3%
       252 days              1        +6.2%        +6.1%       +5.9%

That is the whole thing. A day trader and a buy-and-hold investor own the same
returns; one of them pays 25% a year for the privilege.

Three attempts to escape it, all of which failed:

* **Pick better.** Seven implementable daily selection rules - momentum,
  reversal, gap up, gap down, trend, and the rest - all landed inside the range
  produced by choosing a ticker at random. They are not rules, they are coins.
* **Pick more volatile things.** The correlation between intraday volatility and
  intraday return was **-0.48**. More movement is more movement, not more return.
* **Time it.** Perfect foresight over which days to trade would return thousands
  of percent, so the information is there. No implementable rule captured any of
  it.

The one loophole that is real: **trade less often.** The toll per trade never
changes, so the only lever is how many tolls you pay a year.
"""
from __future__ import annotations

import logging
import math
import random
import statistics as st
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .data import TRADING_DAYS_PER_YEAR, Bar, Series, align

log = logging.getLogger("printmoney.study")

#: Round-trip costs a Thai retail account might actually face.
FEE_SCENARIOS: list[tuple[float, str]] = [
    (0.0002, "institutional (2 bp)"),
    (0.0010, "good retail broker (10 bp)"),
    (0.0030, "typical retail (30 bp)"),
    (0.0100, "Thai retail on US stocks incl FX (100 bp)"),
]


# --------------------------------------------------------------------------- #

def _prev_traded(bars: Sequence[Any], i: int) -> float:
    """Yesterday's close as it actually printed, for comparing against an open.

    ``Bar.close`` is dividend-adjusted and ``Bar.open`` is not, so an overnight
    gap has to be measured on the raw pair or every ex-dividend date invents a
    move nobody could have traded.
    """
    prev = bars[i - 1]
    return getattr(prev, "raw_close", 0.0) or prev.close

def annualise(returns: Sequence[float], periods_per_year: float = TRADING_DAYS_PER_YEAR) -> float:
    """Geometric annual return. Returns -100% if the equity curve hits zero."""
    if not returns:
        return 0.0
    v = 1.0
    for r in returns:
        v *= 1.0 + r
        if v <= 1e-12:
            return -1.0
    return v ** (periods_per_year / len(returns)) - 1.0


def compound(returns: Sequence[float], start: float = 1.0) -> float:
    v = start
    for r in returns:
        v *= 1.0 + r
        if v <= 0:
            return 0.0
    return v


def sharpe(returns: Sequence[float], periods_per_year: float = TRADING_DAYS_PER_YEAR) -> float:
    if len(returns) < 30:
        return 0.0
    sd = st.stdev(returns)
    return (st.fmean(returns) / sd) * math.sqrt(periods_per_year) if sd > 0 else 0.0


def max_drawdown(returns: Sequence[float]) -> float:
    peak = v = 1.0
    worst = 0.0
    for r in returns:
        v *= 1.0 + r
        peak = max(peak, v)
        worst = min(worst, v / peak - 1.0)
    return worst


# --------------------------------------------------------------------------- #
@dataclass
class HoldingPeriodRow:
    days: int
    trips_per_year: float
    gross: float
    net: dict[float, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "trips_per_year": round(self.trips_per_year, 2),
            "gross": round(self.gross, 5),
            "net": {str(k): round(v, 5) for k, v in self.net.items()},
        }


def holding_period_sweep(
    aligned: dict[str, list[Bar]],
    *,
    periods: Sequence[int] = (1, 5, 10, 21, 63, 126, 252),
    fees: Sequence[float] = (0.0010, 0.0030),
) -> list[HoldingPeriodRow]:
    """The central table: same markets, same decade, only the frequency changes."""
    rows: list[HoldingPeriodRow] = []
    for hold in periods:
        rets: list[float] = []
        for bars in aligned.values():
            closes = [b.close for b in bars]
            for i in range(0, len(closes) - hold, hold):
                rets.append(closes[i + hold] / closes[i] - 1.0)
        if not rets:
            continue
        trips = TRADING_DAYS_PER_YEAR / hold
        row = HoldingPeriodRow(
            days=hold, trips_per_year=trips, gross=annualise(rets, trips)
        )
        for fee in fees:
            row.net[fee] = annualise([r - fee for r in rets], trips)
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
@dataclass
class SessionSplit:
    """How the day's return divides between the two sessions."""

    intraday: float = 0.0
    overnight: float = 0.0
    buy_hold: float = 0.0
    intraday_net: dict[float, float] = field(default_factory=dict)
    win_rate: float = 0.0
    drawdown: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "intraday": round(self.intraday, 5),
            "overnight": round(self.overnight, 5),
            "buy_hold": round(self.buy_hold, 5),
            "intraday_net": {str(k): round(v, 5) for k, v in self.intraday_net.items()},
            "win_rate": round(self.win_rate, 4),
            "drawdown": round(self.drawdown, 4),
        }


def session_split(aligned: dict[str, list[Bar]]) -> SessionSplit:
    """Equal-weight basket, split into buy-at-open and buy-at-close halves."""
    symbols = list(aligned)
    n = len(next(iter(aligned.values())))
    intraday = [st.fmean(aligned[s][i].intraday for s in symbols) for i in range(n)]
    overnight = [
        st.fmean(aligned[s][i].open / _prev_traded(aligned[s], i) - 1.0
                 for s in symbols)
        for i in range(1, n)
    ]
    hold = [
        st.fmean(aligned[s][i].close / aligned[s][i - 1].close - 1.0 for s in symbols)
        for i in range(1, n)
    ]
    out = SessionSplit(
        intraday=annualise(intraday),
        overnight=annualise(overnight),
        buy_hold=annualise(hold),
        win_rate=sum(1 for r in intraday if r > 0) / max(len(intraday), 1),
        drawdown=max_drawdown(intraday),
    )
    for fee, _label in FEE_SCENARIOS:
        out.intraday_net[fee] = annualise([r - fee for r in intraday])
    return out


# --------------------------------------------------------------------------- #
@dataclass
class RuleResult:
    name: str
    gross: float
    net: float
    trades_per_year: float
    inside_noise: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "gross": round(self.gross, 5),
            "net": round(self.net, 5),
            "trades_per_year": round(self.trades_per_year, 1),
            "inside_noise": self.inside_noise,
        }


@dataclass
class NoiseBand:
    """What randomly picking a ticker every day produces, for comparison."""

    median: float = 0.0
    low: float = 0.0
    high: float = 0.0
    samples: int = 0

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high

    def to_dict(self) -> dict[str, Any]:
        return {
            "median": round(self.median, 5),
            "low": round(self.low, 5),
            "high": round(self.high, 5),
            "samples": self.samples,
        }


def noise_band(
    aligned: dict[str, list[Bar]], *, trials: int = 400, seed: int = 7
) -> NoiseBand:
    """The distribution of dumb luck.

    Any daily rule has to beat this to be worth the word "strategy". Most of the
    published ones do not, and the only way to find out is to draw the band.
    """
    rng = random.Random(seed)
    symbols = list(aligned)
    n = len(next(iter(aligned.values())))
    results = []
    for _ in range(trials):
        results.append(
            annualise([aligned[rng.choice(symbols)][i].intraday for i in range(1, n)])
        )
    results.sort()
    k = max(1, int(trials * 0.025))
    return NoiseBand(
        median=st.median(results), low=results[k], high=results[-k], samples=trials
    )


def evaluate_rule(
    aligned: dict[str, list[Bar]],
    name: str,
    pick: Callable[[int, dict[str, list[Bar]]], str | None],
    *,
    fee: float = 0.0010,
    warmup: int = 1,
    band: NoiseBand | None = None,
) -> RuleResult:
    """Run one daily selection rule and charge it for every trade it makes."""
    n = len(next(iter(aligned.values())))
    gross: list[float] = []
    net: list[float] = []
    trades = 0
    for i in range(warmup, n):
        symbol = pick(i, aligned)
        if symbol is None:
            gross.append(0.0)
            net.append(0.0)
            continue
        r = aligned[symbol][i].intraday
        gross.append(r)
        net.append(r - fee)
        trades += 1
    days = max(n - warmup, 1)
    result = RuleResult(
        name=name,
        gross=annualise(gross),
        net=annualise(net),
        trades_per_year=trades / days * TRADING_DAYS_PER_YEAR,
    )
    if band is not None:
        result.inside_noise = band.contains(result.gross)
    return result


def standard_rules() -> dict[str, Callable[[int, dict[str, list[Bar]]], str | None]]:
    """The daily rules a person actually reaches for first."""

    def best_yesterday(i, a):
        return max(a, key=lambda s: a[s][i - 1].intraday)

    def worst_yesterday(i, a):
        return min(a, key=lambda s: a[s][i - 1].intraday)

    def biggest_gap_up(i, a):
        return max(a, key=lambda s: a[s][i].open / _prev_traded(a[s], i))

    def biggest_gap_down(i, a):
        return min(a, key=lambda s: a[s][i].open / _prev_traded(a[s], i))

    def strongest_trend(i, a):
        if i < 22:
            return None
        return max(a, key=lambda s: a[s][i - 1].close / a[s][i - 21].close)

    def weakest_trend(i, a):
        if i < 22:
            return None
        return min(a, key=lambda s: a[s][i - 1].close / a[s][i - 21].close)

    return {
        "yesterday's biggest gainer": best_yesterday,
        "yesterday's biggest loser": worst_yesterday,
        "biggest overnight gap up": biggest_gap_up,
        "biggest overnight gap down": biggest_gap_down,
        "strongest 20-day trend": strongest_trend,
        "weakest 20-day trend": weakest_trend,
    }


# --------------------------------------------------------------------------- #
@dataclass
class StudyReport:
    symbols: list[str] = field(default_factory=list)
    days: int = 0
    split: SessionSplit = field(default_factory=SessionSplit)
    holding: list[HoldingPeriodRow] = field(default_factory=list)
    rules: list[RuleResult] = field(default_factory=list)
    band: NoiseBand = field(default_factory=NoiseBand)
    persistence: Any = None

    @property
    def years(self) -> float:
        return self.days / TRADING_DAYS_PER_YEAR

    def breakeven_gross(self, fee: float = 0.0010) -> float:
        """What a daily strategy must gross before it has paid for itself."""
        return fee * TRADING_DAYS_PER_YEAR

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": self.symbols,
            "days": self.days,
            "years": round(self.years, 2),
            "split": self.split.to_dict(),
            "holding": [h.to_dict() for h in self.holding],
            "rules": [r.to_dict() for r in self.rules],
            "noise_band": self.band.to_dict(),
            "persistence": self.persistence.to_dict() if self.persistence else None,
        }


def run_study(series: Sequence[Series], *, fee: float = 0.0010) -> StudyReport:
    stamps, aligned = align(series)
    if len(stamps) < 250:
        raise ValueError(f"only {len(stamps)} common trading days; need at least 250")

    band = noise_band(aligned)
    report = StudyReport(
        symbols=sorted(aligned),
        days=len(stamps),
        split=session_split(aligned),
        holding=holding_period_sweep(aligned),
        band=band,
    )
    for name, rule in standard_rules().items():
        report.rules.append(evaluate_rule(aligned, name, rule, fee=fee, warmup=22, band=band))
    report.rules.sort(key=lambda r: -r.gross)
    report.persistence = persistence(aligned)
    return report


# --------------------------------------------------------------------------- #
# what IS forecastable
# --------------------------------------------------------------------------- #
@dataclass
class Persistence:
    """Does this month's number say anything about next month's?

    Run over the same markets, the same windows and the same method for both
    volatility and return, so the comparison is like for like. Over ten years and
    twenty-four markets the answer came out as +0.76 for volatility and +0.02 for
    return, with volatility positive in 24 of 24 markets and return positive in
    7 - which is why this project flags danger and refuses to pick winners.
    """

    vol_r: float = 0.0
    return_r: float = 0.0
    vol_positive: int = 0
    return_positive: int = 0
    markets: int = 0
    buckets: list[tuple[str, float, float, float]] = field(default_factory=list)

    @property
    def volatility_is_forecastable(self) -> bool:
        return self.vol_r > 0.3 and self.vol_positive >= 0.8 * max(self.markets, 1)

    @property
    def return_is_forecastable(self) -> bool:
        return abs(self.return_r) > 0.3

    def to_dict(self) -> dict[str, Any]:
        return {
            "vol_r": round(self.vol_r, 4),
            "return_r": round(self.return_r, 4),
            "vol_positive": self.vol_positive,
            "return_positive": self.return_positive,
            "markets": self.markets,
            "volatility_is_forecastable": self.volatility_is_forecastable,
            "return_is_forecastable": self.return_is_forecastable,
            "buckets": [
                {"bucket": b, "vol_now": round(v, 5), "vol_next": round(n, 5),
                 "return_next": round(r, 5)}
                for b, v, n, r in self.buckets
            ],
        }


def persistence(
    aligned: dict[str, list[Bar]], *, window: int = 21, step: int = 5
) -> Persistence:
    """Correlate this window with the next one, for volatility and for return."""
    out = Persistence()
    all_vol_now: list[float] = []
    all_vol_next: list[float] = []
    all_ret_now: list[float] = []
    all_ret_next: list[float] = []

    for bars in aligned.values():
        closes = [b.close for b in bars]
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
        if len(rets) < 4 * window:
            continue
        vol_now, vol_next, ret_now, ret_next = [], [], [], []
        for i in range(window, len(rets) - window, step):
            past, future = rets[i - window:i], rets[i:i + window]
            vol_now.append(st.stdev(past))
            vol_next.append(st.stdev(future))
            ret_now.append(sum(past))
            ret_next.append(sum(future))
        if len(vol_now) < 10:
            continue
        out.markets += 1
        if _corr(vol_now, vol_next) > 0:
            out.vol_positive += 1
        if _corr(ret_now, ret_next) > 0:
            out.return_positive += 1
        all_vol_now += vol_now
        all_vol_next += vol_next
        all_ret_now += ret_now
        all_ret_next += ret_next

    out.vol_r = _corr(all_vol_now, all_vol_next)
    out.return_r = _corr(all_ret_now, all_ret_next)

    # Sorted by today's volatility, what does next month look like?
    rows = sorted(zip(all_vol_now, all_vol_next, all_ret_next), key=lambda t: t[0])
    if len(rows) >= 25:
        n = len(rows) // 5
        labels = ["calmest 20%", "second", "middle", "fourth", "wildest 20%"]
        root = math.sqrt(TRADING_DAYS_PER_YEAR)
        for i, label in enumerate(labels):
            chunk = rows[i * n:(i + 1) * n] if i < 4 else rows[4 * n:]
            out.buckets.append((
                label,
                st.fmean(t[0] for t in chunk) * root,
                st.fmean(t[1] for t in chunk) * root,
                st.fmean(t[2] for t in chunk),
            ))
    return out


def _corr(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) < 4 or len(a) != len(b):
        return 0.0
    try:
        return st.correlation(list(a), list(b))
    except st.StatisticsError:
        return 0.0
