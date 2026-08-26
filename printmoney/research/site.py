"""One page: today's decision, the map behind it, and the evidence under both.

There were two pages and they were the same argument told twice.  The brief said
"gold fell, real yields rose"; the map said "gold moves against real yields at
r = -0.30"; the scorecard said how often claims like that have held up.  A reader
had to hold three tabs open and join them by hand, which is exactly the work the
project keeps promising to do for them.

So it is one file with three views and, more importantly, one set of links
between them.  Every market and every reading named in today's brief is a chip
that jumps into the map with that node selected and its edges lit.  That is the
whole point of building the graph in the first place: a claim in the brief and
the evidence for it should be one click apart, not one tab apart.

    วันนี้     the decision, unchanged, with every name clickable
    แผนที่     the causal map, arriving focused on whatever was clicked
    หลักฐาน    the committed numbers the other two views quote

Self-contained by the same rule as everything else: no CDN, no library, one file
that still works when a package host is down.  The layout is a plain force
simulation, which at forty-odd nodes is arithmetic rather than a dependency.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..util import DATA_DIR, fmt_usd, read_json
from .graph import Graph
from .graphview import _payload
from .i18n import market_name, norm, render_note, t

#: Which committed artefacts the evidence view reads, and what each one is for.
EVIDENCE = (
    ("scorecard.json", "ev_scorecard"),
    ("indicators.json", "ev_indicators"),
    ("contamination.json", "ev_contamination"),
    ("impacts.json", "ev_impacts"),
    ("macro.json", "ev_macro"),
)


def _evidence() -> dict[str, Any]:
    """Everything under data/, or as much of it as exists."""
    out: dict[str, Any] = {}
    for name, _key in EVIDENCE:
        blob = read_json(DATA_DIR / name, default=None)
        if blob:
            out[name.removesuffix(".json")] = blob
    return out


def _brief_payload(item: Any, lang: str) -> dict[str, Any]:
    """The decision, rendered to strings, with the node ids kept alongside.

    Rendering happens here rather than in the browser because the wording is
    assembled from numbers in :mod:`i18n` and shipping the assembler to
    JavaScript would mean maintaining it twice.  What the page does need is the
    *ids*, so a sentence can be turned into a link.
    """
    brief = getattr(item, "brief", item)
    decision = getattr(item, "decision", None)
    names = {l.symbol: l.name for l in brief.lines}

    def one(note: Any) -> dict[str, Any]:
        chips = list(getattr(note, "symbols", ()) or ())
        for key in ("label", "driver"):
            raw = (note.params or {}).get(key)
            # params carry i18n keys for readings; the graph node id is the key.
            if raw and raw not in chips:
                chips.append(raw)
        return {"text": render_note(note, lang, names), "nodes": chips,
                "source": note.source}

    sections = []
    if decision is not None:
        for key, notes in (("hdr_changed", decision.changed),
                           ("hdr_why", decision.why),
                           ("hdr_context", decision.context),
                           ("hdr_watch", decision.watch),
                           ("hdr_avoid", decision.avoid),
                           ("hdr_ignore", decision.ignore)):
            if notes:
                sections.append({"title": t(key, lang), "kind": key[4:],
                                 "notes": [one(n) for n in notes]})

    rows = [{"id": l.symbol, "name": market_name(l.symbol, l.name, lang),
             "last": round(l.last, 2), "day": round(l.day, 5),
             "week": round(l.week, 5), "month": round(l.month, 5),
             "vol": round(l.vol_annual, 4), "risk": l.risk}
            for l in sorted(brief.lines, key=lambda x: -abs(x.day))]

    carry = brief.carry or {}
    return {
        "stamp": brief.generated_at.strftime("%A %d %B %Y · %H:%M UTC"),
        "day": brief.generated_at.strftime("%Y-%m-%d"),
        "ok": brief.ok,
        "focus": one(decision.focus) if decision and decision.focus else None,
        "no_changes": bool(decision and not decision.changed),
        "sections": sections,
        "markets": rows,
        "score": getattr(item, "score", None),
        "carry": {"rate": carry.get("basket_net_annual"),
                  "monthly": fmt_usd(carry.get("monthly_usd", 0.0)),
                  "capital": fmt_usd(carry.get("capital", 0.0))} if carry else None,
        "sources": [s.to_dict() for s in __import__(
            "printmoney.research.sources", fromlist=["cited"]).cited(
                decision.source_ids)] if decision else [],
    }


def write_site(item: Any, graph: Graph, path: str | Path, *,
               lang: str = "th") -> Path:
    """Build the one page. ``item`` is a Morning; ``graph`` is the causal map."""
    lang = norm(lang)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    strings = {k: t(k, lang) for k in (
        "site_title", "nav_today", "nav_map", "nav_evidence", "graph_intro",
        "graph_search", "graph_click", "graph_arithmetic", "graph_documented",
        "graph_measured", "graph_contested", "graph_evidence", "graph_causes",
        "graph_effects", "graph_none", "graph_reset", "graph_legend_kind",
        "hdr_sources", "hdr_score", "score_line", "no_changes", "th_where",
        "th_market", "th_day", "th_week", "th_month", "th_vol", "why_note",
        "page_footer", "ev_scorecard", "ev_indicators", "ev_contamination",
        "ev_impacts", "ev_macro", "ev_intro", "ev_none", "chip_hint",
    )}

    payload = json.dumps({
        "lang": lang,
        "brief": _brief_payload(item, lang),
        "graph": _payload(graph, lang),
        "evidence": _evidence(),
        "labels": strings,
    }, ensure_ascii=False, default=str)

    html = _TEMPLATE.replace("__PAYLOAD__", payload) \
                    .replace("__LANG__", lang) \
                    .replace("__TITLE__", strings["site_title"])
    out.write_text(html, encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
_TEMPLATE = r"""<!doctype html><html lang="__LANG__"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<style>
:root{--bg:#fbfbfb;--panel:#fff;--ink:#0a0a0a;--dim:#6b7280;--line:#e8e8e8;
 --up:#0f7a4d;--down:#c0392f;
 --arith:#9aa0ab;--doc:#2563eb;--meas:#0f7a4d;--cont:#c0392f;
 --market:#0f7a4d;--reading:#2563eb;--actor:#7c3aed;--operation:#b45309;--opinion:#c0392f;}
@media (prefers-color-scheme:dark){:root{--bg:#0a0a0a;--panel:#151515;--ink:#fafafa;
 --dim:#9aa0ab;--line:#282828;--up:#4ade80;--down:#f87171;
 --arith:#6b7280;--doc:#60a5fa;--meas:#4ade80;--cont:#f87171;
 --market:#4ade80;--reading:#60a5fa;--actor:#a78bfa;--operation:#fbbf24;--opinion:#f87171;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-text-size-adjust:100%}
header{position:sticky;top:0;z-index:5;background:var(--bg);
 border-bottom:1px solid var(--line);padding:12px 16px 0}
.hd{max-width:900px;margin:0 auto}
h1{font-size:16px;margin:0;letter-spacing:-.01em}
.stamp{color:var(--dim);font-size:12.5px;margin:2px 0 10px}
nav{display:flex;gap:2px;max-width:900px;margin:0 auto}
nav button{appearance:none;background:none;border:0;border-bottom:2px solid transparent;
 color:var(--dim);font:inherit;font-size:13.5px;padding:8px 14px;cursor:pointer}
nav button.on{color:var(--ink);border-bottom-color:var(--ink);font-weight:600}
main{max-width:900px;margin:0 auto;padding:20px 16px 60px}
section{display:none} section.on{display:block}
h2{font-size:10.5px;text-transform:uppercase;letter-spacing:.12em;color:var(--dim);
 margin:26px 0 8px}
h2:first-child{margin-top:0}
.verdict{background:var(--panel);border:1px solid var(--line);border-radius:12px;
 padding:16px 18px;font-size:16px;font-weight:600;line-height:1.5}
ul{margin:0;padding-left:20px} li{margin:8px 0;color:var(--dim)}
li.watch{color:var(--up);font-weight:600} li.avoid{color:var(--down);font-weight:600}
li.changed{color:var(--ink);font-weight:600} li.ignore{opacity:.75}
li.context{color:var(--dim);font-variant-numeric:tabular-nums}
.chip{display:inline-block;margin:2px 3px 0 0;padding:1px 8px;border-radius:999px;
 border:1px solid var(--line);background:var(--panel);font-size:11px;color:var(--dim);
 cursor:pointer;font-weight:500}
.chip:hover{border-color:var(--ink);color:var(--ink)}
table{width:100%;border-collapse:collapse;font-size:13.5px;
 font-variant-numeric:tabular-nums}
th{text-align:right;font-size:10px;text-transform:uppercase;letter-spacing:.1em;
 color:var(--dim);padding:6px 4px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
td{text-align:right;padding:7px 4px;border-bottom:1px solid var(--line)}
td.nm{cursor:pointer} td.nm:hover{text-decoration:underline}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--dim)}
.muted{color:var(--dim);font-size:13.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card .big{font-size:24px;font-weight:600;letter-spacing:-.02em;
 font-variant-numeric:tabular-nums}
.card .lbl{font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--dim)}
.card .sub{font-size:12px;color:var(--dim);margin-top:4px}
#stage{position:relative;height:min(62vh,540px);border:1px solid var(--line);
 border-radius:12px;overflow:hidden;background:var(--panel)}
canvas{display:block;width:100%;height:100%;cursor:grab}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--dim);margin-top:10px}
.legend div{display:flex;align-items:center;gap:7px}
.swatch{width:20px;border-top-width:2px;border-top-style:solid}
.dot{width:9px;height:9px;border-radius:50%}
input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;
 background:var(--bg);color:var(--ink);font:inherit;font-size:13px;margin-bottom:10px}
