"""The causal graph as a page you can push around with a mouse.

Obsidian's graph view is the reference, and the reason it works is that a link
you can *see* is a link you can argue with. A list of correlations gets skimmed;
a node with four arrows into it, one of them dashed red, gets clicked.

Self-contained by the same rule as everything else here: no CDN, no library, one
file that keeps working when a package host goes down. The layout is a plain
force simulation, which at forty-odd nodes is a few lines of arithmetic rather
than a reason to take a dependency.

The visual grammar carries the honesty that :mod:`graph` encodes:

    solid grey     arithmetic - true by construction, and therefore useless as
                   an explanation
    solid blue     documented - an institution says so, and the URL is on the edge
    solid green    measured - this project measured it, r and n on the edge
    dashed red     contested - widely repeated, evidence does not settle it

A reader who learns nothing else should still come away able to tell a dashed
red line from a solid green one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .graph import Graph
from .i18n import norm, t

KIND_ORDER = ("actor", "operation", "reading", "market", "opinion")


def _payload(graph: Graph, lang: str) -> dict[str, Any]:
    nodes = []
    for node in graph.nodes.values():
        degree = len(graph.into(node.id)) + len(graph.out_of(node.id))
        nodes.append({
            "id": node.id,
            "label": node.label(lang),
            "kind": node.kind,
            "degree": degree,
            "source": node.source,
        })
    edges = [{
        "src": e.src, "dst": e.dst, "kind": e.kind,
        "label": e.label(lang), "evidence": e.evidence, "source": e.source,
    } for e in graph.edges]
    return {"nodes": nodes, "edges": edges}


def write_graph_html(graph: Graph, path: str | Path, *, lang: str = "th") -> Path:
    """One interactive page. Everything inline; nothing fetched at view time."""
    lang = norm(lang)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(_payload(graph, lang), ensure_ascii=False)

    strings = {k: t(k, lang) for k in (
        "graph_title", "graph_intro", "graph_search", "graph_click",
        "graph_arithmetic", "graph_documented", "graph_measured",
        "graph_contested", "graph_evidence", "graph_causes", "graph_effects",
        "graph_none", "graph_reset", "graph_legend_kind",
    )}
    labels = json.dumps(strings, ensure_ascii=False)

    html = _TEMPLATE.replace("__DATA__", data).replace("__LABELS__", labels) \
                    .replace("__LANG__", lang) \
                    .replace("__TITLE__", strings["graph_title"]) \
                    .replace("__INTRO__", strings["graph_intro"])
    out.write_text(html, encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# The template is kept as one literal rather than assembled, because an f-string
# around this much CSS and JavaScript turns every brace into an escape and the
# first person to edit it introduces a bug that only shows up in a browser.
_TEMPLATE = r"""<!doctype html><html lang="__LANG__"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<style>
:root{--bg:#fbfbfb;--panel:#fff;--ink:#0a0a0a;--dim:#6b7280;--line:#e8e8e8;
 --arith:#9aa0ab;--doc:#2563eb;--meas:#0f7a4d;--cont:#c0392f;
 --market:#0f7a4d;--reading:#2563eb;--actor:#7c3aed;--operation:#b45309;--opinion:#c0392f;}
@media (prefers-color-scheme:dark){:root{--bg:#0a0a0a;--panel:#151515;--ink:#fafafa;
 --dim:#9aa0ab;--line:#282828;--arith:#6b7280;--doc:#60a5fa;--meas:#4ade80;--cont:#f87171;
 --market:#4ade80;--reading:#60a5fa;--actor:#a78bfa;--operation:#fbbf24;--opinion:#f87171;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;overflow:hidden}
#wrap{display:flex;height:100vh;width:100vw}
#stage{flex:1;position:relative;min-width:0}
canvas{display:block;width:100%;height:100%;cursor:grab}
canvas.dragging{cursor:grabbing}
#side{width:340px;flex:none;border-left:1px solid var(--line);background:var(--panel);
 padding:18px 18px 40px;overflow-y:auto}
h1{font-size:15px;margin:0 0 4px;letter-spacing:-.01em}
p.intro{color:var(--dim);font-size:12.5px;margin:0 0 14px}
h2{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--dim);
 margin:20px 0 8px}
input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;
 background:var(--bg);color:var(--ink);font:inherit;font-size:13px}
