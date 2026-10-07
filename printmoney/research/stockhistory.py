"""Local, integrity-checked prospective research records; no portfolio amounts."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


def private_path(path, root) -> Path:
    root = Path(root).absolute()
    if root.resolve() != root:
        raise ValueError("Private research root must not redirect through a symlink or junction.")
    target = Path(path).resolve()
    if not target.is_relative_to(root) or target == root:
        # Directories beneath the root are allowed; the root itself is not an export file.
        raise ValueError("Research output must stay beneath the private state/research directory.")
    return target


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


_PUBLIC_KEYS = {"schema_version", "generated_at", "policy_version", "policy_validation",
                "mode", "actionability", "required_inputs", "system_status", "coverage",
                "evaluated", "highlights", "consider", "watch", "avoid", "excluded",
                "abstention", "errors", "ranking"}
_PUBLIC_KEYS.update({"scenario", "policy", "personalization", "implementation_version", "evaluation_definition"})
_PUBLIC_KEYS.update({"membership", "membership_context", "collection", "coverage_metrics"})
_PRIVATE_KEYS = {"budget", "loss_limit", "profile", "holdings", "portfolio", "account",
                 "credentials", "position_size", "allocation"}
_VOLATILE_KEYS = {"generated_at", "fetched_at", "retrieved_at", "captured_at", "first_seen_at", "observed_at", "report_id",
                  "evaluated_at", "snapshot_age_seconds"}


def _without(value, keys):
    if isinstance(value, dict):
        return {k: _without(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [_without(v, keys) for v in value]
    return value


def _public(report):
    return _without(deepcopy({k: v for k, v in report.items() if k in _PUBLIC_KEYS}), _PRIVATE_KEYS)


def _identity(payload):
    evidence = dict(payload)
    evidence.pop("collection", None)
    return _digest(_without(evidence, _VOLATILE_KEYS))[:32]


def _checked(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        payload = result["payload"]
        if (result["schema_version"] != 1 or result["payload_sha256"] != _digest(payload)
                or result["id"] != _identity(payload) or path.stem != result["id"]):
            raise ValueError("integrity mismatch")
        return result
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Research record integrity failure: {path.name}") from exc


def record(report: dict, directory) -> str:
    """Create once, preserving the earliest report cutoff for unchanged evidence.

    Callers exposing a path to users must apply private_path before calling this
    low-level storage function. A checksum detects accidental edits, not forgery.
    """
    directory = Path(directory)
    payload = _public(report)
    rid = _identity(payload)
    destination = directory / f"{rid}.json"
    directory.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        _checked(destination)
        return rid
    envelope = {"schema_version": 1, "id": rid, "payload_sha256": _digest(payload), "payload": payload}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix="record-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(_canonical(envelope))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Exclusive publication: never overwrite an earlier decision or expose a partial JSON file.
            os.link(temporary, destination)
        except FileExistsError:
            _checked(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return rid


def load_records(directory) -> list[dict]:
    return [_checked(path) for path in sorted(Path(directory).glob("*.json"))]


def _price_index(bars) -> dict:
    from .stocks import number
    index = {}
    for bar in bars or []:
        if not isinstance(bar, (tuple, list)) or len(bar) < 7:
            continue
        ts = number(bar[0])
        if ts is None:
            continue
        try:
            index[datetime.fromtimestamp(ts, timezone.utc).date().isoformat()] = bar
        except (ValueError, OSError, OverflowError):
            continue
    return index


def _forward(index, entry_day, exit_day):
    from .stocks import number
    first, last = index.get(entry_day), index.get(exit_day)
    if not first or not last:
        return None
    if _adjustment_unverified(index,entry_day,exit_day):
        return None
    opening, adjusted_entry, raw_entry, adjusted_exit = (
        number(first[1]), number(first[4]), number(first[6]), number(last[4]))
    if any(x is None or x <= 0 for x in (opening, adjusted_entry, raw_entry, adjusted_exit)):
        return None
    # Convert the raw opening to the same split/dividend-adjusted basis as the endpoint.
    return adjusted_exit / (opening * adjusted_entry / raw_entry) - 1


def _adjustment_unverified(index, entry_day, exit_day):
    return any(bar and (len(bar)<8 or bar[7] is not True)
               for bar in (index.get(entry_day),index.get(exit_day)))


def _scenario(card, cutoff, price_series, calendar, as_of):
    span = {"days-weeks": 21, "months-plus": 63}.get(card.get("horizon"))
    result = {"horizon_sessions": span, "reasons": []}
    if span is None:
        return {**result, "state": "unscorable", "reasons": ["horizon_unspecified_or_unsupported"]}
    if cutoff > as_of:
        return {**result, "state": "unscorable", "reasons": ["future_report_cutoff"]}
    if not calendar:
        return {**result, "state": "unscorable", "reasons": ["calendar_unavailable"]}
    try:
        entry = calendar.date_to_session(cutoff.date(), direction="next")
        if calendar.session_open(entry).to_pydatetime() <= cutoff:
            entry = calendar.next_session(entry)
        exit_session = calendar.session_offset(entry, span-1)
        result.update(entry_day=entry.date().isoformat(), exit_day=exit_session.date().isoformat())
        if calendar.session_close(exit_session).to_pydatetime() > as_of:
            return {**result, "state": "pending"}
    except (ValueError, KeyError, IndexError):
        return {**result, "state": "unscorable", "reasons": ["calendar_range_unavailable"]}
    index = _price_index(price_series.get(card["symbol"]))
    gross = _forward(index, result["entry_day"], result["exit_day"])
    if gross is None:
        reason = ("adjustment_unverified" if _adjustment_unverified(index,result["entry_day"],result["exit_day"])
                  else "entry_price_missing" if result["entry_day"] not in index else "exit_price_missing_or_invalid")
        return {**result, "state": "unscorable", "reasons": [reason]}
    result.update(gross_return=gross, net_return_10bp=gross-.001, net_return_30bp=gross-.003, cash_return=0)
    benchmark_index = _price_index(price_series.get("SPY"))
    benchmark = _forward(benchmark_index, result["entry_day"], result["exit_day"])
    if benchmark is None:
        reason = ("benchmark_adjustment_unverified" if _adjustment_unverified(benchmark_index,result["entry_day"],result["exit_day"])
                  else "benchmark_price_missing")
        return {**result, "state": "unscorable", "reasons": [reason]}
    result.update(benchmark_return=benchmark, benchmark_net_return_10bp=benchmark-.001,
                  benchmark_net_return_30bp=benchmark-.003, excess_return=gross-benchmark)
    return {**result, "state": "matured"}


def evaluate(records, price_series, calendar=None, as_of=None) -> dict:
    """Conditional next-open scenarios, never real fills or the volatility scorecard."""
    import statistics
    from .stocks import EVALUATION_DEFINITION
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError("Evaluation clock must include a timezone.")
    if calendar is None:
        try:
            from .stockdata import USCalendar
            calendar = USCalendar().calendar
        except (ImportError, ValueError):
            calendar = False
    outcomes = []
    for envelope in records:
        payload = envelope.get("payload", {})
        if envelope.get("payload_sha256") != _digest(payload) or envelope.get("id") != _identity(payload):
            raise ValueError("Research record integrity failure before evaluation.")
        cutoff = datetime.fromisoformat(payload["generated_at"])
        if cutoff.tzinfo is None:
            raise ValueError("Recorded cutoff must include a timezone.")
        cutoff = cutoff.astimezone(timezone.utc)
        for card in payload.get("evaluated", []):
            condition = card.get("entry_condition", {})
            entered = (card.get("status") == "consider" and condition.get("type") == "research_gates"
                       and condition.get("state") == "met" and card.get("horizon") in ("days-weeks", "months-plus"))
            case = _scenario(card, cutoff, price_series, calendar, as_of)
            if payload.get("evaluation_definition") != EVALUATION_DEFINITION:
                case={"state":"unscorable","reasons":["evaluation_definition_unverified"]}
            out = {"report_id": envelope["id"], "policy_version": payload.get("policy_version"),
                   "symbol": card["symbol"], "original_status": card["status"], "report_cutoff": cutoff.isoformat()}
            if entered:
                out.update(case)
            else:
                out.update(state="not_entered", reasons=["original_entry_conditions_not_met"], observation=case)
            outcomes.append(out)
    counts = {state: sum(o["state"] == state for o in outcomes)
              for state in ("matured", "pending", "unscorable", "not_entered")}
    counts.update(records=len(records), evaluated=len(outcomes))
    mature = [o for o in outcomes if o["state"] == "matured"]
    def average(key):
        return statistics.fmean(o[key] for o in mature) if mature else None
    return {"schema_version": 1, "as_of": as_of.isoformat(), "policy_validation": "unvalidated",
            "counts": counts, "outcomes": outcomes,
            "summary": {"n": len(mature), "interpretation":"pooled diagnostics across policies/horizons; not a validated strategy estimate",
                        "negative_gross_outcomes": sum(o["gross_return"] < 0 for o in mature),
                        "mean_gross_return": average("gross_return"), "mean_net_return_10bp": average("net_return_10bp"),
                        "mean_net_return_30bp": average("net_return_30bp"), "mean_excess_return": average("excess_return")},
            "assumptions": {"entry": "first exchange session opening strictly after original report cutoff; research gates met",
                            "exit": "close of the 21st or 63rd session, counting entry session as one",
                            "price_basis": "Yahoo split/dividend-adjusted return proxy; raw open normalized by same-day adjusted/raw close",
                            "round_trip_cost_bps": [10,30], "cost_method": "subtract fixed round-trip cost from gross return",
                            "cash": "zero interest", "benchmark": "SPY over identical entry/exit sessions and cost assumptions",
                            "limitations": ["not actual execution", "no FX, taxes, slippage model or market impact",
                                            "overlapping cases are not independent observations", "no validated expected-return claim",
                                            "calendar or endpoint gaps remain unscorable", "watch/avoid observations never counted as fills"]}}
