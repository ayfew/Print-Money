"""The causal graph: does every edge say what kind of claim it is?

A chain of plausible sentences reads exactly like a chain of established ones,
which is the whole hazard this module was built around. These tests hold the
line at the place it matters: an edge with no evidence must not exist, an edge
that is true by construction must not be offered as an explanation, and a link
the evidence does not settle must be visibly marked as unsettled.
"""
from __future__ import annotations

import json

import pytest

from printmoney.research import feeds as F
from printmoney.research import graph as G
from printmoney.research import macro as M
from printmoney.research import sources
from printmoney.research.graphview import write_graph_html


def _links(*rows):
    return M.Table(links=[M.Link(feed=f, symbol=s, r=r, n=n) for f, s, r, n in rows])


def _graph(**kw):
    return G.build(**kw)


# --------------------------------------------------------------------------- #
class TestEveryEdgeIsAccountable:
    def test_every_edge_kind_is_one_of_the_four(self):
        g = _graph(links=_links(("ust2y", "UUP", 0.43, 659)))
        assert {e.kind for e in g.edges} <= set(G.KINDS)

    def test_there_is_no_category_for_seems_right(self):
        """Four kinds, all of them backed by something. No fifth bucket."""
        assert set(G.KINDS) == {"arithmetic", "documented", "measured", "contested"}

    def test_every_curated_edge_carries_evidence(self):
        for edge in G.MECHANISM + G.CONTESTED:
            assert edge.evidence, edge.src + "->" + edge.dst

    def test_every_curated_edge_cites_an_allowed_source(self):
        for edge in G.MECHANISM + G.CONTESTED:
            assert sources.get(edge.source).tier in (1, 2, 3, 4)

    def test_documented_edges_point_at_a_real_url(self):
        for edge in G.MECHANISM:
            if edge.kind != "documented":
                continue
            # One edge cites a reason rather than a page; the rest link out.
            assert edge.evidence.startswith("http") or "paid feed" in edge.evidence

    def test_measured_edges_carry_r_and_n(self):
        g = _graph(links=_links(("ust2y", "UUP", 0.43, 659)))
        measured = [e for e in g.edges if e.kind == "measured" and e.src == "ust2y"]
        assert measured
        assert "r = +0.43" in measured[0].evidence and "659" in measured[0].evidence

    def test_both_languages_are_present_on_every_edge(self):
        for edge in G.MECHANISM + G.CONTESTED:
            assert edge.label_en.strip() and edge.label_th.strip()
            assert edge.label("th") != edge.label("en")


# --------------------------------------------------------------------------- #
class TestTheWorkedExample:
    """The chain that prompted the module, checked link by link.

    dollar up <- Fed bought them back <- nobody would buy <- future is bad.
    Two of those steps are wrong and the graph has to be able to show it.
    """

    def test_the_fed_rollover_edge_says_it_cannot_support_a_price(self):
        edge = next(e for e in G.MECHANISM
                    if e.src == "fed" and e.dst == "soma_rollover")
        assert edge.kind == "documented"
        assert "non-competitive" in edge.label_en.lower()
        assert "newyorkfed.org" in edge.evidence

    def test_could_not_sell_it_is_dealer_take_up_not_the_fed(self):
        edge = next(e for e in G.MECHANISM if e.dst == "auction_dealer")
        assert edge.src == "dealers"
        assert "HIGH" in edge.label_en          # high share means weak demand

    def test_yields_to_the_dollar_is_marked_contested_not_measured(self):
        edge = next(e for e in G.CONTESTED if e.dst == "UUP")
        assert edge.kind == "contested"
        assert not edge.solid

    def test_the_step_from_an_auction_to_the_future_is_contested(self):
        edge = next(e for e in G.CONTESTED if e.dst == "future_growth")
        assert edge.kind == "contested"
        assert "+0.02" in edge.evidence        # the measured forecastability

    def test_the_opinion_node_exists_so_the_chain_can_end_honestly(self):
        g = _graph()
        assert g.nodes["future_growth"].kind == "opinion"

    def test_walking_back_from_the_dollar_reaches_the_treasury(self):
        g = _graph(links=_links(("ust2y", "UUP", 0.43, 659)))
        chains = g.why("UUP", depth=4)
        assert any(any(e.src == "treasury" for e in c) for c in chains)

    def test_a_chain_through_a_contested_link_is_labelled_contested(self):
        g = _graph(links=_links(("ust2y", "UUP", 0.43, 659)))
        for chain in g.why("UUP", depth=4):
            if any(e.kind == "contested" for e in chain):
                assert G.weakest(chain) == "contested"


