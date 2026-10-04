"""Deterministic issuer feed context: no live HTTP or article copies."""
from datetime import datetime, timezone
import importlib.util

import httpx
import pytest

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
RECEIPT = datetime(2026, 10, 4, 11, tzinfo=timezone.utc)
APPLE = "https://www.apple.com/newsroom/rss-feed.rss"
NVIDIA = "https://nvidianews.nvidia.com/releases.xml"


def api():
    assert importlib.util.find_spec("printmoney.research.stocknews") is not None, "issuer news seam is missing"
    from printmoney.research import stocknews
    return stocknews


def rss(items=None):
    items = items if items is not None else """<item><title>Quarter facts</title>
      <link>https://nvidianews.nvidia.com/news/result</link>
      <pubDate>Sat, 03 Oct 2026 10:00:00 GMT</pubDate>
      <description>Short release facts.</description></item>"""
    return f"<rss version='2.0'><channel><title>NVIDIA News</title>{items}</channel></rss>"


def atom():
    return """<feed xmlns='http://www.w3.org/2005/Atom'>
      <updated>2026-10-03T12:00:00Z</updated><entry>
      <title>&lt;script&gt;alert(1)&lt;/script&gt; &amp; facts</title>
      <link rel='self' href='https://www.apple.com/feed/item'/>
      <link rel='alternate' href='https://www.apple.com/newsroom/example/'/>
      <published>2026-10-03T10:00:00-04:00</published>
      <summary>Brief product release.</summary>
      <content>Full article must not be copied.</content></entry></feed>"""


def parse(xml, **kwargs):
    return api().parse_news_xml(xml, "NVDA", NOW, NVIDIA, retrieved_at=RECEIPT, **kwargs)


def test_rss_retains_publication_receipt_source_and_context_boundary():
    result = parse(rss())
    assert result["status"] == "available"
    item = result["items"][0]
    assert item["title"] == "Quarter facts"
    assert item["published_at"] == "2026-10-03T10:00:00+00:00"
    assert item["retrieved_at"] == RECEIPT.isoformat()
    assert item["source_url"] == NVIDIA
    assert item["verification"] == "context_only"
    assert result["sources"][0]["retrieved_at"] == RECEIPT.isoformat()
    assert result["sources"][0]["url"] == NVIDIA
    assert result["warnings"]


def test_atom_preserves_literal_title_and_prefers_alternate_https_link():
    result = api().parse_news_xml(atom(), "AAPL", NOW, APPLE, RECEIPT)
    item = result["items"][0]
    assert item["title"] == "<script>alert(1)</script> & facts"
    assert item["url"] == "https://www.apple.com/newsroom/example/"
    assert item["published_at"] == "2026-10-03T14:00:00+00:00"
    assert "Full article" not in str(result)
    assert result["sources"][0]["published_at"] is None
    assert result["sources"][0]["updated_at"] == "2026-10-03T12:00:00+00:00"


@pytest.mark.parametrize("date", ["", "garbage", "2026-10-03T10:00:00", "Mon, 05 Oct 2026 10:00:00 GMT"])
def test_unknown_naive_or_future_publication_does_not_enter_context(date):
    xml = rss(f"<item><title>Unknown</title><link>https://example.com/x</link><pubDate>{date}</pubDate></item>")
    result = parse(xml)
    assert not result["items"]
    assert result["status"] == "unavailable"
    assert result["warnings"]


@pytest.mark.parametrize("link", ["javascript:alert(1)", "http://example.com/x", "https://user:pass@example.com/x", "https://example.com/&#10;x", "//example.com/x"])
def test_unsafe_article_links_are_excluded(link):
    result = parse(rss().replace("https://nvidianews.nvidia.com/news/result", link))
    assert not result["items"]


@pytest.mark.parametrize("xml", ["<rss>", "<!DOCTYPE rss [<!ENTITY x 'danger'>]><rss><channel>&x;</channel></rss>", "<!DOCTYPE rss SYSTEM 'https://example.com/x'><rss/>", "<html><body>Not a feed</body></html>", "x" * (1024 * 1024 + 1)], ids=["malformed", "entity", "external-dtd", "html", "oversized"])
def test_malformed_entity_and_oversized_payloads_are_rejected(xml):
    result = parse(xml)
    assert result["status"] == "unavailable"
    assert not result["items"]
    assert result["warnings"]


def test_byte_encoded_doctype_cannot_evade_entity_rejection():
    result = parse("<!DOCTYPE rss [<!ENTITY x 'danger'>]><rss/>".encode("utf-16"))
    assert not result["items"]
    assert "entity" in " ".join(result["warnings"]).lower()


def test_partial_feed_caps_twenty_short_headlines_and_retains_rejection():
    valid = "".join(rss().split("<channel>", 1)[1].split("</channel>", 1)[0].replace("Quarter facts", str(i)) for i in range(25))
    invalid = "<item><title>No date</title><link>https://example.com/x</link></item>"
    result = parse(rss(valid + invalid).replace("Short release facts.", "fact " * 200))
    assert result["status"] == "partial"
    assert len(result["items"]) == 20
    assert all(len(item["summary"]) <= 280 for item in result["items"])
    assert any("date" in warning.lower() for warning in result["warnings"])


