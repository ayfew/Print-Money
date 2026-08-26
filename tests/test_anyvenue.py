"""The carry scan when Binance will not answer.

GitHub's hosted runners sit on US datacentre addresses and Binance returns 451
to those, so the scheduled brief was publishing with its funding section missing
- green run, exit zero, one fewer section, no warning. These tests hold the
fallback to the same standard as the path it replaces: it may only rank things
that can actually be hedged, it must judge on history rather than one print, and
it must never claim a venue answered when none did.

No network. ccxt is stubbed, so a failure here is the wiring rather than an
exchange having a bad morning.
"""
from __future__ import annotations

import sys
import types

import pytest

from printmoney.carry import anyvenue as A
from printmoney.carry.scanner import Carry, CarryReport


# --------------------------------------------------------------------------- #
class _Exchange:
    """One fake venue. ``fail`` makes it behave like a blocked or broken one."""

    def __init__(self, *, rates=None, tickers=None, spot=None, history=None,
                 fail=None, has_funding=True):
        self.has = {"fetchFundingRates": has_funding}
        self._rates = rates or {}
        self._tickers = tickers or {}
        self._spot = spot or {}
        self._history = history or {}
        self._fail = fail

    def fetch_funding_rates(self):
        if self._fail:
            raise RuntimeError(self._fail)
        return self._rates

    def fetch_tickers(self):
        return self._tickers

    def load_markets(self):
        return self._spot

    def fetch_funding_rate_history(self, symbol, limit=60):
        return self._history.get(symbol, [])


def _rates(**kw):
    return {f"{b}/USDT:USDT": {"fundingRate": r, "markPrice": 100.0}
            for b, r in kw.items()}


def _tickers(volume=50_000_000.0, **kw):
    return {f"{b}/USDT:USDT": {"quoteVolume": kw.get(b, volume), "last": 100.0}
            for b in (kw or {"BTC": 0}) if True}


def _spot(*bases):
    return {f"{b}/USDT": {} for b in bases}


def _hist(base, rate, n=60):
    return {f"{base}/USDT:USDT": [{"fundingRate": rate} for _ in range(n)]}


def _install(monkeypatch, venues: dict[str, _Exchange]):
    """A ccxt module with exactly the venues this test cares about."""
    mod = types.ModuleType("ccxt")

    class NetworkError(Exception):
        pass

    mod.NetworkError = NetworkError
    for name, ex in venues.items():
        setattr(mod, name, lambda cfg=None, _ex=ex: _ex)
    monkeypatch.setitem(sys.modules, "ccxt", mod)
    monkeypatch.setattr(A, "available", lambda: True)
    return mod


# --------------------------------------------------------------------------- #
class TestFallingBackToAWorkingVenue:
    def test_it_uses_the_first_venue_that_answers(self, monkeypatch):
        good = _Exchange(rates=_rates(BTC=0.0002), tickers=_tickers(BTC=9e7),
                         spot=_spot("BTC"), history=_hist("BTC", 0.0002))
        _install(monkeypatch, {"binance": _Exchange(fail="451 restricted"),
                               "bybit": good})
        rep = A.scan(venues=("binance", "bybit"))
        assert rep.venue == "bybit"
        assert rep.candidates and rep.candidates[0].symbol == "BTC"

    def test_binance_is_preferred_when_it_does_answer(self, monkeypatch):
        both = dict(rates=_rates(BTC=0.0002), tickers=_tickers(BTC=9e7),
                    spot=_spot("BTC"), history=_hist("BTC", 0.0002))
        _install(monkeypatch, {"binance": _Exchange(**both),
                               "bybit": _Exchange(**both)})
        assert A.scan(venues=("binance", "bybit")).venue == "binance"

    def test_a_venue_without_the_funding_endpoint_is_skipped(self, monkeypatch):
        _install(monkeypatch, {
            "kucoinfutures": _Exchange(has_funding=False),
            "gate": _Exchange(rates=_rates(ETH=0.0003), tickers=_tickers(ETH=8e7),
                              spot=_spot("ETH"), history=_hist("ETH", 0.0003))})
        assert A.scan(venues=("kucoinfutures", "gate")).venue == "gate"

    def test_when_nothing_answers_it_raises_rather_than_returning_empty(
            self, monkeypatch):
        _install(monkeypatch, {"binance": _Exchange(fail="451"),
                               "bybit": _Exchange(fail="timeout")})
        with pytest.raises(RuntimeError, match="no exchange answered"):
            A.scan(venues=("binance", "bybit"))

    def test_a_venue_that_answers_with_nothing_tradable_is_not_used(
            self, monkeypatch):
        _install(monkeypatch, {
            "bybit": _Exchange(rates=_rates(SCAM=0.01),
                               tickers=_tickers(SCAM=1000.0), spot={}),
            "gate": _Exchange(rates=_rates(ETH=0.0003), tickers=_tickers(ETH=8e7),
                              spot=_spot("ETH"), history=_hist("ETH", 0.0003))})
        assert A.scan(venues=("bybit", "gate")).venue == "gate"

    def test_without_ccxt_it_says_so_instead_of_failing_obscurely(self, monkeypatch):
        monkeypatch.setattr(A, "available", lambda: False)
        with pytest.raises(RuntimeError, match="ccxt is not installed"):
            A.scan()


