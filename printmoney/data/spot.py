"""The BTC tape.

These markets settle on the Binance BTC/USDT 1-minute close, so Binance is the
primary source and everything else is a liveness fallback.  If the fallback is in
use we say so loudly, because trading a settlement-price market off a different
venue's price is its own small basis risk.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Sequence

import httpx

from ..config import Config
from ..util import USER_AGENT, retry, safe_float, utcnow
from .types import Candle, Quote, candles_from_binance

log = logging.getLogger("printmoney.spot")

BINANCE_HOSTS = (
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://data-api.binance.vision",
)


class SpotFeedError(RuntimeError):
    pass


class SpotFeed:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._client = httpx.Client(
            timeout=cfg.data.http_timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )
        self._last: Quote | None = None
        self._binance_host: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SpotFeed":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        def call() -> Any:
            r = self._client.get(url, params=params)
            r.raise_for_status()
            return r.json()

        return retry(call, attempts=3, what=f"GET {url}")

    def _binance_base(self) -> str:
        """Pin the first Binance host that answers; some are geo-blocked."""
        if self._binance_host:
            return self._binance_host
        last_exc: Exception | None = None
        for host in BINANCE_HOSTS:
            try:
                self._get(f"{host}/api/v3/ping")
                self._binance_host = host
                log.debug("binance host: %s", host)
                return host
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        raise SpotFeedError(f"no reachable Binance host: {last_exc}")

    # ------------------------------------------------------------------ #
    # spot
    # ------------------------------------------------------------------ #
    def _spot_binance(self) -> Quote:
        base = self._binance_base()
        data = self._get(
            f"{base}/api/v3/ticker/price", {"symbol": self.cfg.data.settlement_symbol}
        )
        price = safe_float(data.get("price"))
        if price is None or price <= 0:
            raise SpotFeedError(f"bad binance price payload: {data}")
        return Quote(price=price, source="binance")

    def _spot_coinbase(self) -> Quote:
        data = self._get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")
        price = safe_float(data.get("price"))
        if price is None or price <= 0:
            raise SpotFeedError(f"bad coinbase price payload: {data}")
        return Quote(price=price, source="coinbase")

    def _spot_kraken(self) -> Quote:
        data = self._get("https://api.kraken.com/0/public/Ticker", {"pair": "XBTUSD"})
        result = (data or {}).get("result") or {}
        for _key, val in result.items():
            price = safe_float((val.get("c") or [None])[0])
            if price and price > 0:
                return Quote(price=price, source="kraken")
        raise SpotFeedError(f"bad kraken price payload: {data}")

    def spot(self) -> Quote:
        """Latest BTC price, trying configured sources in order."""
        sources: dict[str, Callable[[], Quote]] = {
            "binance": self._spot_binance,
            "coinbase": self._spot_coinbase,
            "kraken": self._spot_kraken,
        }
        errors: list[str] = []
        for name in self.cfg.data.spot_sources:
            fn = sources.get(name)
            if fn is None:
                continue
            try:
                q = fn()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                continue
            if name != "binance":
                log.warning(
                    "spot from %s, not the settlement venue (binance) - basis risk applies", name
                )
            self._last = q
            return q
        raise SpotFeedError("all spot sources failed: " + "; ".join(errors))

    @property
    def last(self) -> Quote | None:
        return self._last

    # ------------------------------------------------------------------ #
    # history
    # ------------------------------------------------------------------ #
    def klines(self, interval: str = "1h", limit: int = 720) -> list[Candle]:
        """OHLCV history from the settlement venue.

        Binance caps ``limit`` at 1000 per call, so longer lookbacks are paged
        backwards through ``endTime``.
        """
        base = self._binance_base()
        remaining = int(limit)
        end_time: int | None = None
        chunks: list[list[Any]] = []
        while remaining > 0:
            take = min(1000, remaining)
            params: dict[str, Any] = {
                "symbol": self.cfg.data.settlement_symbol,
                "interval": interval,
                "limit": take,
            }
            if end_time is not None:
                params["endTime"] = end_time
            rows = self._get(f"{base}/api/v3/klines", params)
            if not isinstance(rows, list) or not rows:
                break
            chunks.insert(0, rows)
            remaining -= len(rows)
            end_time = int(rows[0][0]) - 1
            if len(rows) < take:
                break
        flat: list[Any] = [row for chunk in chunks for row in chunk]
        candles = candles_from_binance(flat)
        if not candles:
            raise SpotFeedError("no candles returned")
        # De-duplicate on open_time; paging can overlap by one bar.
        seen: set[float] = set()
        uniq: list[Candle] = []
        for c in candles:
            ts = c.open_time.timestamp()
            if ts in seen:
                continue
            seen.add(ts)
            uniq.append(c)
        uniq.sort(key=lambda c: c.open_time)
        return uniq

    def recent_closes(self, interval: str = "1h", limit: int = 720) -> list[float]:
        return [c.close for c in self.klines(interval, limit)]

    # ------------------------------------------------------------------ #
    # settlement
    # ------------------------------------------------------------------ #
    def klines_between(
        self, start: datetime, end: datetime, interval: str = "1m"
    ) -> list[Candle]:
        """Every bar whose open time falls in [start, end], paging as needed."""
        base = self._binance_base()
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        out: list[Any] = []
        cursor = start_ms
        while cursor <= end_ms:
            rows = self._get(
                f"{base}/api/v3/klines",
                {
                    "symbol": self.cfg.data.settlement_symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            if not isinstance(rows, list) or not rows:
                break
            out.extend(rows)
            last_open = int(rows[-1][0])
            if len(rows) < 1000 or last_open <= cursor:
                break
            cursor = last_open + 1
        return candles_from_binance(out)

    def settlement_close(self, when: datetime) -> float | None:
        """Close of the 1-minute candle that these markets settle on.

        Polymarket resolves BTC price markets to the Binance BTC/USDT 1-minute
        close at the stated time, so this is the exact number, not a proxy - as
        long as the bar has actually printed.
        """
        minute = when.replace(second=0, microsecond=0)
        candles = self.klines_between(minute - timedelta(minutes=2), minute + timedelta(minutes=2))
        for c in candles:
            if c.open_time.replace(second=0, microsecond=0) == minute:
                return c.close
        log.warning("no 1m candle at %s yet; settlement deferred", minute.isoformat())
        return None

    def path_extremes(self, start: datetime, end: datetime) -> tuple[float, float] | None:
        """(highest high, lowest low) over the market window, for touch markets."""
        candles = self.klines_between(start, end, interval="1m")
        if not candles:
            return None
        return max(c.high for c in candles), min(c.low for c in candles)


class StaticSpotFeed:
    """A frozen feed for backtests and tests. Same surface, no network."""

    def __init__(self, price: float, candles: Sequence[Candle] | None = None) -> None:
        self._price = float(price)
        self._candles = list(candles or [])
        self._last = Quote(price=self._price, source="static", timestamp=utcnow())

    def spot(self) -> Quote:
        self._last = Quote(price=self._price, source="static", timestamp=utcnow())
        return self._last

    @property
    def last(self) -> Quote | None:
        return self._last

    def klines(self, interval: str = "1h", limit: int = 720) -> list[Candle]:
        return self._candles[-limit:]

    def recent_closes(self, interval: str = "1h", limit: int = 720) -> list[float]:
        return [c.close for c in self.klines(interval, limit)]

    def settlement_close(self, when: datetime) -> float | None:
        return self._price

    def path_extremes(self, start: datetime, end: datetime) -> tuple[float, float] | None:
        if not self._candles:
            return None
        return max(c.high for c in self._candles), min(c.low for c in self._candles)

    def close(self) -> None:  # pragma: no cover - nothing to close
        pass