.edge{border-left:2px solid var(--line);padding:2px 0 2px 10px;margin:10px 0;font-size:13px}
.edge.arithmetic{border-color:var(--arith)} .edge.documented{border-color:var(--doc)}
.edge.measured{border-color:var(--meas)} .edge.contested{border-color:var(--cont)}
.edge b{font-weight:600} .edge .lbl2{color:var(--dim);display:block;margin-top:2px}
.edge .ev{display:block;margin-top:3px;font-size:11.5px;color:var(--dim);
 word-break:break-word}
.edge a{color:var(--doc)}
.tag{display:inline-block;padding:1px 6px;border-radius:999px;border:1px solid var(--line);
 font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
footer{max-width:900px;margin:0 auto;padding:16px;border-top:1px solid var(--line);
 color:var(--dim);font-size:12px}
ul.srcs{list-style:none;padding-left:0}
ul.srcs a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--line)}
</style></head><body>
<header><div class="hd"><h1 id="ttl"></h1><div class="stamp" id="stamp"></div></div>
<nav id="nav"></nav></header>
<main>
  <section id="v-today" class="on"></section>
  <section id="v-map">
    <input id="q" type="search">
    <div id="stage"><canvas id="c"></canvas></div>
    <div class="legend" id="lg-edge"></div>
    <div class="legend" id="lg-node"></div>
    <div id="detail"></div>
  </section>
  <section id="v-evidence"></section>