def test_future_or_naive_receipt_and_unsafe_source_are_rejected():
    for receipt in [datetime(2026, 10, 5, tzinfo=timezone.utc), datetime(2026, 10, 3)]:
        result = api().parse_news_xml(rss(), "NVDA", NOW, NVIDIA, receipt)
        assert result["status"] == "unavailable"
        assert not result["items"]
    assert api().parse_news_xml(rss(), "NVDA", NOW, "javascript:alert(1)")["status"] == "unavailable"


class Gate:
    def __init__(self):
        self.acquired = []
        self.blocked = {}

    def acquire(self, url=None):
        if self.is_blocked(url):
            raise ValueError("blocked")
        self.acquired.append(url)

    def block(self, url, status):
        self.blocked[url] = status

    def is_blocked(self, url):
        return url in self.blocked


def test_unknown_symbols_do_not_request_or_acquire_gate():
    def unexpected(request):
        pytest.fail("unsupported issuer requested HTTP")
    gate = Gate()
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        provider = api().NewsProvider(client, gate, lambda: RECEIPT)
        for symbol in ["MSFT", "AMZN", "GOOGL", "FAKE"]:
            result = provider.collect(symbol, NOW)
            assert result["status"] == "unsupported"
            assert not result["items"]
    assert not gate.acquired


def test_denial_is_retained_and_other_registered_issuer_can_succeed():
    calls = []
    def respond(request):
        calls.append(str(request.url))
        if str(request.url) == NVIDIA:
            return httpx.Response(403, text="denied")
        return httpx.Response(200, text=atom())
    gate = Gate()
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = api().NewsProvider(client, gate, lambda: RECEIPT)
        denied = provider.collect("NVDA", NOW)
        assert denied["status"] == "blocked"
        assert denied["sources"][0]["http_status"] == 403
        assert provider.collect("NVDA", NOW)["status"] == "blocked"
        assert provider.collect("AAPL", NOW)["status"] == "available"
        provider.close()
        assert not client.is_closed
    assert calls == [NVIDIA, APPLE]
    assert gate.acquired == calls


@pytest.mark.parametrize("status", [404, 429, 500])
def test_http_failures_are_explicit_and_never_retried(status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, text="No payload")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = api().NewsProvider(client, Gate(), lambda: RECEIPT).collect("AAPL", NOW)
    assert result["status"] == ("blocked" if status == 429 else "unavailable")
    assert result["sources"][0]["http_status"] == status
    assert len(calls) == 1


def test_network_exception_is_an_explicit_gap_and_owned_client_closes():
    def respond(request):
        raise httpx.ReadTimeout("timeout", request=request)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = api().NewsProvider(client, clock=lambda: RECEIPT).collect("AAPL", NOW)
    assert result["status"] == "unavailable"
    assert result["warnings"]
    provider = api().NewsProvider()
    provider.close()
    assert provider.client.is_closed


def test_publication_after_actual_receipt_is_future_even_before_report_cutoff():
    result = parse(rss().replace("Sat, 03 Oct 2026 10:00:00 GMT", "Sun, 04 Oct 2026 11:30:00 GMT"))
    assert not result["items"]
    assert result["status"] == "unavailable"


@pytest.mark.parametrize("updated", ["2026-10-05T10:00:00Z", "2026-10-04T11:30:00Z", "2026-10-03T10:00:00"])
def test_entry_updated_clock_cannot_hide_future_or_unknown_content(updated):
    xml = atom().replace("<published>", f"<updated>{updated}</updated><published>")
    result = api().parse_news_xml(xml, "AAPL", NOW, APPLE, RECEIPT)
    assert not result["items"]
    assert result["status"] == "unavailable"


def test_updated_only_atom_retains_context_without_inventing_publication():
    xml = atom().replace("<published>2026-10-03T10:00:00-04:00</published>",
                         "<updated>2026-10-03T10:00:00-04:00</updated>")
    result = api().parse_news_xml(xml, "AAPL", NOW, APPLE, RECEIPT)
    assert result["status"] == "partial"
    item = result["items"][0]
    assert item["published_at"] is None
    assert item["updated_at"] == "2026-10-03T14:00:00+00:00"
    assert item["publication_basis"] == "updated_only; original publication unknown"
    assert item["verification"] == "context_only"
    assert any("publication unknown" in warning.lower() for warning in result["warnings"])


@pytest.mark.parametrize("updated", ["2026-10-05T10:00:00Z", "2026-10-03T10:00:00", ""])
def test_updated_only_atom_still_rejects_future_naive_or_missing_update(updated):
    xml = atom().replace("<published>2026-10-03T10:00:00-04:00</published>", f"<updated>{updated}</updated>")
    result = api().parse_news_xml(xml, "AAPL", NOW, APPLE, RECEIPT)
    assert not result["items"]


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
def test_bomless_entity_declarations_cannot_reach_xml_expansion(encoding):
    xml = "<!DOCTYPE rss [<!ENTITY headline 'expanded'>]>" + rss().replace("Quarter facts", "&headline;")
    result = parse(xml.encode(encoding))
    assert result["status"] == "unavailable"
    assert not result["items"]


def test_rss_feed_publication_and_build_clock_are_distinct():
    xml = rss().replace("<channel>", "<channel><pubDate>Sat, 03 Oct 2026 09:00:00 GMT</pubDate><lastBuildDate>Sat, 03 Oct 2026 10:00:00 GMT</lastBuildDate>")
    source = parse(xml)["sources"][0]
    assert source["published_at"] == "2026-10-03T09:00:00+00:00"
    assert source["updated_at"] == "2026-10-03T10:00:00+00:00"