.legend{display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--dim)}
.legend div{display:flex;align-items:center;gap:8px}
.swatch{width:22px;height:0;border-top-width:2px;border-top-style:solid;flex:none}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
#detail{font-size:13px}
#detail .nm{font-weight:600;font-size:15px;margin-bottom:2px}
#detail .kd{color:var(--dim);font-size:11px;text-transform:uppercase;
 letter-spacing:.1em;margin-bottom:12px}
.edge{border-left:2px solid var(--line);padding:2px 0 2px 10px;margin:10px 0}
.edge.arithmetic{border-color:var(--arith)} .edge.documented{border-color:var(--doc)}
.edge.measured{border-color:var(--meas)} .edge.contested{border-color:var(--cont)}
.edge b{font-weight:600} .edge .lbl{color:var(--dim);display:block;margin-top:2px}
.edge .ev{display:block;margin-top:3px;font-size:11.5px;color:var(--dim);
 font-variant-numeric:tabular-nums;word-break:break-word}
.edge a{color:var(--doc)}
.tag{display:inline-block;padding:1px 6px;border-radius:999px;border:1px solid var(--line);
 font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
button{margin-top:12px;padding:7px 12px;border:1px solid var(--line);border-radius:8px;
 background:var(--bg);color:var(--ink);font:inherit;font-size:12px;cursor:pointer}
@media(max-width:820px){#wrap{flex-direction:column}#side{width:100%;height:46vh;
 border-left:none;border-top:1px solid var(--line)}#stage{height:54vh}}
</style></head><body>
<div id="wrap">
  <div id="stage"><canvas id="c"></canvas></div>
  <aside id="side">
    <h1>__TITLE__</h1>
    <p class="intro">__INTRO__</p>
    <input id="q" type="search" placeholder="">
    <h2 id="h-legend"></h2>
    <div class="legend" id="legend"></div>
    <h2 id="h-kind"></h2>
    <div class="legend" id="kinds"></div>
    <div id="detail"><p class="intro" id="hint"></p></div>
  </aside>
</div>
<script>
const DATA = __DATA__, L = __LABELS__;
const EDGE_COLOR = {arithmetic:"--arith", documented:"--doc", measured:"--meas",
                    contested:"--cont"};
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

const cv = document.getElementById("c"), ctx = cv.getContext("2d");
const nodes = DATA.nodes.map((n,i) => ({...n,
  x: Math.cos(i/DATA.nodes.length*6.283)*220 + (Math.random()-.5)*40,
  y: Math.sin(i/DATA.nodes.length*6.283)*220 + (Math.random()-.5)*40,
  vx:0, vy:0}));
const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
const edges = DATA.edges.filter(e => byId[e.src] && byId[e.dst])
                        .map(e => ({...e, a: byId[e.src], b: byId[e.dst]}));
for (const n of nodes) n.r = 5 + Math.min(9, Math.sqrt(n.degree) * 2.6);

let W=0, H=0, dpr=1, sel=null, hover=null, drag=null, pan={x:0,y:0}, zoom=1, alpha=1;

function resize(){
  const box = document.getElementById("stage").getBoundingClientRect();
  dpr = window.devicePixelRatio || 1;
  W = box.width; H = box.height;
  cv.width = W*dpr; cv.height = H*dpr;
  ctx.setTransform(dpr,0,0,dpr,0,0);
}
window.addEventListener("resize", () => { resize(); alpha = Math.max(alpha,.35); });

// A plain spring/repulsion layout. Forty nodes does not need Barnes-Hut, and a
// dependency-free file is worth more here than an asymptotic improvement.
function step(){
  if (alpha < .002) return;
  for (const n of nodes){
    if (n === drag) continue;
    n.vx -= n.x * 0.0016; n.vy -= n.y * 0.0016;          // gentle centring
  }
  for (let i=0;i<nodes.length;i++) for (let j=i+1;j<nodes.length;j++){
    const a=nodes[i], b=nodes[j];
    let dx=b.x-a.x, dy=b.y-a.y, d2=dx*dx+dy*dy || 0.01;
    if (d2 > 90000) continue;
    const f = 2600/d2, d=Math.sqrt(d2), fx=dx/d*f, fy=dy/d*f;
    a.vx-=fx; a.vy-=fy; b.vx+=fx; b.vy+=fy;
  }
  for (const e of edges){
    const dx=e.b.x-e.a.x, dy=e.b.y-e.a.y, d=Math.hypot(dx,dy)||0.01;
    const f=(d-115)*0.012, fx=dx/d*f, fy=dy/d*f;
    e.a.vx+=fx; e.a.vy+=fy; e.b.vx-=fx; e.b.vy-=fy;
  }
  for (const n of nodes){
    if (n === drag){ n.vx=n.vy=0; continue; }
    n.vx*=0.86; n.vy*=0.86; n.x+=n.vx*alpha; n.y+=n.vy*alpha;
  }
  alpha *= 0.994;
}

const near = id => sel && (sel === id ||
  edges.some(e => (e.src===sel&&e.dst===id)||(e.dst===sel&&e.src===id)));

function draw(){
  ctx.clearRect(0,0,W,H);
  ctx.save(); ctx.translate(W/2+pan.x, H/2+pan.y); ctx.scale(zoom,zoom);

  for (const e of edges){
    const lit = !sel || e.src===sel || e.dst===sel;
    ctx.globalAlpha = lit ? (e.kind==="arithmetic"?.45:.8) : .07;
    ctx.strokeStyle = css(EDGE_COLOR[e.kind]);
    ctx.lineWidth = e.kind==="contested" ? 1.8 : 1.2;
    ctx.setLineDash(e.kind==="contested" ? [5,4] : e.kind==="arithmetic" ? [2,3] : []);
    ctx.beginPath(); ctx.moveTo(e.a.x,e.a.y); ctx.lineTo(e.b.x,e.b.y); ctx.stroke();
    if (lit && (!sel || e.dst===sel || e.src===sel)){
      const dx=e.b.x-e.a.x, dy=e.b.y-e.a.y, d=Math.hypot(dx,dy)||1;
      const tx=e.b.x-dx/d*(e.b.r+4), ty=e.b.y-dy/d*(e.b.r+4), ang=Math.atan2(dy,dx);
      ctx.setLineDash([]); ctx.beginPath(); ctx.moveTo(tx,ty);
      ctx.lineTo(tx-7*Math.cos(ang-0.4), ty-7*Math.sin(ang-0.4));
      ctx.moveTo(tx,ty);
      ctx.lineTo(tx-7*Math.cos(ang+0.4), ty-7*Math.sin(ang+0.4));
      ctx.stroke();
    }
  }
  ctx.setLineDash([]);

  for (const n of nodes){
    const lit = !sel || near(n.id);
    ctx.globalAlpha = lit ? 1 : .12;
    ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,6.2832);
    ctx.fillStyle = css("--"+n.kind); ctx.fill();
    if (n.id===sel || n.id===hover){
      ctx.lineWidth=2.5; ctx.strokeStyle=css("--ink"); ctx.stroke();
    }
    if (lit && (zoom>0.75 || n.degree>3 || n.id===sel)){
      ctx.globalAlpha = lit ? .95 : .12;
      ctx.fillStyle = css("--ink");
      ctx.font = (n.id===sel?"600 ":"") + "11px system-ui,sans-serif";
      ctx.textAlign="center";
      ctx.fillText(n.label, n.x, n.y - n.r - 5);
    }
  }
  ctx.restore(); ctx.globalAlpha = 1;
}