</main>
<footer id="ft"></footer>
<script>
const D = __PAYLOAD__, L = D.labels;
const esc = s => { const d = document.createElement("div"); d.textContent = s ?? "";
                   return d.innerHTML; };
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const pct = (v, dp = 2) => (v == null ? "-" :
  '<span class="' + (v > 0 ? "up" : v < 0 ? "down" : "flat") + '">' +
  (v * 100).toFixed(dp) + "%</span>");

document.getElementById("ttl").textContent = L.site_title;
document.getElementById("stamp").textContent = D.brief.stamp;
document.getElementById("ft").innerHTML = L.page_footer;

/* --------------------------------------------------- graph data first ---- */
/* Built before anything renders: the Today view turns names into links by
   looking each one up here, and a hoisted-but-undefined index is exactly the
   kind of ordering bug that only shows up in a browser. */
const cv = document.getElementById("c"), ctx = cv.getContext("2d");
const nodes = D.graph.nodes.map((n, i) => ({...n,
  x: Math.cos(i / D.graph.nodes.length * 6.283) * 200 + (Math.random() - .5) * 40,
  y: Math.sin(i / D.graph.nodes.length * 6.283) * 200 + (Math.random() - .5) * 40,
  vx: 0, vy: 0}));
const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
const edges = D.graph.edges.filter(e => byId[e.src] && byId[e.dst])
                           .map(e => ({...e, a: byId[e.src], b: byId[e.dst]}));
