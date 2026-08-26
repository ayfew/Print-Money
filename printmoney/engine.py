"""The loop.

One cycle:

  1. read spot and enough BTC history to estimate volatility;
  2. pull every BTC strip that passes the filters, with live books;
  3. settle anything that expired since last time;
  4. for each settlement time, build the state space and solve the LP;
  5. price the barrier markets separately;
  6. run every plan past the risk manager, then route it to the broker;
  7. mark the book, record equity, persist everything.

Nothing here decides *what* is a good trade - that is the strategy layer's job.
This module's only opinions are about ordering, caching and failure handling: a
cycle that throws is logged and skipped, never allowed to leave half a hedge on.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from .broker import ExecOrder, orders_from_plan, orders_from_touch_plan, scale_orders
from .broker.live import LiveBroker
from .broker.paper import DryRunBroker, PaperBroker
from .config import Config
from .data.polymarket import PolymarketClient
from .data.spot import SpotFeed
from .data.types import Candle, Quote, Strip, StripKind
from .ledger import Fill, Ledger
from .model import vol as volmod
from .model.build import prepare_models
from .model.calibrate import VolView
from .model.surface import Surface, build_surface
from .registry import MarketRegistry
from .risk import RiskManager, Verdict
from .settlement import SettlementReport, mark_prices, settle_expired
from .strategy.lp import Plan, solve
from .strategy.single import TouchPlan, plan_touch_trades
from .strategy.statespace import (
    StateSpace,
    build_state_space,
    group_strips_by_expiry,
    holdings_on,
)
from .util import fmt_usd, human_dt, utcnow, write_json, years_between

log = logging.getLogger("printmoney.engine")


@dataclass
class GroupResult:
    """What happened for one settlement time."""

    expiry: datetime
    strips: list[Strip]
    surfaces: list[Surface] = field(default_factory=list)
    space: StateSpace | None = None
    plan: Plan | None = None
    verdict: Verdict | None = None
    vols: VolView | None = None
    fills: list[Fill] = field(default_factory=list)
    error: str = ""

    @property
    def slugs(self) -> list[str]:
        return [s.slug for s in self.strips]

    def to_dict(self) -> dict[str, Any]:
        return {
            "expiry": self.expiry.isoformat(),
            "strips": self.slugs,
            "vols": self.vols.to_dict() if self.vols else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "verdict": None if self.verdict is None else {"ok": bool(self.verdict), "why": self.verdict.why},
            "fills": [f.to_dict() for f in self.fills],
            "error": self.error,
            "surfaces": [s.to_dict() for s in self.surfaces],
        }


@dataclass
class TouchResult:
    strip: Strip
    plan: TouchPlan
    verdict: Verdict | None = None
    vols: VolView | None = None
    fills: list[Fill] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strip": self.strip.slug,
            "vols": self.vols.to_dict() if self.vols else None,
            "plan": self.plan.to_dict(),
            "verdict": None if self.verdict is None else {"ok": bool(self.verdict), "why": self.verdict.why},
            "fills": [f.to_dict() for f in self.fills],
        }


@dataclass
class CycleResult:
    ts: datetime
    spot: Quote | None = None
    vol: volmod.VolEstimate | None = None
    groups: list[GroupResult] = field(default_factory=list)
    touches: list[TouchResult] = field(default_factory=list)
    settlement: SettlementReport | None = None
    equity: float = 0.0
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str = ""
    duration_s: float = 0.0

    @property
    def all_fills(self) -> list[Fill]:
        out: list[Fill] = []
        for g in self.groups:
            out.extend(g.fills)
        for t in self.touches:
            out.extend(t.fills)
        return out

    @property
    def accepted_plans(self) -> list[Plan]:
        return [g.plan for g in self.groups if g.plan is not None and g.plan.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "spot": self.spot.to_dict() if self.spot else None,
            "vol": self.vol.to_dict() if self.vol else None,
            "equity": round(self.equity, 4),
            "stats": self.stats,
            "notes": self.notes,
            "error": self.error,
            "duration_s": round(self.duration_s, 3),
            "groups": [g.to_dict() for g in self.groups],
            "touches": [t.to_dict() for t in self.touches],
        }


# --------------------------------------------------------------------------- #
class Engine:
    #: How often the hourly candle history is refreshed. Vol does not move fast
    #: enough to justify pulling a month of bars every twenty seconds.
    HISTORY_TTL_SECONDS = 600.0

    def __init__(
        self,
        cfg: Config,
        *,
        client: PolymarketClient | None = None,
        feed: Any | None = None,
        ledger: Ledger | None = None,
        registry: MarketRegistry | None = None,
        risk: RiskManager | None = None,
    ) -> None:
        self.cfg = cfg
        self.client = client or PolymarketClient(cfg)
        self.feed = feed or SpotFeed(cfg)
        self.ledger = ledger or Ledger.load_or_new(
            cfg.execution.ledger_path, cfg.risk.capital_usd
        )
        self.registry = registry or MarketRegistry()
        self.risk = risk or RiskManager(cfg)
        self.broker = self._make_broker()
        self._candles: list[Candle] = []
        self._candles_at: datetime | None = None
        self.cycles = 0

    # ------------------------------------------------------------------ #
    def _make_broker(self) -> Any:
        mode = self.cfg.execution.mode
        if mode == "live":
            broker = LiveBroker(self.cfg)
            armed, why = self.cfg.live_armed()
            if not armed:
                log.error("live mode requested but %s - falling back to dry run", why)
                return DryRunBroker(self.cfg)
            log.warning("LIVE TRADING ENABLED. Real money is at risk.")
            return broker
        if mode == "dry":
            return DryRunBroker(self.cfg)
        return PaperBroker(self.cfg)

    def close(self) -> None:
        for obj in (self.client, self.feed):
            try:
                obj.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    def history(self, *, force: bool = False) -> list[Candle]:
        now = utcnow()
        fresh = (
            self._candles
            and self._candles_at is not None
            and (now - self._candles_at).total_seconds() < self.HISTORY_TTL_SECONDS
        )
        if fresh and not force:
            return self._candles
        candles = self.feed.klines("1h", self.cfg.model.vol_lookback_hours)
        self._candles = candles
        self._candles_at = now
        log.debug("refreshed %d hourly candles", len(candles))
        return candles

    # ------------------------------------------------------------------ #
    def cycle(self) -> CycleResult:
        started = time.monotonic()
        result = CycleResult(ts=utcnow())
        try:
            self._run_cycle(result)
        except Exception as exc:  # noqa: BLE001
            log.exception("cycle failed")
            result.error = f"{type(exc).__name__}: {exc}"
        result.duration_s = time.monotonic() - started
        self.cycles += 1
        return result

    # ------------------------------------------------------------------ #
    def _run_cycle(self, result: CycleResult) -> None:
        cfg = self.cfg

        spot = self.feed.spot()
        result.spot = spot
        candles = self.history()
        vol = volmod.estimate(candles, cfg.model)
        result.vol = vol
        shock_pool = None
        try:
            shock_pool = volmod.standardized_returns(candles)
        except ValueError as exc:
            result.notes.append(f"bootstrap disabled: {exc}")

        strips = self.client.scan_strips()
        if not strips:
            result.notes.append("no BTC strips passed the filters this cycle")
        self.registry.record_strips(strips)

        # --- settle first: freed cash can fund this cycle ---------------- #
        result.settlement = settle_expired(self.ledger, self.registry, self.feed)

        marks = mark_prices(strips)
        equity = self.ledger.mark_to_market(marks)
        result.equity = equity
        self.risk.start_cycle(equity)

        kill = self.risk.check_kill_switches(equity)
        if not kill:
            result.notes.extend(kill.reasons)
            self._finish(result, marks)
            return

        fresh = self.risk.check_data_freshness(spot, strips)
        if not fresh:
            result.notes.extend(fresh.reasons)
            self._finish(result, marks)
            return

        open_events = len({p.strip_slug for p in self.ledger.open_positions()})

        # --- terminal strips, grouped by settlement time ------------------ #
        for expiry_key, group in sorted(group_strips_by_expiry(strips).items()):
            gr = GroupResult(expiry=group[0].expiry, strips=group)
            result.groups.append(gr)

            usable = []
            for strip in group:
                verdict = self.risk.check_strip(strip)
                if verdict:
                    usable.append(strip)
                else:
                    result.notes.append(f"{strip.slug}: {verdict.why}")
            if not usable:
                gr.error = "no usable strips at this expiry"
                continue

            years = years_between(utcnow(), gr.expiry)
            if years <= 0:
                gr.error = "already expired"
                continue

            try:
                gr.vols, ensemble, _banks = prepare_models(
                    spot.price, vol.annual, years, usable, cfg, shock_pool=shock_pool
                )
                gr.surfaces = [
                    build_surface(s, ensemble, spot.price, years) for s in usable
                ]
                gr.space = build_state_space(usable, ensemble, spot.price, cfg)
                # Price what we already hold on this date into the risk
                # constraints. Without it every cycle re-solves as though the
                # book were empty and the position quietly compounds past the
                # loss floor one compliant plan at a time.
                gr.plan = solve(
                    gr.space,
                    cfg,
                    capital=self._budget(),
                    holdings=holdings_on(gr.space, self.ledger.open_positions()),
                )
            except Exception as exc:  # noqa: BLE001
                gr.error = f"{type(exc).__name__}: {exc}"
                log.warning("group %s failed: %s", expiry_key, gr.error)
                continue

            if not gr.plan.ok:
                continue

            gr.verdict = self.risk.approve(
                gr.plan, self.ledger, equity, open_events=open_events
            )
            if not gr.verdict:
                result.notes.append(f"{expiry_key}: {gr.verdict.why}")
                continue

            orders = orders_from_plan(gr.plan, expiry=gr.expiry)
            orders = scale_orders(orders, gr.verdict.scale, cfg.strategy.min_order_shares)
            gr.fills = self._execute(orders)
            if gr.fills:
                open_events += 1

        # --- barrier strips ------------------------------------------------ #
        for strip in strips:
            if strip.kind is not StripKind.TOUCH:
                continue
            verdict = self.risk.check_strip(strip)
            if not verdict:
                result.notes.append(f"{strip.slug}: {verdict.why}")
                continue
            years = years_between(utcnow(), strip.expiry)
            if years <= 0:
                continue
            try:
                vols, ensemble, _banks = prepare_models(
                    spot.price, vol.annual, years, [strip], cfg, shock_pool=shock_pool
                )
                plan = plan_touch_trades(
                    strip,
                    ensemble,
                    cfg,
                    capital=self._budget(),
                    held={
                        p.token_id: p.cost_basis for p in self.ledger.open_positions()
                    },
                )
            except Exception as exc:  # noqa: BLE001
                result.notes.append(f"{strip.slug}: touch pricing failed ({exc})")
                continue

            tr = TouchResult(strip=strip, plan=plan, vols=vols)
            result.touches.append(tr)
            if not plan.ok:
                continue
            tr.verdict = self.risk.approve(plan, self.ledger, equity, open_events=open_events)
            if not tr.verdict:
                result.notes.append(f"{strip.slug}: {tr.verdict.why}")
                continue
            orders = orders_from_touch_plan(plan, expiry=strip.expiry)
            orders = scale_orders(orders, tr.verdict.scale, cfg.strategy.min_order_shares)
            tr.fills = self._execute(orders)

        if cfg.execution.record_snapshots:
            self._snapshot(result, strips)

        self._finish(result, marks)

    # ------------------------------------------------------------------ #
    def _budget(self) -> float:
        """Cash we are willing to commit right now."""
        return min(self.ledger.cash, self.cfg.risk.capital_usd)

    def _execute(self, orders: Sequence[ExecOrder]) -> list[Fill]:
        if not orders:
            return []
        try:
            return self.broker.execute(orders, self.ledger)
        except Exception as exc:  # noqa: BLE001
            log.error("execution failed: %s", exc)
            return []

    def _finish(self, result: CycleResult, marks: dict[str, float]) -> None:
        equity = self.ledger.mark_to_market(marks)
        result.equity = equity
        self.ledger.record_equity(equity, result.ts)
        result.stats = self.ledger.stats(equity)
        self.ledger.save()
        self.registry.save()
        self.risk.save()

    def _snapshot(self, result: CycleResult, strips: Sequence[Strip]) -> None:
        """Persist the raw inputs so a decision can be replayed and audited."""
        directory = Path(self.cfg.execution.snapshot_dir)
        stamp = result.ts.strftime("%Y%m%dT%H%M%S")
        payload = {
            "ts": result.ts.isoformat(),
            "spot": result.spot.to_dict() if result.spot else None,
            "vol": result.vol.to_dict() if result.vol else None,
            "strips": [s.to_dict() for s in strips],
            "decisions": [g.to_dict() for g in result.groups],
        }
        try:
            write_json(directory / f"{stamp}.json", payload, indent=None)
        except Exception as exc:  # noqa: BLE001
            log.warning("snapshot write failed: %s", exc)

    # ------------------------------------------------------------------ #
    def run(
        self,
        *,
        max_cycles: int | None = None,
        on_cycle: Callable[[CycleResult], None] | None = None,
    ) -> list[CycleResult]:
        """Poll forever (or ``max_cycles`` times). Ctrl-C exits cleanly."""
        log.info(
            "starting: mode=%s, %s, capital %s, poll %s",
            self.cfg.execution.mode,
            self.broker.describe(),
            fmt_usd(self.cfg.risk.capital_usd),
            human_dt(self.cfg.execution.poll_seconds),
        )
        history: list[CycleResult] = []
        n = 0
        try:
            while max_cycles is None or n < max_cycles:
                res = self.cycle()
                history.append(res)
                if on_cycle:
                    on_cycle(res)
                else:
                    log.info(
                        "cycle %d: equity %s | %d fills | %s",
                        self.cycles,
                        fmt_usd(res.equity),
                        len(res.all_fills),
                        res.error or "ok",
                    )
                n += 1
                if max_cycles is not None and n >= max_cycles:
                    break
                time.sleep(max(1.0, self.cfg.execution.poll_seconds))
        except KeyboardInterrupt:
            log.info("interrupted; state saved")
        finally:
            self.ledger.save()
            self.registry.save()
            self.risk.save()
        return history
