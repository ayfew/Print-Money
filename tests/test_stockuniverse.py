"""Generated constituent facts test security identity and snapshot boundaries."""
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def api():
    try:
        from printmoney.research import stockuniverse
    except ImportError:
        pytest.fail("dynamic membership resolver is missing")
    return stockuniverse


def footer(index, revision="123456"):
    title = "List_of_S%26P_500_companies" if index == "sp500" else "List_of_NASDAQ-100_companies"
    return f'<div class="printfooter">Retrieved from "<a href="/w/index.php?title={title}&amp;oldid={revision}">Permanent</a>"</div>'


def page(index, symbols=None, revision="123456", last_edited="25 September 2026, at 22:49"):
    symbols = symbols if symbols is not None else (["CTVA", "WBD"] + [f"S{i:03}" for i in range(498)] if index == "sp500" else [f"N{i:03}" for i in range(100)])
    headers = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "CIK"] if index == "sp500" else ["Ticker", "Company", "ICB Industry", "ICB Subsector"]
    rows = []
    for symbol in symbols:
        cells = [symbol, "Company " + symbol, "Technology", "Software"]
        if index == "sp500":
            cells.append("0001652044" if symbol in ("GOOG", "GOOGL") else "0000000001")
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return '<html><table><tr>' + ''.join(f'<th>{h}</th>' for h in headers) + '</tr>' + ''.join(rows) + '</table>' + footer(index, revision) + f'<li id="footer-info-lastmod">This page was last edited on {last_edited} (UTC).</li></html>'


class Client:
    def __init__(self, pages=None, clock=None):
        self.pages = pages or {}
        self.calls = []
        self.clock = clock or (lambda: NOW)

    def get(self, url, **kwargs):
        self.calls.append(url)
        index = "sp500" if "S%26P" in url else "nasdaq100"
        body = self.pages.get(index, page(index))
        if isinstance(body, Exception):
            raise body
        if isinstance(body, int):
            return httpx.Response(body, request=httpx.Request("GET", url))
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))


def resolve(tmp_path, **kwargs):
    return api().resolve_universe("sp500-nasdaq100", NOW, tmp_path, **kwargs)


def test_missing_feature_has_real_behavior_contract(tmp_path):
    out = resolve(tmp_path, client=Client())
    assert len(out["members"]) == 601  # Generated 600 + effective VYLR notice.
    assert out["verification"] == "community_uncertified"
    assert out["completeness"]["status"] == "complete"
    assert out["completeness"]["certified_current"] is False


def test_union_keeps_classes_aliases_overlap_and_cik_identity(tmp_path):
    sp = ["GOOG", "GOOGL", "BRK.B", "BF.B", "CTVA", "WBD"] + [f"S{i:03}" for i in range(494)]
    nd = ["GOOG", "GOOGL", "WBD"] + [f"N{i:03}" for i in range(97)]
    out = resolve(tmp_path, client=Client({"sp500": page("sp500", sp), "nasdaq100": page("nasdaq100", nd)}))
    members = {m["symbol"]: m for m in out["members"]}
    assert members["GOOG"]["indices"] == ["sp500", "nasdaq100"]
    assert members["GOOGL"]["cik"] == members["GOOG"]["cik"]
    assert members["BRK.B"]["provider_symbol"] == "BRK-B"
    assert members["BF.B"]["provider_symbol"] == "BF-B"
    assert "CTVA" in members and "WBD" in members and "TWLO" not in members
    assert {(p["symbol"], p["effective_at"]) for p in out["pending"]} >= {("TWLO", "2026-10-06T13:30:00+00:00"), ("CTVA", "2026-10-06T13:30:00+00:00")}
    assert members["VYLR"]["sector"] is None  # Oct 6 sector change is future.


def test_provenance_uses_observed_revision_edit_clock_hash_and_cc_attribution(tmp_path):
    out = resolve(tmp_path, client=Client({"sp500": page("sp500", revision="987654321")}))
    src = next(s for s in out["sources"] if s.get("index") == "sp500")
    assert src["revision"] == "987654321"
    assert src["last_edited_at"] == "2026-09-25T22:49:00+00:00"
    assert src["captured_at"] == NOW.isoformat() and src["effective_at"] is None
    assert len(src["content_hash"]) == 64
    assert src["license"] == "CC BY-SA 4.0" and "adapt" in src["adaptation_notice"].lower()
    assert "Wikipedia" in src["attribution"]