for (const n of nodes) n.r = 5 + Math.min(9, Math.sqrt(n.degree) * 2.6);
const EDGE_COLOR = {arithmetic: "--arith", documented: "--doc",
                    measured: "--meas", contested: "--cont"};

/* ---------------------------------------------------------------- nav ---- */
const VIEWS = [["today", L.nav_today], ["map", L.nav_map],
               ["evidence", L.nav_evidence]];
document.getElementById("nav").innerHTML = VIEWS.map(([k, label], i) =>
  '<button data-v="' + k + '"' + (i ? "" : ' class="on"') + ">" + esc(label) +
  "</button>").join("");
function show(view){
  for (const b of document.querySelectorAll("nav button"))
    b.classList.toggle("on", b.dataset.v === view);
  for (const s of document.querySelectorAll("main section"))
    s.classList.toggle("on", s.id === "v-" + view);
  if (view === "map"){ resize(); alpha = Math.max(alpha, .3); }
  window.scrollTo({top: 0});
}
document.getElementById("nav").addEventListener("click", ev => {
  if (ev.target.dataset.v) show(ev.target.dataset.v);
});

/* -------------------------------------------------------------- today ---- */
function chips(nodes){
  return (nodes || []).filter(n => byId[n])
    .map(n => '<span class="chip" data-go="' + esc(n) + '">' +
              esc(byId[n].label) + "</span>").join("");
}
(function renderToday(){
  const b = D.brief, out = [];
  if (b.focus)
    out.push('<div class="verdict">' + esc(b.focus.text) + "</div>" +
             '<div style="margin-top:8px">' + chips(b.focus.nodes) + "</div>");
  for (const s of b.sections){
    out.push("<h2>" + esc(s.title) + "</h2><ul>" + s.notes.map(n =>
      '<li class="' + s.kind + '">' + esc(n.text) +
      (n.nodes.length ? '<br>' + chips(n.nodes) : "") + "</li>").join("") + "</ul>");
    if (s.kind === "why")
      out.push('<p class="muted">' + esc(L.why_note) + "</p>");
  }
  if (b.no_changes) out.push('<p class="muted">' + esc(L.no_changes) + "</p>");
  if (b.score && b.score.n)
    out.push("<h2>" + esc(L.hdr_score) + '</h2><p class="muted">' +
      esc(L.score_line.replace("{rate}", (b.score.rate * 100).toFixed(0) + "%")
                      .replace("{n}", b.score.n)) + "</p>");

  out.push("<h2>" + esc(L.th_where) + "</h2><table><tr><th>" +
    [L.th_market, L.th_day, L.th_week, L.th_month, L.th_vol].map(esc).join("</th><th>") +
    "</th></tr>" + b.markets.map(m =>
      '<tr><td class="nm" data-go="' + esc(m.id) + '">' + esc(m.name) + "</td><td>" +
      pct(m.day) + "</td><td>" + pct(m.week) + "</td><td>" + pct(m.month) +
      "</td><td>" + (m.vol * 100).toFixed(0) + "%</td></tr>").join("") + "</table>");

  if (b.sources.length)
    out.push("<h2>" + esc(L.hdr_sources) + '</h2><ul class="srcs">' +
      b.sources.map(s => '<li><a href="' + esc(s.url) + '" rel="noopener">' +
        esc(s.name) + '</a> <span class="tag">' + esc(s.tier_name) +
        "</span></li>").join("") + "</ul>");
  out.push('<p class="muted">' + esc(L.chip_hint) + "</p>");
  document.getElementById("v-today").innerHTML = out.join("");
})();

