"""Generated minimal issuer-shaped release fixtures; no live HTTP or article copies."""
from datetime import datetime, timedelta, timezone
import importlib.util

import httpx
import pytest

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
RECEIPT = NOW - timedelta(hours=1)
URLS = {
    "MSFT": "https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast",
    "NVDA": "https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027",
    "AAPL": "https://www.apple.com/newsroom/2026/07/apple-reports-third-quarter-results/",
    "AMZN": "https://www.aboutamazon.com/news/company-news/amazon-earnings-q2-2026-report",
}


def api():
    assert importlib.util.find_spec("printmoney.research.stockfinancials") is not None, "financial release seam is missing"
    from printmoney.research import stockfinancials
    return stockfinancials


def table(caption, rows):
    return "<table><caption>" + caption + "</caption>" + "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
    ) + "</table>"


def msft():
    return """<html><h1>Earnings Release FY26 Q4</h1><p>July 29, 2026</p>
    <p>Microsoft Corp. today announced results for the quarter ended June 30, 2026.</p>
    <p>Financial results reported in accordance with generally accepted accounting principles (GAAP).</p>""" + table(
        "INCOME STATEMENTS (In millions, except per share amounts) (Unaudited)",
        [["", "Three Months Ended June 30,", "Twelve Months Ended June 30,"],
         ["", "2026", "2025", "2026", "2025"],
         ["Total revenue", "$12,345", "$9,876", "$45,678", "$40,000"],
         ["Net income", "$2,345", "$2,000", "$8,901", "$8,000"],
         ["Earnings per share:", "", "", "", ""],
         ["Diluted", "$1.23", "$1.01", "$4.56", "$4.00"]],
    ) + table("CASH FLOWS STATEMENTS (In millions) (Unaudited)",
        [["", "Three Months Ended June 30,", "Twelve Months Ended June 30,"],
         ["", "2026", "2025", "2026", "2025"],
         ["Net cash from operations", "3,456", "3,000", "12,345", "10,000"]]) + "</html>"


def nvda():
    return """<html><h1>NVIDIA Announces Financial Results for Second Quarter Fiscal 2027</h1>
    <p>August 26, 2026</p><p>NVIDIA reported revenue for the second quarter ended July 26, 2026.</p>
    <h2>Q2 Fiscal 2027 Summary</h2><h3>GAAP</h3>""" + table(
        "($ in millions, except earnings per share)",
        [["", "Q2 FY27", "Q1 FY27", "Q2 FY26", "Q/Q", "Y/Y"],
         ["Revenue", "$12,345", "$10,000", "$8,000", "23%", "54%"],
         ["Net income", "$4,567", "$3,000", "$2,000", "52%", "128%"],
         ["Diluted earnings per share", "$1.23", "$0.98", "$0.76", "25%", "61%"]],
    ) + "<h3>Non-GAAP</h3>" + table("($ in millions, except earnings per share)",
        [["", "Q2 FY27", "Q1 FY27", "Q2 FY26", "Q/Q", "Y/Y"],
         ["Revenue", "$12,345", "$10,000", "$8,000", "23%", "54%"],
         ["Net income", "$4,321", "$2,999", "$1,999", "44%", "116%"],
         ["Diluted earnings per share", "$1.16", "$0.97", "$0.75", "19%", "54%"]]) + "</html>"


def apple():
    return """<html><p>PRESS RELEASE July 30, 2026</p><h1>Apple reports third quarter results</h1>
    <p>Apple today announced financial results for its fiscal 2026 third quarter ended June 27, 2026.
    The Company posted quarterly revenue of $12.3 billion, up 10 percent year over year.
    Diluted earnings per share was $1.23, up 20 percent year over year.</p>
    <p>Operating cash flow reached a new record.</p></html>"""


def amazon():
    return """<html><h1>Amazon.com announces second quarter results</h1>
    <p>Amazon.com, Inc. today announced financial results for its second quarter ended June 30, 2026.</p>
    <ul><li>Net sales increased 20% to $12.3 billion in the second quarter, compared with $10.0 billion in second quarter 2025.</li>
    <li>Net income increased to $2.3 billion in the second quarter, or $1.23 per diluted share, compared with $1.8 billion, or $1.00 per diluted share, in second quarter 2025.</li>
    <li>Operating cash flow increased 33% to $45.6 billion for the trailing twelve months.</li></ul>
    <h2>Financial Guidance</h2><p>The following forward-looking statements reflect expectations as of July 30, 2026.</p>
    <p>Net sales are expected to be between $99.0 billion and $100.0 billion.</p></html>"""