function frame(){ step(); draw(); requestAnimationFrame(frame); }

const toWorld = (px,py) => ({x:(px-W/2-pan.x)/zoom, y:(py-H/2-pan.y)/zoom});
function hit(px,py){
  const p = toWorld(px,py);
  let best=null, bd=1e9;
  for (const n of nodes){
    const d = Math.hypot(n.x-p.x, n.y-p.y);
    if (d < n.r+9 && d < bd){ best=n; bd=d; }
  }
  return best;
}

let panning=false, last={x:0,y:0};
cv.addEventListener("pointerdown", ev => {
  const n = hit(ev.offsetX, ev.offsetY);
  if (n){ drag=n; select(n.id); alpha=Math.max(alpha,.3); }
  else { panning=true; last={x:ev.clientX,y:ev.clientY}; select(null); }
  cv.classList.add("dragging"); cv.setPointerCapture(ev.pointerId);
});
cv.addEventListener("pointermove", ev => {
  if (drag){ const p=toWorld(ev.offsetX,ev.offsetY); drag.x=p.x; drag.y=p.y;
             alpha=Math.max(alpha,.15); }
  else if (panning){ pan.x+=ev.clientX-last.x; pan.y+=ev.clientY-last.y;
                     last={x:ev.clientX,y:ev.clientY}; }
  else { const n=hit(ev.offsetX,ev.offsetY); hover = n?n.id:null;
         cv.style.cursor = n ? "pointer" : "grab"; }
});
const release = ev => { drag=null; panning=false; cv.classList.remove("dragging"); };
cv.addEventListener("pointerup", release);
cv.addEventListener("pointercancel", release);
cv.addEventListener("wheel", ev => {
  ev.preventDefault();
  zoom = Math.min(3, Math.max(0.3, zoom * (ev.deltaY < 0 ? 1.12 : 0.89)));
}, {passive:false});

