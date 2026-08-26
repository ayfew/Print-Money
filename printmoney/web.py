"""A browser dashboard.

The terminal view is fine for watching a loop, but it is bad at two things that
matter: seeing the *shape* of the position across settlement outcomes, and
comparing what the market thinks with what the model thinks at a glance.  Both
are pictures, so this module draws them.

Two modes:

* ``pm ui`` writes a single self-contained HTML file - the data is baked in, so
  it can be mailed, archived next to a snapshot, or opened with no server.
* ``pm ui --serve`` runs a small stdlib HTTP server that re-analyses on demand,
  so the page refreshes itself while the engine trades.

No JavaScript libraries, no CDN, no build step. Everything is inline.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import Config
from .data.polymarket import PolymarketClient
from .data.spot import SpotFeed
from .data.types import StripKind
from .ledger import Ledger
from .model import vol as volmod
from .model.build import prepare_models
from .model.surface import build_surface
from .registry import MarketRegistry
from .strategy.lp import solve
from .strategy.single import plan_touch_trades
from .strategy.statespace import build_state_space, group_strips_by_expiry, holdings_on
from .util import utcnow, years_between

log = logging.getLogger("printmoney.web")


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def analyse(cfg: Config, *, slug: str | None = None) -> dict[str, Any]:
    """Everything the page shows, as plain JSON. Reads only - places no orders."""
    started = time.monotonic()
    out: dict[str, Any] = {
        "generated_at": utcnow().isoformat(),
        "mode": cfg.execution.mode,
        "live_armed": cfg.live_armed()[0],
        "capital": cfg.risk.capital_usd,
        "groups": [],
        "touch": [],
        "notes": [],
        "error": "",
    }

    ledger = Ledger.load_or_new(cfg.execution.ledger_path, cfg.risk.capital_usd)
    registry = MarketRegistry()
    positions = ledger.open_positions()

    pm = PolymarketClient(cfg)
    feed = SpotFeed(cfg)
    try:
        spot = feed.spot()
        out["spot"] = spot.to_dict()

        candles = feed.klines("1h", cfg.model.vol_lookback_hours)
        vol = volmod.estimate(candles, cfg.model)
        out["vol"] = vol.to_dict()
        try:
            pool = volmod.standardized_returns(candles)
        except ValueError as exc:
            pool = None
            out["notes"].append(f"bootstrap disabled: {exc}")

        strips = pm.strip_from_slug(slug) if slug else None
        strips = [strips] if strips else (pm.scan_strips() if not slug else [])
        registry.record_strips(strips)
        registry.save()
        out["strips"] = [
            {
                "slug": s.slug,
                "title": s.title,
                "kind": s.kind.value,
                "legs": len(s.legs),
                "expiry": s.expiry.isoformat(),
                "seconds_to_expiry": s.seconds_to_expiry(),
                "liquidity": s.liquidity_usd,
                "volume_24h": s.volume_24h,
                "mid_sum": s.sum_yes_mid(),
            }
            for s in strips
        ]

        marks = _marks(strips)
        equity = ledger.mark_to_market(marks)
        out["account"] = ledger.stats(equity)
        curve = [[t.isoformat(), round(v, 4)] for t, v in ledger.equity_curve[-500:]]
        # The engine writes the curve once per cycle; this page is looking at the
        # book right now. Append today's mark so the line ends where the card says
        # it does instead of trailing the last loop iteration.
        now_point = [out["generated_at"], round(equity, 4)]
        if not curve or abs(curve[-1][1] - now_point[1]) > 1e-9:
            curve.append(now_point)
        out["equity_curve"] = curve
        out["positions"] = [
            {**p.to_dict(), "mark": marks.get(p.token_id)} for p in positions
        ]
        out["fills"] = [f.to_dict() for f in ledger.fills[-40:]][::-1]

        for key, group in sorted(group_strips_by_expiry(strips).items()):
            entry: dict[str, Any] = {"expiry": key, "strips": [s.slug for s in group]}
            years = years_between(utcnow(), group[0].expiry)
            entry["seconds_to_expiry"] = group[0].seconds_to_expiry()
            if years <= 0:
                entry["error"] = "already expired"
                out["groups"].append(entry)
                continue
            try:
                vols, ensemble, _banks = prepare_models(
                    spot.price, vol.annual, years, group, cfg, shock_pool=pool
                )
                surfaces = [build_surface(s, ensemble, spot.price, years) for s in group]
                space = build_state_space(group, ensemble, spot.price, cfg)
                held = holdings_on(space, positions)
                plan = solve(space, cfg, holdings=held)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
                out["groups"].append(entry)
                continue

            entry["vols"] = vols.to_dict()
            entry["vols_text"] = vols.describe()
            entry["surfaces"] = [s.to_dict() for s in surfaces]
            entry["holdings"] = held.to_dict()
            entry["plan"] = plan.to_dict()
            entry["states"] = [
                {
                    "label": space.state_label(i),
                    "prob": float(space.base_probs()[i]),
                    "pnl": float(plan.pnl_by_state[i]) if plan.pnl_by_state.size else 0.0,
                }
                for i in range(space.n_states)
            ]
            out["groups"].append(entry)

        for strip in strips:
            if strip.kind is not StripKind.TOUCH:
                continue
            years = years_between(utcnow(), strip.expiry)
            if years <= 0:
                continue
            try:
                vols, ensemble, _banks = prepare_models(
                    spot.price, vol.annual, years, [strip], cfg, shock_pool=pool
                )
                surface = build_surface(strip, ensemble, spot.price, years)
                tplan = plan_touch_trades(
                    strip,
                    ensemble,
                    cfg,
                    held={p.token_id: p.cost_basis for p in positions},
                )
            except Exception as exc:  # noqa: BLE001
                out["notes"].append(f"{strip.slug}: {exc}")
                continue
            out["touch"].append(
                {
                    "slug": strip.slug,
                    "title": strip.title,
                    "seconds_to_expiry": strip.seconds_to_expiry(),
                    "vols_text": vols.describe(),
                    "surface": surface.to_dict(),
                    "plan": tplan.to_dict(),
                }
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("analysis failed")
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        pm.close()
        feed.close()

    out["elapsed_s"] = round(time.monotonic() - started, 2)
    return out


def carry_snapshot(cfg: Config, *, holding_days: float = 30.0) -> dict[str, Any]:
    """The funding-carry scan, as JSON. Slower than the rest, so it is fetched
    separately and cached for longer - funding only settles every eight hours."""
    from .carry.scanner import scan

    try:
        report = scan(capital=cfg.risk.capital_usd, holding_days=holding_days)
        out = report.to_dict()
        out["generated_at"] = utcnow().isoformat()
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("carry scan failed")
        return {"error": f"{type(exc).__name__}: {exc}", "candidates": []}


def _marks(strips) -> dict[str, float]:
    from .settlement import mark_prices

    return mark_prices(strips)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render_html(
    snapshot: dict[str, Any],
    *,
    live: bool = False,
    carry: dict[str, Any] | None = None,
) -> str:
    payload = json.dumps(snapshot, separators=(",", ":"), default=str)
    carry_payload = json.dumps(carry, separators=(",", ":"), default=str) if carry else "null"
    return (
        PAGE.replace("/*__LIVE__*/false", "true" if live else "false")
        .replace('"__DATA__"', payload)
        .replace('"__CARRY__"', carry_payload)
    )


def write_report(
    cfg: Config, path: str | Path, *, slug: str | None = None, carry: bool = True
) -> Path:
    snapshot = analyse(cfg, slug=slug)
    carry_data = carry_snapshot(cfg) if carry else None
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(snapshot, carry=carry_data), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# server
# --------------------------------------------------------------------------- #
_CARRY_TTL = 600.0
_carry_at = 0.0
_carry_data: dict[str, Any] | None = None
_carry_lock = threading.Lock()


def _carry_cached(fn) -> dict[str, Any]:
    """Ten-minute cache around the funding scan."""
    global _carry_at, _carry_data
    with _carry_lock:
        if _carry_data is not None and (time.monotonic() - _carry_at) < _CARRY_TTL:
            return _carry_data
        _carry_data = fn()
        _carry_at = time.monotonic()
        return _carry_data


class _Cache:
    """Re-analysing costs a few API calls, so hold the result briefly."""

    def __init__(self, cfg: Config, ttl: float, slug: str | None) -> None:
        self.cfg = cfg
        self.ttl = ttl
        self.slug = slug
        self._lock = threading.Lock()
        self._at = 0.0
        self._data: dict[str, Any] | None = None

    def get(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            fresh = self._data is not None and (time.monotonic() - self._at) < self.ttl
            if fresh and not force:
                return self._data  # type: ignore[return-value]
            self._data = analyse(self.cfg, slug=self.slug)
            self._at = time.monotonic()
            return self._data


def serve(
    cfg: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    slug: str | None = None,
    ttl: float = 15.0,
    open_browser: bool = True,
) -> None:
    """Serve the dashboard until interrupted."""
    cache = _Cache(cfg, ttl, slug)
    # Funding settles every eight hours; re-scanning it every fifteen seconds
    # would be a lot of API calls to watch a number that cannot have moved.
    def carry_cache_fn() -> dict[str, Any]:
        return carry_snapshot(cfg)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # quieter
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path in ("/", "/index.html"):
                    html = render_html(cache.get(), live=True, carry=None)
                    self._send(html.encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/api/snapshot":
                    force = "force=1" in self.path
                    body = json.dumps(cache.get(force), default=str).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                elif path == "/api/carry":
                    body = json.dumps(_carry_cached(carry_cache_fn), default=str).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                else:
                    self._send(b"not found", "text/plain; charset=utf-8", 404)
            except BrokenPipeError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.exception("request failed")
                self._send(str(exc).encode("utf-8"), "text/plain; charset=utf-8", 500)

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    log.info("dashboard on %s (ctrl-c to stop)", url)
    print(f"printmoney dashboard: {url}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


# --------------------------------------------------------------------------- #
PAGE = r"""<title>printmoney</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
/* Light, quiet, mostly monochrome. Colour is reserved for signed numbers, so a
   glance at the page finds the profit and loss and nothing else shouts. */
