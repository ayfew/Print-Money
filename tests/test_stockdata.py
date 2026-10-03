"""Real parsers against daily-chart and SEC-shaped payloads; network only is doubled."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def api():
    try:
        from printmoney.research import stockdata
    except ImportError:
        pytest.fail("real stock data collectors are missing")
    return stockdata


class Calendar:
    def last_completed(self, now):
        return "2026-10-02"


def chart(last_day="2026-10-02", kind="EQUITY"):
    start = datetime.fromisoformat(last_day).replace(tzinfo=timezone.utc) - timedelta(days=21)
    times = [int((start + timedelta(days=i)).timestamp()) for i in range(22)]
    return {"chart": {"result": [{"meta": {"symbol": "MSFT", "currency": "USD", "exchangeName": "NMS",
        "instrumentType": kind, "longName": "Microsoft", "exchangeTimezoneName": "America/New_York"},
        "timestamp": times, "indicators": {"quote": [{"open": [100]*22, "high": [101]*22,
        "low": [99]*22, "close": [100]*22, "volume": [100]*21 + [500]}],
        "adjclose": [{"adjclose": [100]*22}]}, "events": {}}]}}


def facts(filed="2026-08-01", form="10-K", unit="USD"):
    def row(value, start="2025-07-01", end="2026-06-30"):
        return {"start": start, "end": end, "val": value, "filed": filed, "form": form,
                "accn": "0000789019-26-000001", "fy": 2026, "fp": "FY"}
    gaap = {}
    for tag, value in [("NetIncomeLoss", 1000), ("NetCashProvidedByUsedInOperatingActivities", 1200),
                       ("RevenueFromContractWithCustomerExcludingAssessedTax", 2000)]:
        gaap[tag] = {"units": {unit: [row(value)]}}
    gaap["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"][unit].append(row(1000,"2024-07-01","2025-06-30"))
    gaap["EarningsPerShareDiluted"] = {"units": {"USD/shares": [row(5)]}}
    return {"cik": 789019, "entityName": "Microsoft", "facts": {"us-gaap": gaap}}


def test_actual_parser_retains_raw_quote_source_dates_and_volume_context():
    out = api().parse_chart(chart(), "MSFT", NOW, Calendar())
    assert out["last"] == 100 and out["quote_day"] == "2026-10-02"
    assert out["volume_ratio"] == 5
    assert out["sources"][0]["kind"] == "aggregator"
    assert out["sources"][0]["origin_url"] is None


def test_incomplete_future_daily_bar_is_excluded():
    out = api().parse_chart(chart("2026-10-03"), "MSFT", NOW, Calendar())
    assert out["quote_day"] == "2026-10-02" and out["history_days"] == 21


def test_delayed_price_does_not_acquire_today_as_its_observation_date():
    out = api().parse_chart(chart("2026-09-30"), "MSFT", NOW, Calendar())
    assert out["quote_day"] == "2026-09-30"
    assert out["last_completed_session"] == "2026-10-02"


def test_recent_split_disables_raw_volume_anomaly_comparison():
    payload = chart()
    payload["chart"]["result"][0]["events"] = {"splits": {"123": {"date": payload["chart"]["result"][0]["timestamp"][-2]}}}
    out = api().parse_chart(payload, "MSFT", NOW, Calendar())
    assert out["recent_split"] is True and out["volume_ratio"] is None


def test_reported_annual_metrics_have_independent_arithmetic_and_filing_identity():
    out = api().annual_facts(facts(), NOW)
    assert out["net_income"] == 1000 and out["operating_cashflow"] == 1200
    assert out["diluted_eps"] == 5 and out["revenue_growth"] == 1
    assert out["annual_end"] == "2026-06-30" and out["filed"] == "2026-08-01"
    assert out["accessions"] == ["0000789019-26-000001"]


@pytest.mark.parametrize("change", [{"filed":"2026-10-04"}, {"filed":"2026-10-03"}, {"form":"10-Q"}, {"unit":"EUR"}])
def test_future_interim_or_wrong_currency_facts_never_become_annual_earnings(change):
    out = api().annual_facts(facts(**change), NOW)
    assert out.get("net_income") is None


def test_http_failure_is_real_unavailable_data_not_a_zero_price():
    transport = httpx.MockTransport(lambda request: httpx.Response(403, request=request))
    with httpx.Client(transport=transport) as client:
        out = api().StockProvider(client=client, calendar=Calendar()).collect("MSFT", NOW)
    assert out.get("last") is None
    assert out["warnings"] and out["symbol"] == "MSFT"


def test_actual_http_collection_rejects_a_non_equity_symbol_before_company_inference():
    def response(request):
        return httpx.Response(200, json=chart(kind="ETF"), request=request)
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        out = api().StockProvider(client=client, calendar=Calendar()).collect("MSFT", NOW)
    assert out["instrument_type"] == "ETF" and not out.get("fundamentals")


@pytest.mark.parametrize("stamp,want", [
    ("2026-07-03T15:00:00+00:00", "2026-07-02"),
    ("2026-10-05T15:00:00+00:00", "2026-10-02"),
    ("2026-11-27T17:59:00+00:00", "2026-11-25"),
    ("2026-11-27T18:05:00+00:00", "2026-11-27"),
])
def test_price_calendar_respects_holidays_open_sessions_and_early_close(stamp, want):
    assert api().USCalendar().last_completed(datetime.fromisoformat(stamp)) == want
def test_null_adjusted_closes_never_claim_corporate_action_verification():
    # A same-length vector of nulls is not adjusted price evidence.
    from printmoney.research.stockdata import parse_chart
    payload = chart()
    data = payload["chart"]["result"][0]
    data["indicators"]["adjclose"][0]["adjclose"] = [None]*len(data["timestamp"])
    r = parse_chart(payload, "MSFT", NOW, Calendar())
    assert r["price_basis"] == "raw_unverified_actions" and not r["anomalous_price"]
