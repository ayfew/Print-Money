"""Configuration: a plain dataclass tree loaded from config.yaml + environment.

Everything the engine does is driven from here so that a change of behaviour is a
config edit, never a code edit.  Env vars beat the file (handy for CI / one-off
runs): PM_LIVE_ENABLED=0, PM_CAPITAL=250, and so on.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from .util import ROOT

DEFAULT_CONFIG_PATH = ROOT / "config.yaml"


# --------------------------------------------------------------------------- #
@dataclass
class DataConfig:
    gamma_url: str = "https://gamma-api.polymarket.com"
    clob_url: str = "https://clob.polymarket.com"
    spot_sources: list[str] = field(default_factory=lambda: ["binance", "coinbase", "kraken"])
    # Markets settle on the Binance BTC/USDT 1-minute close, so that is the
    # reference tape we model.
    settlement_symbol: str = "BTCUSDT"
    http_timeout: float = 20.0
    books_batch_size: int = 40
    max_book_workers: int = 8


@dataclass
class MarketFilters:
    """Which Polymarket events the scanner is allowed to look at."""

    include_slug_patterns: list[str] = field(
        default_factory=lambda: [
            "bitcoin-price-on-",
            "bitcoin-above-",
            "what-price-will-bitcoin-hit",
        ]
    )
    exclude_slug_patterns: list[str] = field(default_factory=lambda: ["up-or-down"])
    # Skip anything resolving sooner than this: no time for the edge to realise,
    # and settlement risk dominates.
    min_seconds_to_expiry: int = 900
    # ...or later than this: model error compounds and capital is locked up.
    max_seconds_to_expiry: int = 60 * 60 * 24 * 21
    min_event_liquidity_usd: float = 5_000.0
    min_leg_liquidity_usd: float = 250.0
    max_events: int = 8


@dataclass
class ModelConfig:
    """How the fair-value distribution of BTC at settlement is built."""

    # Monte-Carlo path engine.
    n_paths: int = 40_000
    steps_per_hour: float = 12.0  # 5-minute steps
    max_steps: int = 4_000
    seed: int = 7

    # Volatility.
    vol_lookback_hours: int = 24 * 30
    vol_estimators: list[str] = field(
        default_factory=lambda: ["yang_zhang", "parkinson", "ewma", "close_to_close"]
    )
    vol_estimator_weights: list[float] = field(default_factory=lambda: [0.4, 0.2, 0.3, 0.1])
    ewma_lambda: float = 0.97
    # Markets price a variance risk premium; >1 widens our fair distribution
    # relative to pure realised vol, which makes us less eager to sell tails.
    vol_risk_premium: float = 1.05
    vol_floor_annual: float = 0.15
    vol_cap_annual: float = 3.00

    # Return generator: "bootstrap" (block-resample real BTC returns - fat tails
    # and vol clustering for free), "student_t", or "gbm".
    generator: str = "bootstrap"
    bootstrap_block_hours: float = 2.0
    student_t_df: float = 4.0
    drift_annual: float = 0.0  # no view. we are not forecasting direction.

    # A resampling model can only produce moves it has seen. Thirty days of BTC
    # history contains no 20% day, so the model would price one at zero and the
    # engine would happily sell that insurance. Mixing a small weight of a much
    # wider distribution into every model puts a floor under the tails.
    tail_mixture_weight: float = 0.03
    tail_vol_mult: float = 2.5
    tail_generator: str = "student_t"

    # Fit the market's own volatility out of the quoted strip and anchor the
    # trading model between it and realised vol. Without this the engine is
    # really just short short-dated vol, which is a losing trade dressed up as
    # a hundred small arbitrages.
    calibrate_to_market: bool = True
    implied_vol_weight: float = 0.65   # 1.0 = trust the market's vol completely

    # Model ensemble used for the robust objective. ``vol_source`` picks which
    # volatility a member is built on: base (the blend), implied (the market's),
    # or realized (ours). Trading only what survives all of them is what makes
    # the engine indifferent to the level of volatility and sensitive only to
    # the shape of the distribution.
    ensemble: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"name": "base", "vol_source": "base", "vol_mult": 1.00, "generator": "bootstrap"},
            {"name": "implied", "vol_source": "implied", "vol_mult": 1.00, "generator": "bootstrap"},
            {"name": "realized", "vol_source": "realized", "vol_mult": 1.00, "generator": "bootstrap"},
            {"name": "highvol", "vol_source": "implied", "vol_mult": 1.15, "generator": "bootstrap"},
            {"name": "gaussian", "vol_source": "base", "vol_mult": 1.00, "generator": "gbm"},
        ]
    )
    robust: bool = True  # maximise the worst ensemble member expected value


@dataclass
class FeeConfig:
    """Polymarket crypto_fees_v2: taker pays rate * min(p,1-p)**exponent per share."""

    rate: float = 0.07
    exponent: float = 1.0
    taker_only: bool = True
    # If the market payload carries its own feeSchedule, prefer that.
    prefer_market_schedule: bool = True
    # Extra safety margin (probability points per share) demanded on top of fees.
    slippage_pad: float = 0.002


@dataclass
class StrategyConfig:
    # Minimum post-fee, post-spread edge per share before we bother.
    min_edge: float = 0.015
    # How much of a book we are willing to eat.
    max_book_levels: int = 6
    max_depth_fraction: float = 0.5
    # LP risk shape.
    max_loss_fraction: float = 0.35  # worst case >= -35% of capital deployed
    # Weight on conditional value at risk in the objective:
    #     maximise  E[PnL] - risk_aversion * CVaR(loss)
    # 0 = pure expected value, which would trade a certain profit for a lottery
    # ticket with a marginally better mean. 0.25 means a dollar of average loss
    # in the worst tenth of outcomes has to be paid for with a quarter of a
    # dollar of expected profit before the trade is worth doing.
    risk_aversion: float = 0.25
    #: The tail CVaR looks at: 0.90 = the worst 10% of settlement outcomes.
    cvar_alpha: float = 0.90
    max_notional_per_event: float = 0.40  # of total capital
    max_notional_per_leg: float = 0.15
    min_order_shares: float = 5.0
    tick_size: float = 0.001
    # Single-leg (touch / barrier) trades.
    enable_touch_markets: bool = True
    touch_min_edge: float = 0.05
    touch_max_notional: float = 0.10


@dataclass
class RiskConfig:
    capital_usd: float = 1_000.0
    max_gross_exposure: float = 0.85  # of capital
    max_daily_loss: float = 0.10  # kill switch
    max_drawdown: float = 0.25  # kill switch (peak-to-trough on equity)
    max_open_events: int = 5
    max_orders_per_cycle: int = 12
    max_trades_per_hour: int = 60
    stale_book_seconds: float = 45.0
    stale_spot_seconds: float = 30.0
    require_complete_strip: bool = True


@dataclass
class ExecutionConfig:
    mode: str = "paper"  # paper | live | dry
    poll_seconds: float = 20.0
    record_snapshots: bool = True
    snapshot_dir: str = "state/snapshots"
    ledger_path: str = "state/ledger.json"
    equity_path: str = "state/equity.json"


@dataclass
class LiveConfig:
    """Live trading. Off by default and gated four separate ways."""

    enabled: bool = False
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    # Never put a key in this file; these name the ENV VARS to read.
    private_key_env: str = "POLYMARKET_PRIVATE_KEY"
    api_key_env: str = "POLYMARKET_API_KEY"
    api_secret_env: str = "POLYMARKET_API_SECRET"
    api_passphrase_env: str = "POLYMARKET_API_PASSPHRASE"
    funder_address_env: str = "POLYMARKET_FUNDER"
    signature_type: int = 1
    confirm_phrase: str = ""  # must equal "I ACCEPT THE RISK" to arm


LIVE_CONFIRM_PHRASE = "I ACCEPT THE RISK"


@dataclass
class Config:
    data: DataConfig = field(default_factory=DataConfig)
    filters: MarketFilters = field(default_factory=MarketFilters)
    model: ModelConfig = field(default_factory=ModelConfig)
    fees: FeeConfig = field(default_factory=FeeConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    log_level: str = "INFO"

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        path = Path(path) if path else DEFAULT_CONFIG_PATH
        raw: dict[str, Any] = {}
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        cfg = cls()
        for name, sub_cls in _NESTED.items():
            section = raw.get(name)
            if isinstance(section, dict):
                setattr(cfg, name, _build(sub_cls, section))
        if isinstance(raw.get("log_level"), str):
            cfg.log_level = raw["log_level"]
        cfg.apply_env()
        cfg.validate()
        return cfg

    # ------------------------------------------------------------------ #
    def apply_env(self) -> None:
        def env_f(name: str) -> float | None:
            v = os.environ.get(name)
            try:
                return float(v) if v not in (None, "") else None
            except ValueError:
                return None

        def env_b(name: str) -> bool | None:
            v = os.environ.get(name)
            if v in (None, ""):
                return None
            return v.strip().lower() in ("1", "true", "yes", "on")

        v = env_f("PM_CAPITAL")
        if v is not None:
            self.risk.capital_usd = v
        v = env_f("PM_MIN_EDGE")
        if v is not None:
            self.strategy.min_edge = v
        v = env_f("PM_POLL_SECONDS")
        if v is not None:
            self.execution.poll_seconds = v
        v = env_f("PM_PATHS")
        if v is not None:
            self.model.n_paths = int(v)
        b = env_b("PM_LIVE_ENABLED")
        if b is not None:
            self.live.enabled = b
        if os.environ.get("PM_MODE"):
            self.execution.mode = os.environ["PM_MODE"]
        if os.environ.get("PM_LOG_LEVEL"):
            self.log_level = os.environ["PM_LOG_LEVEL"]

    # ------------------------------------------------------------------ #
    def validate(self) -> None:
        s, r, m = self.strategy, self.risk, self.model
        if self.execution.mode not in ("paper", "live", "dry"):
            raise ValueError(
                f"execution.mode must be paper|live|dry, got {self.execution.mode!r}"
            )
        if r.capital_usd <= 0:
            raise ValueError("risk.capital_usd must be > 0")
        if s.risk_aversion < 0:
            raise ValueError("strategy.risk_aversion must be >= 0")
        if not 0.0 <= s.cvar_alpha < 1.0:
            raise ValueError("strategy.cvar_alpha must be in [0, 1)")
        if not 0 < s.max_loss_fraction <= 1:
            raise ValueError("strategy.max_loss_fraction must be in (0, 1]")
        if not 0 < s.max_depth_fraction <= 1:
            raise ValueError("strategy.max_depth_fraction must be in (0, 1]")
        if s.min_edge < 0:
            raise ValueError("strategy.min_edge must be >= 0")
        if s.tick_size <= 0:
            raise ValueError("strategy.tick_size must be > 0")
        if m.n_paths < 1000:
            raise ValueError("model.n_paths < 1000 gives useless tail probabilities")
        if len(m.vol_estimators) != len(m.vol_estimator_weights):
            raise ValueError("model.vol_estimators / vol_estimator_weights length mismatch")
        if sum(m.vol_estimator_weights) <= 0:
            raise ValueError("model.vol_estimator_weights must sum to > 0")
        if m.vol_floor_annual >= m.vol_cap_annual:
            raise ValueError("model.vol_floor_annual must be < model.vol_cap_annual")
        if not 0.0 <= m.tail_mixture_weight < 0.5:
            raise ValueError("model.tail_mixture_weight must be in [0, 0.5)")
        if m.tail_vol_mult < 1.0:
            raise ValueError("model.tail_vol_mult must be >= 1")
        if not 0.0 <= m.implied_vol_weight <= 1.0:
            raise ValueError("model.implied_vol_weight must be in [0, 1]")
        if not m.ensemble:
            raise ValueError("model.ensemble must contain at least one member")
        if self.execution.mode == "live" and not self.live.enabled:
            raise ValueError(
                "execution.mode is 'live' but live.enabled is false. Set live.enabled: true "
                f"AND live.confirm_phrase: {LIVE_CONFIRM_PHRASE!r}."
            )

    # ------------------------------------------------------------------ #
    def live_armed(self) -> tuple[bool, str]:
        """Four independent gates must agree before a real order can go out."""
        if self.execution.mode != "live":
            return False, "execution.mode is not 'live'"
        if not self.live.enabled:
            return False, "live.enabled is false"
        if self.live.confirm_phrase.strip() != LIVE_CONFIRM_PHRASE:
            return False, f"live.confirm_phrase is not set to {LIVE_CONFIRM_PHRASE!r}"
        if not os.environ.get(self.live.private_key_env):
            return False, f"env var {self.live.private_key_env} is not set"
        return True, "armed"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


_NESTED: dict[str, type] = {
    "data": DataConfig,
    "filters": MarketFilters,
    "model": ModelConfig,
    "fees": FeeConfig,
    "strategy": StrategyConfig,
    "risk": RiskConfig,
    "execution": ExecutionConfig,
    "live": LiveConfig,
}


# --------------------------------------------------------------------------- #
def _build(cls: type, raw: dict[str, Any]) -> Any:
    """Instantiate a flat dataclass from a dict, ignoring unknown keys."""
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown config keys {sorted(unknown)}")
    return cls(**{k: v for k, v in raw.items() if k in known})


def _to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj
