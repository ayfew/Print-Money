"""Free daily OHLC for anything with a ticker.

Yahoo's chart endpoint needs no key and covers equities, ETFs, futures, FX and
crypto, which is the whole universe this project cares about.  Responses are
cached on disk because the same ten years of history does not need re-downloading
every time a question is asked.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

from ..util import STATE_DIR, USER_AGENT, read_json, retry, write_json

log = logging.getLogger("printmoney.research")

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
CACHE_DIR = STATE_DIR / "bars"

#: A trading day is a trading day; crypto's 24/7 calendar is normalised to the
#: same daily bars so the two can sit in one table without silently lying.
TRADING_DAYS_PER_YEAR = 252.0


@dataclass(frozen=True)
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def date(self) -> datetime:
        return datetime.fromtimestamp(self.ts, tz=timezone.utc)

    @property
    def intraday(self) -> float:
        """Open to close: the half of the day you own if you buy at the bell."""
        return self.close / self.open - 1.0


@dataclass
class Series:
    symbol: str
    name: str
    bars: list[Bar]

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    def intraday_returns(self) -> list[float]:
        return [b.intraday for b in self.bars]

    def overnight_returns(self) -> list[float]:
        """Previous close to this open: the other half of the day."""
        return [
            self.bars[i].open / self.bars[i - 1].close - 1.0
            for i in range(1, len(self.bars))
        ]

    def daily_returns(self) -> list[float]:
        return [
            self.bars[i].close / self.bars[i - 1].close - 1.0
            for i in range(1, len(self.bars))
        ]

    def by_ts(self) -> dict[int, Bar]:
        return {b.ts: b for b in self.bars}


# --------------------------------------------------------------------------- #
def _cache_path(symbol: str, rng: str) -> Path:
    safe = symbol.replace("/", "_").replace("=", "_").replace("^", "_")
    return CACHE_DIR / f"{safe}_{rng}.json"


def fetch(
    symbol: str,
    *,
    name: str | None = None,
    rng: str = "10y",
    client: httpx.Client | None = None,
    cache_hours: float = 12.0,
) -> Series | None:
    """Daily bars for one symbol, cached on disk."""
    path = _cache_path(symbol, rng)
    cached = read_json(path)
    if isinstance(cached, dict) and cached.get("bars"):
        age = time.time() - float(cached.get("fetched_at", 0))
        if age < cache_hours * 3600:
            return Series(
                symbol=symbol,
                name=name or cached.get("name") or symbol,
                bars=[Bar(*row) for row in cached["bars"]],
            )

    own = client is None
    client = client or httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT})
    try:

        def call() -> Any:
            r = client.get(f"{YAHOO}/{symbol}", params={"range": rng, "interval": "1d"})
            r.raise_for_status()
            return r.json()

        payload = retry(call, attempts=3, what=f"yahoo {symbol}")
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            return None
        res = result[0]
        q = (res.get("indicators") or {}).get("quote") or [{}]
        q = q[0]
        bars: list[Bar] = []
        for i, ts in enumerate(res.get("timestamp") or []):
            o, h, l, c = (
                _at(q, "open", i),
                _at(q, "high", i),
                _at(q, "low", i),
                _at(q, "close", i),
            )
            v = _at(q, "volume", i) or 0.0
            if None in (o, h, l, c) or o <= 0 or c <= 0:
                continue
            bars.append(Bar(int(ts), float(o), float(h), float(l), float(c), float(v)))
        if not bars:
            return None
        write_json(
            path,
            {
                "fetched_at": time.time(),
                "name": name or symbol,
                "bars": [[b.ts, b.open, b.high, b.low, b.close, b.volume] for b in bars],
            },
            indent=None,
        )
        return Series(symbol=symbol, name=name or symbol, bars=bars)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not load %s: %s", symbol, exc)
        return None
    finally:
        if own:
            client.close()


def _at(q: dict[str, Any], key: str, i: int) -> float | None:
    seq = q.get(key)
    if not isinstance(seq, list) or i >= len(seq):
        return None
    v = seq[i]
    return float(v) if isinstance(v, (int, float)) else None


def fetch_many(
    symbols: Sequence[tuple[str, str]],
    *,
    rng: str = "10y",
    pause: float = 0.15,
    cache_hours: float = 12.0,
) -> dict[str, Series]:
    out: dict[str, Series] = {}
    with httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}) as client:
        for symbol, name in symbols:
            s = fetch(symbol, name=name, rng=rng, client=client, cache_hours=cache_hours)
            if s is not None and len(s) > 50:
                out[symbol] = s
            else:
                log.debug("skipping %s", symbol)
            time.sleep(pause)
    return out


def day_key(ts: int) -> str:
    """UTC calendar date. Alignment has to happen on dates, not timestamps.

    A US equity bar is stamped at the opening bell in New York and a crypto bar at
    midnight UTC, so they never share a raw timestamp - intersecting on ``ts``
    silently produces an empty set and a study of nothing.
    """
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def align(series: Iterable[Series]) -> tuple[list[str], dict[str, list[Bar]]]:
    """Restrict every series to the calendar days they all traded."""
    series = list(series)
    if not series:
        return [], {}
    per_symbol: dict[str, dict[str, Bar]] = {}
    common: set[str] | None = None
    for s in series:
        by_day = {day_key(b.ts): b for b in s.bars}
        per_symbol[s.symbol] = by_day
        days = set(by_day)
        common = days if common is None else (common & days)
    stamps = sorted(common or set())
    return stamps, {sym: [by_day[d] for d in stamps] for sym, by_day in per_symbol.items()}


# --------------------------------------------------------------------------- #
#: Broad, liquid, and chosen without knowing which of them went up - the point of
#: a universe is that it was not picked after the fact.
UNIVERSE: list[tuple[str, str]] = [
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq 100"),
    ("IWM", "US small caps"),
    ("DIA", "Dow 30"),
    ("EFA", "Developed ex-US"),
    ("EEM", "Emerging markets"),
    ("EWJ", "Japan"),
    ("FXI", "China"),
    ("THD", "Thailand"),
    ("GLD", "Gold"),
    ("SLV", "Silver"),
    ("USO", "Oil"),
    ("TLT", "US long bonds"),
    ("HYG", "High yield credit"),
    ("VNQ", "US real estate"),
    ("XLE", "Energy"),
    ("XLF", "Financials"),
    ("XLK", "Technology"),
    ("XLV", "Healthcare"),
    ("XLU", "Utilities"),
    ("XLP", "Consumer staples"),
    ("UUP", "US dollar"),
    ("BTC-USD", "Bitcoin"),
    ("ETH-USD", "Ethereum"),
]