function esc(s){ const d=document.createElement("div"); d.textContent=s??"";
                 return d.innerHTML; }
function evidenceHtml(ev){
  if (!ev) return "";
  const m = String(ev).match(/https?:\/\/\S+/);
  if (m) return '<a href="'+esc(m[0])+'" target="_blank" rel="noopener">'
                + esc(m[0].replace(/^https?:\/\//,"").slice(0,52)) + "</a>";
  return esc(ev);
}
function edgeHtml(e, otherId, dir){
  const other = byId[otherId];
  return '<div class="edge '+e.kind+'"><b>'+esc(other?other.label:otherId)+'</b>'
       + ' <span class="tag">'+esc(L["graph_"+e.kind])+'</span>'
       + '<span class="lbl">'+esc(e.label)+'</span>'
       + (e.evidence ? '<span class="ev">'+evidenceHtml(e.evidence)+'</span>' : '')
       + '</div>';
}
function select(id){
  sel = id;
  const box = document.getElementById("detail");
  if (!id){ box.innerHTML = '<p class="intro">'+esc(L.graph_click)+'</p>'; return; }
  const n = byId[id];
  const into = edges.filter(e => e.dst===id), out = edges.filter(e => e.src===id);
  box.innerHTML =
    '<h2>&nbsp;</h2><div class="nm">'+esc(n.label)+'</div>'
    + '<div class="kd">'+esc(n.kind)+'</div>'
    + '<h2>'+esc(L.graph_causes)+'</h2>'
    + (into.length ? into.map(e => edgeHtml(e, e.src, "in")).join("")
                   : '<p class="intro">'+esc(L.graph_none)+'</p>')
    + '<h2>'+esc(L.graph_effects)+'</h2>'
    + (out.length ? out.map(e => edgeHtml(e, e.dst, "out")).join("")
                  : '<p class="intro">'+esc(L.graph_none)+'</p>')
    + '<button onclick="select(null)">'+esc(L.graph_reset)+'</button>';
}
window.select = select;

document.getElementById("q").placeholder = L.graph_search;
document.getElementById("q").addEventListener("input", ev => {
  const q = ev.target.value.trim().toLowerCase();
  if (!q) return select(null);
  const n = nodes.find(x => x.label.toLowerCase().includes(q)
                         || x.id.toLowerCase().includes(q));
  if (n){ select(n.id); pan = {x:-n.x*zoom, y:-n.y*zoom}; }
});

document.getElementById("h-legend").textContent = L.graph_evidence;
document.getElementById("h-kind").textContent = L.graph_legend_kind;
document.getElementById("legend").innerHTML = ["arithmetic","documented","measured","contested"]
  .map(k => '<div><span class="swatch" style="border-color:'+css(EDGE_COLOR[k])
          + ';border-top-style:'+(k==="contested"?"dashed":k==="arithmetic"?"dotted":"solid")
          + '"></span>'+esc(L["graph_"+k])+"</div>").join("");
document.getElementById("kinds").innerHTML = ["actor","operation","reading","market","opinion"]
  .map(k => '<div><span class="dot" style="background:'+css("--"+k)+'"></span>'+k+"</div>").join("");

resize(); select(null); frame();
</script></body></html>"""
