"""One place that turns market data into a calibrated model ensemble.

Every caller - the engine, the CLI, the backtester - needs the same three steps
in the same order: draw the standardised paths, fit the market's volatility to
those very paths, then realise the ensemble around the result.  Doing it here
once means the numbers a person sees from ``pm surface`` are the same numbers
the engine trades on, which is the only way the tool is worth looking at.
"""
from __future__ import annotations

import logging
from typing import Sequence

import numpy as np

from ..config import Config
from ..data.types import Strip
from .calibrate import VolView, resolve_vols
from .paths import ModelEnsemble, PathBank, build_banks, build_ensemble

log = logging.getLogger("printmoney.model")


def prepare_models(
    spot: float,
    realized_vol: float,
    years: float,
    strips: Sequence[Strip],
    cfg: Config,
    *,
    shock_pool: np.ndarray | None = None,
    seed: int | None = None,
) -> tuple[VolView, ModelEnsemble, dict[str, PathBank]]:
    """Paths, then calibration, then the ensemble - in that order."""
    if years <= 0:
        raise ValueError(f"years to settlement must be positive, got {years}")

    banks = build_banks(years, cfg.model, shock_pool=shock_pool, seed=seed)
    primary = banks.get(cfg.model.generator) or next(iter(banks.values()))

    vols = resolve_vols(realized_vol, strips, spot, years, cfg, bank=primary)
    ensemble = build_ensemble(
        spot,
        vols.base,
        years,
        cfg.model,
        shock_pool=shock_pool,
        seed=seed,
        vols=vols.as_map(),
        banks=banks,
    )
    log.debug(
        "models for %.4g years: %s (%d paths, %d steps)",
        years,
        vols.describe(),
        primary.n_paths,
        primary.n_steps,
    )
    return vols, ensemble, banks
