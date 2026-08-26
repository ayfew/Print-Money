"""The same carry scan, on whichever exchange will actually talk to us.

``scanner.py`` reads Binance directly and reads it well.  It also stops dead the
moment Binance will not answer, and there is one caller for which that is not
hypothetical: the cloud runner.  GitHub's hosted runners sit on US datacentre
addresses and Binance returns 451 to those, so the scheduled morning brief has
been publishing without its funding section - green run, exit zero, quietly
smaller product.

This is the fallback.  It asks ccxt for whatever venue is reachable, ranks
perpetuals the same way, and hands back the same ``CarryReport`` so nothing
downstream has to know which path produced it.

Two things it deliberately keeps from the original rather than simplifying away:

*History, not a snapshot.*  A single funding print annualised is a number that
has already embarrassed this project once - a microcap read -124% a year when
its median was +19.7%.  Candidates are ranked on the median of their recent
settlements.

*The hedge has to exist.*  A perpetual with no spot market cannot be hedged, so
collecting its funding is a naked short with a carry story attached. Those are
scanned, counted, and excluded.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any, Sequence

from .scanner import Carry, CarryReport

log = logging.getLogger("printmoney.carry.anyvenue")

#: Tried in order, first one that answers wins. Binance is first because when it
#: is reachable it is the deepest book and the most quoted reference; the rest
#: are here because they are not US-restricted.
VENUES = ("binance", "bybit", "gate", "bitget", "hyperliquid")

#: Funding settles three times a day on all of them.
SETTLEMENTS_PER_YEAR = 3 * 365

#: How many past settlements to rank on.
HISTORY = 60

#: How many candidates get a history call each.
DEEP_SCAN = 20


def available() -> bool:
    import importlib.util

    return importlib.util.find_spec("ccxt") is not None


def _base(symbol: str) -> str:
    return symbol.split("/")[0].split(":")[0].upper()


def _linear_usd(symbol: str) -> bool:
    """USD-margined linear perps only; an inverse contract pays differently."""
    return ":" in symbol and symbol.endswith(("USDT", "USDC", "USD"))


def scan(*, capital: float = 1_000.0, holding_days: float = 30.0,
         venues: Sequence[str] = VENUES, min_volume: float = 5_000_000.0,
         timeout_ms: int = 20_000) -> CarryReport:
    """Rank hedgeable perpetuals on the first venue that answers."""
    if not available():
        raise RuntimeError("ccxt is not installed")

    import ccxt

    report = CarryReport(holding_days=holding_days, capital=capital)
    last_error: Exception | None = None

    for name in venues:
        try:
            ex = getattr(ccxt, name)({"enableRateLimit": True,
                                      "timeout": timeout_ms,
                                      "options": {"defaultType": "swap"}})
            if not ex.has.get("fetchFundingRates"):
                raise RuntimeError("no unified fetchFundingRates")
            rates = ex.fetch_funding_rates()
            tickers = ex.fetch_tickers()
            spot = _spot_bases(ccxt, name, timeout_ms)
        except Exception as exc:                   # noqa: BLE001 - try the next
            last_error = exc
            log.info("carry venue %s unavailable (%s)", name, exc)
            continue

        carries = _build(rates, tickers, spot, min_volume, report)
        if not carries:
            last_error = RuntimeError(f"{name} returned nothing tradable")
            continue

        _add_history(ex, carries)
        carries.sort(key=lambda c: -c.net_annual(holding_days))
        report.candidates = carries
        report.venue = name
        log.info("carry basket built on %s (%d candidates)", name, len(carries))
        return report

    raise RuntimeError(f"no exchange answered ({last_error})")


def _spot_bases(ccxt_mod: Any, name: str, timeout_ms: int) -> set[str]:
    """Which bases have a spot market here, so a hedge is actually possible."""
    try:
        spot_ex = getattr(ccxt_mod, name)({"enableRateLimit": True,
                                           "timeout": timeout_ms,
                                           "options": {"defaultType": "spot"}})
        return {_base(s) for s in spot_ex.load_markets()
                if "/" in s and ":" not in s}
    except Exception:                              # noqa: BLE001 - not fatal
        # Without a spot list nothing can be shown to be hedgeable, and the
        # honest response is an empty set rather than assuming everything is.
        return set()


def _build(rates: dict[str, Any], tickers: dict[str, Any], spot: set[str],
           min_volume: float, report: CarryReport) -> list[Carry]:
    out: list[Carry] = []
    for symbol, row in (rates or {}).items():
        funding = row.get("fundingRate")
        if funding is None or not _linear_usd(symbol):
            continue
        report.scanned += 1
        base = _base(symbol)
        hedgeable = base in spot
        if hedgeable:
            report.hedgeable += 1

        t = (tickers or {}).get(symbol) or {}
        volume = t.get("quoteVolume")
        if volume is None and t.get("baseVolume") and t.get("last"):
            volume = float(t["baseVolume"]) * float(t["last"])

        carry = Carry(symbol=base, funding_now=float(funding),
                      mark=float(row.get("markPrice") or t.get("last") or 0.0),
                      hedgeable=hedgeable, perp_volume_24h=float(volume or 0.0))
        # Same two gates as the original: it has to be hedgeable, liquid, and
        # currently paying the short rather than charging it.
        if carry.tradable and carry.perp_volume_24h >= min_volume \
                and carry.funding_now > 0:
            out.append(carry)

    out.sort(key=lambda c: -c.funding_now)
    return out[:DEEP_SCAN]


def _add_history(ex: Any, carries: list[Carry]) -> None:
    """Rank on what each has actually paid, not on one print."""
    for c in carries:
        try:
            rows = ex.fetch_funding_rate_history(f"{c.symbol}/USDT:USDT",
                                                 limit=HISTORY)
            c.history = [float(r["fundingRate"]) for r in rows
                         if r.get("fundingRate") is not None]
        except Exception as exc:                   # noqa: BLE001
            log.debug("history failed for %s: %s", c.symbol, exc)


def basket_summary(report: CarryReport) -> dict[str, Any]:
    """The same dict shape ``brief.py`` already reads, plus the venue used."""
    d = report.to_dict()
    d["venue"] = getattr(report, "venue", "")
    return d


def median_annual(carry: Carry) -> float:
    if not carry.history:
        return carry.annual_now
    return statistics.median(carry.history) * SETTLEMENTS_PER_YEAR
