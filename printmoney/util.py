"""Small shared helpers: console/UTF-8 setup, HTTP with retries, time, logging."""
from __future__ import annotations

import json
import logging
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"

USER_AGENT = "printmoney/1.0 (+polymarket-surface-arb)"


# --------------------------------------------------------------------------- #
# console
# --------------------------------------------------------------------------- #
def setup_console() -> None:
    """Force UTF-8 on stdout/stderr.

    Windows consoles default to a legacy code page (cp874 on this box), which
    raises UnicodeEncodeError the moment we print a box-drawing character or a
    Thai string.  Reconfiguring is cheap and idempotent.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # pragma: no cover - very old Python / odd stream
            pass


def setup_logging(level: str = "INFO", logfile: str | None = None) -> logging.Logger:
    setup_console()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("printmoney")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    path = Path(logfile) if logfile else LOG_DIR / "printmoney.log"
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logger.addHandler(fh)

    logger.propagate = False
    return logger


log = logging.getLogger("printmoney")


# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(s: str) -> datetime:
    """Parse the ISO-8601 shapes Polymarket actually emits."""
    if s is None:
        raise ValueError("empty timestamp")
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def years_between(a: datetime, b: datetime) -> float:
    """Calendar-year fraction. Crypto trades 24/7, so no business-day fudge."""
    return max((b - a).total_seconds(), 0.0) / (365.25 * 24 * 3600)


def human_dt(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


# --------------------------------------------------------------------------- #
# numeric
# --------------------------------------------------------------------------- #
def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def safe_float(x: Any, default: float | None = None) -> float | None:
    if x is None or x == "":
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return v


def fmt_usd(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def fmt_pct(x: float, digits: int = 1) -> str:
    return f"{100.0 * x:.{digits}f}%"


# --------------------------------------------------------------------------- #
# retries
# --------------------------------------------------------------------------- #
def retry(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 0.4,
    max_delay: float = 6.0,
    what: str = "call",
) -> T:
    """Retry with exponential backoff + jitter. Raises the last exception."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, we re-raise
            last = exc
            if i == attempts - 1:
                break
            delay = min(max_delay, base_delay * (2**i)) * (0.6 + 0.8 * random.random())
            log.debug("%s failed (%s); retry %d/%d in %.2fs", what, exc, i + 1, attempts - 1, delay)
            time.sleep(delay)
    assert last is not None
    raise last


# --------------------------------------------------------------------------- #
# json io
# --------------------------------------------------------------------------- #
def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read %s: %s", p, exc)
        return default


def write_json(path: str | Path, obj: Any, *, indent: int | None = 2) -> None:
    """Atomic-ish write so a crash mid-write cannot corrupt the ledger."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=indent, default=_json_default), encoding="utf-8")
    os.replace(tmp, p)


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)


def chunked(seq: Iterable[T], n: int) -> Iterable[list[T]]:
    buf: list[T] = []
    for item in seq:
        buf.append(item)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf
