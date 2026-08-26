"""Rank every perpetual by what a hedged position would actually pay.

Three filters do almost all the work, and each of them removes most of what looks
attractive on a funding-rate leaderboard:

1. **Can it be hedged at all?**  A coin paying 160% a year with no spot market
   cannot be held delta-neutral.  Shorting it unhedged is not carry, it is a
   naked short on an illiquid token, and the reason the rate is 160% is that
   everyone else can see the same trade and is refusing to take the other side.

2. **Is the rate stable?**  Funding resets every eight hours.  Today's number is
   a snapshot, not an income stream.  The honest estimate is the average over
   weeks, and coins with the highest current rates usually have the least stable
   ones.

3. **Does it survive fees?**  Getting in and out is four transactions.  Held for
   a week that cost is enormous, held for a quarter it is a rounding error, and
   the position has to be sized around that rather than around the headline.
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..util import USER_AGENT, retry, safe_float

log = logging.getLogger("printmoney.carry")

BINANCE_FUTURES = "https://fapi.binance.com"
BINANCE_SPOT = "https://api.binance.com"

#: Funding settles three times a day on Binance.
SETTLEMENTS_PER_YEAR = 3 * 365.25

#: Binance published taker fees. Maker is cheaper but a hedge that does not fill
#: is not a hedge, so cost the pessimistic case.
SPOT_TAKER_FEE = 0.0010
PERP_TAKER_FEE = 0.0005

#: Entering costs a spot buy plus a perp short; exiting costs the reverse.
ROUND_TRIP_COST = 2.0 * (SPOT_TAKER_FEE + PERP_TAKER_FEE)


# --------------------------------------------------------------------------- #
def _get(client: httpx.Client, url: str, params: dict[str, Any] | None = None) -> Any:
    def call() -> Any:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    return retry(call, attempts=3, what=f"GET {url}")


def fetch_funding_snapshot(client: httpx.Client) -> dict[str, dict[str, float]]:
    """Current funding rate and mark price for every USDT perpetual."""
    rows = _get(client, f"{BINANCE_FUTURES}/fapi/v1/premiumIndex")
    out: dict[str, dict[str, float]] = {}
    for row in rows or []:
        symbol = str(row.get("symbol") or "")
        if not symbol.endswith("USDT"):
            continue
        rate = safe_float(row.get("lastFundingRate"))
        mark = safe_float(row.get("markPrice"))
        if rate is None or mark is None or mark <= 0:
            continue
        out[symbol] = {"funding": rate, "mark": mark}
    return out


def fetch_spot_symbols(client: httpx.Client) -> set[str]:
    """USDT spot pairs that are actually trading - the hedge has to exist."""
    info = _get(client, f"{BINANCE_SPOT}/api/v3/exchangeInfo", {"permissions": "SPOT"})
    return {
        str(s.get("symbol"))
        for s in (info.get("symbols") or [])
        if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
    }


def fetch_perp_volume(client: httpx.Client) -> dict[str, float]:
    """24h quote volume per perpetual, as a liquidity sanity check."""
    rows = _get(client, f"{BINANCE_FUTURES}/fapi/v1/ticker/24hr")
    return {
        str(r.get("symbol")): safe_float(r.get("quoteVolume"), 0.0) or 0.0
        for r in rows or []
    }


def fetch_funding_history(
    client: httpx.Client, symbol: str, limit: int = 90
) -> list[float]:
    """Recent settled funding rates, oldest first. 90 settlements is 30 days."""
    rows = _get(
        client,
        f"{BINANCE_FUTURES}/fapi/v1/fundingRate",
        {"symbol": symbol, "limit": min(limit, 1000)},
    )
    rates = [safe_float(r.get("fundingRate")) for r in rows or []]
    return [r for r in rates if r is not None]


# --------------------------------------------------------------------------- #
@dataclass
class Carry:
    """One hedged position, priced honestly."""

    symbol: str
    funding_now: float
    mark: float
    hedgeable: bool
    perp_volume_24h: float
    history: list[float] = field(default_factory=list)

    # ---- rates ------------------------------------------------------- #
    @property
    def annual_now(self) -> float:
        return self.funding_now * SETTLEMENTS_PER_YEAR

    @property
    def annual_mean(self) -> float:
        """The number to plan on: the average of what it has actually paid."""
        if not self.history:
            return self.annual_now
        return statistics.fmean(self.history) * SETTLEMENTS_PER_YEAR

    @property
    def annual_median(self) -> float:
        if not self.history:
            return self.annual_now
        return statistics.median(self.history) * SETTLEMENTS_PER_YEAR

    @property
    def positive_fraction(self) -> float:
        if not self.history:
            return 1.0 if self.funding_now > 0 else 0.0
        return sum(1 for r in self.history if r > 0) / len(self.history)

    @property
    def volatility(self) -> float:
        """Standard deviation of the annualised rate. High means it is a snapshot."""
        if len(self.history) < 3:
            return math.inf
        return statistics.stdev(self.history) * SETTLEMENTS_PER_YEAR

    def net_annual(self, holding_days: float = 30.0) -> float:
        """Expected rate after the four transactions it takes to open and close."""
        if holding_days <= 0:
            return -math.inf
        drag = ROUND_TRIP_COST * (365.25 / holding_days)
        return self.annual_mean - drag

    # ---- judgement --------------------------------------------------- #
    @property
    def risks(self) -> list[str]:
        out: list[str] = []
        if not self.hedgeable:
            out.append("NO SPOT MARKET - cannot be hedged, this is a naked short")
        if self.perp_volume_24h < 5_000_000:
            out.append(f"thin: ${self.perp_volume_24h/1e6:.1f}M daily volume")
        if self.history and self.positive_fraction < 0.7:
            out.append(f"paid only {self.positive_fraction:.0%} of the time")
        if self.history and self.volatility > 3 * abs(self.annual_mean):
            out.append("rate swings far wider than its average")
        if self.history and self.annual_now > 3 * max(self.annual_mean, 0.01):
            out.append("today is an outlier, not the norm")
        if len(self.history) < 30:
            out.append("short history")
        return out

    @property
    def tradable(self) -> bool:
        return self.hedgeable and self.perp_volume_24h >= 5_000_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "annual_now": round(self.annual_now, 5),
            "annual_mean": round(self.annual_mean, 5),
            "annual_median": round(self.annual_median, 5),
            "net_annual_30d": round(self.net_annual(30), 5),
            "net_annual_90d": round(self.net_annual(90), 5),
            "positive_fraction": round(self.positive_fraction, 3),
            "volatility": None if math.isinf(self.volatility) else round(self.volatility, 4),
            "hedgeable": self.hedgeable,
            "perp_volume_24h": round(self.perp_volume_24h, 0),
            "samples": len(self.history),
            "tradable": self.tradable,
            "risks": self.risks,
        }


# --------------------------------------------------------------------------- #
@dataclass
class CarryReport:
    scanned: int = 0
    hedgeable: int = 0
    candidates: list[Carry] = field(default_factory=list)
    holding_days: float = 30.0
    capital: float = 1_000.0

    def basket(self, n: int = 10) -> list[Carry]:
        return self.candidates[:n]

    def basket_net_annual(self, n: int = 10) -> float:
        picks = self.basket(n)
        if not picks:
            return 0.0
        return statistics.fmean(c.net_annual(self.holding_days) for c in picks)

    def monthly_usd(self, n: int = 10) -> float:
        return self.capital * self.basket_net_annual(n) / 12.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "hedgeable": self.hedgeable,
            "holding_days": self.holding_days,
            "capital": self.capital,
            "basket_net_annual": round(self.basket_net_annual(), 5),
            "monthly_usd": round(self.monthly_usd(), 2),
            "candidates": [c.to_dict() for c in self.candidates],
        }


def scan(
    *,
    capital: float = 1_000.0,
    holding_days: float = 30.0,
    history_depth: int = 90,
    deep_scan: int = 25,
    min_volume: float = 5_000_000.0,
    timeout: float = 30.0,
) -> CarryReport:
    """Rank hedgeable perpetuals by what they would actually pay, after costs."""
    report = CarryReport(holding_days=holding_days, capital=capital)
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        snapshot = fetch_funding_snapshot(client)
        spot = fetch_spot_symbols(client)
        volume = fetch_perp_volume(client)
        report.scanned = len(snapshot)

        carries: list[Carry] = []
        for symbol, row in snapshot.items():
            hedgeable = symbol in spot
            if hedgeable:
                report.hedgeable += 1
            carries.append(
                Carry(
                    symbol=symbol,
                    funding_now=row["funding"],
                    mark=row["mark"],
                    hedgeable=hedgeable,
                    perp_volume_24h=volume.get(symbol, 0.0),
                )
            )

        # Only the plausible ones are worth a history call each.
        shortlist = [
            c for c in carries if c.tradable and c.funding_now > 0
        ]
        shortlist.sort(key=lambda c: -c.funding_now)
        shortlist = shortlist[:deep_scan]
        log.info("pulling funding history for %d candidates", len(shortlist))
        for c in shortlist:
            try:
                c.history = fetch_funding_history(client, c.symbol, history_depth)
            except Exception as exc:  # noqa: BLE001
                log.debug("history failed for %s: %s", c.symbol, exc)

    # Rank on what it has paid on average, net of the cost of being there.
    shortlist.sort(key=lambda c: -c.net_annual(holding_days))
    report.candidates = shortlist
    return report