@pytest.mark.parametrize("bad", ["<html>login</html>", page("sp500", []), page("sp500", ["AAPL"]), page("sp500", [f"S{i:03}" for i in range(499)] + ["bad symbol"]), page("sp500", [f"S{i:03}" for i in range(499)] + ["S000"])], ids=["malformed", "empty", "small", "invalid", "duplicate"])
def test_malformed_empty_small_invalid_or_duplicate_source_is_partial(tmp_path, bad):
    out = resolve(tmp_path, client=Client({"sp500": bad}))
    assert len(out["members"]) == 100
    assert out["completeness"]["status"] == "partial"
    assert out["completeness"]["missing_indices"] == ["sp500"]
    assert out["warnings"]


def test_denied_source_keeps_other_index_without_retry(tmp_path):
    client = Client({"nasdaq100": 403})
    out = resolve(tmp_path, client=client)
    assert len(out["members"]) == 501 and len(client.calls) == 2
    assert out["completeness"]["missing_indices"] == ["nasdaq100"]
    assert any(s["status"] == "unavailable" for s in out["sources"])


def test_cache_preserves_version_and_original_clocks_and_offline_makes_no_requests(tmp_path):
    out = resolve(tmp_path, client=Client())
    client = Client()
    cached = api().resolve_universe(out["name"], NOW + timedelta(hours=1), tmp_path, client=client, offline=True)
    assert cached == out and client.calls == []
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_historical_cutoff_rejects_later_observed_cache(tmp_path):
    resolve(tmp_path, client=Client())
    out = api().resolve_universe("sp500-nasdaq100", NOW - timedelta(days=1), tmp_path, offline=True)
    assert out["members"] == [] and out["completeness"]["status"] == "unavailable"
    assert any("cutoff" in w.lower() for w in out["warnings"])


def test_future_live_receipt_and_revision_rejected(tmp_path):
    out = resolve(tmp_path, client=Client(clock=lambda: NOW + timedelta(seconds=1)))
    assert out["members"] == []
    out = resolve(tmp_path, client=Client({"sp500": page("sp500", last_edited="5 October 2026, at 22:49")}))
    assert out["completeness"]["missing_indices"] == ["sp500"]


def test_corrupt_offline_cache_is_explicit_gap(tmp_path):
    resolve(tmp_path, client=Client())
    next(tmp_path.glob("*.json")).write_text("{broken", encoding="utf-8")
    out = resolve(tmp_path, offline=True)
    assert out["members"] == [] and out["warnings"]


@pytest.mark.parametrize("mutation", ["future", "unsafe", "class_alias", "index", "duplicate", "version", "source_missing", "source_future", "small", "certified"])
def test_import_validation_cannot_promote_untrusted_or_future_membership(tmp_path, mutation):
    out = resolve(tmp_path, client=Client())
    imported = copy.deepcopy(out)
    if mutation == "future": imported["captured_at"] = (NOW + timedelta(days=1)).isoformat()
    if mutation == "unsafe": imported["sources"][0]["url"] = "javascript:alert(1)"
    if mutation == "class_alias": imported["members"][0]["provider_symbol"] = "OTHER"
    if mutation == "index": imported["members"][0]["indices"] = ["nasdaq_composite"]
    if mutation == "duplicate": imported["members"].append(imported["members"][0])
    if mutation == "version": imported["version"] = "forged"
    if mutation == "source_missing": imported["sources"] = []
    if mutation == "source_future": imported["sources"][0]["captured_at"] = (NOW + timedelta(days=1)).isoformat()
    if mutation == "small": imported["members"] = imported["members"][:1]
    if mutation == "certified": imported["verification"] = "verified"
    if mutation != "version": imported["version"] = api()._version(imported)
    path = tmp_path / "import.json"
    path.write_text(json.dumps(imported), encoding="utf-8")
    result = resolve(tmp_path / "fresh", offline=True, import_path=path)
    assert result["members"] == [] and result["warnings"]


def test_valid_import_preserves_snapshot(tmp_path):
    out = resolve(tmp_path, client=Client())
    path = tmp_path / "input.json"
    path.write_text(json.dumps(out), encoding="utf-8")
    assert resolve(tmp_path / "fresh", offline=True, import_path=path) == out