:root {
  --bg:#fbfbfb; --panel:#ffffff; --ink:#0a0a0a; --dim:#737373; --dimmer:#a3a3a3;
  --line:#e8e8e8; --line-2:#f2f2f2; --dot:rgba(0,0,0,0.055);
  --solid:#0a0a0a; --solid-ink:#ffffff;
  --up:#0f7a4d; --down:#c0392f; --accent:#4f46e5;
  --upbg:#effaf4; --downbg:#fdf1f0; --chart:rgba(10,10,10,0.72);
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI Variable Text","Segoe UI",Inter,Roboto,sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#0a0a0a; --panel:#121212; --ink:#fafafa; --dim:#8f8f8f; --dimmer:#606060;
    --line:#262626; --line-2:#1c1c1c; --dot:rgba(255,255,255,0.055);
    --solid:#fafafa; --solid-ink:#0a0a0a;
    --up:#4ade80; --down:#f87171; --accent:#a5b4fc;
    --upbg:#10251b; --downbg:#2a1615; --chart:rgba(250,250,250,0.75);
  }
}
:root[data-theme="dark"] {
  --bg:#0a0a0a; --panel:#121212; --ink:#fafafa; --dim:#8f8f8f; --dimmer:#606060;
  --line:#262626; --line-2:#1c1c1c; --dot:rgba(255,255,255,0.055);
  --solid:#fafafa; --solid-ink:#0a0a0a;
  --up:#4ade80; --down:#f87171; --accent:#a5b4fc;
  --upbg:#10251b; --downbg:#2a1615; --chart:rgba(250,250,250,0.75);
}
* { box-sizing:border-box; }
html { background:var(--bg); }
body {
  margin:0; color:var(--ink); background:var(--bg);
  background-image:radial-gradient(var(--dot) 1px, transparent 1px);
  background-size:22px 22px; background-position:-1px -1px;
  font-family:var(--sans); font-size:14px; line-height:1.6;
  -webkit-font-smoothing:antialiased; min-height:100vh;
}
.wrap { max-width:1080px; margin:0 auto; padding:30px 22px 90px; }
a { color:var(--ink); }
.mono { font-family:var(--mono); font-variant-numeric:tabular-nums; }
.spacer { flex:1; }
.muted { color:var(--dim); }
.small { font-size:12.5px; }
.up { color:var(--up); } .down { color:var(--down); } .dim { color:var(--dim); }

