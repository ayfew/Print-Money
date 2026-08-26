"""A causal graph of the market, where every edge says what kind of claim it is.

The request this answers is "why did the dollar move" - and then "why that", and
then "why that", the way a note in Obsidian links to the note behind it.  That is
a good way to think and a dangerous thing to automate, because a chain of
plausible sentences reads exactly like a chain of established ones.  A worked
example, the one this module was built from:

    the dollar rose
      <- because the Fed bought the bonds back
        <- because nobody else would buy them
          <- so the future must be bad

Every step sounds reasonable.  Checked against the primary sources, two of the
four are wrong:

* The Fed does not buy Treasuries back to support an auction.  Its SOMA
  rollovers are placed as *non-competitive* bids, treated as add-ons to the
  announced size, and awarded at whatever price the competitive bidding
  produces.  They cannot hold a price up.  (NY Fed, Treasury Rollovers FAQ.)
* "Nobody would buy it" is measurable and it is not the Fed - it is the share
  taken by primary dealers, who are obliged to bid and therefore absorb what
  nobody else wanted.  That number is published for every auction.
* Weak demand pushing yields up does not have a settled sign for the dollar.
  Higher yields attract capital, which lifts it; a rising fiscal risk premium
  pushes it the other way.  Both happen, and which wins is contested.

So the graph keeps the chain, and labels each link with what supports it:

    ``arithmetic``   true by construction. TLT against the ten-year yield is
                     -0.91 because TLT *is* long-dated Treasuries.
    ``documented``   an institution documents the mechanism and the edge carries
                     the URL. The Fed sets the target range; dealers must bid.
    ``measured``     this project measured it, and the edge carries r and n.
    ``contested``    widely repeated, and the evidence does not settle it. Kept
                     visible on purpose, because deleting it would not stop
                     anyone believing it.

An edge with no support does not exist.  There is no fifth category for "seems
right", which is the whole difference between this and a story.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import sources

#: Edge kinds ordered by how well established each is, most certain first. A
#: chain is only as good as its least-established link, which is what
#: :func:`weakest` reads off this list.
KINDS = ("arithmetic", "documented", "measured", "contested")

#: ...and a different order, for deciding which chain to show a reader first.
#:
#: The two are not the same and conflating them is a real bug, not a nicety.
#: Arithmetic is the *most* certain kind of link and the *least* informative
#: one - "bonds fell because yields rose" cannot be wrong and cannot help - so
#: it sits at the top of KINDS and near the bottom here. Contested is last in
#: both, being neither settled nor useful.
USEFULNESS = ("documented", "measured", "arithmetic", "contested")


@dataclass(frozen=True)
class Node:
    id: str
    kind: str            # market | reading | actor | operation
    label_en: str
    label_th: str
    source: str = ""     # id in sources.REGISTRY, where one applies

    def label(self, lang: str = "th") -> str:
        return self.label_th if lang == "th" else self.label_en

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label_en": self.label_en,
                "label_th": self.label_th, "source": self.source}


@dataclass(frozen=True)
class Edge:
    """``src`` acts on ``dst``, and ``kind`` says how well that is established."""

    src: str
    dst: str
    kind: str
    label_en: str
    label_th: str
    evidence: str = ""       # r and n, or a URL, or why it is contested
    source: str = ""

    def label(self, lang: str = "th") -> str:
        return self.label_th if lang == "th" else self.label_en

    @property
    def solid(self) -> bool:
        return self.kind in ("arithmetic", "documented", "measured")

    def to_dict(self) -> dict[str, Any]:
        return {"src": self.src, "dst": self.dst, "kind": self.kind,
                "label_en": self.label_en, "label_th": self.label_th,
                "evidence": self.evidence, "source": self.source,
                "solid": self.solid}


# --------------------------------------------------------------------------- #
# The curated half: actors and operations, and the mechanism edges between them.
# Every one of these was checked against the institution's own page before it was
# written down, and the URL is on the edge rather than in a comment so a reader
# can do the same.
ACTORS: list[Node] = [
    Node("fed", "actor", "Federal Reserve", "ธนาคารกลางสหรัฐ (เฟด)", "fed"),
    Node("treasury", "actor", "US Treasury", "กระทรวงการคลังสหรัฐ", "treasury"),
    Node("dealers", "actor", "primary dealers", "ไพรมารีดีลเลอร์", "treasurydirect"),
    Node("foreign_cb", "actor", "foreign central banks",
         "ธนาคารกลางต่างประเทศ", "treasurydirect"),
]

OPERATIONS: list[Node] = [
    Node("fomc", "operation", "FOMC rate decision", "เฟดประกาศดอกเบี้ย", "fed"),
    Node("auction", "operation", "Treasury auction", "การประมูลพันธบัตร",
         "treasurydirect"),
    Node("soma_rollover", "operation", "SOMA rollover",
         "การต่ออายุพันธบัตรของเฟด (SOMA)", "nyfed"),
]

MECHANISM: list[Edge] = [
    Edge("fed", "fomc", "documented",
         "sets the target range at eight scheduled meetings a year",
         "กำหนดกรอบดอกเบี้ยนโยบาย ปีละ 8 ครั้งตามกำหนดการ",
         "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", "fed"),
    Edge("fomc", "effr", "documented",
         "the effective rate settles inside the range the decision sets",
         "อัตราดอกเบี้ยที่เกิดขึ้นจริงจะอยู่ในกรอบที่ประกาศ",
         "https://www.newyorkfed.org/markets/reference-rates/effr", "nyfed"),
    Edge("effr", "ust2y", "documented",
         "the 2-year prices the expected path of the policy rate",
         "พันธบัตร 2 ปีสะท้อนเส้นทางดอกเบี้ยนโยบายที่ตลาดคาด",
         "the free stand-in for CME FedWatch, whose data is a paid feed", "treasury"),
    Edge("treasury", "auction", "documented",
         "issues debt on a published auction calendar",
         "ออกพันธบัตรตามปฏิทินประมูลที่ประกาศล่วงหน้า",
         "https://www.treasurydirect.gov/auctions/announcements-data-results/",
         "treasurydirect"),
    Edge("auction", "auction_btc", "documented",
         "total bids over amount sold; higher is more demand",
         "ยอดเสนอซื้อหารด้วยยอดขาย ยิ่งสูงยิ่งมีคนอยากได้",
         "https://www.treasurydirect.gov/auctions/announcements-data-results/",
         "treasurydirect"),
    Edge("dealers", "auction_dealer", "documented",
         "obliged to bid, so they absorb whatever nobody else wanted - a HIGH "
         "share means weak demand",
         "มีหน้าที่ต้องเสนอซื้อ จึงรับส่วนที่ไม่มีใครเอาไว้ — ตัวเลข*สูง*แปลว่า"
         "ขายไม่ออก ไม่ใช่ขายดี",
         "https://www.newyorkfed.org/markets/primarydealers", "treasurydirect"),
    Edge("foreign_cb", "auction_indirect", "documented",
         "indirect bidders are largely foreign official accounts",
         "ผู้เสนอซื้อทางอ้อมส่วนใหญ่คือหน่วยงานทางการของต่างประเทศ",
         "https://www.treasurydirect.gov/auctions/announcements-data-results/",
         "treasurydirect"),
    Edge("auction_btc", "ust10y", "documented",
         "an auction that clears poorly clears at a higher yield",
         "ประมูลไม่ดี = ต้องให้ผลตอบแทนสูงขึ้นถึงจะขายหมด",
         "https://www.crfb.org/blogs/weak-auctions-underscore-risks-our-growing-debt-burden",
         "treasurydirect"),
    Edge("fed", "soma_rollover", "documented",
         "reinvests maturing holdings via NON-COMPETITIVE bids, awarded at the "
         "price competitive bidding produces - this cannot support an auction",
         "ลงทุนต่อในพันธบัตรที่ครบอายุ ด้วยการเสนอซื้อแบบ*ไม่แข่งราคา* "
         "ได้ราคาตามที่ตลาดประมูลกันเอง — จึงพยุงราคาประมูลไม่ได้",
         "https://www.newyorkfed.org/markets/treasury-rollover-faq", "nyfed"),
    Edge("soma_rollover", "soma", "documented",
         "what the Fed still holds after rollovers and runoff",
         "ยอดคงเหลือที่เฟดถืออยู่ หลังการต่ออายุและการปล่อยให้ครบอายุ",
         "https://markets.newyorkfed.org/api/soma/summary.json", "nyfed"),
]

#: The links people repeat that the evidence does not settle. Shown, and labelled.
CONTESTED: list[Edge] = [
    Edge("ust10y", "UUP", "contested",
         "higher yields attract capital and lift the dollar, while a rising "
         "fiscal risk premium pushes it down - both happen and neither wins "
         "reliably",
         "ผลตอบแทนสูงขึ้นดึงเงินเข้า ทำให้ดอลลาร์แข็ง — แต่ความเสี่ยงทางการคลัง"
         "ที่สูงขึ้นกดให้อ่อน สองแรงนี้เกิดพร้อมกัน และไม่มีฝั่งไหนชนะเสมอ",
         "measured here at r = +0.34 for the 10-year, which is real but is the "
         "net of two opposing forces rather than evidence for either",
         "macro"),
    Edge("auction_dealer", "future_growth", "contested",
         "weak auctions are read as a signal about the fiscal outlook, but the "
         "step from one auction to a forecast is not something this project can "
         "support",
         "คนตีความว่าประมูลไม่ดี = อนาคตการคลังแย่ แต่ก้าวจากการประมูลครั้งเดียว"
         "ไปสู่การทำนายอนาคต เป็นก้าวที่โปรเจกต์นี้พิสูจน์ให้ไม่ได้",
         "return forecastability measured at r = +0.02 over ten years and "
         "twenty-four markets", "study"),
]

#: The one node that is deliberately an opinion, so the graph has somewhere
#: honest to put the end of the chain rather than pretending it stops earlier.
TERMINAL: list[Node] = [
    Node("future_growth", "opinion", "the future outlook",
         "แนวโน้มอนาคต", "study"),
]


# --------------------------------------------------------------------------- #
@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add(self, node: Node) -> None:
        self.nodes.setdefault(node.id, node)

    def link(self, edge: Edge) -> None:
        if edge.src in self.nodes and edge.dst in self.nodes:
            self.edges.append(edge)

    def into(self, node_id: str) -> list[Edge]:
        """Edges pointing at this node: the answers to "why did this move"."""
        return [e for e in self.edges if e.dst == node_id]

    def out_of(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.src == node_id]

    def why(self, node_id: str, *, depth: int = 4) -> list[list[Edge]]:
        """Every chain of causes leading into a node, strongest links first.

        Depth-first, refusing to revisit a node inside one chain so a cycle
        cannot produce an infinite explanation - which the rates complex would
        otherwise do immediately, since yields and the dollar each move the
        other.
        """
        out: list[list[Edge]] = []

        def walk(current: str, path: list[Edge], seen: set[str]) -> None:
            if len(path) >= depth:
                out.append(path)
                return
            parents = sorted(self.into(current),
                             key=lambda e: USEFULNESS.index(e.kind))
            parents = [e for e in parents if e.src not in seen]
            if not parents:
                if path:
                    out.append(path)
                return
            for edge in parents:
                walk(edge.src, path + [edge], seen | {edge.src})

        walk(node_id, [], {node_id})
        # Rank by the least useful link in each chain, best first - so a
        # documented mechanism outranks a measured correlation, which outranks a
        # restatement, which outranks something nobody has settled.
        return sorted(out, key=lambda p: (max(USEFULNESS.index(e.kind) for e in p),
                                          len(p)))

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": [n.to_dict() for n in self.nodes.values()],
                "edges": [e.to_dict() for e in self.edges]}


# --------------------------------------------------------------------------- #
def build(*, feeds: dict[str, Any] | None = None, links: Any = None,
          impacts: dict[str, Any] | None = None,
          universe: Sequence[tuple[str, str]] | None = None) -> Graph:
    """Assemble the graph from the curated mechanism plus everything measured."""
    from .data import UNIVERSE
    from .i18n import MARKET_TH

    g = Graph()
    for node in ACTORS + OPERATIONS + TERMINAL:
        g.add(node)

    # Readings become nodes whether or not they loaded today; the mechanism
    # edges between them are documented facts and do not depend on a feed being
    # reachable this morning.
    from .feeds import SPECS

    for key, spec in SPECS.items():
        g.add(Node(key, "reading", spec["name"], _reading_th(key, spec["name"]),
                   spec["source"]))
    g.add(Node("curve", "reading", "2s10s spread", "ส่วนต่าง 2 ปี–10 ปี", "curve"))

    for symbol, name in (universe or UNIVERSE):
        g.add(Node(symbol, "market", name, MARKET_TH.get(symbol, name), "yahoo"))

    for edge in MECHANISM + CONTESTED:
        g.link(edge)

    if links is not None:
        for link in links.real():
            g.link(Edge(
                src=link.feed, dst=link.symbol, kind="measured",
                label_en=f"moves {link.direction} it ({link.strength})",
                label_th=("เคลื่อนไปทางเดียวกัน" if link.r > 0 else "เคลื่อนสวนทาง")
                         + f" (ความสัมพันธ์{_strength_th(link.strength)})",
                evidence=f"r = {link.r:+.2f} over {link.n} days, same-day",
                source="macro",
            ))
        for link in links.links:
            if link.mechanical and abs(link.r) >= 0.5:
                g.link(Edge(
                    src=link.feed, dst=link.symbol, kind="arithmetic",
                    label_en="the same fact stated twice",
                    label_th="เป็นข้อเท็จจริงเดียวกันพูดสองรอบ",
                    evidence=f"r = {link.r:+.2f}, but the instrument is built "
                             "from the reading",
                    source="macro",
                ))

    if impacts:
        for kind, impact in impacts.items():
            if not getattr(impact, "real", False):
                continue
            node = "fomc" if kind == "fomc" else "payrolls"
            if node not in g.nodes:
                g.add(Node("payrolls", "operation", "US jobs report",
                           "ตัวเลขการจ้างงานสหรัฐ", "bls"))
            for symbol, ratio in impact.touches(5):
                g.link(Edge(
                    src=node, dst=symbol, kind="measured",
                    label_en=f"moves it {ratio:.2f}x an ordinary day",
                    label_th=f"ทำให้ขยับ {ratio:.2f} เท่าของวันปกติ",
                    evidence=f"{impact.events} events, {ratio:.2f}x",
                    source="events",
                ))
    return g


def _reading_th(key: str, fallback: str) -> str:
    from .i18n import STRINGS

    return STRINGS["th"].get(f"feed_{key}", fallback)


def _strength_th(word: str) -> str:
    return {"strong": "แรง", "moderate": "ปานกลาง", "weak": "อ่อน"}.get(word, word)


def cited_by(graph: Graph, path: Sequence[Edge]) -> list[sources.Source]:
    ids = [e.source for e in path if e.source]
    ids += [graph.nodes[e.src].source for e in path
            if e.src in graph.nodes and graph.nodes[e.src].source]
    return sources.cited([i for i in ids if i])


def weakest(path: Iterable[Edge]) -> str:
    kinds = [e.kind for e in path]
    return max(kinds, key=KINDS.index) if kinds else "measured"