# --------------------------------------------------------------------------- #
class TestArithmeticIsNotExplanation:
    def test_a_mechanical_link_enters_the_graph_as_arithmetic(self):
        g = _graph(links=_links(("ust10y", "TLT", -0.91, 659)))
        edge = next(e for e in g.edges if e.src == "ust10y" and e.dst == "TLT")
        assert edge.kind == "arithmetic"
        assert "built from" in edge.evidence

    def test_a_mechanical_link_is_never_offered_as_measured(self):
        g = _graph(links=_links(("vix", "SPY", -0.82, 750)))
        kinds = {e.kind for e in g.edges if e.src == "vix" and e.dst == "SPY"}
        assert kinds == {"arithmetic"}

    def test_a_faint_mechanical_link_is_left_out_entirely(self):
        """Below 0.5 it is neither an explanation nor a useful sanity check."""
        g = _graph(links=_links(("skew", "SPY", -0.20, 750)))
        assert not [e for e in g.edges if e.src == "skew" and e.dst == "SPY"]

    def test_a_real_cross_asset_link_survives(self):
        g = _graph(links=_links(("vix", "BTC-USD", -0.34, 750)))
        edge = next(e for e in g.edges if e.src == "vix" and e.dst == "BTC-USD")
        assert edge.kind == "measured"

    def test_chains_rank_solid_links_above_contested_ones(self):
        g = _graph(links=_links(("ust2y", "UUP", 0.43, 659)))
        chains = g.why("UUP", depth=4)
        kinds = [G.weakest(c) for c in chains]
        assert kinds.index("measured") < kinds.index("contested")


# --------------------------------------------------------------------------- #
class TestGraphShape:
    def test_an_edge_to_a_node_that_does_not_exist_is_dropped(self):
        g = G.Graph()
        g.add(G.Node("a", "market", "A", "A"))
        g.link(G.Edge("a", "ghost", "measured", "x", "x"))
        assert g.edges == []

    def test_a_cycle_does_not_produce_an_infinite_explanation(self):
        """Yields move the dollar and the dollar moves yields; both are in here."""
        g = G.Graph()
        for n in "abc":
            g.add(G.Node(n, "market", n, n))
        g.link(G.Edge("a", "b", "measured", "x", "x"))
        g.link(G.Edge("b", "c", "measured", "x", "x"))
        g.link(G.Edge("c", "a", "measured", "x", "x"))
        chains = g.why("a", depth=6)
        assert chains and all(len(c) <= 6 for c in chains)

    def test_depth_is_respected(self):
        g = _graph(links=_links(("ust2y", "UUP", 0.43, 659)))
        assert all(len(c) <= 2 for c in g.why("UUP", depth=2))

    def test_a_node_with_no_causes_yields_no_chains(self):
        g = _graph()
        assert g.why("fed") == []

    def test_markets_readings_actors_and_operations_are_all_present(self):
        g = _graph()
        kinds = {n.kind for n in g.nodes.values()}
        assert {"market", "reading", "actor", "operation", "opinion"} <= kinds

    def test_event_impact_becomes_edges_from_the_event(self):
        from printmoney.research.events import Impact

        imp = Impact(kind="fomc", ratio=1.16, tstat=3.7, events=44,
                     markets_bigger=21, markets=24,
                     by_market={"UUP": 1.58, "GLD": 1.41})
        g = _graph(impacts={"fomc": imp})
        out = [e for e in g.edges if e.src == "fomc" and e.dst == "UUP"]
        assert out and out[0].kind == "measured" and "44 events" in out[0].evidence

    def test_an_event_that_failed_its_test_contributes_no_edges(self):
        from printmoney.research.events import Impact

        weak = Impact(kind="fomc", ratio=1.01, tstat=0.4, events=44,
                      markets_bigger=10, markets=24, by_market={"UUP": 1.58})
        assert not weak.real
        g = _graph(impacts={"fomc": weak})
        assert not [e for e in g.edges if e.src == "fomc" and e.dst == "UUP"]

    def test_the_graph_survives_a_json_round_trip(self):
        g = _graph(links=_links(("ust2y", "UUP", 0.43, 659)))
        assert json.loads(json.dumps(g.to_dict())) == g.to_dict()