def test_effective_notice_applies_only_at_market_open_and_preserves_other_index(tmp_path):
    sp = ["CTVA", "WBD"] + [f"S{i:03}" for i in range(498)]
    nd = ["WBD"] + [f"N{i:03}" for i in range(99)]
    after = datetime(2026, 10, 6, 13, 30, tzinfo=timezone.utc)
    out = api().resolve_universe("sp500-nasdaq100", after, tmp_path, client=Client({"sp500": page("sp500", sp), "nasdaq100": page("nasdaq100", nd)}, clock=lambda: after))
    members = {m["symbol"]: m for m in out["members"]}
    assert "CTVA" not in members and members["WBD"]["indices"] == ["nasdaq100"]
    assert members["TWLO"]["indices"] == ["sp500"] and members["VYLR"]["sector"] == "Consumer Staples"
    assert out["pending"] == []


def test_starter_is_explicit_local_scope_and_unknown_names_fail(tmp_path):
    out = api().resolve_universe("starter", NOW, tmp_path, offline=True)
    assert len(out["members"]) == 12 and out["verification"] == "local_starter"
    with pytest.raises(ValueError):
        api().resolve_universe("nasdaq_composite", NOW, tmp_path)


def test_live_none_uses_collection_end_without_inventing_future_cutoff(tmp_path):
    receipt = datetime.now(timezone.utc)
    out = api().resolve_universe("nasdaq100", None, tmp_path, client=Client(clock=lambda: receipt))
    assert len(out["members"]) == 100 and out["captured_at"] == receipt.isoformat()


def test_unknown_metadata_remains_unknown_with_warning(tmp_path):
    html = page("nasdaq100").split('<a href=')[0] + '</html>'
    out = api().resolve_universe("nasdaq100", NOW, tmp_path, client=Client({"nasdaq100": html}))
    assert out["sources"][0]["revision"] is None and out["sources"][0]["last_edited_at"] is None
    assert any("revision" in warning.lower() for warning in out["warnings"])


def test_reviewed_future_notice_overrides_anticipatory_community_table(tmp_path):
    symbols = ["VYLR", "TWLO"] + [f"S{i:03}" for i in range(498)]
    out = api().resolve_universe("sp500", NOW, tmp_path, client=Client({"sp500": page("sp500", symbols)}))
    members = {m["symbol"]: m for m in out["members"]}
    assert "TWLO" not in members and "WBD" in members and "CTVA" in members
    assert members["VYLR"]["sector"] is None


def test_constituent_table_conflict_rejects_whole_source(tmp_path):
    html = page("sp500") + page("sp500")
    out = resolve(tmp_path, client=Client({"sp500": html}))
    assert out["completeness"]["missing_indices"] == ["sp500"]


def test_revision_comes_from_page_metadata_not_arbitrary_historical_link(tmp_path):
    html = page("nasdaq100", revision="234567").replace('<html>', '<html><script>{"wgRevisionId":234567}</script><a href="/w/index.php?oldid=111111">Old historical row</a>')
    out = api().resolve_universe("nasdaq100", NOW, tmp_path, client=Client({"nasdaq100": html}))
    assert out["sources"][0]["revision"] == "234567"


def test_historical_cutoff_before_notice_review_never_uses_registry(tmp_path):
    cutoff = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    out = api().resolve_universe("sp500", cutoff, tmp_path, client=Client(clock=lambda: cutoff))
    assert len(out["members"]) == 500
    assert not any(s["kind"] == "official_notice" for s in out["sources"])


def test_import_future_retrieval_clock_rejected_even_with_valid_content_version(tmp_path):
    imported = resolve(tmp_path, client=Client())
    imported["sources"][0]["retrieved_at"] = (NOW + timedelta(days=1)).isoformat()
    imported["version"] = api()._version(imported)
    path = tmp_path / "future-clock.json"
    path.write_text(json.dumps(imported), encoding="utf-8")
    assert resolve(tmp_path / "fresh", import_path=path)["members"] == []


def test_import_future_effective_source_rejected_even_with_valid_content_version(tmp_path):
    imported = resolve(tmp_path, client=Client())
    imported["sources"][0]["effective_at"] = (NOW + timedelta(days=1)).isoformat()
    imported["version"] = api()._version(imported)
    path = tmp_path / "future-effective.json"
    path.write_text(json.dumps(imported), encoding="utf-8")
    assert resolve(tmp_path / "fresh", import_path=path)["members"] == []


