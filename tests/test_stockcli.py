"""The callable/CLI seam must emit strict JSON and keep exports private."""
import json
from pathlib import Path

import pytest

from test_stocks import NOW, observation


def api():
    try:
        from printmoney.research import stockcli
    except ImportError:
        pytest.fail("stock CLI integration is missing")
    return stockcli


class Provider:
    def collect(self, symbol, now):
        return observation(symbol, earnings=None)


def test_real_orchestration_produces_useful_default_watch_research():
    from printmoney.research import stocks
    assert hasattr(stocks, "run_stock_research"), "research orchestration is missing"
    r = stocks.run_stock_research(stocks.ResearchRequest(), Provider(), NOW)
    assert len(r["evaluated"]) == 12 and r["highlights"] and not r["consider"]


def test_registered_cli_emits_one_json_document_for_provider_warnings(monkeypatch, capsys):
    c = api()
    monkeypatch.setattr(c, "StockProvider", Provider)
    from printmoney.cli import main
    rc = main(["research", "--symbols", "MSFT", "--json", "--no-record"])
    captured = capsys.readouterr()
    r = json.loads(captured.out)
    assert rc == 0 and r["evaluated"][0]["status"] == "watch"
    assert r["evaluated"][0]["quote_day"] == "2026-10-02"


def test_invalid_budget_has_structured_json_error_and_never_becomes_zero(capsys):
    api()
    from printmoney.cli import main
    assert main(["research", "--json", "--budget", "NaN", "--no-record"]) != 0
    r = json.loads(capsys.readouterr().out)
    assert r["system_status"] == "unavailable" and r["errors"]


def test_unsupported_market_has_no_claim_of_connected_thai_prices(monkeypatch, capsys):
    c = api()
    monkeypatch.setattr(c, "StockProvider", Provider)
    from printmoney.cli import main
    assert main(["research", "--json", "--market", "th", "--no-record"]) != 0
    r = json.loads(capsys.readouterr().out)
    assert r["coverage"]["thai_equities"] == "not_connected"
    assert not r["consider"] and r["excluded"]


def test_html_source_content_is_literal_and_quality_limits_are_visible():
    c = api()
    from printmoney.research.stocks import ResearchRequest, screen
    r = screen(ResearchRequest(symbols=("MSFT",)), [observation(name='<script>alert("buy")</script>', earnings=None)], NOW)
    html = c.render_html(r, "en")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "earnings_unknown" in html and "single_provider" in html
    assert "not_connected" in html
    assert "Sol" in c.render_html(r, "th")


def test_public_html_destination_is_rejected_before_writing(tmp_path, monkeypatch, capsys):
    c = api()
    root = tmp_path / "state" / "research"
    monkeypatch.setattr(c, "PRIVATE_ROOT", root)
    dest = tmp_path / "reports" / "index.html"
    from printmoney.cli import main
    assert main(["research", "--json", "--html", str(dest), "--no-record"]) != 0
    r = json.loads(capsys.readouterr().out)
    assert r["errors"] and not dest.exists()


def test_private_export_uses_same_report_without_touching_public_files(tmp_path, monkeypatch, capsys):
    c = api()
    monkeypatch.setattr(c, "PRIVATE_ROOT", tmp_path/"state"/"research")
    monkeypatch.setattr(c, "StockProvider", Provider)
    dest = c.PRIVATE_ROOT / "latest.html"
    from printmoney.cli import main
    assert main(["research", "--symbols", "MSFT", "--json", "--html", str(dest), "--no-record"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert dest.exists() and r["evaluated"][0]["symbol"] in dest.read_text(encoding="utf-8")
    assert not (tmp_path/"reports").exists()


def test_malformed_symbol_does_not_terminate_other_json_research(monkeypatch,capsys):
    import httpx
    from test_stockdata import chart, Calendar
    c=api()
    def response(request):
        if "/MSFT?" in str(request.url):
            payload=chart()
            payload["chart"]["result"][0]["timestamp"]=[1e100]*22
        elif "/AAPL?" in str(request.url):
            payload=chart()
            payload["chart"]["result"][0]["meta"]["symbol"]="AAPL"
        else:
            return httpx.Response(403,request=request)
        return httpx.Response(200,json=payload,request=request)
    from printmoney.research.stockdata import StockProvider
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr(c,"StockProvider",lambda:StockProvider(client=client,calendar=Calendar()))
        from printmoney.cli import main
        assert main(["research","--symbols","MSFT,AAPL","--json","--no-record"])==0
    result=json.loads(capsys.readouterr().out)
    assert result["excluded"][0]["symbol"]=="MSFT" and result["watch"][0]["symbol"]=="AAPL"


@pytest.mark.parametrize("extra", [["--pe-cap","invalid"],["--lang","invalid"],["--symbols"],["--unknown"]])
def test_research_parser_errors_have_one_json_document(extra,capsys):
    from printmoney.cli import main
    assert main(["research","--json","--no-record",*extra])==2
    r=json.loads(capsys.readouterr().out)
    assert r["system_status"]=="unavailable" and r["errors"]
