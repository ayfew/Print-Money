"""Experimental US momentum research. No orders, recommendations or notifications."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

from .stocks import number, safe_url

VERSION = "intraday-momentum-v1"
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Policy:
    gap: float = .05
    min_price: float = 3
    min_premarket_volume: float = 50000
    max_age_seconds: float = 600
    stop_fraction: float = .02
    target_fraction: float = .04
    round_trip_cost_bps: float = 30

    def __post_init__(self):
        if any(number(v) is None or v <= 0 for v in asdict(self).values()):
            raise ValueError("Policy values must be finite and positive.")
        if max(self.gap, self.stop_fraction, self.target_fraction) >= 1:
            raise ValueError("Fractional thresholds must be below one.")


def stamp(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required.")
    return result.astimezone(timezone.utc)


@lru_cache(maxsize=1)
def calendar():
    from .stockdata import USCalendar
    return USCalendar().calendar


def session(now):
    local = stamp(now).astimezone(ET)
    day = local.date().isoformat()
    cal = calendar()
    if not cal.is_session(day):
        return None
    return {"day": day, "open": cal.session_open(day).to_pydatetime(),
            "close": cal.session_close(day).to_pydatetime(),
            "premarket_start": local.replace(hour=4, minute=0, second=0, microsecond=0),
            "entry_start": local.replace(hour=10, minute=0, second=0, microsecond=0)}


def evaluation_contract(policy):
    return {"status": "unvalidated", "sample_count": 0,
        "reason": "No verified point-in-time intraday outcome, news and universe archive has been evaluated.",
        "entry": "Next completed five-minute bar open after first signal, at most one trade per symbol/session.",
        "exit": "Fixed stop or target; stop first if both touch in one bar; gaps use worse opening fill; otherwise session close (including early closes).",
        "stop_fraction": policy.stop_fraction, "target_fraction": policy.target_fraction,
        "round_trip_cost_bps": policy.round_trip_cost_bps,
        "cost_sensitivity_bps": [10, 30, 60],
        "cost_model": "Hypothetical total fees, spread and slippage; not a live execution estimate. Halts and unfillable bars are unscorable.",
        "risk": "Research only. No position size or user risk tolerance assumed; stops cannot guarantee loss limits.",
        "validation": "Freeze policy before prospective holdout. Chronological walk-forward only; no tuning on holdout; retain delisted names and contemporaneous membership/news.",
        "benchmark": "SPY over identical entry/exit timestamps, with the same cost assumption.",
        "net_expectancy": None, "win_rate": None, "mean_win": None, "mean_loss": None,
        "max_drawdown": None, "benchmark_return": None}


def _row(item, now, window, policy, policy_id):
    symbol = item["symbol"]
    row = {"symbol": symbol, "state": "cannot_evaluate", "reasons": [], "metrics": {},
           "source_url": item.get("source_url"), "retrieved_at": item.get("retrieved_at"),
           "source_status": item.get("source_status", "imported_attestation"),
           "catalyst": item.get("catalyst") or {"status": "unknown"},
           "new_signal": False, "signal_id": None, "premarket_fingerprint": None,
           "premarket_freeze_accepted": False,
           "session": window["day"] if window else None}
    if not window or now >= window["close"] or now < window["premarket_start"]:
        row.update(state="outside_session", reasons=["market_closed"])
        return row
    if now < window["entry_start"]:
        row.update(state="waiting", reasons=["entries_start_at_10_ET"])
        return row
    missing = row["reasons"]
    if item.get("currency") != "USD" or item.get("instrument_type") != "EQUITY":
        missing.append("unsupported_instrument")
    if not safe_url(item.get("source_url")):
        missing.append("price_source_missing")
    try:
        age = (now-stamp(item.get("retrieved_at"))).total_seconds()
        row["source_age_seconds"] = age
        if not 0 <= age <= policy.max_age_seconds:
            missing.append("source_stale")
    except (ValueError, TypeError, AttributeError):
        missing.append("source_timestamp_missing")
    if item.get("premarket_complete") is not True:
        missing.append("premarket_coverage_unverified")
    if item.get("corporate_actions_verified") is not True:
        missing.append("corporate_actions_unverified")
    daily = item.get("daily") or []
    expected = [str(d.date()) for d in calendar().sessions_window(calendar().previous_session(window["day"]), -200)]
    daily = [d for d in daily if isinstance(d, dict) and str(d.get("session", "")) < window["day"]]
    daily.sort(key=lambda d: d["session"])
    if (len(daily) < 200 or [d.get("session") for d in daily[-200:]] != expected
            or any(number(d.get("close")) is None or d["close"] <= 0
                   or number(d.get("high")) is None or d["high"] < d["close"] for d in daily[-200:])):
        missing.append("daily_history_incomplete")
        daily = []
    bars = []
    try:
        seen = set()
        for raw in item.get("bars") or []:
            start = stamp(raw["start"])
            # Do not let unfinished or future bars affect either HOD or volume.
            if start + timedelta(minutes=5) > now or not window["premarket_start"] <= start < window["close"]:
                continue
            if start in seen or start.second or start.microsecond or start.minute % 5:
                raise ValueError("Duplicate or unaligned five-minute bar")
            seen.add(start)
            if any(number(raw.get(k)) is None for k in ("open", "high", "low", "close", "volume")):
                raise ValueError("Invalid OHLCV")
            if not (0 < raw["low"] <= min(raw["open"], raw["close"]) <= max(raw["open"], raw["close"]) <= raw["high"] and raw["volume"] >= 0):
                raise ValueError("Invalid OHLCV range")
            bars.append(dict(raw, start=start))
        bars.sort(key=lambda b: b["start"])
    except (ValueError, KeyError, TypeError, AttributeError):
        missing.append("invalid_bars")
        bars = []
    pre = [b for b in bars if b["start"] < window["open"]]
    regular = [b for b in bars if b["start"] >= window["open"]]
    if not pre:
        missing.append("premarket_missing")
    if len(regular) < 2:
        missing.append("regular_bars_missing")
    if regular:
        end = regular[-1]["start"] + timedelta(minutes=5)
        row["bar_end"] = end.isoformat()
        if (now-end).total_seconds() > policy.max_age_seconds:
            missing.append("quote_stale")
        # Full regular-session history is required for the prior HOD.
        expected_starts = int((regular[-1]["start"]-window["open"]).total_seconds()/300)+1
        if len(regular) != expected_starts:
            missing.append("regular_session_incomplete")
    catalyst = row["catalyst"]
    try:
        published, observed, verified = (stamp(catalyst[k]) for k in ("published_at", "observed_at", "verified_at"))
        cutoff = regular[-1]["start"] + timedelta(minutes=5) if regular else now
        if not (catalyst.get("status") == "verified" and safe_url(catalyst.get("url"))
                and catalyst.get("source") and catalyst.get("description")
                and now-timedelta(hours=24) <= published <= observed <= verified <= cutoff):
            raise ValueError("Unknown catalyst")
    except (ValueError, KeyError, TypeError, AttributeError):
        missing.append("catalyst_unknown")
    if pre:
        row["premarket_fingerprint"] = sha256(json.dumps(pre, sort_keys=True, default=str).encode()).hexdigest()
        row["premarket_freeze_accepted"] = item.get("premarket_complete") is True
        row["metrics"].update(premarket_high=max(b["high"] for b in pre), premarket_volume=sum(b["volume"] for b in pre),
                               premarket_last=pre[-1]["close"], premarket_frozen_at=window["open"].isoformat())
    if missing:
        return row
    current = regular[-1]
    previous = daily[-1]
    metrics = row["metrics"]
    metrics.update(price=current["close"], previous_close=previous["close"], previous_high=previous["high"],
                   sma200=sum(d["close"] for d in daily[-200:])/200,
                   prior_hod=max(b["high"] for b in regular[:-1]),
                   gap=metrics["premarket_last"]/previous["close"]-1)
    checks = {"gap_below_threshold": metrics["gap"] > policy.gap,
              "price_below_threshold": min(current["close"], metrics["premarket_last"]) > policy.min_price,
              "premarket_volume_below_threshold": metrics["premarket_volume"] > policy.min_premarket_volume,
              "below_previous_high": current["close"] > previous["high"],
              "below_sma200": previous["close"] > metrics["sma200"],
              "premarket_high_not_broken": current["close"] > metrics["premarket_high"],
              "prior_hod_not_broken": current["close"] > metrics["prior_hod"],
              "signal_bar_before_10_ET": current["start"] >= window["entry_start"]}
    row["reasons"] = [key for key, passed in checks.items() if not passed]
    row["state"] = "watch" if row["reasons"] else "experimental_signal"
    if row["state"] == "experimental_signal":
        row["signal_id"] = sha256(f"{policy_id}:{window['day']}:{symbol}".encode()).hexdigest()[:24]
    return row


def scan(items, now, policy=None):
    now = stamp(now)
    policy = policy or Policy()
    policy_id = VERSION + "-" + sha256(json.dumps(asdict(policy), sort_keys=True).encode()).hexdigest()[:12]
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("Supply a list of at most 50 symbols.")
    if any(not isinstance(i, dict) for i in items):
        raise ValueError("Every observation must be an object.")
    symbols = [i.get("symbol") for i in items]
    if any(not isinstance(s, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) for s in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError("Unique plain ticker symbols required.")
    try:
        window = session(now)
    except (ImportError, ValueError) as exc:
        raise ValueError("Exchange calendar unavailable; cannot evaluate.") from exc
    rows = [_row(item, now, window, policy, policy_id) for item in items]
    return {"schema_version": 1, "strategy_version": VERSION, "policy_id": policy_id,
            "generated_at": now.isoformat(), "mode": "experimental_intraday_research",
            "policy": asdict(policy), "evaluation": evaluation_contract(policy),
            "fundamentals": "Not a strategy requirement; long-term research is separate.",
            "notifications": "unconfigured", "orders": "disabled", "rows": rows,
            "coverage": {"requested": len(items), "evaluated": sum(r["state"] in ("watch", "experimental_signal") for r in rows),
                         "signals": sum(r["state"] == "experimental_signal" for r in rows)}}


def save_run(report, directory):
    """Append-only evidence; fail closed on corrupt history or concurrent writes."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".writer.lock"
    with lock.open("x") as lock_handle:
        try:
            seen, frozen = set(), {}
            for path in sorted(directory.glob("*.json")):
                old = json.loads(path.read_text(encoding="utf-8"))
                for row in old["rows"]:
                    if row.get("signal_id") and row["state"] == "experimental_signal":
                        seen.add(row["signal_id"])
                    key = (row["session"], row["symbol"])
                    if row.get("premarket_freeze_accepted"):
                        frozen.setdefault(key, row["premarket_fingerprint"])
            result = deepcopy(report)
            for row in result["rows"]:
                key = (row["session"], row["symbol"])
                if key in frozen and row.get("premarket_fingerprint") and frozen[key] != row["premarket_fingerprint"]:
                    row.update(state="cannot_evaluate", signal_id=None)
                    row["premarket_freeze_accepted"] = False
                    row["reasons"].append("premarket_revised_after_freeze")
                row["new_signal"] = bool(row["signal_id"] and row["signal_id"] not in seen)
            result["coverage"]["signals"] = sum(r["state"] == "experimental_signal" for r in result["rows"])
            result["coverage"]["evaluated"] = sum(r["state"] in ("watch", "experimental_signal") for r in result["rows"])
            name = stamp(report["generated_at"]).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex + ".json"
            path = directory / name
            payload = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)
            with path.open("x", encoding="utf-8") as handle:
                handle.write(payload)
            return path
        finally:
            lock_handle.close()
            lock.unlink()