# --------------------------------------------------------------------------- #
class TestAuctionParsing:
    AUCTIONS = json.dumps([
        {"securityType": "Note", "securityTerm": "2-Year",
         "auctionDate": "2026-08-25T00:00:00", "bidToCoverRatio": "2.600000",
         "totalAccepted": "77870538900", "primaryDealerAccepted": "7421560000",
         "indirectBidderAccepted": "44958379400"},
        {"securityType": "Bill", "securityTerm": "4-Week",
         "auctionDate": "2026-08-25T00:00:00", "bidToCoverRatio": "3.100000",
         "totalAccepted": "1000", "primaryDealerAccepted": "900",
         "indirectBidderAccepted": "50"},
        {"securityType": "Bond", "securityTerm": "30-Year",
         "auctionDate": "2026-08-20T00:00:00", "bidToCoverRatio": "2.200000",
         "totalAccepted": "1000", "primaryDealerAccepted": "200",
         "indirectBidderAccepted": "600"},
    ])

    def test_bills_are_excluded(self):
        """Money-market rollovers say nothing about appetite for US duration."""
        rows = F.parse_auctions(self.AUCTIONS, "bid_to_cover")
        assert [r["value"] for r in rows] == [2.20, 2.60]      # no 3.10

    def test_dealer_share_is_a_percentage_of_what_was_sold(self):
        rows = {r["day"]: r["value"] for r in
                F.parse_auctions(self.AUCTIONS, "dealer")}
        assert abs(rows["2026-08-25"] - 9.53) < 0.05
        assert abs(rows["2026-08-20"] - 20.0) < 0.05

    def test_indirect_share_is_computed_the_same_way(self):
        rows = {r["day"]: r["value"] for r in
                F.parse_auctions(self.AUCTIONS, "indirect")}
        assert abs(rows["2026-08-25"] - 57.73) < 0.05

    def test_two_auctions_on_one_day_collapse_to_one_observation(self):
        both = json.dumps(json.loads(self.AUCTIONS) + [{
            "securityType": "Note", "securityTerm": "5-Year",
            "auctionDate": "2026-08-20T00:00:00", "bidToCoverRatio": "2.400000",
            "totalAccepted": "1000", "primaryDealerAccepted": "100",
            "indirectBidderAccepted": "700"}])
        rows = F.parse_auctions(both, "bid_to_cover")
        assert len(rows) == 2
        assert abs(dict((r["day"], r["value"]) for r in rows)["2026-08-20"]
                   - 2.30) < 1e-9              # the average of 2.20 and 2.40

    def test_a_redesigned_payload_parses_to_nothing(self):
        assert F.parse_auctions("[]", "bid_to_cover") == []

    def test_soma_is_reported_in_trillions(self):
        text = json.dumps({"soma": {"summary": [
            {"asOfDate": "2026-08-19", "total": "6369000000000"}]}})
        assert F.parse_soma(text) == [{"day": "2026-08-19", "value": 6.369}]


# --------------------------------------------------------------------------- #
class TestGraphPage:
    def test_the_page_is_self_contained(self, tmp_path):
        g = _graph(links=_links(("ust2y", "UUP", 0.43, 659)))
        html = write_graph_html(g, tmp_path / "g.html").read_text(encoding="utf-8")
        assert "<script src" not in html and "<link rel=\"stylesheet\"" not in html
        assert "cdn" not in html.lower().split("cdn.cboe")[0]

    def test_the_page_carries_every_node_and_edge(self, tmp_path):
        g = _graph(links=_links(("ust2y", "UUP", 0.43, 659)))
        html = write_graph_html(g, tmp_path / "g.html").read_text(encoding="utf-8")
        assert '"id": "UUP"' in html or '"id":"UUP"' in html
        for node_id in ("fed", "treasury", "auction_dealer", "soma_rollover"):
            assert node_id in html

    def test_thai_and_english_produce_different_pages(self, tmp_path):
        g = _graph()
        th = write_graph_html(g, tmp_path / "th.html", lang="th").read_text("utf-8")
        en = write_graph_html(g, tmp_path / "en.html", lang="en").read_text("utf-8")
        assert th != en
        assert "ธนาคารกลางสหรัฐ" in th
        assert "Federal Reserve" in en

    def test_no_template_placeholder_survives(self, tmp_path):
        g = _graph()
        html = write_graph_html(g, tmp_path / "g.html").read_text(encoding="utf-8")
        for marker in ("__DATA__", "__LABELS__", "__TITLE__", "__INTRO__", "__LANG__"):
            assert marker not in html

    def test_the_embedded_payload_is_valid_json(self, tmp_path):
        g = _graph(links=_links(("ust2y", "UUP", 0.43, 659)))
        html = write_graph_html(g, tmp_path / "g.html").read_text(encoding="utf-8")
        blob = html.split("const DATA = ", 1)[1].split(", L = ", 1)[0]
        payload = json.loads(blob)
        assert len(payload["nodes"]) == len(g.nodes)
        assert len(payload["edges"]) == len(g.edges)

    @pytest.mark.parametrize("lang", ["th", "en"])
    def test_the_legend_names_all_four_evidence_kinds(self, tmp_path, lang):
        from printmoney.research.i18n import t

        html = write_graph_html(_graph(), tmp_path / "g.html",
                                lang=lang).read_text(encoding="utf-8")
        for kind in G.KINDS:
            assert t(f"graph_{kind}", lang) in html
