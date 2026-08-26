"""Monte-Carlo path engine.

One simulator answers every question the strips can ask:

* bracket markets need P(a < S_T <= b)  -> terminal price
* above-ladders need P(S_T > K)         -> terminal price
* touch markets need P(max S_t >= B)    -> running extremes

Simulating once and reading all three off the same paths keeps the fair values
*mutually consistent*, which is the whole point: an inconsistency between our own
numbers would show up as a fake arbitrage and we would happily trade into it.

The paths are generated once at unit volatility and no drift - a ``PathBank`` -
and then *realised* at whatever volatility is wanted.  Because the log price is

    log S_T = log S_0 + (mu - sigma^2/2) T + sigma * L_T

changing sigma is an affine transform of the same random numbers, so an ensemble
of five volatilities costs one simulation instead of five, and calibrating a
volatility to market quotes costs a binary search over a sorted array instead of
a hundred simulations.  It also means every member of the ensemble sees the same
draws, so differences between them are differences of assumption, not of luck.

Memory is O(n_paths), not O(n_paths x n_steps): the state carried forward is the
standardised log return plus its running max and min.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from ..config import ModelConfig

log = logging.getLogger("printmoney.paths")

#: Broadie-Glasserman-Kou barrier continuity correction, -zeta(1/2)/sqrt(2*pi).
BGK_BETA = 0.5826


# --------------------------------------------------------------------------- #
def _n_steps(years: float, cfg: ModelConfig) -> int:
    hours = max(years * 365.25 * 24.0, 1e-6)
    n = int(math.ceil(hours * cfg.steps_per_hour))
    return max(1, min(n, cfg.max_steps))


def _antithetic(rng: np.random.Generator, n: int, draw) -> np.ndarray:
    """Draw ``n`` shocks as mirrored pairs: path i and path i+n/2 are opposites.

    Applied with the same split at every step, this makes the two halves exact
    antithetic partners, which cuts the sampling error on tail probabilities -
    the numbers that matter most here, since the tails are where these markets
    are most often wrong.
    """
    half = (n + 1) // 2
    z = draw(rng, half)
    return np.concatenate([z, -z])[:n]


class _BootstrapSampler:
    """Vectorised stationary block bootstrap over a pool of standardised shocks.

    Every path holds its own cursor into the historical series and its own block
    countdown, so runs of high-volatility history stay glued together - that is
    what reproduces volatility clustering instead of averaging it away.
    """

    def __init__(
        self, pool: np.ndarray, n_paths: int, block_steps: int, rng: np.random.Generator
    ) -> None:
        self.pool = np.asarray(pool, dtype=float)
        self.h = int(self.pool.size)
        if self.h < 20:
            raise ValueError("bootstrap pool too small")
        self.n = int(n_paths)
        self.block = max(1, int(block_steps))
        self.rng = rng
        self.idx = rng.integers(0, self.h, size=self.n)
        self.remaining = rng.integers(1, self.block + 1, size=self.n)

    def draw(self) -> np.ndarray:
        fresh = self.remaining <= 0
        k = int(np.count_nonzero(fresh))
        if k:
            self.idx[fresh] = self.rng.integers(0, self.h, size=k)
            self.remaining[fresh] = self.block
        shocks = self.pool[self.idx]
        self.idx = (self.idx + 1) % self.h
        self.remaining -= 1
        return shocks


# --------------------------------------------------------------------------- #
@dataclass
class PathBank:
    """Standardised paths: unit annual volatility, zero drift.

    ``l_term`` is the terminal log return divided by volatility; ``l_max`` and
    ``l_min`` are the running extremes of the same standardised path.  Sorted
    copies are kept so a probability query is a binary search rather than a scan.
    """

    l_term: np.ndarray
    l_max: np.ndarray
    l_min: np.ndarray
    years: float
    n_steps: int
    dt_years: float
    generator: str

    _sorted_term: np.ndarray | None = field(default=None, repr=False)
    _sorted_max: np.ndarray | None = field(default=None, repr=False)
    _sorted_min: np.ndarray | None = field(default=None, repr=False)

    # ---------------------------------------------------------------- #
    @property
    def n_paths(self) -> int:
        return int(self.l_term.size)

    def _term(self) -> np.ndarray:
        if self._sorted_term is None:
            self._sorted_term = np.sort(self.l_term)
        return self._sorted_term

    def _max(self) -> np.ndarray:
        if self._sorted_max is None:
            self._sorted_max = np.sort(self.l_max)
        return self._sorted_max

    def _min(self) -> np.ndarray:
        if self._sorted_min is None:
            self._sorted_min = np.sort(self.l_min)
        return self._sorted_min

    # ---------------------------------------------------------------- #
    def _shift(self, sigma: float, drift: float) -> float:
        """The deterministic part of the terminal log return."""
        return (drift - 0.5 * sigma * sigma) * self.years

    def _z(self, spot: float, sigma: float, drift: float, price: float) -> float:
        """The standardised log return that lands exactly on ``price``."""
        return (math.log(price / spot) - self._shift(sigma, drift)) / sigma

    # ---------------------------------------------------------------- #
    def prob_below(self, spot: float, sigma: float, drift: float, price: float) -> float:
        if price <= 0:
            return 0.0
        z = self._z(spot, sigma, drift, price)
        return float(np.searchsorted(self._term(), z, side="right")) / self.n_paths

    def prob_range(
        self, spot: float, sigma: float, drift: float, lo: float, hi: float
    ) -> float:
        below_hi = 1.0 if hi == math.inf else self.prob_below(spot, sigma, drift, hi)
        below_lo = 0.0 if lo == -math.inf else self.prob_below(spot, sigma, drift, lo)
        return max(0.0, below_hi - below_lo)

    def prob_above(self, spot: float, sigma: float, drift: float, price: float) -> float:
        return 1.0 - self.prob_below(spot, sigma, drift, price)

    def prob_touch_up(
        self, spot: float, sigma: float, drift: float, barrier: float, correct: bool = True
    ) -> float:
        if barrier <= spot:
            return 1.0
        b = barrier * math.exp(-BGK_BETA * sigma * math.sqrt(self.dt_years)) if correct else barrier
        if b <= spot:
            return 1.0
        # The running extremes ignore the (mu - sigma^2/2) drift, which over a
        # day at crypto volatilities is a few basis points of log price - two
        # orders of magnitude below the barrier distances these markets quote,
        # and far below the continuity correction just applied.
        z = math.log(b / spot) / sigma
        sm = self._max()
        return float(sm.size - np.searchsorted(sm, z, side="left")) / self.n_paths

    def prob_touch_down(
        self, spot: float, sigma: float, drift: float, barrier: float, correct: bool = True
    ) -> float:
        if barrier >= spot:
            return 1.0
        b = barrier * math.exp(BGK_BETA * sigma * math.sqrt(self.dt_years)) if correct else barrier
        if b >= spot:
            return 1.0
        z = math.log(b / spot) / sigma
        return float(np.searchsorted(self._min(), z, side="right")) / self.n_paths

    # ---------------------------------------------------------------- #
    def realize(
        self, spot: float, sigma: float, drift: float, name: str = "model"
    ) -> "PathResult":
        """Materialise prices at a given volatility."""
        sigma = max(float(sigma), 1e-6)
        shift = self._shift(sigma, drift)
        return PathResult(
            name=name,
            spot=float(spot),
            terminal=np.exp(math.log(spot) + shift + sigma * self.l_term),
            run_max=np.exp(math.log(spot) + sigma * self.l_max),
            run_min=np.exp(math.log(spot) + sigma * self.l_min),
            sigma_annual=sigma,
            drift_annual=drift,
            years=self.years,
            n_steps=self.n_steps,
            dt_years=self.dt_years,
            generator=self.generator,
            bank=self,
        )


# --------------------------------------------------------------------------- #
@dataclass
class PathResult:
    """Simulated settlement outcomes for one model of the world."""

    name: str
    spot: float
    terminal: np.ndarray
    run_max: np.ndarray
    run_min: np.ndarray
    sigma_annual: float
    years: float
    n_steps: int
    dt_years: float
    generator: str
    drift_annual: float = 0.0
    bank: PathBank | None = field(default=None, repr=False)

    # ---------------------------------------------------------------- #
    @property
    def n_paths(self) -> int:
        return int(self.terminal.size)

    @property
    def sigma_step(self) -> float:
        return self.sigma_annual * math.sqrt(max(self.dt_years, 0.0))

    def stderr(self, p: float) -> float:
        """Monte-Carlo standard error of a probability estimate."""
        p = min(max(p, 0.0), 1.0)
        return math.sqrt(max(p * (1.0 - p), 0.0) / max(self.n_paths, 1))

    # -- terminal ------------------------------------------------------ #
    def prob_range(self, lo: float, hi: float) -> float:
        if self.bank is not None:
            return self.bank.prob_range(self.spot, self.sigma_annual, self.drift_annual, lo, hi)
        t = self.terminal
        if lo == -math.inf and hi == math.inf:
            return 1.0
        if lo == -math.inf:
            mask = t <= hi
        elif hi == math.inf:
            mask = t > lo
        else:
            mask = (t > lo) & (t <= hi)
        return float(np.count_nonzero(mask)) / self.n_paths

    def prob_above(self, strike: float) -> float:
        if self.bank is not None:
            return self.bank.prob_above(self.spot, self.sigma_annual, self.drift_annual, strike)
        return float(np.count_nonzero(self.terminal > strike)) / self.n_paths

    def quantile(self, q: float) -> float:
        return float(np.quantile(self.terminal, min(max(q, 0.0), 1.0)))

    # -- path dependent ------------------------------------------------ #
    def _effective_barrier(self, barrier: float, up: bool, correct: bool) -> float:
        """Shift a discretely monitored barrier toward spot (Broadie-Glasserman-Kou).

        Sampling every five minutes misses touches that happen between samples, so
        without this correction touch probabilities come out systematically low
        and the engine would happily sell barrier markets that are fairly priced.
        """
        if not correct:
            return barrier
        shift = BGK_BETA * self.sigma_step
        return barrier * math.exp(-shift) if up else barrier * math.exp(shift)

    def prob_touch_up(self, barrier: float, correct: bool = True) -> float:
        if self.bank is not None:
            return self.bank.prob_touch_up(
                self.spot, self.sigma_annual, self.drift_annual, barrier, correct
            )
        eff = self._effective_barrier(barrier, True, correct)
        return float(np.count_nonzero(self.run_max >= eff)) / self.n_paths

    def prob_touch_down(self, barrier: float, correct: bool = True) -> float:
        if self.bank is not None:
            return self.bank.prob_touch_down(
                self.spot, self.sigma_annual, self.drift_annual, barrier, correct
            )
        eff = self._effective_barrier(barrier, False, correct)
        return float(np.count_nonzero(self.run_min <= eff)) / self.n_paths

    # ------------------------------------------------------------------ #
    def summary(self) -> dict[str, float | int | str]:
        return {
            "name": self.name,
            "generator": self.generator,
            "spot": self.spot,
            "sigma_annual": self.sigma_annual,
            "years": self.years,
            "n_paths": self.n_paths,
            "n_steps": self.n_steps,
            "sigma_horizon": self.sigma_annual * math.sqrt(self.years),
            "p05": self.quantile(0.05),
            "p50": self.quantile(0.50),
            "p95": self.quantile(0.95),
        }


# --------------------------------------------------------------------------- #
def simulate_bank(
    years: float,
    cfg: ModelConfig,
    *,
    shock_pool: Sequence[float] | np.ndarray | None = None,
    generator: str | None = None,
    seed: int | None = None,
    n_paths: int | None = None,
) -> PathBank:
    """Generate standardised paths: unit annual volatility, zero drift."""
    if years <= 0:
        raise ValueError(f"years must be positive, got {years}")

    gen = (generator or cfg.generator).lower()
    paths = int(n_paths or cfg.n_paths)
    steps = _n_steps(years, cfg)
    dt = years / steps
    step_scale = math.sqrt(dt)

    rng = np.random.default_rng(cfg.seed if seed is None else seed)

    if gen == "bootstrap" and (shock_pool is None or len(shock_pool) < 30):
        log.debug("no usable shock pool, falling back to gbm")
        gen = "gbm"

    sampler: _BootstrapSampler | None = None
    scale = 1.0
    if gen == "bootstrap":
        # Historical bars are hourly, so a block of `bootstrap_block_hours`
        # spans that much real tape no matter what our step size is.
        block_steps = max(1, int(round(cfg.bootstrap_block_hours * cfg.steps_per_hour)))
        sampler = _BootstrapSampler(np.asarray(shock_pool, dtype=float), paths, block_steps, rng)
    elif gen == "student_t":
        scale = math.sqrt(cfg.student_t_df / max(cfg.student_t_df - 2.0, 1e-6))
    elif gen != "gbm":
        raise ValueError(f"unknown model.generator {gen!r}; use bootstrap|student_t|gbm")

    l = np.zeros(paths, dtype=float)
    l_max = np.zeros(paths, dtype=float)
    l_min = np.zeros(paths, dtype=float)

    for _ in range(steps):
        if sampler is not None:
            z = sampler.draw()
        elif gen == "student_t":
            z = _antithetic(rng, paths, lambda r, k: r.standard_t(cfg.student_t_df, size=k) / scale)
        else:
            z = _antithetic(rng, paths, lambda r, k: r.standard_normal(k))
        l += step_scale * z
        np.maximum(l_max, l, out=l_max)
        np.minimum(l_min, l, out=l_min)

    return PathBank(
        l_term=l,
        l_max=l_max,
        l_min=l_min,
        years=float(years),
        n_steps=steps,
        dt_years=dt,
        generator=gen,
    )


def simulate(
    spot: float,
    sigma_annual: float,
    years: float,
    cfg: ModelConfig,
    *,
    shock_pool: Sequence[float] | np.ndarray | None = None,
    generator: str | None = None,
    vol_mult: float = 1.0,
    seed: int | None = None,
    name: str = "base",
    n_paths: int | None = None,
) -> PathResult:
    """Simulate BTC to settlement at a specific volatility."""
    if spot <= 0:
        raise ValueError(f"spot must be positive, got {spot}")
    bank = simulate_bank(
        years, cfg, shock_pool=shock_pool, generator=generator, seed=seed, n_paths=n_paths
    )
    return bank.realize(spot, max(sigma_annual * vol_mult, 1e-6), cfg.drift_annual, name)


# --------------------------------------------------------------------------- #
@dataclass
class ModelEnsemble:
    """Several views of the same settlement, used for the robust objective.

    Any one volatility number is a guess.  Trading only what survives the *worst*
    member of a plausible set is how the engine avoids sizing up on a position
    whose whole edge came from a volatility estimate being 15% too low.
    """

    members: list[PathResult] = field(default_factory=list)
    years: float = 0.0
    spot: float = 0.0
    banks: dict[str, PathBank] = field(default_factory=dict, repr=False)

    @property
    def base(self) -> PathResult:
        if not self.members:
            raise ValueError("empty ensemble")
        return self.members[0]

    def __len__(self) -> int:
        return len(self.members)

    def __iter__(self):
        return iter(self.members)

    def summary(self) -> list[dict[str, float | int | str]]:
        return [m.summary() for m in self.members]


def build_ensemble(
    spot: float,
    sigma_annual: float,
    years: float,
    cfg: ModelConfig,
    *,
    shock_pool: np.ndarray | None = None,
    seed: int | None = None,
    vols: dict[str, float] | None = None,
    banks: dict[str, PathBank] | None = None,
) -> ModelEnsemble:
    """Realise every ensemble member from one path bank per generator.

    ``vols`` maps a ``vol_source`` name to a volatility, so one member can be
    built on the market's implied volatility and another on our realised
    estimate.  Missing sources fall back to ``sigma_annual``.
    """
    sources = {"base": sigma_annual, "implied": sigma_annual, "realized": sigma_annual}
    if vols:
        for k, v in vols.items():
            if v and math.isfinite(v) and v > 0:
                sources[k] = float(v)

    bank_cache: dict[str, PathBank] = dict(banks or {})
    members: list[PathResult] = []

    for i, spec in enumerate(cfg.ensemble):
        gen = str(spec.get("generator", cfg.generator)).lower()
        bank = bank_cache.get(gen)
        if bank is None:
            bank = simulate_bank(
                years,
                cfg,
                shock_pool=shock_pool,
                generator=gen,
                seed=(cfg.seed if seed is None else seed) + 1000 * i,
                n_paths=int(spec["n_paths"]) if spec.get("n_paths") else None,
            )
            bank_cache[gen] = bank
        sigma = sources.get(str(spec.get("vol_source", "base")), sigma_annual)
        sigma *= float(spec.get("vol_mult", 1.0))
        members.append(
            bank.realize(spot, sigma, cfg.drift_annual, str(spec.get("name", f"m{i}")))
        )

    return ModelEnsemble(members=members, years=years, spot=spot, banks=bank_cache)


def mix_tail(main: PathBank, tail: PathBank, weight: float, vol_mult: float) -> PathBank:
    """Blend a small weight of a much wider distribution into a path bank.

    The bank is nothing but a list of standardised returns, so mixing two models
    is concatenating two lists in the right proportion.  ``weight`` ends up being
    the exact share of paths drawn from the wide model, whose returns are scaled
    by ``vol_mult`` before they join.
    """
    if weight <= 0 or tail.n_paths == 0:
        return main
    m = max(1, int(round(weight * main.n_paths / (1.0 - weight))))
    take = min(m, tail.n_paths)
    if take < m:
        log.debug("tail bank has %d paths, wanted %d; mixture weight will be light", take, m)

    def blend(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return np.concatenate([a, b[:take] * vol_mult])

    return PathBank(
        l_term=blend(main.l_term, tail.l_term),
        l_max=blend(main.l_max, tail.l_max),
        l_min=blend(main.l_min, tail.l_min),
        years=main.years,
        n_steps=main.n_steps,
        dt_years=main.dt_years,
        generator=f"{main.generator}+tail",
    )


def build_banks(
    years: float,
    cfg: ModelConfig,
    *,
    shock_pool: np.ndarray | None = None,
    seed: int | None = None,
) -> dict[str, PathBank]:
    """One path bank per generator the ensemble mentions, each tail-reinforced."""
    base_seed = cfg.seed if seed is None else seed
    generators: list[str] = []
    for spec in cfg.ensemble:
        gen = str(spec.get("generator", cfg.generator)).lower()
        if gen not in generators:
            generators.append(gen)

    tail: PathBank | None = None
    if cfg.tail_mixture_weight > 0:
        n_tail = max(1, int(round(cfg.tail_mixture_weight * cfg.n_paths / (1.0 - cfg.tail_mixture_weight))))
        try:
            tail = simulate_bank(
                years,
                cfg,
                shock_pool=shock_pool,
                generator=cfg.tail_generator,
                seed=base_seed + 991,
                n_paths=n_tail,
            )
        except ValueError as exc:
            log.warning("tail mixture disabled: %s", exc)

    out: dict[str, PathBank] = {}
    for i, gen in enumerate(generators):
        bank = simulate_bank(
            years, cfg, shock_pool=shock_pool, generator=gen, seed=base_seed + 1000 * i
        )
        if tail is not None:
            bank = mix_tail(bank, tail, cfg.tail_mixture_weight, cfg.tail_vol_mult)
        out[gen] = bank
    return out