.bar { display:flex; align-items:center; gap:11px; flex-wrap:wrap; margin-bottom:24px; }
.brand { font-size:14px; font-weight:600; letter-spacing:-0.02em; }
.tag {
  font-size:10.5px; font-weight:600; letter-spacing:0.04em; padding:3px 9px;
  border-radius:999px; border:1px solid var(--line); color:var(--dim); background:var(--panel);
}
.tag.solid { background:var(--solid); color:var(--solid-ink); border-color:var(--solid); }
.tag.live { background:var(--downbg); color:var(--down); border-color:var(--down); }

.card {
  background:var(--panel); border:1px solid var(--line); border-radius:12px;
  padding:22px 24px; margin:14px 0;
}
.card.tight { padding:16px 18px; }
.card.quietbox { background:transparent; border-style:dashed; }
h2 { font-size:15px; margin:0; font-weight:600; letter-spacing:-0.015em; }
h3 { font-size:11px; margin:22px 0 10px; color:var(--dimmer); font-weight:600;
     text-transform:uppercase; letter-spacing:0.1em; }
.head { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }

/* hero */
.price { font-family:var(--mono); font-size:40px; line-height:1.05; letter-spacing:-0.04em; font-weight:500; }
.price small { font-size:12.5px; color:var(--dimmer); margin-left:11px; letter-spacing:0; font-family:var(--sans); }
.verdict { font-size:16px; line-height:1.55; margin-top:13px; max-width:64ch; color:var(--dim); }
.verdict b { color:var(--ink); font-weight:600; }
.stats { display:flex; gap:26px; flex-wrap:wrap; margin-top:20px; padding-top:18px; border-top:1px solid var(--line-2); }
.stat .k { font-size:10.5px; color:var(--dimmer); text-transform:uppercase; letter-spacing:0.09em; font-weight:600; }
.stat .v { font-family:var(--mono); font-size:19px; letter-spacing:-0.025em; margin-top:2px; }

.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(122px,1fr)); gap:1px;
        background:var(--line); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
.cell { background:var(--panel); padding:12px 14px; }
.cell .k { font-size:10px; color:var(--dimmer); text-transform:uppercase; letter-spacing:0.1em; font-weight:600; }
.cell .v { font-size:18px; font-family:var(--mono); letter-spacing:-0.025em; margin-top:3px; }

table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:right; font-weight:600; color:var(--dimmer); font-size:10px;
     text-transform:uppercase; letter-spacing:0.1em; padding:8px 10px;
     border-bottom:1px solid var(--line); white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
td { padding:7px 10px; border-bottom:1px solid var(--line-2); text-align:right;
     font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; }
tr:hover td { background:var(--line-2); }
tr:last-child td { border-bottom:none; }
.scroll { overflow-x:auto; }
table:not(.all) tr.quiet { display:none; }

