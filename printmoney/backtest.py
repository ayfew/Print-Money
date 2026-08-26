"""Two ways of not fooling ourselves.

``replay`` re-runs the solver over recorded snapshots and settles every position
against the real Binance print, so the reported PnL is what those decisions would
actually have paid.  It is honest about what it cannot do: snapshots are taken at
the poll interval, so fills assume the quoted level was reachable.

``validate`` is the stricter test.  It builds a synthetic strip whose true
probabilities we choose, distorts the prices in a known direction, and checks
that the machinery recovers the edge - and, just as importantly, that it declines
to trade when the prices are *fair*.  A strategy engine that finds profit in a
correctly priced market is broken, and this is the test that catches it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .config import Config
from .data.polymarket import strip_from_dict
from .data.types import INF, BookLevel, Leg, LegKind, OrderBook, Strip, StripKind
from .model.build import prepare_models
from .model.paths import simulate
from .strategy.lp import Plan, solve
from .strategy.statespace import build_state_space, group_strips_by_expiry
from .util import fmt_usd, parse_iso, read_json, utcnow, years_between

log = logging.getLogger("printmoney.backtest")


# --------------------------------------------------------------------------- #
# replay
# --------------------------------------------------------------------------- #
@dataclass
class ReplayTrade:
    ts: datetime
    strip: str
    leg: str
    side: str
    price: float
    shares: float
    cost: float
    expiry: datetime
    payoff: float | None = None

    @property
    def pnl(self) -> float | None:
        if self.payoff is None:
            return None
        return self.shares * self.payoff - self.cost


@dataclass
class ReplayReport:
    cycles: int = 0
    plans: int = 0
    accepted: int = 0
    arbitrage: int = 0
    trades: list[ReplayTrade] = field(default_factory=list)
    unsettled: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def staked(self) -> float:
        return sum(t.cost for t in self.trades)

    @property
    def realized(self) -> float:
        return sum(t.pnl for t in self.trades if t.pnl is not None)

    @property
    def hit_rate(self) -> float:
        done = [t for t in self.trades if t.pnl is not None]
        if not done:
            return 0.0
        return sum(1 for t in done if t.pnl > 0) / len(done)

    def summary(self) -> str:
        if not self.trades:
            return f"{self.cycles} cycles replayed, {self.plans} plans, no trades taken"
        roi = self.realized / self.staked if self.staked else 0.0
        return (
            f"{self.cycles} cycles | {self.accepted}/{self.plans} plans accepted "
            f"({self.arbitrage} risk-free) | {len(self.trades)} trades | "
            f"staked {fmt_usd(self.staked)} | realised {fmt_usd(self.realized)} ({roi:+.2%}) | "
            f"win rate {self.hit_rate:.0%}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycles": self.cycles,
            "plans": self.plans,
            "accepted": self.accepted,
            "arbitrage": self.arbitrage,
            "trades": len(self.trades),
            "staked": round(self.staked, 4),
            "realized": round(self.realized, 4),
            "roi": round(self.realized / self.staked, 6) if self.staked else 0.0,
            "hit_rate": round(self.hit_rate, 4),
            "unsettled": self.unsettled,
            "notes": self.notes,
        }


def load_snapshots(directory: str | Path) -> list[dict[str, Any]]:
    d = Path(directory)
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.json")):
        raw = read_json(path)
        if isinstance(raw, dict) and raw.get("strips"):
            out.append(raw)
    return out


def replay(
    cfg: Config,
    *,
    directory: str | Path | None = None,
    settlement_feed: Any | None = None,
    limit: int | None = None,
) -> ReplayReport:
    """Re-solve every recorded snapshot and settle the results for real."""
    snaps = load_snapshots(directory or cfg.execution.snapshot_dir)
    if limit:
        snaps = snaps[-limit:]
    report = ReplayReport()
    if not snaps:
        report.notes.append(
            "no snapshots found - run `pm run` for a while with execution.record_snapshots on"
        )
        return report

    for snap in snaps:
        report.cycles += 1
        ts = parse_iso(snap["ts"])
        spot_raw = snap.get("spot") or {}
        spot = float(spot_raw.get("price") or 0.0)
        vol_raw = snap.get("vol") or {}
        sigma = float(vol_raw.get("annual") or 0.0)
        if spot <= 0 or sigma <= 0:
            report.notes.append(f"{ts:%H:%M:%S}: snapshot missing spot or vol, skipped")
            continue

        try:
            strips = [strip_from_dict(s) for s in snap["strips"]]
        except Exception as exc:  # noqa: BLE001
            report.notes.append(f"{ts:%H:%M:%S}: unreadable strips ({exc})")
            continue

        for _key, group in sorted(group_strips_by_expiry(strips).items()):
            years = years_between(ts, group[0].expiry)
            if years <= 0:
                continue
            try:
                _vols, ensemble, _banks = prepare_models(spot, sigma, years, group, cfg)
                space = build_state_space(group, ensemble, spot, cfg)
                plan = solve(space, cfg)
            except Exception as exc:  # noqa: BLE001
                report.notes.append(f"{ts:%H:%M:%S}: {exc}")
                continue

            report.plans += 1
            if not plan.ok:
                continue
            report.accepted += 1
            if plan.is_arbitrage:
                report.arbitrage += 1
            for o in plan.orders:
                report.trades.append(
                    ReplayTrade(
                        ts=ts,
                        strip=o.instrument.strip_slug,
                        leg=o.instrument.leg.label,
                        side=o.instrument.side.value,
                        price=o.price,
                        shares=o.shares,
                        cost=o.cost,
                        expiry=group[0].expiry,
                    )
                )

    _settle_replay(report, snaps, settlement_feed)
    return report


def _settle_replay(
    report: ReplayReport, snaps: Sequence[dict[str, Any]], feed: Any | None
) -> None:
    """Attach realised payoffs using the actual settlement print."""
    if feed is None:
        report.notes.append("no settlement feed supplied; trades left unsettled")
        report.unsettled = len(report.trades)
        return

    # token -> leg geometry, gathered from every snapshot we saw
    legs: dict[tuple[str, str], tuple[Leg, str]] = {}
    for snap in snaps:
        for s in snap.get("strips", []):
            for lr in s.get("legs", []):
                for side, tok in (("YES", lr.get("yes_token")), ("NO", lr.get("no_token"))):
                    if tok:
                        legs[(s["slug"], lr.get("label", ""))] = (lr, side)  # type: ignore[assignment]

    prices: dict[datetime, float | None] = {}
    for trade in report.trades:
        if trade.expiry not in prices:
            try:
                prices[trade.expiry] = feed.settlement_close(trade.expiry)
            except Exception as exc:  # noqa: BLE001
                log.warning("settlement lookup failed: %s", exc)
                prices[trade.expiry] = None
        price = prices[trade.expiry]
        if price is None:
            report.unsettled += 1
            continue
        entry = legs.get((trade.strip, trade.leg))
        if entry is None:
            report.unsettled += 1
            continue
        lr, _ = entry
        lo = -INF if lr.get("lo") is None else float(lr["lo"])
        hi = INF if lr.get("hi") is None else float(lr["hi"])
        strike = lr.get("strike")
        kind = lr.get("kind")
        if kind == LegKind.RANGE.value:
            yes = lo < price <= hi
        elif kind == LegKind.ABOVE.value and strike is not None:
            yes = price > float(strike)
        else:
            report.unsettled += 1
            continue
        pays = yes if trade.side == "YES" else not yes
        trade.payoff = 1.0 if pays else 0.0


# --------------------------------------------------------------------------- #
# synthetic validation
# --------------------------------------------------------------------------- #
@dataclass
class ValidationCase:
    name: str
    passed: bool
    detail: str

    def line(self) -> str:
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name}: {self.detail}"


@dataclass
class ValidationReport:
    cases: list[ValidationCase] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.cases)

    def summary(self) -> str:
        n_ok = sum(1 for c in self.cases if c.passed)
        return f"{n_ok}/{len(self.cases)} checks passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary(),
            "cases": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.cases],
        }


def synthetic_bracket_strip(
    spot: float,
    edges: Sequence[float],
    yes_prices: Sequence[float],
    *,
    expiry: datetime | None = None,
    depth: float = 5_000.0,
    spread: float = 0.004,
    slug: str = "synthetic-btc-brackets",
) -> Strip:
    """A bracket strip with books built to quote exactly the prices given."""
    expiry = expiry or (utcnow() + timedelta(hours=6))
    legs: list[Leg] = []
    bounds = [(-INF, edges[0])]
    bounds += list(zip(edges, edges[1:]))
    bounds.append((edges[-1], INF))
    if len(bounds) != len(yes_prices):
        raise ValueError(f"need {len(bounds)} prices for {len(edges)} edges, got {len(yes_prices)}")

    for i, ((lo, hi), p) in enumerate(zip(bounds, yes_prices)):
        p = min(max(float(p), 0.002), 0.998)
        bid = max(0.001, p - spread / 2)
        ask = min(0.999, p + spread / 2)
        label = (
            f"<{hi:,.0f}" if lo == -INF else f">{lo:,.0f}" if hi == INF else f"{lo:,.0f}-{hi:,.0f}"
        )
        yes_book = OrderBook(
            token_id=f"Y{i}",
            bids=[BookLevel(bid, depth)],
            asks=[BookLevel(ask, depth)],
        )
        no_book = OrderBook(
            token_id=f"N{i}",
            bids=[BookLevel(round(1.0 - ask, 4), depth)],
            asks=[BookLevel(round(1.0 - bid, 4), depth)],
        )
        legs.append(
            Leg(
                market_id=f"m{i}",
                condition_id=f"c{i}",
                slug=f"{slug}-{i}",
                question=f"synthetic bucket {label}",
                label=label,
                kind=LegKind.RANGE,
                yes_token=f"Y{i}",
                no_token=f"N{i}",
                lo=lo,
                hi=hi,
                liquidity_usd=depth,
                fee_rate=0.0,
                tick_size=0.001,
                min_order_shares=5.0,
                yes_book=yes_book,
                no_book=no_book,
                market_price=p,
            )
        )

    return Strip(
        event_id="synthetic",
        slug=slug,
        title="Synthetic BTC brackets",
        kind=StripKind.BRACKET,
        expiry=expiry,
        start=utcnow() - timedelta(hours=18),
        legs=legs,
        neg_risk=True,
        liquidity_usd=depth * len(legs),
        volume_24h=depth * len(legs),
    )


def _true_probs(spot: float, sigma: float, years: float, edges: Sequence[float], cfg: Config) -> np.ndarray:
    model = simulate(spot, sigma, years, cfg.model, generator="gbm", seed=99, name="truth")
    probs = [model.prob_range(-INF, edges[0])]
    probs += [model.prob_range(a, b) for a, b in zip(edges, edges[1:])]
    probs.append(model.prob_range(edges[-1], INF))
    arr = np.asarray(probs, dtype=float)
    return arr / arr.sum()


def validate(cfg: Config, *, spot: float = 80_000.0, sigma: float = 0.55) -> ValidationReport:
    """Three end-to-end checks against a market whose truth we control."""
    report = ValidationReport()
    years = 6.0 / (365.25 * 24.0)
    edges = [76_000.0, 78_000.0, 80_000.0, 82_000.0, 84_000.0]
    truth = _true_probs(spot, sigma, years, edges, cfg)

    cfg_fair = _clone_with_zero_fees(cfg)

    # --- 1. fairly priced market: must decline to trade ------------------ #
    fair_strip = synthetic_bracket_strip(spot, edges, truth, spread=0.004)
    plan_fair, _ = _solve_synthetic(fair_strip, spot, sigma, years, cfg_fair)
    report.cases.append(
        ValidationCase(
            "declines a fairly priced strip",
            not plan_fair.ok,
            plan_fair.reason if not plan_fair.ok else f"traded anyway: {plan_fair.summary()}",
        )
    )

    # --- 2. cheap strip (sums to 0.94): must find the dutch book --------- #
    cheap = truth * 0.94
    cheap_strip = synthetic_bracket_strip(spot, edges, cheap, spread=0.002)
    plan_cheap, space_cheap = _solve_synthetic(cheap_strip, spot, sigma, years, cfg_fair)
    ok_cheap = plan_cheap.ok and plan_cheap.is_arbitrage
    report.cases.append(
        ValidationCase(
            "finds the risk-free trade when the strip prices under 1.00",
            ok_cheap,
            plan_cheap.summary(),
        )
    )

    # --- 3. one bucket badly overpriced: must sell it, and win on average - #
    skewed = np.array(truth, dtype=float)
    peak = int(np.argmax(truth))
    skewed[peak] *= 1.6
    skewed = skewed / skewed.sum()
    skew_strip = synthetic_bracket_strip(spot, edges, skewed, spread=0.004)
    plan_skew, space_skew = _solve_synthetic(skew_strip, spot, sigma, years, cfg_fair)
    if plan_skew.ok and space_skew is not None:
        realised = float(np.dot(truth, plan_skew.pnl_by_state))
        report.cases.append(
            ValidationCase(
                "profits from an overpriced bucket under the true distribution",
                realised > 0,
                f"{plan_skew.summary()}; true-model EV {fmt_usd(realised)}",
            )
        )
    else:
        report.cases.append(
            ValidationCase(
                "profits from an overpriced bucket under the true distribution",
                False,
                f"no plan produced: {plan_skew.reason}",
            )
        )

    # --- 4. loss floor is respected in every state ----------------------- #
    checked = [p for p in (plan_cheap, plan_skew) if p.ok]
    if checked:
        allowed = (
            cfg.strategy.max_loss_fraction
            * cfg.strategy.max_notional_per_event
            * cfg.risk.capital_usd
        )
        floor_ok = all(p.worst_case >= -allowed - 1e-3 for p in checked)
        worst = min(p.worst_case for p in checked)
        report.cases.append(
            ValidationCase(
                "worst-case loss stays inside the configured floor",
                floor_ok,
                f"worst observed {fmt_usd(worst)}",
            )
        )
    return report


def _clone_with_zero_fees(cfg: Config) -> Config:
    """Synthetic legs carry no fee schedule; make the config agree."""
    import copy

    out = copy.deepcopy(cfg)
    out.fees.prefer_market_schedule = False
    out.fees.rate = 0.0
    out.fees.slippage_pad = 0.0
    return out


def _solve_synthetic(
    strip: Strip, spot: float, sigma: float, years: float, cfg: Config
) -> tuple[Plan, Any]:
    """Run the synthetic strip through the *whole* pipeline, calibration included.

    Going through ``prepare_models`` rather than straight to the simulator is the
    point of the exercise: it means these checks would fail if the calibration
    started absorbing real mispricings, not just if the solver broke.
    """
    _vols, ensemble, _banks = prepare_models(spot, sigma, years, [strip], cfg)
    space = build_state_space([strip], ensemble, spot, cfg)
    return solve(space, cfg), space