class TestItKeepsTheOriginalDiscipline:
    def _scan(self, monkeypatch, **kw):
        _install(monkeypatch, {"bybit": _Exchange(**kw)})
        return A.scan(venues=("bybit",))

    def test_a_perp_with_no_spot_market_is_never_a_candidate(self, monkeypatch):
        """No spot leg means no hedge - that is a naked short, not carry."""
        with pytest.raises(RuntimeError):
            self._scan(monkeypatch, rates=_rates(GHOST=0.01),
                       tickers=_tickers(GHOST=9e7), spot=_spot("BTC"))

    def test_a_thin_market_is_never_a_candidate(self, monkeypatch):
        with pytest.raises(RuntimeError):
            self._scan(monkeypatch, rates=_rates(BTC=0.01),
                       tickers=_tickers(BTC=1_000.0), spot=_spot("BTC"))

    def test_a_perp_that_charges_rather_than_pays_is_excluded(self, monkeypatch):
        with pytest.raises(RuntimeError):
            self._scan(monkeypatch, rates=_rates(BTC=-0.0005),
                       tickers=_tickers(BTC=9e7), spot=_spot("BTC"))

    def test_inverse_contracts_are_excluded(self, monkeypatch):
        """A coin-margined contract pays differently and cannot be compared."""
        _install(monkeypatch, {"bybit": _Exchange(
            rates={"BTC/USD:BTC": {"fundingRate": 0.01, "markPrice": 100.0}},
            tickers={"BTC/USD:BTC": {"quoteVolume": 9e7, "last": 100.0}},
            spot=_spot("BTC"))})
        with pytest.raises(RuntimeError):
            A.scan(venues=("bybit",))

    def test_ranking_uses_history_not_the_current_print(self, monkeypatch):
        """A spike annualised has embarrassed this project once already."""
        rates = _rates(SPIKE=0.01, STEADY=0.0004)
        tickers = {**_tickers(SPIKE=9e7), **_tickers(STEADY=9e7)}
        history = {**_hist("SPIKE", 0.00001), **_hist("STEADY", 0.0004)}
        _install(monkeypatch, {"bybit": _Exchange(
            rates=rates, tickers=tickers, spot=_spot("SPIKE", "STEADY"),
            history=history)})
        rep = A.scan(venues=("bybit",))
        assert rep.candidates[0].symbol == "STEADY"

    def test_a_missing_history_does_not_drop_the_candidate(self, monkeypatch):
        rep = self._scan(monkeypatch, rates=_rates(BTC=0.0002),
                         tickers=_tickers(BTC=9e7), spot=_spot("BTC"),
                         history={})
        assert rep.candidates and rep.candidates[0].history == []


class TestTheReportShapeIsUnchanged:
    def test_it_returns_the_same_type_the_brief_already_reads(self, monkeypatch):
        _install(monkeypatch, {"bybit": _Exchange(
            rates=_rates(BTC=0.0002), tickers=_tickers(BTC=9e7),
            spot=_spot("BTC"), history=_hist("BTC", 0.0002))})
        rep = A.scan(capital=1000.0, venues=("bybit",))
        assert isinstance(rep, CarryReport)
        d = rep.to_dict()
        for key in ("venue", "scanned", "hedgeable", "basket_net_annual",
                    "monthly_usd", "capital", "candidates"):
            assert key in d

    def test_the_venue_is_recorded_so_a_reader_knows_where_it_came_from(
            self, monkeypatch):
        _install(monkeypatch, {"binance": _Exchange(fail="451"),
                               "gate": _Exchange(
                                   rates=_rates(ETH=0.0003),
                                   tickers=_tickers(ETH=8e7),
                                   spot=_spot("ETH"),
                                   history=_hist("ETH", 0.0003))})
        assert A.scan(venues=("binance", "gate")).to_dict()["venue"] == "gate"

    def test_the_default_venue_on_a_plain_report_is_binance(self):
        assert CarryReport().to_dict()["venue"] == "binance"

    def test_scanned_and_hedgeable_are_counted_honestly(self, monkeypatch):
        rates = {**_rates(BTC=0.0002), **_rates(GHOST=0.0002)}
        tickers = {**_tickers(BTC=9e7), **_tickers(GHOST=9e7)}
        _install(monkeypatch, {"bybit": _Exchange(
            rates=rates, tickers=tickers, spot=_spot("BTC"),
            history=_hist("BTC", 0.0002))})
        rep = A.scan(venues=("bybit",))
        assert rep.scanned == 2 and rep.hedgeable == 1
        assert [c.symbol for c in rep.candidates] == ["BTC"]


class TestTheBriefFallsBackRatherThanLosingTheSection:
    def test_a_blocked_binance_still_produces_a_carry_section(self, monkeypatch):
        from printmoney.research import brief as B

        def boom(**kw):
            raise RuntimeError("451 restricted location")

        good = CarryReport(capital=1000.0)
        good.venue = "bybit"
        good.candidates = [Carry(symbol="BTC", funding_now=0.0002, mark=100.0,
                                 hedgeable=True, perp_volume_24h=9e7,
                                 history=[0.0002] * 60)]
        monkeypatch.setattr("printmoney.carry.scanner.scan", boom)
        monkeypatch.setattr("printmoney.carry.anyvenue.scan",
                            lambda **kw: good)
        monkeypatch.setattr("printmoney.carry.anyvenue.available", lambda: True)

        brief = B.build_brief(universe=(("SPY", "S&P 500"),), cache_hours=999999)
        if brief.ok:                       # skip if the bars could not be read
            assert brief.carry and not brief.carry.get("error")
            assert brief.carry["venue"] == "bybit"