def test_pending_change_must_reference_retained_source(tmp_path):
    imported = resolve(tmp_path, client=Client())
    imported["pending"][0]["source_url"] = "https://example.org/forged"
    imported["version"] = api()._version(imported)
    path = tmp_path / "forged-pending.json"
    path.write_text(json.dumps(imported), encoding="utf-8")
    assert resolve(tmp_path / "fresh", import_path=path)["members"] == []


def test_failed_cross_index_cik_conflict_rolls_back_entire_source(tmp_path):
    sp = ["OVERLAP"] + [f"S{i:03}" for i in range(499)]
    nd = [f"N{i:03}" for i in range(99)] + ["OVERLAP"]
    nd_html = page("nasdaq100", nd).replace('<th>ICB Subsector</th>', '<th>ICB Subsector</th><th>CIK</th>').replace('</tr>', '<td>9999999999</td></tr>')
    # The header is fixed independently from generated body cells.
    nd_html = nd_html.replace('<th>CIK</th><td>9999999999</td>', '<th>CIK</th>')
    out = resolve(tmp_path, client=Client({"sp500": page("sp500", sp), "nasdaq100": nd_html}))
    assert out["completeness"]["missing_indices"] == ["nasdaq100"]
    assert all("nasdaq100" not in m["indices"] for m in out["members"])


def test_excluded_future_receipt_keeps_real_diagnostic_clock_and_valid_other_source(tmp_path):
    clocks = iter([NOW + timedelta(seconds=1), NOW])
    out = resolve(tmp_path, client=Client(clock=lambda: next(clocks)))
    assert out["completeness"]["missing_indices"] == ["sp500"]
    failed = next(s for s in out["sources"] if s["index"] == "sp500")
    assert failed["captured_at"] == (NOW + timedelta(seconds=1)).isoformat()
    assert failed["excluded_after_cutoff"] is True
    assert len(out["members"]) == 100


@pytest.mark.parametrize("field", ["published_at", "as_of"])
@pytest.mark.parametrize("value", ["2026-10-05", "2026-10-04T12:00:01+00:00", "2026-10-04T12:00:00", "not-a-date", ""])
def test_import_source_clock_requires_valid_precision_and_no_future_evidence(tmp_path, field, value):
    imported = resolve(tmp_path, client=Client())
    source = next(s for s in imported["sources"] if s["kind"] == "official_notice")
    source[field] = value
    imported["version"] = api()._version(imported)
    path = tmp_path / "source-clock.json"
    path.write_text(json.dumps(imported), encoding="utf-8")
    rejected = resolve(tmp_path / "fresh", offline=True, import_path=path)
    assert rejected["members"] == [] and any(field in warning for warning in rejected["warnings"])


@pytest.mark.parametrize("field", ["published_at", "as_of"])
@pytest.mark.parametrize("value", ["2026-10-04", "2026-10-04T03:16:10+00:00", None])
def test_import_valid_source_day_precision_is_retained_without_inventing_midnight(tmp_path, field, value):
    imported = resolve(tmp_path, client=Client())
    source = next(s for s in imported["sources"] if s["kind"] == "official_notice")
    source[field] = value
    imported["version"] = api()._version(imported)
    path = tmp_path / "source-clock.json"
    path.write_text(json.dumps(imported), encoding="utf-8")
    assert resolve(tmp_path / "fresh", offline=True, import_path=path) == imported


@pytest.mark.parametrize("field", ["published_at", "as_of"])
def test_import_source_instant_must_not_follow_its_own_receipt(tmp_path, field):
    imported = resolve(tmp_path, client=Client())
    source = next(s for s in imported["sources"] if s["kind"] == "official_notice")
    source[field] = "2026-10-04T03:16:11+00:00"  # Before cutoff, after notice receipt.
    imported["version"] = api()._version(imported)
    path = tmp_path / "source-after-receipt.json"
    path.write_text(json.dumps(imported), encoding="utf-8")
    assert resolve(tmp_path / "fresh", offline=True, import_path=path)["members"] == []