.gauge { position:relative; height:6px; min-width:104px; border-radius:3px; background:var(--line); overflow:hidden; }
.gauge i { position:absolute; top:-2px; bottom:-2px; width:2px; border-radius:1px; }
.gauge .band { position:absolute; top:0; bottom:0; background:var(--accent); opacity:0.22; }
.gauge .mkt { background:var(--dimmer); }
.gauge .fair { background:var(--ink); }

.chip { display:inline-block; font-size:11px; font-family:var(--mono); padding:2px 7px;
        border-radius:5px; background:var(--line-2); color:var(--dim); }
.chip.up { background:var(--upbg); color:var(--up); }
.chip.down { background:var(--downbg); color:var(--down); }

.legend { display:flex; gap:18px; flex-wrap:wrap; font-size:11.5px; color:var(--dim); margin-top:10px; }
.legend span i { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px; vertical-align:-1px; }
.reason { font-size:13.5px; color:var(--dim); }
svg { display:block; max-width:100%; }
.empty { color:var(--dimmer); padding:10px 0; }
.warn { color:#a16207; }
footer { margin-top:44px; padding-top:18px; border-top:1px solid var(--line);
         color:var(--dimmer); font-size:12px; line-height:1.75; }
button { font:inherit; font-size:11.5px; font-weight:500; padding:5px 12px; border-radius:7px;
         cursor:pointer; color:var(--ink); border:1px solid var(--line); background:var(--panel);
         transition:background .12s, border-color .12s; }
button:hover:not(:disabled) { background:var(--line-2); border-color:var(--dimmer); }
button:disabled { opacity:0.45; cursor:default; }
</style>

<div class="wrap">
  <div class="bar">
    <span class="brand">printmoney</span>
    <span class="tag" id="mode">paper</span>
    <span class="spacer"></span>
    <span class="small muted mono" id="stamp"></span>
    <button id="refresh">Refresh</button>
    <button id="theme">Theme</button>
  </div>
  <div id="hero"></div>
  <div id="root"></div>
  <footer>
    Read-only. Every number comes from live exchange data at the moment shown, and nothing on
    this page places an order.<br>
    Paper trading is not trading: fills here assume the quoted size was still there when the
    order arrived, which for the legs with the most edge is exactly when it is not.
  </footer>
</div>

<script>
const LIVE = /*__LIVE__*/false;
let DATA = "__DATA__";
let CARRY = "__CARRY__";

const usd = (x) => (x < 0 ? "-$" : "$") + Math.abs(Number(x || 0)).toLocaleString(undefined,
  {minimumFractionDigits: 2, maximumFractionDigits: 2});
const pct = (x, d = 1) => (100 * Number(x || 0)).toFixed(d) + "%";
const spct = (x, d = 1) => (x >= 0 ? "+" : "") + (100 * Number(x || 0)).toFixed(d) + "%";
const sgn = (x, d = 3) => (x >= 0 ? "+" : "") + Number(x).toFixed(d);
const cls = (x) => (x > 0 ? "up" : x < 0 ? "down" : "dim");
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const num = (x, d = 2) => Number(x).toLocaleString(undefined, {maximumFractionDigits: d});
function dur(sec) {
  sec = Math.max(0, Number(sec || 0));
  if (sec < 5400) return Math.round(sec / 60) + "m";
  if (sec < 172800) return (sec / 3600).toFixed(1) + "h";
  return (sec / 86400).toFixed(1) + "d";
}

/* ------------------------------------------------------------------ hero -- */
function heroBlock(d) {
  const groups = d.groups || [];
  const taken = groups.filter((g) => g.plan && g.plan.status === "accepted");
  const arb = taken.filter((g) => g.plan.is_arbitrage);
  const held = groups.reduce((n, g) => n + ((g.holdings && g.holdings.tokens) || 0), 0);
  const markets = (d.strips || []).length;
  const a = d.account || {};

  let verdict;
  if (!markets) {
    verdict = "<b>Nothing on the board.</b> No market passed the liquidity and time filters.";
  } else if (taken.length) {
    const ev = taken.reduce((s, g) => s + g.plan.expected_pnl, 0);
    const stake = taken.reduce((s, g) => s + g.plan.capital_used, 0);
    verdict = `<b>Trading.</b> ${taken.length} settlement date(s) priced badly enough to act on` +
      (arb.length ? `, ${arb.length} risk-free` : "") + `. Staking ${usd(stake)} for ${usd(ev)} expected.`;
  } else {
    const why = groups.map((g) => g.plan && g.plan.reason).filter(Boolean)[0] || "no edge after fees";
    verdict = `<b>Standing down.</b> ${markets} markets read, none wrong enough to beat the fee. ` +
      `<span class="dim">${esc(why)}</span>`;
  }
  const stat = (k, v, c) => `<div class="stat"><div class="k">${k}</div>
    <div class="v ${c || ""}">${v}</div></div>`;
  return `<div class="card">
    <div class="price">${d.spot ? num(d.spot.price) : "-"}<small>BTC &middot; ${
      d.spot ? esc(d.spot.source) : ""}</small></div>
    <div class="verdict">${verdict}</div>
    <div class="stats">
      ${stat("equity", usd(a.equity))}
      ${stat("cash", usd(a.cash))}
      ${stat("at risk", usd(a.exposure))}
      ${stat("return", spct(a.total_return, 2), cls(a.total_return))}
      ${stat("markets", markets)}
      ${held ? stat("holding", held) : ""}
    </div>
  </div>`;
}

/* ---------------------------------------------------------------- charts -- */
function sparkline(points, w = 300, h = 48) {
  if (!points || points.length < 2) return '<div class="empty small">not enough history yet</div>';
  const vals = points.map((p) => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1, step = w / (vals.length - 1);
  const y = (v) => h - 5 - ((v - lo) / span) * (h - 12);
  const line = vals.map((v, i) => (i ? "L" : "M") + (i * step).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img"
      aria-label="equity from ${usd(vals[0])} to ${usd(vals[vals.length - 1])}">
    <defs><linearGradient id="eqf" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--chart)" stop-opacity="0.16"/>
      <stop offset="100%" stop-color="var(--chart)" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${line} L ${w} ${h} L 0 ${h} Z" fill="url(#eqf)"/>
    <path d="${line}" fill="none" stroke="var(--chart)" stroke-width="1.5"
      stroke-linejoin="round" stroke-linecap="round"/>
  </svg>
  <div class="small muted mono" style="margin-top:4px">${usd(vals[0])} &rarr;
    <span class="${up ? "up" : "down"}">${usd(vals[vals.length - 1])}</span></div>`;
}

function payoffChart(states) {
  if (!states || !states.length) return "";
  const W = 940, H = 280, padL = 66, padR = 16, padT = 16, padB = 78;
  const n = states.length, iw = (W - padL - padR) / n;
  const pnls = states.map((s) => s.pnl);
  let lo = Math.min(0, ...pnls), hi = Math.max(0, ...pnls);
  if (hi - lo < 1e-9) { hi = 1; lo = -1; }
  const pad = (hi - lo) * 0.15; lo -= pad; hi += pad;
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
  const zero = y(0);
  const maxP = Math.max(...states.map((s) => s.prob), 1e-9);

  let bars = "", probs = "", labels = "", ticks = "";
  states.forEach((s, i) => {
    const x = padL + i * iw;
    const top = Math.min(y(s.pnl), zero), bh = Math.abs(y(s.pnl) - zero);
    const col = s.pnl > 0 ? "var(--up)" : s.pnl < 0 ? "var(--down)" : "var(--dimmer)";
    bars += `<rect x="${(x + iw * 0.2).toFixed(1)}" y="${top.toFixed(1)}"
      width="${(iw * 0.6).toFixed(1)}" height="${Math.max(bh, 1.5).toFixed(1)}" rx="2" fill="${col}"
      ><title>${esc(s.label)}: ${usd(s.pnl)} (p = ${pct(s.prob, 2)})</title></rect>`;
    const ph = (s.prob / maxP) * 26;
    probs += `<rect x="${(x + iw * 0.2).toFixed(1)}" y="${(H - padB + 14 + (26 - ph)).toFixed(1)}"
      width="${(iw * 0.6).toFixed(1)}" height="${ph.toFixed(1)}" rx="1"
      fill="var(--accent)" opacity="0.4"/>`;
    if (n <= 18 || i % 2 === 0) {
      labels += `<text x="${(x + iw / 2).toFixed(1)}" y="${H - 4}" text-anchor="end"
        transform="rotate(-40 ${(x + iw / 2).toFixed(1)} ${H - 4})"
        font-size="9.5" fill="var(--dimmer)" font-family="var(--mono)">${esc(s.label)}</text>`;
    }
  });
  [hi, (hi + lo) / 2, lo].forEach((v) => {
    ticks += `<line x1="${padL}" x2="${W - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"
      stroke="var(--line)"/><text x="${padL - 9}" y="${(y(v) + 3.5).toFixed(1)}" text-anchor="end"
      font-size="10" fill="var(--dimmer)" font-family="var(--mono)">${usd(v)}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
      aria-label="profit and loss in every settlement outcome">${ticks}
    <line x1="${padL}" x2="${W - padR}" y1="${zero.toFixed(1)}" y2="${zero.toFixed(1)}"
      stroke="var(--ink)" stroke-width="1" opacity="0.55"/>
    ${bars}${probs}${labels}</svg>
  <div class="legend">
    <span><i style="background:var(--up)"></i>profit</span>
    <span><i style="background:var(--down)"></i>loss</span>
    <span><i style="background:var(--accent);opacity:.4"></i>chance BTC settles there</span>
  </div>`;
}

function gauge(q) {
  const p = (v) => Math.max(0, Math.min(100, 100 * Number(v)));
  const lo = q.fair_lo == null ? null : p(q.fair_lo), hi = q.fair_hi == null ? null : p(q.fair_hi);
  const band = lo != null && hi != null
    ? `<div class="band" style="left:${Math.min(lo, hi)}%;width:${Math.max(Math.abs(hi - lo), 0.7)}%"></div>` : "";
  return `<div class="gauge" title="market ${q.implied == null ? "-" : pct(q.implied)} \u00b7 model ${
    q.fair == null ? "-" : pct(q.fair)}">${band}${
    q.implied == null ? "" : `<i class="mkt" style="left:${p(q.implied)}%"></i>`}${
    q.fair == null ? "" : `<i class="fair" style="left:${p(q.fair)}%"></i>`}</div>`;
}

/* ------------------------------------------------------------- the carry -- */
function carrySection(c) {
  if (!c) return `<div class="card quietbox"><div class="empty small">
    funding carry &mdash; loading&hellip;</div></div>`;
  if (c.error) return `<div class="card quietbox"><h2>funding carry</h2>
    <div class="down small" style="margin-top:6px">${esc(c.error)}</div></div>`;
  const rows = (c.candidates || []).slice(0, 12).map((x) => `<tr>
      <td>${esc(x.symbol)}</td>
      <td>${spct(x.annual_now)}</td>
      <td>${spct(x.annual_mean)}</td>
      <td class="${cls(x.net_annual_30d)}">${spct(x.net_annual_30d)}</td>
      <td>${pct(x.positive_fraction, 0)}</td>
      <td>$${num(x.perp_volume_24h / 1e6, 0)}M</td>
      <td class="small ${x.risks && x.risks.length ? "warn" : "dim"}"
        style="text-align:left">${esc((x.risks || []).join("; ") || "-")}</td>
    </tr>`).join("");
  return `<div class="card">
    <div class="head"><h2>funding carry</h2>
      <span class="tag">${c.scanned} perps &middot; ${c.hedgeable} hedgeable</span></div>
    <div class="reason" style="margin-top:8px;max-width:70ch">
      Hold the coin on spot, short the same size of perpetual. The position has no exposure to
      the price at all; it only collects the funding payment. <b>Direction-neutral, and the only
      strategy here that is not a bet.</b>
    </div>
    <div class="grid" style="margin-top:16px">
      <div class="cell"><div class="k">basket net</div>
        <div class="v ${cls(c.basket_net_annual)}">${spct(c.basket_net_annual)}</div></div>
      <div class="cell"><div class="k">on ${usd(c.capital)}</div>
        <div class="v ${cls(c.monthly_usd)}">${usd(c.monthly_usd)}<span class="small muted"> /mo</span></div></div>
      <div class="cell"><div class="k">in baht</div>
        <div class="v">${num(c.monthly_usd * 36, 0)}<span class="small muted"> /mo</span></div></div>
      <div class="cell"><div class="k">hold assumed</div>
        <div class="v">${c.holding_days}<span class="small muted"> days</span></div></div>
    </div>
    <div class="scroll" style="margin-top:16px"><table>
      <tr><th>symbol</th><th>now</th><th>30d avg</th><th>net</th><th>paid</th><th>volume</th>
          <th style="text-align:left">warnings</th></tr>${rows}</table></div>
    <div class="small muted" style="margin-top:10px">
      Net is after 0.30% of round-trip fees amortised over the holding period. Coins with the
      highest headline rates are missing from this list because they have no spot market, so
      they cannot be hedged &mdash; shorting those is not carry, it is a naked short.
    </div>
  </div>`;
}

/* ------------------------------------------------------------- rendering -- */
let TID = 0;
function surfaceTable(surface) {
  const id = "t" + (++TID);
  let quiet = 0;
  const rows = surface.quotes.map((q) => {
    const eY = q.fair != null && q.yes_ask != null ? q.fair - q.yes_ask : null;
    const eN = q.fair != null && q.no_ask != null ? (1 - q.fair) - q.no_ask : null;
    const dull = (q.implied == null || q.implied > 0.985 || q.implied < 0.015)
      && (q.fair == null || q.fair > 0.985 || q.fair < 0.015);
    if (dull) quiet++;
    const chip = (e) => e == null ? '<span class="dim">-</span>' : `<span class="chip ${cls(e)}">${sgn(e)}</span>`;
    return `<tr class="${dull ? "quiet" : ""}">
      <td>${esc(q.label)}</td>
      <td>${q.yes_bid == null ? "-" : q.yes_bid.toFixed(3)}</td>
      <td>${q.yes_ask == null ? "-" : q.yes_ask.toFixed(3)}</td>
      <td>${q.implied == null ? "-" : pct(q.implied)}</td>
      <td>${q.fair == null ? "-" : pct(q.fair)}</td>
      <td style="width:120px">${gauge(q)}</td>
      <td>${chip(eY)}</td><td>${chip(eN)}</td></tr>`;
  }).join("");
  const inc = (surface.incoherences || []).map((i) =>
    `<div class="small warn">${esc(i.kind)} &mdash; ${esc(i.detail)}</div>`).join("");
  const sum = surface.implied_sum == null ? "" :
    `<div class="small muted" style="margin-top:8px">bucket prices sum to
      <span class="mono">${surface.implied_sum.toFixed(4)}</span>, ${sgn(surface.implied_sum - 1, 4)} from 1.0000</div>`;
  const toggle = quiet ? `<div style="margin-top:9px"><button onclick="
      document.getElementById('${id}').classList.toggle('all');
      this.textContent = this.textContent[0] === 'S' ? 'Hide ${quiet} settled legs' : 'Show ${quiet} settled legs'
    ">Show ${quiet} settled legs</button></div>` : "";
  return `<h3>${esc(surface.slug)}</h3>
    <div class="scroll"><table id="${id}">
      <tr><th>leg</th><th>bid</th><th>ask</th><th>market</th><th>model</th>
          <th>market &middot; model</th><th>buy yes</th><th>buy no</th></tr>${rows}
    </table></div>${toggle}${sum}${inc}`;
}

function planPanel(plan, states, holdings) {
  if (!plan) return "";
  const held = holdings && holdings.tokens ? holdings : null;
  const arb = plan.is_arbitrage, ok = plan.status === "accepted";
  const cell = (k, v, c) => `<div class="cell"><div class="k">${k}</div>
    <div class="v ${c || ""}">${v}</div></div>`;

  if (!plan.orders || !plan.orders.length) {
    if (!held) return `<div class="card quietbox"><h2 class="muted">No position</h2>
      <div class="reason" style="margin-top:6px">${esc(plan.reason || plan.status)}</div></div>`;
    return `<div class="card">
      <div class="head"><h2>Holding</h2><span class="tag">${held.tokens} position(s)</span></div>
      <div class="reason" style="margin:8px 0 16px">no new orders &mdash; ${esc(plan.reason || plan.status)}</div>
      <div class="grid">
        ${cell("committed", usd(held.cost))}
        ${cell("worst case", usd(plan.worst_case), cls(plan.worst_case))}
        ${cell("best case", usd(plan.best_case), cls(plan.best_case))}
        ${cell("cvar 10%", usd(plan.cvar), cls(-plan.cvar))}
      </div>
      <h3>profit and loss in every settlement outcome</h3>${payoffChart(states)}
    </div>`;
  }
  const orders = plan.orders.map((o) => `<tr>
      <td>${esc(o.leg)}</td><td>${esc(o.side)}</td><td>${Number(o.price).toFixed(3)}</td>
      <td>${num(o.shares)}</td><td>${usd(o.cost)}</td>
      <td class="dim small">${esc(o.strip)}</td></tr>`).join("");
  return `<div class="card">
    <div class="head"><h2>${arb ? "Risk-free trade" : ok ? "Positive expected value" : "Plan not taken"}</h2>
      <span class="tag ${ok ? "solid" : ""}">${esc(plan.status)}</span></div>
    <div class="reason" style="margin:8px 0 16px">${esc(plan.reason)}</div>
    <div class="grid">
      ${cell("stake", usd(plan.capital_used))}
      ${cell("expected", usd(plan.expected_pnl), cls(plan.expected_pnl))}
      ${cell("on capital", spct(plan.return_on_capital, 2), cls(plan.return_on_capital))}
      ${cell("worst case", usd(plan.worst_case), cls(plan.worst_case))}
      ${cell("best case", usd(plan.best_case), cls(plan.best_case))}
      ${cell("cvar 10%", usd(plan.cvar), cls(-plan.cvar))}
    </div>
    <div class="scroll" style="margin-top:16px"><table>
      <tr><th>leg</th><th>side</th><th>price</th><th>shares</th><th>cost</th><th>market</th></tr>
      ${orders}</table></div>
    <h3>profit and loss in every settlement outcome</h3>${payoffChart(states)}
    <div class="small muted" style="margin-top:10px">
      Monte-Carlo noise on the expected value: ${usd(plan.ev_noise)} per standard error.</div>
    ${held ? `<div class="small muted" style="margin-top:6px">Risk figures cover the combined book:
      ${held.tokens} position(s) already held, ${usd(held.cost)} committed.</div>` : ""}
  </div>`;
}

function groupSection(g) {
  if (g.error) return `<div class="card quietbox"><h2>${esc(g.strips.join(", "))}</h2>
    <div class="down small" style="margin-top:6px">${esc(g.error)}</div></div>`;
  return `<div class="card">
      <div class="head"><h2>settles ${esc(g.expiry.replace("T", " ").replace("+00:00", " UTC"))}</h2>
        <span class="tag">in ${dur(g.seconds_to_expiry)}</span></div>
      <div class="small muted mono" style="margin-top:6px">${esc(g.vols_text || "")}</div>
      ${(g.surfaces || []).map(surfaceTable).join("")}
    </div>${planPanel(g.plan, g.states, g.holdings)}`;
}

function touchSection(t) {
  const trades = (t.plan.trades || []).map((x) => `<tr>
      <td>${esc(x.leg)}</td><td>${esc(x.side)}</td><td>${Number(x.price).toFixed(3)}</td>
      <td>${pct(x.fair)}</td><td><span class="chip ${cls(x.edge)}">${sgn(x.edge)}</span></td>
      <td>${num(x.shares)}</td><td>${usd(x.cost)}</td></tr>`).join("");
  return `<div class="card tight">
    <div class="head"><h2>${esc(t.title)}</h2><span class="tag">in ${dur(t.seconds_to_expiry)}</span></div>
    <div class="small muted mono" style="margin-top:6px">${esc(t.vols_text || "")}</div>
    ${trades ? `<div class="scroll" style="margin-top:12px"><table>
        <tr><th>barrier</th><th>side</th><th>price</th><th>model</th><th>edge</th><th>shares</th><th>cost</th></tr>
        ${trades}</table></div>` : '<div class="empty small">no barrier market cleared the edge threshold</div>'}
  </div>`;
}

function accountSection(d) {
  const a = d.account || {};
  const cell = (k, v, c) => `<div class="cell"><div class="k">${k}</div>
    <div class="v ${c || ""}">${v}</div></div>`;
  const positions = (d.positions || []).map((p) => `<tr>
      <td>${esc(p.leg_label)}</td><td>${esc(p.side)}</td><td>${num(p.shares)}</td>
      <td>${Number(p.avg_price).toFixed(3)}</td>
      <td>${p.mark == null ? '<span class="dim">-</span>' : Number(p.mark).toFixed(3)}</td>
      <td>${usd(p.cost_basis)}</td>
      <td class="dim small">${esc((p.strip_slug || "").replace("bitcoin-", ""))}</td></tr>`).join("");
  const fills = (d.fills || []).slice(0, 10).map((f) => `<tr>
      <td>${esc(f.ts.slice(11, 19))}</td><td>${esc(f.leg_label)}</td><td>${esc(f.side)}</td>
      <td>${Number(f.price).toFixed(3)}</td><td>${num(f.shares)}</td><td>${usd(f.cost)}</td></tr>`).join("");
  return `<div class="card">
    <h2>account</h2>
    <div class="grid" style="margin-top:14px">
      ${cell("equity", usd(a.equity))}${cell("cash", usd(a.cash))}
      ${cell("at risk", usd(a.exposure))}
      ${cell("realised", usd(a.realized_pnl), cls(a.realized_pnl))}
      ${cell("fees paid", usd(a.fees_paid))}
      ${cell("return", spct(a.total_return, 2), cls(a.total_return))}
      ${cell("drawdown", pct(a.drawdown, 2), a.drawdown > 0 ? "down" : "")}
      ${cell("fills", a.fills == null ? "0" : a.fills)}
    </div>
    <h3>equity</h3>${sparkline(d.equity_curve)}
    ${positions ? `<h3>open positions</h3><div class="scroll"><table>
        <tr><th>leg</th><th>side</th><th>shares</th><th>avg</th><th>mark</th><th>cost</th><th>market</th></tr>
        ${positions}</table></div>` : '<h3>open positions</h3><div class="empty small">flat</div>'}
    ${fills ? `<h3>recent fills</h3><div class="scroll"><table>
        <tr><th>time</th><th>leg</th><th>side</th><th>price</th><th>shares</th><th>cost</th></tr>
        ${fills}</table></div>` : ""}
  </div>`;
}

function render() {
  const d = DATA || {};
  const mode = document.getElementById("mode");
  mode.textContent = d.live_armed ? "live" : (d.mode || "paper");
  mode.className = "tag" + (d.live_armed ? " live" : "");
  document.getElementById("stamp").textContent = d.generated_at
    ? new Date(d.generated_at).toLocaleTimeString() + " \u00b7 " + (d.elapsed_s || 0) + "s" : "";
  document.getElementById("hero").innerHTML = heroBlock(d);

  const parts = [carrySection(CARRY)];
  if (d.error) parts.push(`<div class="card quietbox"><h2>analysis failed</h2>
    <div class="down small" style="margin-top:6px">${esc(d.error)}</div></div>`);
  parts.push(accountSection(d));
  (d.groups || []).forEach((g) => parts.push(groupSection(g)));
  (d.touch || []).forEach((t) => parts.push(touchSection(t)));
  if ((d.notes || []).length) {
    parts.push(`<div class="card quietbox"><h2>notes</h2><div style="margin-top:8px">` +
      d.notes.map((n) => `<div class="small muted">${esc(n)}</div>`).join("") + `</div></div>`);
  }
  document.getElementById("root").innerHTML = parts.join("");
}

async function refresh(force) {
  if (!LIVE) return;
  const btn = document.getElementById("refresh");
  btn.disabled = true; btn.textContent = "Loading";
  try {
    DATA = await (await fetch("/api/snapshot" + (force ? "?force=1" : ""))).json();
    render();
  } catch (e) { console.error(e); }
  finally { btn.disabled = false; btn.textContent = "Refresh"; }
}

async function loadCarry() {
  if (!LIVE) return;
  try {
    CARRY = await (await fetch("/api/carry")).json();
    render();
  } catch (e) { console.error(e); }
}

document.getElementById("refresh").addEventListener("click", () => refresh(true));
document.getElementById("theme").addEventListener("click", () => {
  const now = document.documentElement.getAttribute("data-theme");
  const next = now === "dark" ? "light" : now === "light" ? "" : "dark";
  if (next) document.documentElement.setAttribute("data-theme", next);
  else document.documentElement.removeAttribute("data-theme");
});
if (!LIVE) document.getElementById("refresh").style.display = "none";
render();
if (LIVE) { loadCarry(); setInterval(() => refresh(false), 20000); }
</script>
"""