def parse(symbol, html=None, **kwargs):
    payload = html if html is not None else {"MSFT": msft, "NVDA": nvda, "AAPL": apple, "AMZN": amazon}[symbol]()
    return api().parse_release(payload, symbol, URLS[symbol], NOW, retrieved_at=RECEIPT, **kwargs)


def test_msft_actual_annual_and_quarter_columns_currency_and_cashflow():
    result = parse("MSFT")
    assert result["status"] == "available"
    quarter, annual = result["periods"]
    assert quarter["kind"] == "quarter" and annual["kind"] == "annual"
    assert quarter["start"] is None and annual["start"] is None
    assert quarter["end"] == annual["end"] == "2026-06-30"
    assert quarter["currency"] == annual["currency"] == "USD"
    assert quarter["basis"] == annual["basis"] == "GAAP"
    assert quarter["metrics"]["revenue"]["value"] == 12_345_000_000
    assert annual["metrics"]["revenue"]["value"] == 45_678_000_000
    assert annual["metrics"]["operating_cashflow"]["value"] == 12_345_000_000
    assert quarter["metrics"]["diluted_eps"]["unit"] == "USD/share"
    assert annual["metrics"]["diluted_eps"]["value"] == 4.56
    assert result["sources"][0]["retrieved_at"] == RECEIPT.isoformat()
    assert result["sources"][0]["published_day"] == "2026-07-29"
    assert result["sources"][0]["publication_time_precision"] == "day"
    assert "published_at" not in result["sources"][0]
    assert result["sources"][0]["type"] == "issuer_financial_release"
    assert all(metric["locator"] for p in result["periods"] for metric in p["metrics"].values())


def test_nvda_gaap_non_gaap_remain_separate_without_calendar_start_guess():
    result = parse("NVDA")
    gaap, adjusted = result["periods"]
    assert (gaap["basis"], adjusted["basis"]) == ("GAAP", "non-GAAP")
    assert gaap["end"] == adjusted["end"] == "2026-07-26"
    assert gaap["start"] is None
    assert gaap["metrics"]["net_income"]["value"] == 4_567_000_000
    assert adjusted["metrics"]["net_income"]["value"] == 4_321_000_000
    assert gaap["metrics"]["diluted_eps"]["value"] == 1.23
    assert adjusted["metrics"]["diluted_eps"]["basis"] == "non-GAAP"
    assert all(p["kind"] == "quarter" for p in result["periods"])


@pytest.mark.parametrize("symbol,end,fields", [("AAPL", "2026-06-27", {"revenue", "diluted_eps"}), ("AMZN", "2026-06-30", {"revenue", "net_income", "diluted_eps"})])
def test_headline_facts_keep_unspecified_basis_and_do_not_invent_missing_metrics(symbol, end, fields):
    result = parse(symbol)
    assert result["status"] == "available"
    period, = result["periods"]
    assert period["kind"] == "quarter" and period["end"] == end
    assert period["basis"] == "reported_unspecified"
    assert set(period["metrics"]) == fields
    assert period["metrics"]["revenue"]["value"] == 12_300_000_000
    assert period["metrics"]["diluted_eps"]["value"] == 1.23
    assert any("basis" in w.lower() for w in result["warnings"])


@pytest.mark.parametrize("payload", ["<html><h1>Loading</h1><script>revenue=123</script></html>", "", "<html><p>quarter ended June 30, 2026</p>", None, 123, b"\xff", "x" * (2_000_000 + 1)], ids=["shell", "empty", "fragment", "none", "number", "invalid_bytes", "oversized"])
def test_dynamic_malformed_and_oversized_payloads_do_not_become_financial_evidence(payload):
    result = api().parse_release(payload, "MSFT", URLS["MSFT"], NOW, RECEIPT)
    assert result["status"] == "missing" and result["periods"] == []
    assert result["warnings"]