def test_arbitrary_archived_revision_link_cannot_claim_current_page_revision(tmp_path):
    html = page("sp500", revision="123456").replace(footer("sp500"), '<a href="/w/index.php?title=Example&oldid=999999">Archived history entry</a>')
    out = api().resolve_universe("sp500", NOW, tmp_path, client=Client({"sp500": html}))
    source = next(s for s in out["sources"] if s.get("index") == "sp500")
    assert source["revision"] is None
    assert any("revision" in warning.lower() for warning in out["warnings"])


def test_same_page_archived_revision_is_not_current_metadata(tmp_path):
    html = page("sp500").replace(footer("sp500"), '<a href="/w/index.php?title=List_of_S%26P_500_companies&oldid=999999">Archived history entry</a>')
    out = api().resolve_universe("sp500", NOW, tmp_path, client=Client({"sp500": html}))
    assert out["sources"][0]["revision"] is None


@pytest.mark.parametrize("index,title", [("sp500", "List_of_S%26P_500_companies"), ("nasdaq100", "List_of_NASDAQ-100_companies")])
@pytest.mark.parametrize("container", ['<div class="printfooter">Retrieved from {link}</div>', '<li id="t-permalink">{link}</li>'])
def test_current_page_scoped_footer_or_permanent_link_retains_revision(tmp_path, index, title, container):
    link = f'<a href="/w/index.php?title={title}&amp;oldid=234567">Permanent link</a>'
    html = page(index).replace(footer(index), container.format(link=link))
    out = api().resolve_universe(index, NOW, tmp_path, client=Client({index: html}))
    assert out["sources"][0]["revision"] == "234567"


@pytest.mark.parametrize("unrelated", ['<p>"wgRevisionId":999999</p>', '<div about="https://en.wikipedia.org/wiki/Special:Redirect/revision/999999">Historical embedded revision</div>'], ids=["body", "nested"])
def test_unrelated_body_text_or_nested_about_attribute_is_not_current_metadata(tmp_path, unrelated):
    html = page("nasdaq100").replace(footer("nasdaq100"), unrelated)
    out = api().resolve_universe("nasdaq100", NOW, tmp_path, client=Client({"nasdaq100": html}))
    assert out["sources"][0]["revision"] is None


def test_parsoid_document_root_revision_remains_supported(tmp_path):
    html = page("nasdaq100").replace(footer("nasdaq100"), '').replace('<html>', '<html about="https://en.wikipedia.org/wiki/Special:Redirect/revision/234567">')
    out = api().resolve_universe("nasdaq100", NOW, tmp_path, client=Client({"nasdaq100": html}))
    assert out["sources"][0]["revision"] == "234567"


def test_owned_http_client_rejects_redirect_without_fetching_or_misattributing_body(tmp_path, monkeypatch):
    normal_client = httpx.Client
    requests = []
    def handler(request):
        requests.append(str(request.url))
        if request.url.host == "en.wikipedia.org":
            return httpx.Response(302, headers={"Location": "https://other.example/constituents"})
        return httpx.Response(200, text=page("sp500"))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: normal_client(transport=httpx.MockTransport(handler), **kwargs))
    out = api().resolve_universe("sp500", None, tmp_path)
    assert requests == [api().URLS["sp500"]]
    assert out["members"] == [] and out["sources"][0]["status"] == "unavailable"
    assert "302" in out["sources"][0]["error"]


def test_borrowed_http_client_redirect_default_cannot_bypass_per_get_option(tmp_path):
    requests = []
    def handler(request):
        requests.append(str(request.url))
        if request.url.host == "en.wikipedia.org":
            return httpx.Response(302, headers={"Location": "https://other.example/constituents"})
        return httpx.Response(200, text=page("sp500"))
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        out = api().resolve_universe("sp500", None, tmp_path, client=client)
        assert not client.is_closed
    assert requests == [api().URLS["sp500"]]
    assert out["members"] == [] and out["sources"][0]["status"] == "unavailable"


@pytest.mark.parametrize("status", [401, 403, 429])
def test_owned_same_host_denial_circuit_skips_second_index_without_request(tmp_path, monkeypatch, status):
    normal_client = httpx.Client
    requests = []
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(status)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: normal_client(transport=httpx.MockTransport(handler), **kwargs))
    out = api().resolve_universe("sp500-nasdaq100", None, tmp_path)
    assert requests == [api().URLS["sp500"]]
    assert out["members"] == []
    assert out["completeness"]["missing_indices"] == ["sp500", "nasdaq100"]
    second = next(s for s in out["sources"] if s["index"] == "nasdaq100")
    assert "blocked" in second["error"].lower() and second["retrieved_at"] is None


