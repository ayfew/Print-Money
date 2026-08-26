"""Ledger, registry, settlement and risk limits: the parts that must simply be right."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from printmoney.config import Config, LIVE_CONFIRM_PHRASE
from printmoney.data.types import LegKind, Quote
from printmoney.ledger import Fill, Ledger
from printmoney.registry import MarketRegistry, TokenSpec
from printmoney.risk import RiskManager
from printmoney.settlement import settle_expired
from printmoney.backtest import synthetic_bracket_strip

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _fill(token="T1", side="YES", price=0.40, shares=100.0, fee=0.5, ts=NOW):
    return Fill(
        ts=ts,
        token_id=token,
        strip_slug="strip",
        leg_label="76,000-78,000",
        question="?",
        side=side,
        price=price,
        shares=shares,
        fee=fee,
        mode="paper",
    )


class TestLedger:
    def test_a_fill_moves_cash_and_creates_a_position(self, tmp_path):
        led = Ledger(tmp_path / "l.json", 1_000.0)
        led.record_fill(_fill(), expiry=NOW + timedelta(hours=4))
        assert led.cash == pytest.approx(1_000.0 - 40.5)
        pos = led.positions["T1"]
        assert pos.shares == 100.0
        assert pos.cost_basis == pytest.approx(40.5)
        assert pos.avg_price == pytest.approx(0.405)
        assert led.fees_paid == pytest.approx(0.5)

    def test_a_fill_it_cannot_afford_is_refused(self, tmp_path):
        led = Ledger(tmp_path / "l.json", 10.0)
        with pytest.raises(ValueError, match="cash"):
            led.record_fill(_fill())

    def test_settlement_realises_the_right_pnl(self, tmp_path):
        led = Ledger(tmp_path / "l.json", 1_000.0)
        led.record_fill(_fill())
        pnl = led.settle("T1", 1.0)
        assert pnl == pytest.approx(100.0 - 40.5)
        assert led.cash == pytest.approx(1_000.0 - 40.5 + 100.0)
        assert led.positions["T1"].settled
        # settling twice must not pay twice
        assert led.settle("T1", 1.0) == 0.0

    def test_a_worthless_expiry_loses_the_stake(self, tmp_path):
        led = Ledger(tmp_path / "l.json", 1_000.0)
        led.record_fill(_fill())
        assert led.settle("T1", 0.0) == pytest.approx(-40.5)

    def test_positions_without_a_quote_are_held_at_cost(self, tmp_path):
        led = Ledger(tmp_path / "l.json", 1_000.0)
        led.record_fill(_fill())
        assert led.mark_to_market({}) == pytest.approx(1_000.0)
        assert led.mark_to_market({"T1": 0.60}) == pytest.approx(1_000.0 - 40.5 + 60.0)

    def test_round_trip_through_disk(self, tmp_path):
        path = tmp_path / "l.json"
        led = Ledger(path, 1_000.0)
        led.record_fill(_fill(), expiry=NOW + timedelta(hours=4))
        led.record_equity(999.0, NOW)
        led.save()

        again = Ledger.load_or_new(path, 1_000.0)
        assert again.cash == pytest.approx(led.cash)
        assert len(again.fills) == 1
        assert again.positions["T1"].expiry == NOW + timedelta(hours=4)
        assert again.equity_curve[0][1] == pytest.approx(999.0)

    def test_a_corrupt_ledger_starts_over_rather_than_crashing(self, tmp_path):
        path = tmp_path / "l.json"
        path.write_text('{"cash": "not a number"}', encoding="utf-8")
        led = Ledger.load_or_new(path, 500.0)
        assert led.cash == 500.0

    def test_trade_rate_window(self, tmp_path):
        led = Ledger(tmp_path / "l.json", 1_000.0)
        led.fills = [_fill(ts=NOW - timedelta(minutes=30)), _fill(ts=NOW - timedelta(hours=3))]
        assert led.fills_last_hour(NOW) == 1


class TestRegistry:
    def test_a_recorded_leg_can_be_settled_without_the_api(self, tmp_path):
        reg = MarketRegistry(tmp_path / "m.json")
        strip = synthetic_bracket_strip(79_000.0, [78_000.0, 80_000.0], [0.3, 0.4, 0.3])
        reg.record_strip(strip)
        reg.save()

        again = MarketRegistry(tmp_path / "m.json")
        spec = again.get(strip.legs[1].yes_token)
        assert spec is not None
        assert spec.pays(79_000.0) is True
        assert spec.pays(77_000.0) is False

    def test_no_tokens_pay_the_complement(self, tmp_path):
        reg = MarketRegistry(tmp_path / "m.json")
        strip = synthetic_bracket_strip(79_000.0, [78_000.0, 80_000.0], [0.3, 0.4, 0.3])
        reg.record_strip(strip)
        spec = reg.get(strip.legs[1].no_token)
        assert spec.pays(79_000.0) is False
        assert spec.pays(77_000.0) is True

    def test_a_barrier_needs_the_path_not_just_the_close(self):
        spec = TokenSpec(
            token_id="B",
            side="YES",
            kind=LegKind.TOUCH_UP.value,
            label="up 85k",
            question="?",
            strip_slug="s",
            expiry=NOW,
            barrier=85_000.0,
        )
        assert spec.needs_path
        assert spec.pays(80_000.0) is None
        assert spec.pays(80_000.0, run_max=86_000.0, run_min=79_000.0) is True
        assert spec.pays(80_000.0, run_max=84_000.0, run_min=79_000.0) is False


class _StubFeed:
    def __init__(self, price=None, extremes=None):
        self.price = price
        self.extremes = extremes

    def settlement_close(self, when):
        return self.price

    def path_extremes(self, start, end):
        return self.extremes


class TestSettlement:
    def _ledger_with_expired_position(self, tmp_path, strip):
        led = Ledger(tmp_path / "l.json", 1_000.0)
        leg = strip.legs[1]
        led.record_fill(
            Fill(
                ts=NOW - timedelta(hours=5),
                token_id=leg.yes_token,
                strip_slug=strip.slug,
                leg_label=leg.label,
                question=leg.question,
                side="YES",
                price=0.40,
                shares=100.0,
                fee=0.0,
                mode="paper",
            ),
            expiry=NOW - timedelta(hours=1),
        )
        return led

    def test_settles_against_the_real_print(self, tmp_path):
        strip = synthetic_bracket_strip(79_000.0, [78_000.0, 80_000.0], [0.3, 0.4, 0.3])
        strip.expiry = NOW - timedelta(hours=1)
        reg = MarketRegistry(tmp_path / "m.json")
        reg.record_strip(strip)
        led = self._ledger_with_expired_position(tmp_path, strip)

        report = settle_expired(led, reg, _StubFeed(price=79_100.0), now=NOW)
        assert report.settled == 1
        assert report.realized_pnl == pytest.approx(60.0)

    def test_defers_when_the_print_is_not_out_yet(self, tmp_path):
        strip = synthetic_bracket_strip(79_000.0, [78_000.0, 80_000.0], [0.3, 0.4, 0.3])
        strip.expiry = NOW - timedelta(hours=1)
        reg = MarketRegistry(tmp_path / "m.json")
        reg.record_strip(strip)
        led = self._ledger_with_expired_position(tmp_path, strip)

        report = settle_expired(led, reg, _StubFeed(price=None), now=NOW)
        assert report.settled == 0
        assert report.deferred == 1
        assert led.open_positions()  # still open, not guessed at

    def test_an_unknown_token_is_left_alone(self, tmp_path):
        strip = synthetic_bracket_strip(79_000.0, [78_000.0, 80_000.0], [0.3, 0.4, 0.3])
        strip.expiry = NOW - timedelta(hours=1)
        reg = MarketRegistry(tmp_path / "m.json")  # deliberately empty
        led = self._ledger_with_expired_position(tmp_path, strip)

        report = settle_expired(led, reg, _StubFeed(price=79_100.0), now=NOW)
        assert report.unknown == 1
        assert report.settled == 0


class TestRisk:
    def _mgr(self, tmp_path, **risk):
        cfg = Config()
        for k, v in risk.items():
            setattr(cfg.risk, k, v)
        return RiskManager(cfg, state_path=str(tmp_path / "risk.json"))

    def test_daily_loss_halts_trading(self, tmp_path):
        mgr = self._mgr(tmp_path, max_daily_loss=0.10)
        mgr.start_cycle(1_000.0, NOW)
        assert mgr.check_kill_switches(950.0)
        assert not mgr.check_kill_switches(880.0)
        assert mgr.state.halted

    def test_drawdown_halts_trading(self, tmp_path):
        mgr = self._mgr(tmp_path, max_drawdown=0.20, max_daily_loss=0.99)
        mgr.start_cycle(1_000.0, NOW)
        mgr.start_cycle(1_200.0, NOW)
        assert not mgr.check_kill_switches(900.0)
        assert "drawdown" in mgr.state.halt_reason

    def test_a_halt_can_be_cleared_by_an_operator(self, tmp_path):
        mgr = self._mgr(tmp_path, max_daily_loss=0.01)
        mgr.start_cycle(1_000.0, NOW)
        mgr.check_kill_switches(500.0)
        assert mgr.state.halted

        mgr.resume()
        assert not mgr.state.halted
        # Resuming clears the flag, it does not repeal the limit: the same loss
        # trips it again on the next look.
        assert mgr.check_kill_switches(999.0)
        assert not mgr.check_kill_switches(500.0)

    def test_stale_spot_blocks_trading(self, tmp_path):
        mgr = self._mgr(tmp_path, stale_spot_seconds=10)
        old = Quote(price=79_000.0, source="binance", timestamp=NOW - timedelta(minutes=5))
        verdict = mgr.check_data_freshness(old, [])
        assert not verdict
        assert "spot quote" in verdict.why


class TestConfigGates:
    def test_unknown_keys_are_rejected(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("risk:\n  capital_usd: 100\n  wishful_thinking: true\n", encoding="utf-8")
        with pytest.raises(ValueError, match="unknown config keys"):
            Config.load(path)

    def test_live_needs_every_gate(self, monkeypatch):
        cfg = Config()
        assert cfg.live_armed() == (False, "execution.mode is not 'live'")

        cfg.execution.mode = "live"
        cfg.live.enabled = True
        ok, why = cfg.live_armed()
        assert not ok and "confirm_phrase" in why

        cfg.live.confirm_phrase = LIVE_CONFIRM_PHRASE
        monkeypatch.delenv(cfg.live.private_key_env, raising=False)
        ok, why = cfg.live_armed()
        assert not ok and cfg.live.private_key_env in why

        monkeypatch.setenv(cfg.live.private_key_env, "0xdeadbeef")
        assert cfg.live_armed()[0]

    def test_live_mode_without_enabling_is_a_config_error(self):
        cfg = Config()
        cfg.execution.mode = "live"
        with pytest.raises(ValueError, match="live.enabled"):
            cfg.validate()

    def test_nonsense_settings_are_caught(self):
        cfg = Config()
        cfg.strategy.max_loss_fraction = 0.0
        with pytest.raises(ValueError):
            cfg.validate()