def render_html(report):
    rows = "".join("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in
        (r["symbol"], r["state"], ", ".join(r["reasons"]) or "All experimental conditions met",
         r.get("bar_end", "unknown"), r["catalyst"].get("status", "unknown"))) + "</tr>" for r in report["rows"])
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>PrintMoney · Experimental intraday</title><style>body{font:16px system-ui;background:#101820;color:#eef3f5;max-width:1100px;margin:40px auto;padding:20px}h1{color:#8de0c3}td,th{padding:12px;text-align:left;border-bottom:1px solid #52616b}table{width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#8de0c3}</style>'
        '<h1>Experimental intraday scanner</h1><p>Validation: <strong>unvalidated</strong> · No trades or notifications.</p>'
        f'<p>Run: {escape(report["generated_at"])} · Signals: {report["coverage"]["signals"]} / {report["coverage"]["requested"]}</p>'
        '<p>Missing or stale evidence means cannot evaluate. Fundamental coverage is separate. Thresholds are hypotheses, not a demonstrated edge.</p>'
        '<table><thead><tr><th>Symbol</th><th>State</th><th>Reasons</th><th>Last completed bar</th><th>Catalyst</th></tr></thead><tbody>'
        + (rows or '<tr><td colspan="5">No symbols evaluated; no signal.</td></tr>') + '</tbody></table>'
        '<h2>Evidence and evaluation assumptions</h2><pre>' + escape(json.dumps(report, indent=2, ensure_ascii=False)) + '</pre></html>')