def test_owned_two_actual_gets_use_shared_one_second_gate(tmp_path, monkeypatch):
    from printmoney.research import stockbatch
    normal_client, normal_gate = httpx.Client, stockbatch.RequestGate
    starts = []
    elapsed = [0.0]
    waits = []
    def sleep(delay):
        waits.append(delay)
        elapsed[0] += delay
    def handler(request):
        starts.append(elapsed[0])
        return httpx.Response(200, text=page("sp500" if "S%26P" in str(request.url) else "nasdaq100"))
    monkeypatch.setattr(stockbatch, "RequestGate", lambda: normal_gate(clock=lambda: elapsed[0], sleeper=sleep))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: normal_client(transport=httpx.MockTransport(handler), **kwargs))
    out = api().resolve_universe("sp500-nasdaq100", None, tmp_path)
    assert starts == [0.0, 1.0] and waits == [1.0]
    assert out["completeness"]["status"] == "complete"


def test_legacy_borrowed_get_without_redirect_keyword_fails_closed_before_request(tmp_path):
    class LegacyClient:
        def __init__(self): self.calls = []
        def get(self, url):
            self.calls.append(url)
            return httpx.Response(200, text=page("sp500"), request=httpx.Request("GET", url))
    client = LegacyClient()
    out = api().resolve_universe("sp500", None, tmp_path, client=client)
    assert client.calls == [] and out["members"] == []
    assert out["completeness"]["status"] == "unavailable"


def test_membership_version_ignores_only_observation_clocks_recursively(tmp_path):
    original = resolve(tmp_path, client=Client())
    later = copy.deepcopy(original)
    later["captured_at"] = (NOW + timedelta(minutes=1)).isoformat()
    for source in later["sources"]:
        for field in ("captured_at", "retrieved_at"):
            source[field] = (datetime.fromisoformat(source[field]) + timedelta(minutes=1)).isoformat()
    assert api()._version(later) == original["version"]
    assert original["captured_at"] == NOW.isoformat()
    assert later["sources"][0]["retrieved_at"] == (NOW + timedelta(minutes=1)).isoformat()


@pytest.mark.parametrize("change", ["member", "source_revision", "source_hash", "published_at", "as_of", "pending", "metadata", "nested_version"])
def test_membership_version_retains_semantic_evidence_changes(tmp_path, change):
    original = resolve(tmp_path, client=Client())
    changed = copy.deepcopy(original)
    if change == "member": changed["members"][0]["name"] = "Different issuer name"
    if change == "source_revision": changed["sources"][0]["revision"] = "234567"
    if change == "source_hash": changed["sources"][0]["content_hash"] = "a" * 64
    if change == "published_at": changed["sources"][0]["published_at"] = "2026-10-02"
    if change == "as_of": changed["sources"][0]["as_of"] = "2026-10-02"
    if change == "pending": changed["pending"][0]["effective_at"] = "2026-10-07T13:30:00+00:00"
    if change == "metadata": changed["warnings"].append("New meaningful membership caveat.")
    if change == "nested_version": changed["sources"][0]["version"] = "different-provider-version"
    assert api()._version(changed) != original["version"]


def test_semantic_version_import_preserves_original_full_clocks_and_cutoff_gates(tmp_path):
    original = resolve(tmp_path, client=Client())
    later = copy.deepcopy(original)
    later["captured_at"] = (NOW + timedelta(minutes=1)).isoformat()
    for source in later["sources"]:
        for field in ("captured_at", "retrieved_at"):
            source[field] = (datetime.fromisoformat(source[field]) + timedelta(minutes=1)).isoformat()
    later["version"] = api()._version(later)
    assert later["version"] == original["version"]
    path = tmp_path / "later-same-evidence.json"
    path.write_text(json.dumps(later), encoding="utf-8")
    accepted = api().resolve_universe(original["name"], NOW + timedelta(minutes=2), tmp_path / "imported", offline=True, import_path=path)
    assert accepted == later
    rejected = resolve(tmp_path / "before", offline=True, import_path=path)
    assert rejected["members"] == [] and any("cutoff" in warning for warning in rejected["warnings"])


