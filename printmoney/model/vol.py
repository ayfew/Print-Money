"""Realised volatility.

Close-to-close throws away three quarters of every bar.  Range estimators
(Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang) use the high and low too
and are several times more efficient per observation, which matters a lot when
the horizon is hours and we only have a few hundred bars of relevant history.
We blend a configured basket rather than betting on one estimator.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from ..config import ModelConfig
from ..data.types import Candle

log = logging.getLogger("printmoney.vol")

SECONDS_PER_YEAR = 365.25 * 24 * 3600


@dataclass
class VolEstimate:
    """Annualised volatility plus the components that produced it."""

    annual: float
    components: dict[str, float] = field(default_factory=dict)
    bars: int = 0
    bar_seconds: float = 3600.0
    raw_annual: float = 0.0  # before risk premium, floor and cap
    clipped: bool = False

    def sigma_for(self, years: float) -> float:
        """Total (not annualised) sigma of log-return over ``years``."""
        return self.annual * math.sqrt(max(years, 0.0))

    def to_dict(self) -> dict[str, float | int | bool]:
        return {
            "annual": self.annual,
            "raw_annual": self.raw_annual,
            "bars": self.bars,
            "bar_seconds": self.bar_seconds,
            "clipped": self.clipped,
            **{f"est_{k}": v for k, v in self.components.items()},
        }


# --------------------------------------------------------------------------- #
def _arrays(candles: Sequence[Candle]) -> tuple[np.ndarray, ...]:
    o = np.array([c.open for c in candles], dtype=float)
    h = np.array([c.high for c in candles], dtype=float)
    l = np.array([c.low for c in candles], dtype=float)
    c_ = np.array([c.close for c in candles], dtype=float)
    ok = (o > 0) & (h > 0) & (l > 0) & (c_ > 0) & (h >= l)
    return o[ok], h[ok], l[ok], c_[ok]


def _bar_seconds(candles: Sequence[Candle]) -> float:
    if len(candles) < 2:
        return 3600.0
    deltas = [
        (b.open_time - a.open_time).total_seconds()
        for a, b in zip(candles, candles[1:])
        if (b.open_time - a.open_time).total_seconds() > 0
    ]
    return float(np.median(deltas)) if deltas else 3600.0


def _annualise(per_bar_var: float, bar_seconds: float) -> float:
    if per_bar_var <= 0 or not math.isfinite(per_bar_var):
        return 0.0
    bars_per_year = SECONDS_PER_YEAR / max(bar_seconds, 1.0)
    return math.sqrt(per_bar_var * bars_per_year)


# --------------------------------------------------------------------------- #
# estimators (each returns annualised sigma)
# --------------------------------------------------------------------------- #
def close_to_close(candles: Sequence[Candle]) -> float:
    _, _, _, c = _arrays(candles)
    if len(c) < 3:
        return 0.0
    r = np.diff(np.log(c))
    return _annualise(float(np.var(r, ddof=1)), _bar_seconds(candles))


def ewma(candles: Sequence[Candle], lam: float = 0.97) -> float:
    _, _, _, c = _arrays(candles)
    if len(c) < 3:
        return 0.0
    r = np.diff(np.log(c))
    lam = min(max(lam, 0.5), 0.9999)
    # weights newest-heaviest, normalised so they sum to 1
    age = np.arange(len(r) - 1, -1, -1, dtype=float)
    w = (1.0 - lam) * lam**age
    w /= w.sum()
    var = float(np.sum(w * (r - float(np.sum(w * r))) ** 2))
    return _annualise(var, _bar_seconds(candles))


def parkinson(candles: Sequence[Candle]) -> float:
    _, h, l, _ = _arrays(candles)
    if len(h) < 3:
        return 0.0
    hl = np.log(h / l) ** 2
    var = float(np.mean(hl) / (4.0 * math.log(2.0)))
    return _annualise(var, _bar_seconds(candles))


def garman_klass(candles: Sequence[Candle]) -> float:
    o, h, l, c = _arrays(candles)
    if len(o) < 3:
        return 0.0
    hl = np.log(h / l) ** 2
    co = np.log(c / o) ** 2
    var = float(np.mean(0.5 * hl - (2.0 * math.log(2.0) - 1.0) * co))
    return _annualise(var, _bar_seconds(candles))


def rogers_satchell(candles: Sequence[Candle]) -> float:
    o, h, l, c = _arrays(candles)
    if len(o) < 3:
        return 0.0
    term = np.log(h / c) * np.log(h / o) + np.log(l / c) * np.log(l / o)
    return _annualise(float(np.mean(term)), _bar_seconds(candles))


def yang_zhang(candles: Sequence[Candle]) -> float:
    """Drift-independent and gap-aware; the best all-rounder of the family."""
    o, h, l, c = _arrays(candles)
    n = len(o)
    if n < 5:
        return 0.0
    # overnight (previous close -> open) and open-to-close components
    ro = np.log(o[1:] / c[:-1])
    rc = np.log(c[1:] / o[1:])
    v_o = float(np.var(ro, ddof=1))
    v_c = float(np.var(rc, ddof=1))
    term = np.log(h[1:] / c[1:]) * np.log(h[1:] / o[1:]) + np.log(l[1:] / c[1:]) * np.log(
        l[1:] / o[1:]
    )
    v_rs = float(np.mean(term))
    m = n - 1
    k = 0.34 / (1.34 + (m + 1) / max(m - 1, 1))
    var = v_o + k * v_c + (1.0 - k) * v_rs
    return _annualise(var, _bar_seconds(candles))


ESTIMATORS: dict[str, Callable[..., float]] = {
    "close_to_close": close_to_close,
    "parkinson": parkinson,
    "garman_klass": garman_klass,
    "rogers_satchell": rogers_satchell,
    "yang_zhang": yang_zhang,
}


# --------------------------------------------------------------------------- #
def estimate(candles: Sequence[Candle], cfg: ModelConfig) -> VolEstimate:
    """Blend the configured estimators, then apply premium, floor and cap."""
    if len(candles) < 5:
        raise ValueError(f"need at least 5 candles to estimate vol, got {len(candles)}")

    components: dict[str, float] = {}
    weights: list[float] = []
    values: list[float] = []
    for name, w in zip(cfg.vol_estimators, cfg.vol_estimator_weights):
        if name == "ewma":
            v = ewma(candles, cfg.ewma_lambda)
        else:
            fn = ESTIMATORS.get(name)
            if fn is None:
                log.warning("unknown vol estimator %r, ignoring", name)
                continue
            v = fn(candles)
        components[name] = v
        if v > 0 and math.isfinite(v):
            values.append(v)
            weights.append(max(w, 0.0))

    if not values or sum(weights) <= 0:
        raise ValueError("every volatility estimator returned zero - bad candle data?")

    raw = float(np.average(values, weights=weights))
    adj = raw * cfg.vol_risk_premium
    final = min(max(adj, cfg.vol_floor_annual), cfg.vol_cap_annual)

    est = VolEstimate(
        annual=final,
        components=components,
        bars=len(candles),
        bar_seconds=_bar_seconds(candles),
        raw_annual=raw,
        clipped=not math.isclose(final, adj, rel_tol=1e-9),
    )
    if est.clipped:
        log.info(
            "vol %.1f%% clipped to %.1f%% by [%.0f%%, %.0f%%] bounds",
            100 * adj,
            100 * final,
            100 * cfg.vol_floor_annual,
            100 * cfg.vol_cap_annual,
        )
    return est


def standardized_returns(candles: Sequence[Candle]) -> np.ndarray:
    """Zero-mean, unit-variance log returns - the shock pool for the bootstrap.

    Standardising lets us keep the empirical *shape* (fat tails, skew) while the
    scale comes from whichever vol estimate we trust for the horizon.
    """
    _, _, _, c = _arrays(candles)
    if len(c) < 30:
        raise ValueError(f"need >=30 closes for a bootstrap pool, got {len(c)}")
    r = np.diff(np.log(c))
    r = r[np.isfinite(r)]
    sd = float(np.std(r, ddof=1))
    if sd <= 0:
        raise ValueError("degenerate return series (zero variance)")
    return (r - float(np.mean(r))) / sd
