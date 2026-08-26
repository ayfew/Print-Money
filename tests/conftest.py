"""Shared fixtures. Nothing here touches the network."""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from printmoney.config import Config  # noqa: E402
from printmoney.data.types import Candle  # noqa: E402


@pytest.fixture
def cfg() -> Config:
    """Defaults, but small and fast: fewer paths, no market calibration."""
    c = Config()
    c.model.n_paths = 8_000
    c.model.steps_per_hour = 6.0
    c.model.calibrate_to_market = False
    c.model.tail_mixture_weight = 0.0
    c.fees.rate = 0.0
    c.fees.slippage_pad = 0.0
    c.fees.prefer_market_schedule = False
    return c


@pytest.fixture
def hour_candles() -> list[Candle]:
    """720 hourly bars of a driftless lognormal walk at 50% annual volatility."""
    rng = np.random.default_rng(1234)
    sigma_annual = 0.50
    dt = 1.0 / (365.25 * 24.0)
    step = sigma_annual * math.sqrt(dt)
    price = 80_000.0
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out: list[Candle] = []
    for i in range(720):
        o = price
        # Sample the path finely inside each bar. Range estimators measure the
        # high and the low, and a coarsely sampled path simply never reaches
        # them, which reads back as volatility that is not there.
        subs = 24
        sub_step = step / math.sqrt(subs)
        path = [o]
        for _ in range(subs):
            path.append(path[-1] * math.exp(sub_step * rng.standard_normal()))
        c = path[-1]
        out.append(
            Candle(
                open_time=start + timedelta(hours=i),
                open=o,
                high=max(path),
                low=min(path),
                close=c,
                volume=100.0,
            )
        )
        price = c
    return out
