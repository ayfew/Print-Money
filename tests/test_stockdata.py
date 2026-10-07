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
    def is_session(self,stamp):
        return datetime.fromisoformat(stamp).weekday()<5 and stamp!="2026-09-07"


def chart(last_day="2026-10-02", kind="EQUITY"):
    end=datetime.fromisoformat(last_day).replace(tzinfo=timezone.utc)
    dates=[end]
    while len(dates)<22:
        end-=timedelta(days=1)
        if Calendar().is_session(end.date().isoformat()):
            dates.append(end)
    times = [int(d.timestamp()) for d in reversed(dates)]
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


def test_revenue_comparison_uses_the_current_filings_comparative_denominator():
    from copy import deepcopy
    payload=facts()
    rows=payload["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
    rows[1]["val"]=4000
    later=deepcopy(rows[1])
    later.update(val=1000,filed="2026-09-01",accn="0000789019-26-000099")
    rows.append(later)
    out=api().annual_facts(payload,NOW)
    assert out["revenue_growth"]==-.5
    assert out["revenue_comparison"]["prior_value"]==4000
    assert out["revenue_comparison"]["accession"]==out["accessions"][0]


def test_missing_comparable_denominator_stays_unknown_instead_of_mixing_versions():
    payload=facts()
    rows=payload["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
    rows[1]["accn"]="0000789019-26-000099"
    out=api().annual_facts(payload,NOW)
    assert out["revenue_growth"] is None and out["revenue_comparison"] is None


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
    assert all(len(b)==8 and b[7] is False for b in r["bars"])


def test_repeated_timestamp_is_one_session_not_a_year_of_history():
    payload=chart()
    data=payload["chart"]["result"][0]
    data["timestamp"]=[data["timestamp"][-1]]*252
    data["indicators"]["quote"]=[{k:[v[-1]]*252 for k,v in data["indicators"]["quote"][0].items()}]
    data["indicators"]["adjclose"][0]["adjclose"]=[100]*252
    out=api().parse_chart(payload,"MSFT",NOW,Calendar())
    assert out["history_days"]==1 and out["dollar_turnover"] is None


def test_conflicting_duplicate_session_is_an_explicit_quality_failure():
    payload=chart()
    data=payload["chart"]["result"][0]
    data["timestamp"][-1]=data["timestamp"][-2]
    with pytest.raises(ValueError,match="duplicate"):
        api().parse_chart(payload,"MSFT",NOW,Calendar())


def test_calendar_rejects_weekend_row_in_completed_history():
    payload=chart()
    data=payload["chart"]["result"][0]
    data["timestamp"][0]=int(datetime(2026,9,5,tzinfo=timezone.utc).timestamp())
    out=api().parse_chart(payload,"MSFT",NOW,Calendar())
    assert out["history_days"]==21
    assert "2026-09-05" not in [datetime.fromtimestamp(b[0],timezone.utc).date().isoformat() for b in out["bars"]]


@pytest.mark.parametrize("kind", ["overflow_timestamp","list_root","bad_chart","bad_result","bad_quote"])
def test_malformed_http_data_is_per_symbol_unavailable_not_an_exception(kind):
    payload=chart()
    if kind=="overflow_timestamp":
        payload["chart"]["result"][0]["timestamp"]=[1e100]*22
    elif kind=="list_root":
        payload=[]
    elif kind=="bad_chart":
        payload={"chart":[]}
    elif kind=="bad_result":
        payload={"chart":{"result":[None]}}
    elif kind=="bad_quote":
        payload["chart"]["result"][0]["indicators"]["quote"]=[None]
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=payload,request=r))) as client:
        out=api().StockProvider(client=client,calendar=Calendar()).collect("MSFT",NOW)
    assert out["symbol"]=="MSFT" and out.get("last") is None and out["warnings"]


@pytest.mark.parametrize("symbol,alias", [("BRK.B", "BRK-B"), ("BF.B", "BF-B")])
def test_class_alias_keeps_canonical_identity_and_actual_receipt_clock(symbol, alias):
    from printmoney.research.stockbatch import RequestGate
    receipt = NOW+timedelta(seconds=5)
    calls = []
    def response(request):
        calls.append(str(request.url))
        payload = chart()
        payload["chart"]["result"][0]["meta"]["symbol"] = alias
        return httpx.Response(200, json=payload, request=request)
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        out = api().StockProvider(client=client, calendar=Calendar(), request_gate=RequestGate(interval=0), sec_enabled=False, clock=lambda: receipt).collect(symbol, NOW)
    assert out["symbol"] == symbol and out["provider_symbol"] == alias
    assert alias in calls[0] and len(calls) == 1
    assert out["fetched_at"] == receipt.isoformat()
    assert out["sources"][0]["retrieved_at"] == receipt.isoformat()
    assert out["sources"][0]["url"].endswith(alias)
    assert out["provider_status"] == {"price": "available", "annual": "blocked"}


@pytest.mark.parametrize("status", [401, 403, 429])
def test_shared_provider_denial_circuit_prevents_more_actual_gets(status):
    from printmoney.research.stockbatch import RequestGate
    calls = []
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(status, request=request)
    gate = RequestGate(interval=0)
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        first = api().StockProvider(client=client, calendar=Calendar(), request_gate=gate).collect("MSFT", NOW)
        second = api().StockProvider(client=client, calendar=Calendar(), request_gate=gate).collect("AAPL", NOW)
    assert len(calls) == 1
    assert first["provider_status"]["price"] == second["provider_status"]["price"] == "blocked"
    assert first.get("last") is None and second.get("last") is None


def test_every_get_is_gated_and_sec_denial_does_not_block_price_host():
    from printmoney.research.stockbatch import RequestGate
    calls, gated = [], []
    class Gate(RequestGate):
        def acquire(self, url=None):
            super().acquire(url)
            gated.append(url)
    def response(request):
        calls.append(str(request.url))
        if "sec.gov" in str(request.url):
            return httpx.Response(403, request=request)
        payload = chart()
        payload["chart"]["result"][0]["meta"]["symbol"] = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=payload, request=request)
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        provider = api().StockProvider(client=client, calendar=Calendar(), request_gate=Gate(interval=0))
        first = provider.collect("MSFT", NOW)
        second = provider.collect("AAPL", NOW)
    assert len(calls) == len(gated) == 3
    assert first["provider_status"]["annual"] == second["provider_status"]["annual"] == "blocked"
    assert second["provider_status"]["price"] == "available"


def test_borrowed_redirect_enabled_client_cannot_bypass_request_gate():
    from printmoney.research.stockbatch import RequestGate
    calls, gated = [], []
    start, final = 'https://example.com/start', 'https://example.com/final'
    class Gate(RequestGate):
        def acquire(self, url=None):
            super().acquire(url)
            gated.append(url)
    def response(request):
        calls.append(str(request.url))
        if str(request.url) == start:
            return httpx.Response(302, headers={'Location': final}, request=request)
        return httpx.Response(200, json={'ok': 1}, request=request)
    with httpx.Client(transport=httpx.MockTransport(response), follow_redirects=True) as client:
        provider = api().StockProvider(client=client, calendar=Calendar(), request_gate=Gate(interval=0), sec_enabled=False)
        with pytest.raises(httpx.HTTPStatusError) as error:
            provider._json(start, '', NOW)
    assert error.value.response.status_code == 302
    assert calls == gated == [start]
