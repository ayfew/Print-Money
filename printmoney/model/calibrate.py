"""Reading the market's own volatility back out of the strip.

This is the piece that keeps the engine from accidentally becoming a volatility
punter.  A bot that prices BTC buckets off 30-day realised volatility and then
buys whatever looks cheap is not arbitraging anything - it is making one big bet
that short-dated implied vol is too high, dressed up as a hundred small bets.
Short-dated crypto markets carry a large and persistent variance risk premium, so
that bet loses slowly and looks like alpha for weeks first.

So we fit a lognormal to the whole quoted strip and recover the volatility the
market is charging.  The trading model is then anchored between that number and
realised volatility, and the robust objective demands a profit under *both*.
What survives is disagreement about the **shape** of the distribution - fat
tails, skew, one lottery bucket bid up by somebody's hunch - which is where the
real inefficiency in these markets lives.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from scipy.optimize import minimize_scalar

from ..data.types import INF, LegKind, Strip
from .paths import PathBank

log = logging.getLogger("printmoney.calibrate")

SQRT2 = math.sqrt(2.0)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def lognormal_cdf(spot: float, sigma: float, years: float, k: float, drift: float = 0.0) -> float:
    """P(S_T <= k) under geometric Brownian motion."""
    if k <= 0:
        return 0.0
    if years <= 0 or sigma <= 0:
        return 1.0 if spot <= k else 0.0
    vol = sigma * math.sqrt(years)
    mu = (drift - 0.5 * sigma * sigma) * years
    return _norm_cdf((math.log(k / spot) - mu) / vol)


def lognormal_prob_range(
    spot: float, sigma: float, years: float, lo: float, hi: float, drift: float = 0.0
) -> float:
    below_hi = 1.0 if hi == INF else lognormal_cdf(spot, sigma, years, hi, drift)
    below_lo = 0.0 if lo == -INF else lognormal_cdf(spot, sigma, years, lo, drift)
    return max(0.0, below_hi - below_lo)


def lognormal_prob_above(spot: float, sigma: float, years: float, k: float, drift: float = 0.0) -> float:
    return 1.0 - lognormal_cdf(spot, sigma, years, k, drift)


def lognormal_prob_touch(
    spot: float, sigma: float, years: float, barrier: float, *, up: bool, drift: float = 0.0
) -> float:
    """P(the running max reaches ``barrier``), or the running min for ``up=False``.

    Closed form from the reflection principle for Brownian motion with drift.
    Used only for calibration: the trading fair values come from the simulated
    paths, which do not assume the returns are Gaussian.
    """
    if barrier <= 0 or spot <= 0:
        return 0.0
    if up and barrier <= spot:
        return 1.0
    if not up and barrier >= spot:
        return 1.0
    if years <= 0 or sigma <= 0:
        return 0.0

    m = math.log(barrier / spot)
    mu = drift - 0.5 * sigma * sigma
    vol = sigma * math.sqrt(years)
    # exp(2*mu*m/sigma^2) blows up for large |m|; the second term is negligible
    # there anyway, so clamp the exponent rather than overflow.
    exponent = max(min(2.0 * mu * m / (sigma * sigma), 50.0), -50.0)
    if up:
        return min(
            1.0,
            _norm_cdf((-m + mu * years) / vol)
            + math.exp(exponent) * _norm_cdf((-m - mu * years) / vol),
        )
    return min(
        1.0,
        _norm_cdf((m - mu * years) / vol)
        + math.exp(exponent) * _norm_cdf((m + mu * years) / vol),
    )


# --------------------------------------------------------------------------- #
@dataclass
class Observation:
    """One quoted probability with the payoff it belongs to."""

    kind: LegKind
    market_prob: float
    weight: float
    lo: float = -INF
    hi: float = INF
    strike: float = math.nan
    label: str = ""

    barrier: float = math.nan

    def model_prob(self, spot: float, sigma: float, years: float, drift: float = 0.0) -> float:
        if self.kind is LegKind.RANGE:
            return lognormal_prob_range(spot, sigma, years, self.lo, self.hi, drift)
        if self.kind is LegKind.ABOVE:
            return lognormal_prob_above(spot, sigma, years, self.strike, drift)
        return lognormal_prob_touch(
            spot, sigma, years, self.barrier, up=self.kind is LegKind.TOUCH_UP, drift=drift
        )


@dataclass
class Calibration:
    sigma: float = math.nan
    rmse: float = math.nan
    n_points: int = 0
    ok: bool = False
    reason: str = ""
    observations: list[Observation] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "sigma": None if math.isnan(self.sigma) else round(self.sigma, 6),
            "rmse": None if math.isnan(self.rmse) else round(self.rmse, 6),
            "n_points": self.n_points,
            "ok": self.ok,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------- #
#: Quotes this close to 0 or 1 carry almost no information about volatility and
#: are dominated by the tick size, so they are dropped from the fit.
MIN_INFORMATIVE_PROB = 0.02
MAX_INFORMATIVE_PROB = 0.98

#: A quote wider than this is not a price, it is two people disagreeing.
MAX_INFORMATIVE_SPREAD = 0.15


def observations_from_strip(strip: Strip) -> list[Observation]:
    """Two-sided, informative quotes from one strip, weighted by information.

    Barrier legs are included: the reflection principle gives their probability
    in closed form too, so a touch strip can be calibrated on its own quotes
    rather than borrowing a volatility from a differently-dated bracket strip.
    """
    out: list[Observation] = []

    for leg in strip.legs:
        if not leg.accepting_orders:
            continue
        bid, ask = leg.yes_book.best_bid, leg.yes_book.best_ask
        if bid is None or ask is None:
            # fall back to the NO book, which quotes the same thing inverted
            nb, na = leg.no_book.best_bid, leg.no_book.best_ask
            if nb is None or na is None:
                continue
            bid, ask = 1.0 - na, 1.0 - nb
        if ask <= bid:
            continue
        spread = ask - bid
        if spread > MAX_INFORMATIVE_SPREAD:
            continue
        p = 0.5 * (bid + ask)
        if not MIN_INFORMATIVE_PROB <= p <= MAX_INFORMATIVE_PROB:
            continue

        # Fisher-style information weight: a 50/50 quote pins down sigma far
        # better than a 3% one, and a tight quote better than a wide one.
        weight = p * (1.0 - p) / (spread + 0.005)
        weight *= 1.0 + math.log1p(max(leg.liquidity_usd, 0.0)) / 12.0

        out.append(
            Observation(
                kind=leg.kind,
                market_prob=p,
                weight=weight,
                lo=leg.lo,
                hi=leg.hi,
                strike=leg.strike,
                barrier=leg.barrier,
                label=leg.label,
            )
        )
    return out


def calibrate(
    observations: Sequence[Observation],
    spot: float,
    years: float,
    *,
    drift: float = 0.0,
    lo: float = 0.05,
    hi: float = 5.0,
    min_points: int = 3,
) -> Calibration:
    """Least-squares fit of a single lognormal volatility to the quoted strip."""
    obs = [o for o in observations if o.weight > 0]
    if len(obs) < min_points:
        return Calibration(
            n_points=len(obs),
            reason=f"only {len(obs)} informative quotes, need {min_points}",
            observations=obs,
        )
    if years <= 0 or spot <= 0:
        return Calibration(n_points=len(obs), reason="degenerate horizon or spot", observations=obs)

    total_w = sum(o.weight for o in obs)

    def loss(log_sigma: float) -> float:
        sigma = math.exp(log_sigma)
        err = 0.0
        for o in obs:
            d = o.model_prob(spot, sigma, years, drift) - o.market_prob
            err += o.weight * d * d
        return err / total_w

    res = minimize_scalar(
        loss, bounds=(math.log(lo), math.log(hi)), method="bounded",
        options={"xatol": 1e-4},
    )
    if not res.success:
        return Calibration(n_points=len(obs), reason=f"fit failed: {res.message}", observations=obs)

    sigma = float(math.exp(res.x))
    rmse = math.sqrt(max(float(res.fun), 0.0))

    at_bound = sigma <= lo * 1.001 or sigma >= hi * 0.999
    cal = Calibration(
        sigma=sigma,
        rmse=rmse,
        n_points=len(obs),
        ok=not at_bound,
        reason="hit the search bound" if at_bound else "",
        observations=obs,
    )
    if at_bound:
        log.debug("implied vol fit hit a bound at %.3f; treating it as unusable", sigma)
    return cal


def calibrate_strips(
    strips: Iterable[Strip], spot: float, years: float, *, drift: float = 0.0
) -> Calibration:
    """Fit one volatility across every strip sharing a settlement time."""
    obs: list[Observation] = []
    for strip in strips:
        obs.extend(observations_from_strip(strip))
    return calibrate(obs, spot, years, drift=drift)


# --------------------------------------------------------------------------- #
def bank_prob(
    bank: PathBank, obs: Observation, spot: float, sigma: float, drift: float
) -> float:
    if obs.kind is LegKind.RANGE:
        return bank.prob_range(spot, sigma, drift, obs.lo, obs.hi)
    if obs.kind is LegKind.ABOVE:
        return bank.prob_above(spot, sigma, drift, obs.strike)
    if obs.kind is LegKind.TOUCH_UP:
        return bank.prob_touch_up(spot, sigma, drift, obs.barrier)
    return bank.prob_touch_down(spot, sigma, drift, obs.barrier)


def calibrate_bank(
    bank: PathBank,
    observations: Sequence[Observation],
    spot: float,
    *,
    drift: float = 0.0,
    lo: float = 0.05,
    hi: float = 5.0,
    min_points: int = 3,
) -> Calibration:
    """Fit volatility using the *trading* distribution, not a lognormal proxy.

    This matters more than it looks.  The trading model resamples real BTC
    returns, so it has fatter tails and a correspondingly thinner middle than a
    lognormal at the same volatility.  Calibrate against a lognormal and every
    mid-strike leg inherits that shape difference as a fake few-point "edge" -
    a bias that points the same way every single cycle and would quietly become
    the strategy.  Fitting the same distribution we trade removes it, and what
    is left over is genuine disagreement about shape.

    Each evaluation is a binary search in a sorted array, so the fit costs
    microseconds rather than a new simulation.
    """
    obs = [o for o in observations if o.weight > 0]
    if len(obs) < min_points:
        return Calibration(
            n_points=len(obs),
            reason=f"only {len(obs)} informative quotes, need {min_points}",
            observations=obs,
        )
    if spot <= 0 or bank.years <= 0:
        return Calibration(n_points=len(obs), reason="degenerate horizon or spot", observations=obs)

    total_w = sum(o.weight for o in obs)

    def loss(log_sigma: float) -> float:
        sigma = math.exp(log_sigma)
        err = 0.0
        for o in obs:
            d = bank_prob(bank, o, spot, sigma, drift) - o.market_prob
            err += o.weight * d * d
        return err / total_w

    res = minimize_scalar(
        loss, bounds=(math.log(lo), math.log(hi)), method="bounded",
        options={"xatol": 1e-3},
    )
    if not res.success:
        return Calibration(n_points=len(obs), reason=f"fit failed: {res.message}", observations=obs)

    sigma = float(math.exp(res.x))
    at_bound = sigma <= lo * 1.001 or sigma >= hi * 0.999
    return Calibration(
        sigma=sigma,
        rmse=math.sqrt(max(float(res.fun), 0.0)),
        n_points=len(obs),
        ok=not at_bound,
        reason="hit the search bound" if at_bound else "",
        observations=obs,
    )


# --------------------------------------------------------------------------- #
def blend_vol(realized: float, implied: float | None, weight: float) -> float:
    """Geometric blend. ``weight`` is how far to lean on the market's number.

    Geometric rather than arithmetic because volatility is a scale parameter:
    halfway between 30% and 60% should be 42%, not 45%.
    """
    if implied is None or implied <= 0 or not math.isfinite(implied):
        return realized
    w = min(max(float(weight), 0.0), 1.0)
    return float(math.exp(w * math.log(implied) + (1.0 - w) * math.log(max(realized, 1e-6))))


# --------------------------------------------------------------------------- #
@dataclass
class VolView:
    """The three volatilities the engine reasons with, and where they came from."""

    realized: float
    base: float
    implied: float | None = None
    calibration: Calibration | None = None

    def as_map(self) -> dict[str, float]:
        return {
            "base": self.base,
            "realized": self.realized,
            "implied": self.implied if self.implied else self.realized,
        }

    @property
    def premium(self) -> float | None:
        """How much more volatility the market is charging than we measure."""
        if not self.implied or self.realized <= 0:
            return None
        return self.implied / self.realized

    def describe(self) -> str:
        if self.implied:
            return (
                f"realised {100 * self.realized:.1f}% | implied {100 * self.implied:.1f}% "
                f"({self.premium:.2f}x) | model {100 * self.base:.1f}%"
            )
        why = self.calibration.reason if self.calibration else "not attempted"
        return f"realised {100 * self.realized:.1f}% | implied n/a ({why}) | model {100 * self.base:.1f}%"

    def to_dict(self) -> dict[str, object]:
        return {
            "realized": round(self.realized, 6),
            "base": round(self.base, 6),
            "implied": round(self.implied, 6) if self.implied else None,
            "premium": round(self.premium, 4) if self.premium else None,
            "calibration": self.calibration.to_dict() if self.calibration else None,
        }


def resolve_vols(
    realized_annual: float,
    strips: Iterable[Strip],
    spot: float,
    years: float,
    cfg,
    *,
    bank: PathBank | None = None,
) -> VolView:
    """Realised vol, the strip's implied vol, and the blend the engine trades on.

    Pass ``bank`` to fit against the distribution the engine actually trades;
    without one this falls back to a lognormal fit, which is fast and roughly
    right but leaves a shape bias at the middle strikes.
    """
    if not cfg.model.calibrate_to_market:
        return VolView(realized=realized_annual, base=realized_annual)

    obs: list[Observation] = []
    for strip in strips:
        obs.extend(observations_from_strip(strip))

    if bank is not None:
        cal = calibrate_bank(bank, obs, spot, drift=cfg.model.drift_annual)
    else:
        cal = calibrate(obs, spot, years, drift=cfg.model.drift_annual)

    implied: float | None = None
    if cal.ok and math.isfinite(cal.sigma):
        implied = min(max(cal.sigma, cfg.model.vol_floor_annual), cfg.model.vol_cap_annual)

    base = blend_vol(realized_annual, implied, cfg.model.implied_vol_weight)
    base = min(max(base, cfg.model.vol_floor_annual), cfg.model.vol_cap_annual)
    return VolView(realized=realized_annual, base=base, implied=implied, calibration=cal)