document.addEventListener("click", ev => {
  const id = ev.target.dataset && ev.target.dataset.go;
  if (!id || !byId[id]) return;
  show("map"); select(id);
  const n = byId[id]; pan = {x: -n.x * zoom, y: -n.y * zoom};
  alpha = Math.max(alpha, .2);
});

/* ---------------------------------------------------------------- map ---- */
let W = 0, H = 0, dpr = 1, sel = null, hover = null, drag = null;
let pan = {x: 0, y: 0}, zoom = 1, alpha = 1;

function resize(){
  const box = document.getElementById("stage").getBoundingClientRect();
  if (!box.width) return;
  dpr = window.devicePixelRatio || 1;
  W = box.width; H = box.height;
  cv.width = W * dpr; cv.height = H * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener("resize", () => { resize(); alpha = Math.max(alpha, .3); });

function step(){
  if (alpha < .002) return;
  for (const n of nodes){ if (n !== drag){ n.vx -= n.x * .0016; n.vy -= n.y * .0016; } }
  for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++){
    const a = nodes[i], b = nodes[j];
    const dx = b.x - a.x, dy = b.y - a.y; let d2 = dx * dx + dy * dy || .01;
    if (d2 > 90000) continue;
    const f = 2600 / d2, d = Math.sqrt(d2);
    a.vx -= dx / d * f; a.vy -= dy / d * f; b.vx += dx / d * f; b.vy += dy / d * f;
  }
  for (const e of edges){
    const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y, d = Math.hypot(dx, dy) || .01;
    const f = (d - 110) * .012;
    e.a.vx += dx / d * f; e.a.vy += dy / d * f;
    e.b.vx -= dx / d * f; e.b.vy -= dy / d * f;
  }
  for (const n of nodes){
    if (n === drag){ n.vx = n.vy = 0; continue; }
    n.vx *= .86; n.vy *= .86; n.x += n.vx * alpha; n.y += n.vy * alpha;
  }
  alpha *= .994;
}
const near = id => sel && (sel === id ||
  edges.some(e => (e.src === sel && e.dst === id) || (e.dst === sel && e.src === id)));

function draw(){
  if (!W) return;
  ctx.clearRect(0, 0, W, H);
  ctx.save(); ctx.translate(W / 2 + pan.x, H / 2 + pan.y); ctx.scale(zoom, zoom);
  for (const e of edges){
    const lit = !sel || e.src === sel || e.dst === sel;
    ctx.globalAlpha = lit ? (e.kind === "arithmetic" ? .45 : .8) : .07;
    ctx.strokeStyle = css(EDGE_COLOR[e.kind]);
    ctx.lineWidth = e.kind === "contested" ? 1.8 : 1.2;
    ctx.setLineDash(e.kind === "contested" ? [5, 4] :
                    e.kind === "arithmetic" ? [2, 3] : []);
    ctx.beginPath(); ctx.moveTo(e.a.x, e.a.y); ctx.lineTo(e.b.x, e.b.y); ctx.stroke();
    if (lit){
      const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y, d = Math.hypot(dx, dy) || 1;
      const tx = e.b.x - dx / d * (e.b.r + 4), ty = e.b.y - dy / d * (e.b.r + 4);
      const ang = Math.atan2(dy, dx);
      ctx.setLineDash([]); ctx.beginPath(); ctx.moveTo(tx, ty);
      ctx.lineTo(tx - 7 * Math.cos(ang - .4), ty - 7 * Math.sin(ang - .4));
      ctx.moveTo(tx, ty);
      ctx.lineTo(tx - 7 * Math.cos(ang + .4), ty - 7 * Math.sin(ang + .4));
      ctx.stroke();
    }
  }
  ctx.setLineDash([]);
  for (const n of nodes){
    const lit = !sel || near(n.id);
    ctx.globalAlpha = lit ? 1 : .12;
    ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, 6.2832);
    ctx.fillStyle = css("--" + n.kind); ctx.fill();
    if (n.id === sel || n.id === hover){
      ctx.lineWidth = 2.5; ctx.strokeStyle = css("--ink"); ctx.stroke();
    }
    if (lit && (zoom > .75 || n.degree > 3 || n.id === sel)){
      ctx.fillStyle = css("--ink");
      ctx.font = (n.id === sel ? "600 " : "") + "11px system-ui,sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(n.label, n.x, n.y - n.r - 5);
    }
  }
  ctx.restore(); ctx.globalAlpha = 1;
}
function frame(){ step(); draw(); requestAnimationFrame(frame); }

