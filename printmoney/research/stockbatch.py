"""Bounded private stock observations, shared request pacing and resumable cache."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlsplit


class BlockedRequest(ValueError):
    def __init__(self, host, status):
        self.status = status
        super().__init__(f"Request host {host} blocked after HTTP {status}.")


def _check_interval(interval):
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval < 0:
        raise ValueError("Request interval must be finite and nonnegative.")


class RequestGate:
    """One start per interval across workers; denials open an exact-host circuit."""
    def __init__(self, interval=1.0, clock=None, sleeper=None):
        _check_interval(interval)
        self.interval = interval
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self._lock = threading.Lock()
        self._next = None
        self._blocked = {}

    @staticmethod
    def _host(url):
        return urlsplit(url).hostname if url else None

    def acquire(self, url=None):
        host = self._host(url)
        while True:
            with self._lock:
                if host in self._blocked:
                    raise BlockedRequest(host, self._blocked[host])
                current = self.clock()
                if self._next is None or current >= self._next:
                    self._next = current+self.interval
                    return
                delay = self._next-current
            # Denial responses can open the circuit during another worker's wait.
            self.sleeper(delay)

    def block(self, url, status):
        if status in (401, 403, 429):
            with self._lock:
                self._blocked[self._host(url)] = status

    def is_blocked(self, url):
        with self._lock:
            return self._host(url) in self._blocked


def _stamp(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Observation clocks must be timezone aware.")
    return stamp


def _evidence_clock(value, limits, day_only=False):
    """Keep unknown/day precision; reject known clocks beyond receipt/cutoff."""
    if value is None:
        return
    if isinstance(value, str) and len(value) == 10:
        stamp_day = date.fromisoformat(value)
        if stamp_day.isoformat() != value or any(stamp_day > limit.date() for limit in limits):
            raise ValueError("Invalid or future evidence day.")
    elif day_only:
        raise ValueError("Evidence day must retain date precision.")
    else:
        stamp = _stamp(value)
        if any(stamp > limit for limit in limits):
            raise ValueError("Evidence clock is after receipt or cutoff.")


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate(observation, symbol, cutoff=None):
    if not isinstance(observation, dict) or observation.get("symbol") != symbol:
        raise ValueError("Malformed observation or canonical identity mismatch.")
    if not isinstance(observation.get("warnings", []), list) or not isinstance(observation.get("sources", []), list):
        raise ValueError("Malformed observation provenance.")
    if not isinstance(observation.get("bars", []), list):
        raise ValueError("Malformed daily bars.")
    for source in observation.get("sources", []):
        if not isinstance(source, dict):
            raise ValueError("Malformed source object.")
        url = urlsplit(source.get("url", ""))
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Unsafe observation source URL.")
        _stamp(source["retrieved_at"])
    def clocks(value, receipt=None):
        if isinstance(value, dict):
            if "retrieved_at" in value:
                receipt = _stamp(value["retrieved_at"])
            elif "fetched_at" in value:
                receipt = _stamp(value["fetched_at"])
            limits = [limit for limit in (cutoff, receipt) if limit is not None]
            for key, child in value.items():
                if key in ("fetched_at", "retrieved_at", "captured_at"):
                    stamp = _stamp(child)
                    if cutoff is not None and stamp > cutoff:
                        raise ValueError("Observation clock is after cutoff.")
                elif key in ("published_at", "updated_at", "as_of"):
                    _evidence_clock(child, limits)
                elif key in ("published_day", "updated_day", "as_of_day", "quote_day", "annual_start", "annual_end", "filed"):
                    _evidence_clock(child, limits, day_only=True)
                else:
                    clocks(child, receipt)
        elif isinstance(value, list):
            for child in value:
                clocks(child, receipt)
    clocks(observation)
    for bar in observation.get("bars", []):
        if not isinstance(bar, list) or len(bar) not in (7, 8):
            raise ValueError("Malformed daily bar.")
        if not all(_finite(field) for field in bar[:7]):
            raise ValueError("Daily bar requires finite numeric OHLCV fields.")
        if bar[0] < 0 or int(bar[0]) != bar[0] or any(field <= 0 for field in (*bar[1:5], bar[6])) or bar[5] < 0:
            raise ValueError("Daily bar requires integral timestamp, positive prices and nonnegative volume.")
        # open/high/low/raw_close are raw prints; adjusted close can lie outside
        # that range following a dividend or split and must not be compared to it.
        if not (bar[3] <= bar[1] <= bar[2] and bar[3] <= bar[6] <= bar[2]):
            raise ValueError("Incoherent raw daily OHLC bounds.")
        if len(bar) == 8 and not isinstance(bar[7], bool):
            raise ValueError("Adjustment verification must be a boolean.")
        stamp = datetime.fromtimestamp(bar[0], timezone.utc)
        if cutoff is not None and stamp > cutoff:
            raise ValueError("Future daily bar.")
    # Ensure data is a finite, portable JSON observation before caching/exporting.
    json.dumps(observation, allow_nan=False)
    return observation


def _success(observation):
    price = observation.get("last")
    return isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price) and price > 0


def _cache_path(cache_dir, symbol):
    return Path(cache_dir)/f"observation-{hashlib.sha256(symbol.encode()).hexdigest()}.json"


def _read_cache(path, symbol, now):
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        if blob.get("schema_version") != 1:
            return None
        observation = _validate(blob["observation"], symbol, now)
        age = (now-_stamp(observation["fetched_at"])).total_seconds()
        return observation if 0 <= age < 21600 and _success(observation) else None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None


def _write_cache(path, observation):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.stem+"-", suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
            json.dump({"schema_version": 1, "observation": observation}, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _gap(symbol, warning, status="missing"):
    return {"symbol": symbol, "warnings": [warning], "sources": [],
            "provider_status": {"price": status, "annual": "missing"}, "collection_status": "failed"}


def collect_many(symbols, provider_factory, now, cache_dir, workers=4, interval=1.0, offline=False, refresh=False, request_gate=None):
    """Retain every requested security; cache successful prices for six hours.

    Caller supplies a private Git-ignored cache directory. Receipt/source clocks
    are never advanced on cache hits; a historical cutoff rejects newer caches.
    Completed counts newly collected usable prices; cached is a separate count.
    Offline counts missing-cache rows (also failed), never attempted requests.
    A supplied gate retains pacing/circuits from earlier phases of the same run.
    """
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 4:
        raise ValueError("Use one to four collection workers.")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Collection cutoff must be timezone aware.")
    _check_interval(interval)
    gate = request_gate if request_gate is not None else RequestGate(interval=interval)
    symbols = tuple(symbols)
    rows = [None]*len(symbols)
    pending = []
    counts = {"requested": len(symbols), "cached": 0, "attempted": 0,
              "completed": 0, "failed": 0, "offline": 0,
              "workers": workers, "request_interval_seconds": getattr(gate, "interval", interval)}
    for index, symbol in enumerate(symbols):
        path = _cache_path(cache_dir, symbol)
        cached = _read_cache(path, symbol, now) if not refresh else None
        if cached is not None:
            rows[index] = cached
            counts["cached"] += 1
        elif offline:
            rows[index] = _gap(symbol, "Offline: no valid observation cache before cutoff.")
            counts["offline"] += 1
            counts["failed"] += 1
        else:
            pending.append((index, symbol, path))
    local = threading.local()
    providers = []
    provider_lock = threading.Lock()
    def collect(entry):
        index, symbol, path = entry
        try:
            if not hasattr(local, "provider"):
                local.provider = provider_factory(gate)
                with provider_lock:
                    providers.append(local.provider)
            observation = deepcopy(_validate(local.provider.collect(symbol, now), symbol))
            observation.setdefault("warnings", [])
            success = _success(observation)
            observation["collection_status"] = "completed" if success else "failed"
            if success:
                _stamp(observation["fetched_at"])
                try:
                    _write_cache(path, observation)
                except (OSError, ValueError, TypeError) as exc:
                    observation["warnings"].append(f"Observation cache write unavailable: {type(exc).__name__}.")
            elif not observation["warnings"]:
                observation["warnings"].append("Daily price observation unavailable.")
            return index, observation, success
        except Exception as exc:
            return index, _gap(symbol, f"Collection unavailable: {type(exc).__name__}."), False
    try:
        if pending:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for index, observation, success in pool.map(collect, pending):
                    rows[index] = observation
                    counts["attempted"] += 1
                    counts["completed" if success else "failed"] += 1
    finally:
        for provider in providers:
            try:
                provider.close()
            except Exception:
                # Cleanup cannot erase completed observations or unrelated failures.
                pass
    return {"observations": rows, "collection": counts}
