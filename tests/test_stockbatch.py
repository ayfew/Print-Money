"""Deterministic bounded collection and private resume cache regressions."""
from datetime import datetime, timedelta, timezone
import json
import threading
import time

import pytest

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def api():
    from printmoney.research import stockbatch
    return stockbatch


def observation(symbol, now=NOW):
    return {"symbol": symbol, "last": 100, "fetched_at": now.isoformat(),
            "sources": [{"url": "https://prices.example/chart", "retrieved_at": now.isoformat()}],
            "bars": [], "warnings": []}


def test_global_gate_serializes_threads_and_denial_is_host_specific():
    current = [0.0]
    starts = []
    gate = api().RequestGate(clock=lambda: current[0], sleeper=lambda s: current.__setitem__(0, current[0]+s))
    def get():
        gate.acquire("https://prices.example/chart")
        starts.append(current[0])
    threads = [threading.Thread(target=get) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(starts) == [0, 1, 2, 3, 4]
    gate.block("https://prices.example/chart", 403)
    assert gate.is_blocked("https://prices.example/other")
    assert not gate.is_blocked("https://issuer.example/")
    with pytest.raises(ValueError, match="403"):
        gate.acquire("https://prices.example/again")
    gate.acquire("https://issuer.example/")


def test_collection_is_bounded_ordered_and_retains_errors_then_resumes(tmp_path):
    active = [0, 0]
    lock = threading.Lock()
    gates, calls = [], []
    broken = {"BAD", "MAL"}
    class Provider:
        def collect(self, symbol, now):
            with lock:
                active[0] += 1
                active[1] = max(active)
                calls.append(symbol)
            try:
                time.sleep(.015)
                if symbol == "BAD" and symbol in broken:
                    raise ValueError("bad data")
                if symbol == "MAL" and symbol in broken:
                    return []
                return observation(symbol, now)
            finally:
                with lock:
                    active[0] -= 1
        def close(self):
            pass
    def factory(gate):
        gates.append(gate)
        return Provider()
    symbols = ["A", "BAD", "MAL", "B", "C", "D"]
    result = api().collect_many(symbols, factory, NOW, tmp_path, workers=2, interval=0)
    assert [o["symbol"] for o in result["observations"]] == symbols
    assert 1 < active[1] <= 2 and len({id(g) for g in gates}) == 1
    assert result["collection"]["failed"] == 2
    assert result["collection"]["completed"] == 4
    assert all(result["observations"][i]["warnings"] for i in (1, 2))
    broken.clear()
    calls.clear()
    resumed = api().collect_many(symbols, factory, NOW+timedelta(hours=1), tmp_path, interval=0)
    assert set(calls) == {"BAD", "MAL"}
    assert resumed["collection"]["cached"] == 4
    assert resumed["observations"][0]["fetched_at"] == NOW.isoformat()
    assert resumed["observations"][0]["sources"] == observation("A")["sources"]


def test_offline_corrupt_future_expired_cache_retains_every_symbol(tmp_path):
    def factory(gate):
        class Provider:
            def collect(self, symbol, now):
                return observation(symbol, now)
            def close(self):
                pass
        return Provider()
    api().collect_many(["A", "B", "C", "D"], factory, NOW, tmp_path, interval=0)
    paths = list(tmp_path.glob("*.json"))
    assert len(paths) == 4 and not list(tmp_path.glob("*.tmp"))
    for path in paths:
        data = json.loads(path.read_text())
        symbol = data["observation"]["symbol"]
        if symbol == "B":
            path.write_text("invalid json")
        elif symbol == "C":
            data["observation"]["sources"][0]["retrieved_at"] = (NOW+timedelta(days=1)).isoformat()
            path.write_text(json.dumps(data))
        elif symbol == "D":
            data["observation"]["fetched_at"] = (NOW-timedelta(hours=6)).isoformat()
            path.write_text(json.dumps(data))
    out = api().collect_many(["A", "B", "C", "D", "MISSING"], lambda g: pytest.fail("offline created provider"), NOW, tmp_path, offline=True)
    assert out["collection"]["cached"] == 1
    assert out["collection"]["attempted"] == 0
    assert out["collection"]["offline"] == 4
    assert [o["symbol"] for o in out["observations"]] == ["A", "B", "C", "D", "MISSING"]
    assert all(o.get("last") is None and o["warnings"] for o in out["observations"][1:])


def test_atomic_cache_write_failure_does_not_discard_success(tmp_path, monkeypatch):
    import os
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(os, "replace", fail)
    class Provider:
        def collect(self, symbol, now):
            return observation(symbol)
        def close(self):
            pass
    result = api().collect_many(["A"], lambda g: Provider(), NOW, tmp_path, interval=0)
    assert result["observations"][0]["last"] == 100
    assert result["observations"][0]["warnings"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("workers,interval", [(0, 1), (5, 1), (4, -1), (4, float("nan"))])
def test_unsafe_worker_or_gate_limits_are_rejected(tmp_path, workers, interval):
    with pytest.raises(ValueError):
        api().collect_many([], None, NOW, tmp_path, workers=workers, interval=interval)


def test_denial_can_open_circuit_while_another_worker_waits():
    sleeping, release, blocked = threading.Event(), threading.Event(), threading.Event()
    gate = api().RequestGate(interval=1, clock=lambda: 0, sleeper=lambda s: (sleeping.set(), release.wait(2)))
    gate.acquire('https://prices.example/first')
    errors = []
    def waiting():
        try:
            gate.acquire('https://prices.example/waiting')
        except ValueError as exc:
            errors.append(exc)
    thread = threading.Thread(target=waiting)
    thread.start()
    assert sleeping.wait(1)
    def deny():
        gate.block('https://prices.example/first', 429)
        blocked.set()
    denial = threading.Thread(target=deny)
    denial.start()
    try:
        assert blocked.wait(.2), 'denial must not wait behind pacing sleeps'
    finally:
        release.set()
        thread.join(2)
        denial.join(2)
    assert errors and not thread.is_alive()


@pytest.mark.parametrize('mutation', ['source_object', 'unsafe_source', 'bar_shape', 'nested_future'])
def test_malformed_or_future_evidence_cache_is_never_a_success(tmp_path, mutation):
    class Provider:
        def collect(self, symbol, now):
            return observation(symbol, now)
        def close(self):
            pass
    api().collect_many(['A'], lambda g: Provider(), NOW, tmp_path, interval=0)
    path = next(tmp_path.glob('*.json'))
    data = json.loads(path.read_text())
    row = data['observation']
    if mutation == 'source_object':
        row['sources'] = [None]
    elif mutation == 'unsafe_source':
        row['sources'][0]['url'] = 'javascript:alert(1)'
    elif mutation == 'bar_shape':
        row['bars'] = [[0]]
    else:
        row['fundamentals'] = {'filed': '2026-10-05'}
    path.write_text(json.dumps(data))
    out = api().collect_many(['A'], None, NOW, tmp_path, offline=True)
    assert out['collection']['cached'] == 0
    assert out['observations'][0].get('last') is None


def test_refresh_failure_preserves_previous_atomic_cache(tmp_path, monkeypatch):
    import os
    class Provider:
        def collect(self, symbol, now):
            return observation(symbol, now)
        def close(self):
            pass
    factory = lambda g: Provider()
    api().collect_many(['A'], factory, NOW, tmp_path, interval=0)
    def fail(*args):
        raise OSError('interrupted write')
    monkeypatch.setattr(os, 'replace', fail)
    api().collect_many(['A'], factory, NOW+timedelta(minutes=1), tmp_path, refresh=True, interval=0)
    offline = api().collect_many(['A'], None, NOW+timedelta(minutes=2), tmp_path, offline=True)
    assert offline['observations'][0]['fetched_at'] == NOW.isoformat()
    assert not list(tmp_path.glob('*.tmp'))


def cached_bar_observation(symbol='A'):
    row = observation(symbol)
    row['bars'] = [[int((NOW-timedelta(days=1)).timestamp()), 100, 110, 90, 100, 1000, 100, True]]
    return row


def seed_observation_cache(tmp_path, row):
    class Provider:
        def collect(self, symbol, now):
            return row
        def close(self):
            pass
    result = api().collect_many([row['symbol']], lambda g: Provider(), NOW, tmp_path, interval=0)
    assert result['collection']['completed'] == 1
    return next(tmp_path.glob('*.json'))


@pytest.mark.parametrize('field,value', [
    (0, True), (0, -1), (0, 1.5), (1, '100'), (1, True), (1, 0),
    (2, 99), (3, 101), (4, 'not-a-price'), (4, 0), (5, -1),
    (5, True), (6, None), (6, 120), (7, 'true'),
])
def test_poisoned_cached_ohlcv_and_adjustment_fields_are_rejected(tmp_path, field, value):
    path = seed_observation_cache(tmp_path, cached_bar_observation())
    blob = json.loads(path.read_text())
    blob['observation']['bars'][0][field] = value
    path.write_text(json.dumps(blob))
    out = api().collect_many(['A'], None, NOW, tmp_path, offline=True)
    assert out['collection']['cached'] == 0
    assert out['collection']['failed'] == 1
    assert out['observations'][0].get('last') is None


@pytest.mark.parametrize('key', ['published_at', 'updated_at', 'as_of', 'published_day', 'updated_day', 'as_of_day'])
def test_cached_optional_evidence_clock_cannot_be_after_cutoff(tmp_path, key):
    path = seed_observation_cache(tmp_path, cached_bar_observation())
    blob = json.loads(path.read_text())
    blob['observation']['sources'][0][key] = '2026-10-05' if key.endswith('_day') else '2026-10-05T00:00:00+00:00'
    path.write_text(json.dumps(blob))
    out = api().collect_many(['A'], None, NOW, tmp_path, offline=True)
    assert out['collection']['cached'] == 0


@pytest.mark.parametrize('key,value', [
    ('published_at', '2026-10-04T11:30:00+00:00'),
    ('updated_at', '2026-10-04T11:30:00+00:00'),
    ('as_of', '2026-10-04T11:30:00+00:00'),
    ('published_at', '2026-10-04T10:00:00'),
    ('updated_at', 'unknown'),
    ('published_day', '2026-10-04'),
])
def test_cached_optional_evidence_clock_respects_original_source_receipt(tmp_path, key, value):
    path = seed_observation_cache(tmp_path, cached_bar_observation())
    blob = json.loads(path.read_text())
    source = blob['observation']['sources'][0]
    source['retrieved_at'] = '2026-10-04T11:00:00+00:00' if not key.endswith('_day') else '2026-10-03T23:59:00+00:00'
    source[key] = value
    path.write_text(json.dumps(blob))
    out = api().collect_many(['A'], None, NOW, tmp_path, offline=True)
    assert out['collection']['cached'] == 0


def test_day_precision_unknown_publication_and_future_planned_event_stay_context(tmp_path):
    row = cached_bar_observation()
    row['bars'][0][4] = 50  # Adjusted close may be outside raw OHLC bounds.
    source = row['sources'][0]
    source.update(published_at=None, updated_at='2026-10-04', as_of=None, publication_time_precision='day')
    row['news'] = {'items': [{'published_at': None, 'updated_at': '2026-10-04T11:00:00+00:00', 'retrieved_at': NOW.isoformat(), 'verification': 'context_only'}]}
    row['earnings'] = {'date': '2026-11-01', 'verification': 'unverified'}
    seed_observation_cache(tmp_path, row)
    out = api().collect_many(['A'], None, NOW, tmp_path, offline=True)
    assert out['collection']['cached'] == 1
    assert out['observations'][0]['sources'][0]['published_at'] is None
    assert out['observations'][0]['sources'][0]['updated_at'] == '2026-10-04'
    assert out['observations'][0]['news']['items'][0]['verification'] == 'context_only'
    assert out['observations'][0]['earnings']['date'] == '2026-11-01'


def test_optional_clock_cannot_hide_in_nested_context_without_its_own_receipt(tmp_path):
    path = seed_observation_cache(tmp_path, cached_bar_observation())
    blob = json.loads(path.read_text())
    blob['observation']['news'] = {'items': [{'published_at': '2026-10-04T12:30:00+00:00'}]}
    path.write_text(json.dumps(blob))
    out = api().collect_many(['A'], None, NOW+timedelta(hours=1), tmp_path, offline=True)
    assert out['collection']['cached'] == 0


def test_supplied_membership_gate_is_reused_with_inherited_request_pacing(tmp_path):
    current, starts, received = [0.0], [], []
    gate = api().RequestGate(interval=2, clock=lambda: current[0], sleeper=lambda delay: current.__setitem__(0, current[0]+delay))
    gate.acquire('https://membership.example/index')
    starts.append(current[0])
    class Provider:
        def __init__(self, shared):
            self.gate = shared
        def collect(self, symbol, now):
            self.gate.acquire('https://prices.example/chart')
            starts.append(current[0])
            return observation(symbol, now)
        def close(self):
            pass
    def factory(shared):
        received.append(shared)
        return Provider(shared)
    result = api().collect_many(['A', 'B'], factory, NOW, tmp_path, workers=1, request_gate=gate)
    assert received == [gate]
    assert starts == [0, 2, 4]
    assert result['collection']['completed'] == 2
    assert result['collection']['request_interval_seconds'] == 2


def test_supplied_gate_preserves_existing_denial_circuit(tmp_path):
    gate = api().RequestGate(interval=0)
    gate.block('https://prices.example/denied', 403)
    class Provider:
        def collect(self, symbol, now):
            gate.acquire('https://prices.example/chart')
            pytest.fail('denied request was permitted')
        def close(self):
            pass
    out = api().collect_many(['A'], lambda shared: Provider() if shared is gate else pytest.fail('gate replaced'), NOW, tmp_path, interval=0, request_gate=gate)
    assert out['collection']['failed'] == 1
    assert out['observations'][0]['symbol'] == 'A'


@pytest.mark.parametrize('interval', [-1, float('nan'), True])
def test_supplied_gate_does_not_bypass_interval_validation(tmp_path, interval):
    gate = api().RequestGate(interval=0)
    with pytest.raises(ValueError):
        api().collect_many([], None, NOW, tmp_path, interval=interval, request_gate=gate)
