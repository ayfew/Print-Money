"""Funding rates across every exchange that publishes them, not just Binance.

``scanner.py`` reads Binance directly and does it well, but it can only ever
answer "what does Binance pay".  The number that matters for a delta-neutral
book is different: funding on the same contract differs between venues, and the
*spread* between the best payer and the worst is a position you can actually
take - long the perp where funding is negative, short it where funding is
positive, flat on price either way.

ccxt is the right dependency for exactly this and nothing more.  It is one
interface over 103 exchanges, which is a genuinely hard problem nobody should
solve again by hand; it is also, notably, not a strategy library, so taking it
on does not import anybody's opinion about what to trade.

Two honest limits, both structural rather than fixable:

*The spread is not free money.*  Capturing it means capital on two venues, two
sets of withdrawal risk, and a funding leg that can flip between the eight-hour
settlements.  This module reports the spread; it does not claim you can keep it.

*A venue nobody trades on can print any number it likes.*  Funding on an
illiquid perp is a quote, not a market, so anything without real open interest
is filtered out before it is allowed into a comparison.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

log = logging.getLogger("printmoney.carry.venues")

#: Exchanges worth asking. Chosen for size and for actually implementing the
#: funding-rate endpoint; ccxt lists a hundred more that either do not run
#: perpetuals or do not publish funding through the unified method.
VENUES = ("binance", "bybit", "okx", "gate", "bitget", "kucoinfutures", "hyperliquid")

#: Funding settles three times a day on every venue here, which is what turns a
#: per-interval rate into an annual one.
SETTLEMENTS_PER_YEAR = 3 * 365

#: Below this day's turnover, the quote is not a market.
#:
#: Without it the top of the table was STORJ at -2190% a year on one venue and
#: zero on another, which is a momentary dislocation on a microcap perp rather
#: than carry anybody can collect. Funding is quoted per settlement and
#: annualising a single spike produces numbers in the hundreds of percent; the
#: filter is what stops those spikes being presented as opportunities.
MIN_VOLUME_USD = 25_000_000.0

#: And a ceiling on plausibility. Anything past this is a dislocation being
#: annualised, not a rate a position could actually earn for a year.
MAX_PLAUSIBLE_ANNUAL = 2.0


@dataclass(frozen=True)
class VenueRate:
    venue: str
    symbol: str
    rate: float                 # per settlement, as a fraction
    mark: float = 0.0
    volume: float = 0.0         # 24h turnover in quote currency

    @property
    def plausible(self) -> bool:
        return abs(self.annual) <= MAX_PLAUSIBLE_ANNUAL

    @property
    def annual(self) -> float:
        return self.rate * SETTLEMENTS_PER_YEAR

    def to_dict(self) -> dict[str, Any]:
        return {"venue": self.venue, "symbol": self.symbol,
                "rate": round(self.rate, 8), "annual": round(self.annual, 5),
                "mark": round(self.mark, 4), "volume": round(self.volume, 0)}


@dataclass
class Spread:
    """The same contract, funded differently on two venues."""

    symbol: str
    long_venue: str             # pay the least (or get paid) - go long here
    short_venue: str            # collect the most - go short here
    long_annual: float
    short_annual: float

    @property
    def spread_annual(self) -> float:
        return self.short_annual - self.long_annual

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "long_venue": self.long_venue,
                "short_venue": self.short_venue,
                "long_annual": round(self.long_annual, 5),
                "short_annual": round(self.short_annual, 5),
                "spread_annual": round(self.spread_annual, 5)}


@dataclass
class VenueReport:
    rates: list[VenueRate] = field(default_factory=list)
    spreads: list[Spread] = field(default_factory=list)
    reachable: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    def by_symbol(self) -> dict[str, list[VenueRate]]:
        out: dict[str, list[VenueRate]] = {}
        for r in self.rates:
            out.setdefault(r.symbol, []).append(r)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"reachable": self.reachable, "failed": self.failed,
                "rates": [r.to_dict() for r in self.rates],
                "spreads": [s.to_dict() for s in self.spreads]}


# --------------------------------------------------------------------------- #
def available() -> bool:
    import importlib.util

    return importlib.util.find_spec("ccxt") is not None


def _base(symbol: str) -> str:
    """BTC/USDT:USDT -> BTC. Venues spell the same contract several ways."""
    return symbol.split("/")[0].split(":")[0].upper()


def _volumes(ex: Any) -> dict[str, float]:
    """Twenty-four-hour turnover per contract, in quote currency."""
    try:
        tickers = ex.fetch_tickers()
    except Exception:                              # noqa: BLE001 - optional
        return {}
    out: dict[str, float] = {}
    for symbol, t in (tickers or {}).items():
        vol = t.get("quoteVolume")
        if vol is None and t.get("baseVolume") and t.get("last"):
            vol = float(t["baseVolume"]) * float(t["last"])
        if vol:
            out[symbol] = float(vol)
    return out


def _fetch_one(name: str, *, timeout_ms: int = 20_000,
               min_volume: float = MIN_VOLUME_USD) -> list[VenueRate]:
    import ccxt

    ex = getattr(ccxt, name)({"enableRateLimit": True, "timeout": timeout_ms,
                              "options": {"defaultType": "swap"}})
    if not ex.has.get("fetchFundingRates"):
        raise RuntimeError("no unified fetchFundingRates")
    payload = ex.fetch_funding_rates()
    volumes = _volumes(ex)

    out: list[VenueRate] = []
    for symbol, row in (payload or {}).items():
        rate = row.get("fundingRate")
        if rate is None:
            continue
        # USD-margined linear perps only. An inverse or a coin-margined contract
        # has a different payoff and cannot be compared like for like.
        if ":" in symbol and not symbol.endswith(("USDT", "USDC", "USD")):
            continue
        volume = volumes.get(symbol, 0.0)
        if volumes and volume < min_volume:
            continue
        out.append(VenueRate(venue=name, symbol=_base(symbol),
                             rate=float(rate),
                             mark=float(row.get("markPrice") or 0.0),
                             volume=volume))
    return out


def scan(venues: Sequence[str] = VENUES, *, min_venues: int = 3,
         top: int = 15) -> VenueReport:
    """Funding on every reachable venue, and the widest same-contract spreads.

    A venue that times out or changes its API costs its own row and nothing
    else; the comparison is simply made over the ones that answered, and the
    ones that did not are named in the report rather than silently dropped.
    """
    if not available():
        raise RuntimeError("ccxt is not installed. `pip install ccxt`")

    report = VenueReport()
    for name in venues:
        try:
            rows = _fetch_one(name)
        except Exception as exc:                   # noqa: BLE001 - never fatal
            report.failed[name] = f"{type(exc).__name__}: {exc}"[:120]
            log.warning("venue %s unavailable (%s)", name, exc)
            continue
        report.reachable.append(name)
        report.rates.extend(rows)

    for symbol, rows in report.by_symbol().items():
        # One rate per venue, and enough venues that the widest gap is not just
        # the only two quotes that existed.
        best = {}
        for r in rows:
            if r.venue not in best or abs(r.annual) > abs(best[r.venue].annual):
                best[r.venue] = r
        if len(best) < min_venues:
            continue
        ordered = sorted((r for r in best.values() if r.plausible),
                         key=lambda r: r.annual)
        if len(ordered) < min_venues:
            continue
        lo, hi = ordered[0], ordered[-1]
        report.spreads.append(Spread(
            symbol=symbol, long_venue=lo.venue, short_venue=hi.venue,
            long_annual=lo.annual, short_annual=hi.annual))

    report.spreads.sort(key=lambda s: -s.spread_annual)
    report.spreads = report.spreads[:top]
    return report