const toWorld = (px, py) => ({x: (px - W / 2 - pan.x) / zoom,
                              y: (py - H / 2 - pan.y) / zoom});
function hit(px, py){
  const p = toWorld(px, py); let best = null, bd = 1e9;
  for (const n of nodes){
    const d = Math.hypot(n.x - p.x, n.y - p.y);
    if (d < n.r + 9 && d < bd){ best = n; bd = d; }
  }
  return best;
}
let panning = false, last = {x: 0, y: 0};
cv.addEventListener("pointerdown", ev => {
  const n = hit(ev.offsetX, ev.offsetY);
  if (n){ drag = n; select(n.id); alpha = Math.max(alpha, .3); }
  else { panning = true; last = {x: ev.clientX, y: ev.clientY}; select(null); }
  cv.setPointerCapture(ev.pointerId);
});
cv.addEventListener("pointermove", ev => {
  if (drag){ const p = toWorld(ev.offsetX, ev.offsetY);
             drag.x = p.x; drag.y = p.y; alpha = Math.max(alpha, .15); }
  else if (panning){ pan.x += ev.clientX - last.x; pan.y += ev.clientY - last.y;
                     last = {x: ev.clientX, y: ev.clientY}; }
  else { const n = hit(ev.offsetX, ev.offsetY); hover = n ? n.id : null;
         cv.style.cursor = n ? "pointer" : "grab"; }
});
const rel = () => { drag = null; panning = false; };
cv.addEventListener("pointerup", rel); cv.addEventListener("pointercancel", rel);
cv.addEventListener("wheel", ev => {
  ev.preventDefault();
  zoom = Math.min(3, Math.max(.3, zoom * (ev.deltaY < 0 ? 1.12 : .89)));
}, {passive: false});