@pytest.mark.parametrize("url", ["https://example.com/release", "http://www.apple.com/newsroom/2026/07/apple-reports-third-quarter-results/", "https://user:password@www.apple.com/newsroom/2026/07/apple-reports-third-quarter-results/", URLS["AAPL"] + "?redirect=evil"])
def test_unregistered_or_unsafe_source_cannot_enter_results(url):
    result = api().parse_release(apple(), "AAPL", url, NOW, RECEIPT)
    assert result["status"] == "missing" and not result["periods"]


@pytest.mark.parametrize("html,now,receipt", [(apple(), datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 7, 1, tzinfo=timezone.utc)), (apple().replace("June 27, 2026", "June 27, 2027"), NOW, RECEIPT), (apple(), NOW, NOW + timedelta(seconds=1)), (apple(), NOW.replace(tzinfo=None), RECEIPT)])
def test_future_period_publication_receipt_and_naive_cutoff_rejected(html, now, receipt):
    result = api().parse_release(html, "AAPL", URLS["AAPL"], now, receipt)
    assert result["status"] == "missing" and not result["periods"]


def test_missing_scale_or_current_column_does_not_use_prior_or_percent_values():
    result = parse("NVDA", nvda().replace("in millions", "in unknown units"))
    assert result["status"] == "missing" and not result["periods"]
    result = parse("MSFT", msft().replace("$12,345", "—"))
    assert "revenue" not in result["periods"][0]["metrics"]


def test_provider_unknown_issuer_zero_requests_and_bounded_denial_block():
    requested = []
    client = httpx.Client(transport=httpx.MockTransport(lambda req: requested.append(str(req.url)) or httpx.Response(403)))
    class Gate:
        def __init__(self):
            self.blocked = False
            self.acquisitions = []
        def acquire(self, url=None):
            self.acquisitions.append(url)
        def block(self, url, status):
            assert status == 403
            self.blocked = True
        def is_blocked(self, url):
            return self.blocked
    gate = Gate()
    provider = api().FinancialReleaseProvider(client, gate, clock=lambda: RECEIPT)
    assert provider.collect("UNKNOWN", NOW)["status"] == "missing"
    assert requested == []
    assert provider.collect("AAPL", NOW)["status"] == "blocked"
    assert provider.collect("AAPL", NOW)["status"] == "blocked"
    assert requested == [URLS["AAPL"]] and gate.acquisitions == [URLS["AAPL"]]
    provider.close()
    assert not client.is_closed
    client.close()


@pytest.mark.parametrize("response", [httpx.Response(200, text=apple(), headers={"Content-Type": "text/html"}), httpx.Response(200, text=apple(), headers={"Content-Type": "application/json"}), httpx.Response(302, headers={"Location": "https://example.com/evil"}), httpx.Response(200, content=b"x" * 2_000_001)])
def test_provider_uses_exact_registered_url_and_preserves_receipt_and_failures(response):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return response
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = api().FinancialReleaseProvider(client, clock=lambda: RECEIPT).collect("AAPL", NOW)
    assert seen == [URLS["AAPL"]]
    expected = "available" if response.status_code == 200 and response.headers.get("Content-Type") == "text/html" else "missing"
    assert result["status"] == expected
    assert result["sources"][0]["retrieved_at"] == RECEIPT.isoformat()


def test_parser_returns_only_short_fact_locators_not_article_or_scripts():
    result = parse("AAPL", apple().replace("</html>", "<script>alert('secret')</script><p>UNIQUE_ARTICLE_SENTENCE</p></html>"))
    assert "UNIQUE_ARTICLE_SENTENCE" not in str(result)
    assert "alert" not in str(result)


def test_fiscal_end_date_cannot_masquerade_as_missing_publication():
    result = parse("AAPL", apple().replace("PRESS RELEASE July 30, 2026", "PRESS RELEASE"))
    assert result["status"] == "missing" and not result["periods"]


def test_exact_publication_timestamp_after_receipt_is_future_even_on_same_day():
    html = apple().replace("<html>", '<html><meta property="article:published_time" content="2026-10-04T23:00:00Z">')
    result = parse("AAPL", html)
    assert result["status"] == "missing" and not result["periods"]


def test_exact_metadata_publication_clock_is_preserved():
    html = apple().replace("<html>", '<html><meta property="article:published_time" content="2026-07-30T14:00:00-04:00">')
    result = parse("AAPL", html)
    assert result["status"] == "available"
    assert result["sources"][0]["published_at"] == "2026-07-30T18:00:00+00:00"


