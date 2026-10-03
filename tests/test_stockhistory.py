"""Private prospective records must preserve original decisions and integrity."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from test_stocks import NOW, observation


def api():
    try:
        from printmoney.research import stockhistory
    except ImportError:
        pytest.fail("private recommendation record is missing")
    return stockhistory


def report():
    from printmoney.research.stocks import ResearchRequest, screen
    return screen(ResearchRequest(symbols=("MSFT",),horizon="months-plus"),[observation()],NOW)


def test_same_evidence_with_new_retrieval_time_is_not_a_second_decision(tmp_path):
    h = api()
    a = report()
    first = h.record(a,tmp_path)
    b = deepcopy(a)
    b["generated_at"] = "2026-10-03T14:00:00+00:00"
    for card in b["evaluated"]:
        card["fetched_at"] = b["generated_at"]
        for source in card["sources"]:
            source["retrieved_at"] = b["generated_at"]
            source["observed_at"] = b["generated_at"]
    assert h.record(b,tmp_path) == first
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert h.load_records(tmp_path)[0]["payload"]["generated_at"] == a["generated_at"]


def test_changed_qualifying_catalyst_creates_distinct_immutable_decisions(tmp_path):
    h = api()
    from printmoney.research.stocks import ResearchRequest, screen
    request = ResearchRequest(symbols=("MSFT",), horizon="days-weeks")
    item = observation(verified_catalyst=True)
    a = screen(request, [item], NOW)
    first = h.record(a, tmp_path)
    item["verified_catalyst"]["date"] = "2026-10-21"
    b = screen(request, [item], NOW)
    second = h.record(b, tmp_path)
    assert [card["symbol"] for card in a["consider"]] == ["MSFT"]
    assert [card["symbol"] for card in b["consider"]] == ["MSFT"]
    assert second != first
    saved = {r["id"]: r["payload"] for r in h.load_records(tmp_path)}
    assert len(saved) == 2
    assert saved[first]["evaluated"][0]["catalyst"]["date"] == "2026-10-20"
    assert saved[second]["evaluated"][0]["catalyst"]["date"] == "2026-10-21"


def test_catalyst_retrieval_only_change_keeps_original_evidence_and_cutoff(tmp_path):
    h = api()
    from printmoney.research.stocks import ResearchRequest, screen
    request = ResearchRequest(symbols=("MSFT",), horizon="days-weeks")
    item = observation(verified_catalyst=True)
    a = screen(request, [item], NOW)
    first = h.record(a, tmp_path)
    later = NOW + timedelta(hours=1)
    item["fetched_at"] = later.isoformat()
    for source in item["sources"]:
        source["retrieved_at"] = later.isoformat()
    for key in ("earnings", "verified_catalyst"):
        item[key]["retrieved_at"] = later.isoformat()
    b = screen(request, [item], later)
    assert [card["symbol"] for card in a["consider"]] == ["MSFT"]
    assert [card["symbol"] for card in b["consider"]] == ["MSFT"]
    assert h.record(b, tmp_path) == first
    saved = h.load_records(tmp_path)
    assert len(saved) == 1 and saved[0]["payload"]["generated_at"] == a["generated_at"]
    assert saved[0]["payload"]["evaluated"][0]["catalyst"] == a["evaluated"][0]["catalyst"]


def test_record_does_not_copy_private_amounts_or_profile_into_evidence(tmp_path):
    h = api()
    r = report()
    r.update(budget="1234.56", loss_limit="87.65", profile={"holdings":"private_company"})
    h.record(r,tmp_path)
    text = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert "1234.56" not in text and "87.65" not in text and "private_company" not in text


def test_record_freezes_implementation_and_evaluation_definition_without_claiming_first_sighting(tmp_path):
    h=api()
    r=report()
    rid=h.record(r,tmp_path)
    saved=h.load_records(tmp_path)[0]["payload"]
    assert saved["implementation_version"] and saved["evaluation_definition"]["round_trip_cost_bps"]==[10,30]
    source=saved["evaluated"][0]["sources"][0]
    assert "first_seen_at" not in source and source["observed_at"]==source["retrieved_at"]


def test_altered_record_is_detected_instead_of_quietly_scored(tmp_path):
    h = api()
    rid = h.record(report(),tmp_path)
    path = tmp_path/f"{rid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["payload"]["evaluated"][0]["status"] = "avoid"
    path.write_text(json.dumps(data),encoding="utf-8")
    with pytest.raises(ValueError,match="integrity"):
        h.load_records(tmp_path)


def test_traversal_and_public_destinations_are_not_private_paths(tmp_path):
    h = api()
    root = tmp_path/"state"/"research"
    assert h.private_path(root/"safe.html",root) == root/"safe.html"
    with pytest.raises(ValueError):
        h.private_path(tmp_path/"reports"/"x.html",root)
    with pytest.raises(ValueError):
        h.private_path(root/".."/".."/"reports"/"x.html",root)


def test_symlink_escape_is_rejected(tmp_path):
    h = api()
    root = tmp_path/"state"/"research"
    root.mkdir(parents=True)
    target = tmp_path/"reports"
    target.mkdir()
    try:
        (root/"export").symlink_to(target,target_is_directory=True)
    except OSError:
        pytest.skip("Windows process lacks symlink privilege")
    with pytest.raises(ValueError):
        h.private_path(root/"export"/"x.html",root)