function evidenceHtml(ev){
  if (!ev) return "";
  const m = String(ev).match(/https?:\/\/\S+/);
  return m ? '<a href="' + esc(m[0]) + '" target="_blank" rel="noopener">' +
             esc(m[0].replace(/^https?:\/\//, "").slice(0, 46)) + "</a>" : esc(ev);
}
function select(id){
  sel = id;
  const box = document.getElementById("detail");
  if (!id){ box.innerHTML = '<p class="muted">' + esc(L.graph_click) + "</p>"; return; }
  const n = byId[id];
  const row = (e, other) => '<div class="edge ' + e.kind + '"><b>' +
    esc(byId[other] ? byId[other].label : other) + '</b> <span class="tag">' +
    esc(L["graph_" + e.kind]) + '</span><span class="lbl2">' + esc(e.label) +
    "</span>" + (e.evidence ? '<span class="ev">' + evidenceHtml(e.evidence) +
    "</span>" : "") + "</div>";
  const into = edges.filter(e => e.dst === id), out = edges.filter(e => e.src === id);
  box.innerHTML = "<h2>" + esc(n.label) + "</h2>" +
    "<h2>" + esc(L.graph_causes) + "</h2>" +
    (into.length ? into.map(e => row(e, e.src)).join("")
                 : '<p class="muted">' + esc(L.graph_none) + "</p>") +
    "<h2>" + esc(L.graph_effects) + "</h2>" +
    (out.length ? out.map(e => row(e, e.dst)).join("")
                : '<p class="muted">' + esc(L.graph_none) + "</p>");
}
document.getElementById("q").placeholder = L.graph_search;
document.getElementById("q").addEventListener("input", ev => {
  const q = ev.target.value.trim().toLowerCase();
  if (!q) return select(null);
  const n = nodes.find(x => x.label.toLowerCase().includes(q) ||
                            x.id.toLowerCase().includes(q));
  if (n){ select(n.id); pan = {x: -n.x * zoom, y: -n.y * zoom}; }
});
document.getElementById("lg-edge").innerHTML =
  ["arithmetic", "documented", "measured", "contested"].map(k =>
    '<div><span class="swatch" style="border-color:' + css(EDGE_COLOR[k]) +
    ";border-top-style:" + (k === "contested" ? "dashed" :
                            k === "arithmetic" ? "dotted" : "solid") + '"></span>' +
    esc(L["graph_" + k]) + "</div>").join("");
document.getElementById("lg-node").innerHTML =
  ["actor", "operation", "reading", "market", "opinion"].map(k =>
    '<div><span class="dot" style="background:' + css("--" + k) + '"></span>' + k +
    "</div>").join("");

/* ----------------------------------------------------------- evidence ---- */
(function renderEvidence(){
  const e = D.evidence, out = ['<p class="muted">' + esc(L.ev_intro) + "</p>"];
  const card = (lbl, big, sub) => '<div class="card"><div class="lbl">' + esc(lbl) +
    '</div><div class="big">' + big + '</div><div class="sub">' + esc(sub) +
    "</div></div>";

  if (e.scorecard && e.scorecard.backtest){
    const b = e.scorecard.backtest;
    out.push("<h2>" + esc(L.ev_scorecard) + '</h2><div class="grid">' +
      card("hit rate", (b.rate * 100).toFixed(1) + "%", b.n + " scored calls") +
      card("a coin", "50%", "the bar") +
      Object.entries(b.by_call || {}).map(([k, v]) =>
        card(k, (v.rate * 100).toFixed(1) + "%", v.n + " calls")).join("") +
      "</div>");
  }
  if (e.indicators){
    const i = e.indicators;
    out.push("<h2>" + esc(L.ev_indicators) + '</h2><div class="grid">' +
      card("survivors", i.survivors.length, "of " + i.tested + " rules") +
      card("buy and hold", (i.buy_and_hold * 100).toFixed(1) + "%", "a year") +
      card("random signals", (i.null_low * 100).toFixed(1) + ".." +
           (i.null_high * 100).toFixed(1) + "%", "matched turnover") +
      "</div>");
  }
  if (e.contamination){
    const c = e.contamination;
    out.push("<h2>" + esc(L.ev_contamination) + '</h2><div class="grid">' +
      card("before cutoff", (c.before_cutoff.rate * 100).toFixed(1) + "%",
           c.before_cutoff.n + " questions") +
      card("after cutoff", (c.after_cutoff.rate * 100).toFixed(1) + "%",
           c.after_cutoff.n + " questions") +
      card("a coin", "50%", c.model) + "</div>");
  }
  if (e.impacts && e.impacts.impacts){
    out.push("<h2>" + esc(L.ev_impacts) + '</h2><div class="grid">' +
      Object.entries(e.impacts.impacts).map(([k, v]) =>
        card(k, v.ratio.toFixed(2) + "x", "t=" + v.tstat + ", " + v.events +
             " events")).join("") + "</div>");
  }
  if (e.macro && e.macro.links){
    const real = e.macro.links.filter(l => l.real).sort((a, b) =>
      Math.abs(b.r) - Math.abs(a.r)).slice(0, 10);
    out.push("<h2>" + esc(L.ev_macro) + "</h2><table><tr><th>market</th>" +
      "<th>reading</th><th>r</th><th>days</th></tr>" + real.map(l =>
        '<tr><td class="nm" data-go="' + esc(l.symbol) + '">' +
        esc(byId[l.symbol] ? byId[l.symbol].label : l.symbol) +
        '</td><td class="nm" data-go="' + esc(l.feed) + '">' +
        esc(byId[l.feed] ? byId[l.feed].label : l.feed) + "</td><td>" +
        l.r.toFixed(2) + "</td><td>" + l.n + "</td></tr>").join("") + "</table>");
  }
  if (out.length === 1) out.push('<p class="muted">' + esc(L.ev_none) + "</p>");
  document.getElementById("v-evidence").innerHTML = out.join("");
})();

resize(); select(null); frame();
</script></body></html>"""