def test_provider_network_error_remains_explicit_and_owned_client_closes():
    def handler(request):
        raise httpx.ReadTimeout("test timeout", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = api().FinancialReleaseProvider(client, clock=lambda: RECEIPT).collect("AAPL", NOW)
    assert result["status"] == "missing" and result["warnings"]
    provider = api().FinancialReleaseProvider()
    provider.close()
    assert provider.client.is_closed


def test_nvda_reordered_quarter_header_cannot_label_prior_column_as_current():
    html = nvda().replace("<td>Q2 FY27</td><td>Q1 FY27</td><td>Q2 FY26</td>",
                          "<td>Q2 FY26</td><td>Q1 FY27</td><td>Q2 FY27</td>")
    result = parse("NVDA", html)
    assert result["status"] == "missing" and result["periods"] == []


@pytest.mark.parametrize("mutation", ["both_end_dates", "quarter_end_date", "annual_end_date", "reordered_duration_headers", "absent_end_dates"])
def test_msft_statement_dates_and_duration_columns_must_match_the_reported_period(mutation):
    html = msft()
    if mutation in ("both_end_dates", "quarter_end_date"):
        html = html.replace("Three Months Ended June 30,", "Three Months Ended March 31,")
    if mutation in ("both_end_dates", "annual_end_date"):
        html = html.replace("Twelve Months Ended June 30,", "Twelve Months Ended March 31,")
    if mutation == "reordered_duration_headers":
        html = html.replace("<td>Three Months Ended June 30,</td><td>Twelve Months Ended June 30,</td>",
                            "<td>Twelve Months Ended June 30,</td><td>Three Months Ended June 30,</td>")
    if mutation == "absent_end_dates":
        html = html.replace("Ended June 30,", "Ended")
    result = parse("MSFT", html)
    assert result["status"] == "missing" and result["periods"] == []


@pytest.mark.parametrize("symbol,currency", [("MSFT", "EUR"), ("NVDA", "EUR"), ("MSFT", "GBP"), ("NVDA", "CAD")])
def test_explicit_non_usd_table_currency_cannot_be_overridden_by_issuer_default(symbol, currency):
    html = {"MSFT": msft, "NVDA": nvda}[symbol]()
    html = html.replace("In millions", "In millions, " + currency).replace("$ in millions", currency + " in millions")
    result = parse(symbol, html)
    assert result["status"] == "missing" and result["periods"] == []


def test_currency_in_statement_heading_outside_the_table_is_not_lost():
    html = msft().replace("<table><caption>INCOME STATEMENTS (In millions, except per share amounts) (Unaudited)</caption>",
                          "<h2>INCOME STATEMENTS (In millions, EUR, except per share amounts) (Unaudited)</h2><table>")
    html = html.replace("<table><caption>CASH FLOWS STATEMENTS (In millions) (Unaudited)</caption>",
                        "<h2>CASH FLOWS STATEMENTS (In millions, EUR) (Unaudited)</h2><table>")
    result = parse("MSFT", html)
    assert result["status"] == "missing" and result["periods"] == []


def test_invalid_cash_table_date_does_not_discard_independent_valid_income_facts():
    html = msft()
    before, cash = html.split("CASH FLOWS STATEMENTS", 1)
    html = before + "CASH FLOWS STATEMENTS" + cash.replace("Ended June 30,", "Ended March 31,")
    result = parse("MSFT", html)
    assert result["status"] == "available"
    assert all("operating_cashflow" not in p["metrics"] for p in result["periods"])
    assert result["periods"][0]["metrics"]["revenue"]["value"] == 12_345_000_000


def test_explicit_external_income_currency_overrules_generic_caption_without_contaminating_cash():
    html = msft().replace("<table><caption>INCOME STATEMENTS",
                          "<h2>INCOME STATEMENTS (In millions, EUR, except per share amounts)</h2><table><caption>INCOME STATEMENTS", 1)
    result = parse("MSFT", html)
    assert result["status"] == "available"
    assert all(set(p["metrics"]) == {"operating_cashflow"} for p in result["periods"])
    assert result["periods"][0]["metrics"]["operating_cashflow"]["value"] == 3_456_000_000
    assert result["periods"][1]["metrics"]["operating_cashflow"]["value"] == 12_345_000_000