def test_nested_list_and_object_observation_clocks_do_not_change_version(tmp_path):
    original = resolve(tmp_path, client=Client())
    original["sources"][0]["lineage"] = [{"receipt": {"captured_at": NOW.isoformat(), "retrieved_at": NOW.isoformat()}, "revision": "stable"}]
    original["version"] = api()._version(original)
    later = copy.deepcopy(original)
    later["sources"][0]["lineage"][0]["receipt"]["captured_at"] = (NOW + timedelta(minutes=1)).isoformat()
    later["sources"][0]["lineage"][0]["receipt"]["retrieved_at"] = (NOW + timedelta(minutes=1)).isoformat()
    assert api()._version(later) == original["version"]
    assert "captured_at" in later["sources"][0]["lineage"][0]["receipt"]


def test_legacy_clock_inclusive_hash_requires_explicit_validated_migration(tmp_path):
    original = resolve(tmp_path, client=Client())
    legacy = copy.deepcopy(original)
    legacy["version"] = api()._hash({key: value for key, value in legacy.items() if key != "version"})
    path = tmp_path / "legacy-clock-hash.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    rejected = resolve(tmp_path / "before", offline=True, import_path=path)
    assert rejected["members"] == [] and any("version" in warning for warning in rejected["warnings"])
    migrated = copy.deepcopy(legacy)
    migrated["version"] = api()._version(migrated)
    assert api()._validate(migrated, migrated["name"], NOW) == original
    assert path.read_text(encoding="utf-8") == json.dumps(legacy)  # Legacy artifact untouched.


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_import_rejects_nonfinite_json_even_with_forged_matching_semantic_hash(tmp_path, token):
    bad = resolve(tmp_path, client=Client())
    value = float("nan") if token == "NaN" else float("-inf") if token == "-Infinity" else float("inf")
    if token == "NaN":
        bad["sources"][0]["revision"] = value  # Exact independent-review reproduction.
    else:
        bad["sources"][0]["metadata"] = {"nested": [{"metric": value}]}
    # Attacker supplies a matching digest under the documented semantic layout;
    # the JSON safety boundary must reject independently of digest consistency.
    identity = copy.deepcopy(bad)
    for key in ("version", "captured_at", "retrieved_at"):
        identity.pop(key, None)
    for source in identity["sources"]:
        source.pop("captured_at", None)
        source.pop("retrieved_at", None)
    bad["version"] = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=True).encode("utf-8")).hexdigest()
    body = json.dumps(bad, allow_nan=True)
    if token == "1e999": body = body.replace("Infinity", "1e999")
    path = tmp_path / "nonfinite-import.json"
    path.write_text(body, encoding="utf-8")
    rejected = resolve(tmp_path / "fresh", offline=True, import_path=path)
    assert rejected["members"] == []
    assert any("float" in warning.lower() or "non-finite" in warning.lower() for warning in rejected["warnings"])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "negative-inf"])
@pytest.mark.parametrize("placement", ["revision", "nested_observation_clock"])
def test_version_rejects_nonfinite_anywhere_in_full_snapshot(tmp_path, value, placement):
    bad = resolve(tmp_path, client=Client())
    if placement == "revision": bad["sources"][0]["revision"] = value
    else: bad["sources"][0]["metadata"] = [{"captured_at": value}]
    with pytest.raises(ValueError, match="float|non-finite"):
        api()._version(bad)


def test_validation_rejects_nonfinite_clock_metadata_even_when_identity_omits_that_key(tmp_path):
    bad = resolve(tmp_path, client=Client())
    bad["sources"][0]["metadata"] = [{"captured_at": NOW.isoformat()}]
    bad["version"] = api()._version(bad)
    bad["sources"][0]["metadata"][0]["captured_at"] = float("nan")
    with pytest.raises(ValueError, match="float|non-finite"):
        api()._validate(bad, bad["name"], NOW)


@pytest.mark.parametrize("value", [None, "NaN and 1e999 are literal source text"])
def test_unknown_or_literal_nonfinite_looking_metadata_remains_valid(tmp_path, value):
    valid = resolve(tmp_path, client=Client())
    valid["sources"][0]["metadata"] = {"value": value}
    valid["version"] = api()._version(valid)
    path = tmp_path / "literal-import.json"
    path.write_text(json.dumps(valid, allow_nan=False), encoding="utf-8")
    assert resolve(tmp_path / "fresh", offline=True, import_path=path) == valid
