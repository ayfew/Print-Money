"""Parsing market questions into payoff geometry, and order book normalisation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from printmoney.data.types import (
    INF,
    BookLevel,
    LegKind,
    OrderBook,
    StripKind,
    classify_leg,
    infer_strip_kind,
    parse_prices,
)


class TestLegClassification:
    def test_between_becomes_a_range(self):
        kind, geo = classify_leg(
            "Will the price of Bitcoin be between $56,000 and $58,000 on August 25?",
            "56,000-58,000",
        )
        assert kind is LegKind.RANGE
        assert geo == {"lo": 56_000.0, "hi": 58_000.0}

    def test_less_than_is_open_below(self):
        kind, geo = classify_leg(
            "Will the price of Bitcoin be less than $56,000 on August 25?", "<56,000"
        )
        assert kind is LegKind.RANGE
        assert geo["lo"] == -INF and geo["hi"] == 56_000.0

    def test_greater_than_is_a_digital(self):
        kind, geo = classify_leg(
            "Will the price of Bitcoin be greater than $74,000 on August 25?", ">74,000"
        )
        assert kind is LegKind.ABOVE
        assert geo["strike"] == 74_000.0

    def test_above_ladder_leg(self):
        kind, geo = classify_leg(
            "Will the price of Bitcoin be above $80,000 on August 26?", "80,000"
        )
        assert kind is LegKind.ABOVE
        assert geo["strike"] == 80_000.0

    def test_reach_is_an_up_barrier(self):
        kind, geo = classify_leg("Will Bitcoin reach $88,000 on August 25?", "↑ 88,000")
        assert kind is LegKind.TOUCH_UP
        assert geo["barrier"] == 88_000.0

    def test_dip_is_a_down_barrier(self):
        kind, geo = classify_leg("Will Bitcoin dip to $73,000 on August 25?", "↓ 73,000")
        assert kind is LegKind.TOUCH_DOWN
        assert geo["barrier"] == 73_000.0

    def test_day_of_month_is_not_a_price(self):
        """"on August 25" must not be mistaken for a $25 strike."""
        assert parse_prices("Will the price of Bitcoin be above $54,000 on August 25?") == [54_000.0]

    def test_unparseable_question_raises(self):
        with pytest.raises(ValueError):
            classify_leg("Who will win the election?", "")


def test_infer_strip_kind():
    assert infer_strip_kind("bitcoin-price-on-august-25-2026", "Bitcoin price on August 25?") is StripKind.BRACKET
    assert infer_strip_kind("bitcoin-above-on-august-25-2026", "Bitcoin above ___?") is StripKind.ABOVE
    assert infer_strip_kind("what-price-will-bitcoin-hit-on-august-25-2026", "hit?") is StripKind.TOUCH


class TestOrderBook:
    def test_clob_levels_are_sorted_best_first(self):
        """The CLOB returns bids ascending and asks descending; we flip both."""
        book = OrderBook.from_clob(
            {
                "asset_id": "tok",
                "bids": [{"price": "0.40", "size": "10"}, {"price": "0.45", "size": "5"}],
                "asks": [{"price": "0.60", "size": "10"}, {"price": "0.50", "size": "5"}],
            }
        )
        assert book.best_bid == 0.45
        assert book.best_ask == 0.50
        assert book.mid == pytest.approx(0.475)
        assert book.spread == pytest.approx(0.05)

    def test_junk_levels_are_dropped(self):
        book = OrderBook.from_clob(
            {
                "asset_id": "tok",
                "bids": [{"price": "0", "size": "10"}, {"price": "0.3", "size": "0"}],
                "asks": [{"price": "1.0", "size": "10"}, {"price": "abc", "size": "5"}],
            }
        )
        assert book.is_empty

    def test_freshness_is_about_when_we_read_it(self):
        """A quiet market is not stale data."""
        now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
        book = OrderBook(
            token_id="t",
            bids=[BookLevel(0.4, 10)],
            timestamp=now - timedelta(hours=2),
            fetched_at=now - timedelta(seconds=3),
        )
        assert book.age_seconds(now) == pytest.approx(3.0)
        assert book.quote_age_seconds(now) == pytest.approx(7200.0)
