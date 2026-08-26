"""Polymarket read-only feeds: Gamma (market metadata) + CLOB (order books).

Only public, unauthenticated endpoints are used here.  Nothing in this module can
place, cancel or sign an order - order entry lives in ``printmoney.broker``.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Sequence

import httpx

from ..config import Config
from ..util import USER_AGENT, chunked, parse_iso, retry, safe_float, utcnow
from .types import (
    INF,
    Leg,
    LegKind,
    OrderBook,
    Strip,
    StripKind,
    classify_leg,
    infer_strip_kind,
)

log = logging.getLogger("printmoney.polymarket")


class PolymarketError(RuntimeError):
    pass


class PolymarketClient:
    """Thin, synchronous, retrying HTTP client for the two public APIs."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._client = httpx.Client(
            timeout=cfg.data.http_timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PolymarketClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # gamma
    # ------------------------------------------------------------------ #
    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        def call() -> Any:
            r = self._client.get(url, params=params)
            r.raise_for_status()
            return r.json()

        return retry(call, what=f"GET {url}")

    def _post_json(self, url: str, body: Any) -> Any:
        def call() -> Any:
            r = self._client.post(url, json=body)
            r.raise_for_status()
            return r.json()

        return retry(call, what=f"POST {url}")

    def fetch_events(self, limit: int = 300) -> list[dict[str, Any]]:
        """Active, open events ordered by 24h volume."""
        data = self._get_json(
            f"{self.cfg.data.gamma_url}/events",
            {
                "closed": "false",
                "active": "true",
                "limit": limit,
                "order": "volume24hr",
                "ascending": "false",
            },
        )
        if not isinstance(data, list):
            raise PolymarketError(f"unexpected /events payload: {type(data)}")
        return data

    def fetch_event_by_slug(self, slug: str) -> dict[str, Any] | None:
        data = self._get_json(f"{self.cfg.data.gamma_url}/events", {"slug": slug})
        if isinstance(data, list) and data:
            return data[0]
        return None

    # ------------------------------------------------------------------ #
    # clob
    # ------------------------------------------------------------------ #
    def fetch_books(self, token_ids: Sequence[str]) -> dict[str, OrderBook]:
        """Batched order books, keyed by token id.

        The CLOB happily returns fewer entries than requested (illiquid tokens are
        simply omitted), so we key by ``asset_id`` rather than trusting order.
        """
        out: dict[str, OrderBook] = {}
        uniq = [t for t in dict.fromkeys(token_ids) if t]
        for batch in chunked(uniq, self.cfg.data.books_batch_size):
            body = [{"token_id": t} for t in batch]
            try:
                payload = self._post_json(f"{self.cfg.data.clob_url}/books", body)
            except Exception as exc:  # noqa: BLE001
                log.warning("batched /books failed (%s); falling back to per-token GET", exc)
                for t in batch:
                    book = self._fetch_book_single(t)
                    if book is not None:
                        out[t] = book
                continue
            if not isinstance(payload, list):
                continue
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                book = OrderBook.from_clob(entry)
                if book.token_id:
                    out[book.token_id] = book
        missing = [t for t in uniq if t not in out]
        if missing:
            log.debug("no book returned for %d token(s)", len(missing))
        return out

    def _fetch_book_single(self, token_id: str) -> OrderBook | None:
        try:
            payload = self._get_json(
                f"{self.cfg.data.clob_url}/book", {"token_id": token_id}
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("book fetch failed for %s: %s", token_id[:12], exc)
            return None
        if not isinstance(payload, dict):
            return None
        payload.setdefault("asset_id", token_id)
        return OrderBook.from_clob(payload)

    # ------------------------------------------------------------------ #
    # strips
    # ------------------------------------------------------------------ #
    def scan_strips(self) -> list[Strip]:
        """Find BTC strips that pass the configured filters, books attached."""
        events = self.fetch_events()
        selected = [e for e in events if self._event_matches(e)]
        log.info("scanned %d events, %d match filters", len(events), len(selected))

        strips: list[Strip] = []
        for ev in selected:
            try:
                strip = build_strip(ev, cfg=self.cfg)
            except Exception as exc:  # noqa: BLE001
                log.warning("skipping event %s: %s", ev.get("slug"), exc)
                continue
            if not self._strip_matches(strip):
                continue
            strips.append(strip)

        strips.sort(key=lambda s: -s.volume_24h)
        strips = strips[: self.cfg.filters.max_events]
        self.hydrate(strips)
        return strips

    def strip_from_slug(self, slug: str, *, hydrate: bool = True) -> Strip | None:
        ev = self.fetch_event_by_slug(slug)
        if ev is None:
            return None
        strip = build_strip(ev, cfg=self.cfg)
        if hydrate:
            self.hydrate([strip])
        return strip

    def hydrate(self, strips: Iterable[Strip]) -> None:
        """Attach live YES/NO order books to every leg of every strip."""
        strips = list(strips)
        tokens: list[str] = []
        for s in strips:
            for leg in s.legs:
                tokens.extend([leg.yes_token, leg.no_token])
        books = self.fetch_books(tokens)
        now = utcnow()
        for s in strips:
            for leg in s.legs:
                leg.yes_book = books.get(leg.yes_token, OrderBook(token_id=leg.yes_token))
                leg.no_book = books.get(leg.no_token, OrderBook(token_id=leg.no_token))
                leg.yes_book.tick_size = leg.tick_size
                leg.no_book.tick_size = leg.tick_size
                leg.yes_book.min_order_size = leg.min_order_shares
                leg.no_book.min_order_size = leg.min_order_shares
                leg.yes_book.fetched_at = now
                leg.no_book.fetched_at = now
            s.fetched_at = now

    # ------------------------------------------------------------------ #
    def _event_matches(self, ev: dict[str, Any]) -> bool:
        f = self.cfg.filters
        slug = (ev.get("slug") or "").lower()
        if not slug:
            return False
        if not any(p.lower() in slug for p in f.include_slug_patterns):
            return False
        if any(p.lower() in slug for p in f.exclude_slug_patterns):
            return False
        if ev.get("closed") or not ev.get("active", True):
            return False
        if (safe_float(ev.get("liquidity"), 0.0) or 0.0) < f.min_event_liquidity_usd:
            return False
        return len(ev.get("markets") or []) >= 2

    def _strip_matches(self, strip: Strip) -> bool:
        f = self.cfg.filters
        tte = strip.seconds_to_expiry()
        if tte < f.min_seconds_to_expiry:
            log.debug("skip %s: expires in %.0fs", strip.slug, tte)
            return False
        if tte > f.max_seconds_to_expiry:
            log.debug("skip %s: expires in %.1f days", strip.slug, tte / 86400)
            return False
        if strip.kind is StripKind.TOUCH and not self.cfg.strategy.enable_touch_markets:
            return False
        return len(strip.legs) >= 2


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def _json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            val = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return val if isinstance(val, list) else []
    return []


def _fee_schedule(market: dict[str, Any], cfg_rate: float, cfg_exp: float) -> tuple[float, float, bool]:
    sched = market.get("feeSchedule")
    if isinstance(sched, dict) and market.get("feesEnabled", True):
        rate = safe_float(sched.get("rate"), cfg_rate) or cfg_rate
        exp = safe_float(sched.get("exponent"), cfg_exp) or cfg_exp
        taker_only = bool(sched.get("takerOnly", True))
        return rate, exp, taker_only
    if not market.get("feesEnabled", True):
        return 0.0, cfg_exp, True
    return cfg_rate, cfg_exp, True


def build_leg(
    market: dict[str, Any],
    strip_kind: StripKind,
    *,
    fee_rate: float = 0.07,
    fee_exponent: float = 1.0,
) -> Leg:
    tokens = _json_list(market.get("clobTokenIds"))
    if len(tokens) < 2:
        raise ValueError(f"market {market.get('id')} has no CLOB tokens")

    outcomes = [str(o).strip().lower() for o in _json_list(market.get("outcomes"))]
    yes_idx = outcomes.index("yes") if "yes" in outcomes else 0
    no_idx = 1 - yes_idx if len(tokens) == 2 else (outcomes.index("no") if "no" in outcomes else 1)

    question = str(market.get("question") or "")
    label = str(market.get("groupItemTitle") or "")
    kind, geo = classify_leg(question, label)

    # Inside a bracket strip the ">74,000" leg is the open top bucket, not a
    # standalone digital; converting it here keeps the strip a true partition.
    if strip_kind is StripKind.BRACKET and kind is LegKind.ABOVE:
        kind, geo = LegKind.RANGE, {"lo": geo["strike"], "hi": INF}

    prices = _json_list(market.get("outcomePrices"))
    market_price = safe_float(prices[yes_idx]) if len(prices) > yes_idx else None

    rate, exp, taker_only = _fee_schedule(market, fee_rate, fee_exponent)

    return Leg(
        market_id=str(market.get("id") or ""),
        condition_id=str(market.get("conditionId") or ""),
        slug=str(market.get("slug") or ""),
        question=question,
        label=label,
        kind=kind,
        yes_token=str(tokens[yes_idx]),
        no_token=str(tokens[no_idx]),
        lo=float(geo.get("lo", -INF)),
        hi=float(geo.get("hi", INF)),
        strike=float(geo.get("strike", float("nan"))),
        barrier=float(geo.get("barrier", float("nan"))),
        liquidity_usd=safe_float(market.get("liquidityNum"), 0.0) or 0.0,
        volume_24h=safe_float(market.get("volume24hr"), 0.0) or 0.0,
        fee_rate=rate,
        fee_exponent=exp,
        fee_taker_only=taker_only,
        tick_size=safe_float(market.get("orderPriceMinTickSize"), 0.001) or 0.001,
        min_order_shares=safe_float(market.get("orderMinSize"), 5.0) or 5.0,
        accepting_orders=bool(market.get("acceptingOrders", True))
        and bool(market.get("active", True))
        and not market.get("closed", False),
        neg_risk=bool(market.get("negRisk", False)),
        market_price=market_price,
    )


def build_strip(event: dict[str, Any], *, cfg: Config | None = None) -> Strip:
    slug = str(event.get("slug") or "")
    title = str(event.get("title") or "")
    kind = infer_strip_kind(slug, title)

    end_raw = event.get("endDate") or event.get("end_date")
    if not end_raw:
        raise ValueError(f"event {slug} has no endDate")
    expiry = parse_iso(str(end_raw))
    start_raw = event.get("startDate") or event.get("start_date")
    start = parse_iso(str(start_raw)) if start_raw else None

    fee_rate = cfg.fees.rate if cfg else 0.07
    fee_exp = cfg.fees.exponent if cfg else 1.0

    legs: list[Leg] = []
    for market in event.get("markets") or []:
        try:
            legs.append(build_leg(market, kind, fee_rate=fee_rate, fee_exponent=fee_exp))
        except Exception as exc:  # noqa: BLE001
            log.debug("skipping market %s in %s: %s", market.get("id"), slug, exc)

    if not legs:
        raise ValueError(f"event {slug} produced no usable legs")

    # Sort by the natural price ordering so downstream tables read like a ladder.
    def sort_key(leg: Leg) -> tuple[int, float]:
        if leg.kind is LegKind.RANGE:
            return (0, leg.lo if leg.lo != -INF else -1e18)
        if leg.kind is LegKind.ABOVE:
            return (0, leg.strike)
        return (1 if leg.kind is LegKind.TOUCH_UP else -1, leg.barrier)

    legs.sort(key=sort_key)

    return Strip(
        event_id=str(event.get("id") or ""),
        slug=slug,
        title=title,
        kind=kind,
        expiry=expiry,
        start=start,
        legs=legs,
        neg_risk=bool(event.get("negRisk", False)),
        liquidity_usd=safe_float(event.get("liquidity"), 0.0) or 0.0,
        volume_24h=safe_float(event.get("volume24hr"), 0.0) or 0.0,
    )


def strip_from_dict(payload: dict[str, Any]) -> Strip:
    """Rebuild a Strip from a recorded snapshot (used by the backtester)."""
    from .types import BookLevel

    def book(raw: dict[str, Any] | None, token: str) -> OrderBook:
        raw = raw or {}
        return OrderBook(
            token_id=str(raw.get("token_id") or token),
            bids=[BookLevel(float(l["price"]), float(l["size"])) for l in raw.get("bids", [])],
            asks=[BookLevel(float(l["price"]), float(l["size"])) for l in raw.get("asks", [])],
            timestamp=parse_iso(raw["timestamp"]) if raw.get("timestamp") else utcnow(),
            fetched_at=parse_iso(raw["fetched_at"]) if raw.get("fetched_at") else utcnow(),
            tick_size=float(raw.get("tick_size", 0.001)),
            min_order_size=float(raw.get("min_order_size", 5.0)),
            neg_risk=bool(raw.get("neg_risk", False)),
        )

    legs: list[Leg] = []
    for lr in payload.get("legs", []):
        legs.append(
            Leg(
                market_id=str(lr.get("market_id", "")),
                condition_id="",
                slug="",
                question="",
                label=str(lr.get("label", "")),
                kind=LegKind(lr["kind"]),
                yes_token=str(lr.get("yes_token", "")),
                no_token=str(lr.get("no_token", "")),
                lo=-INF if lr.get("lo") is None else float(lr["lo"]),
                hi=INF if lr.get("hi") is None else float(lr["hi"]),
                strike=float("nan") if lr.get("strike") is None else float(lr["strike"]),
                barrier=float("nan") if lr.get("barrier") is None else float(lr["barrier"]),
                liquidity_usd=float(lr.get("liquidity_usd", 0.0)),
                fee_rate=float(lr.get("fee_rate", 0.0)),
                market_price=lr.get("market_price"),
                yes_book=book(lr.get("yes_book"), str(lr.get("yes_token", ""))),
                no_book=book(lr.get("no_book"), str(lr.get("no_token", ""))),
            )
        )
    return Strip(
        event_id=str(payload.get("event_id", "")),
        slug=str(payload.get("slug", "")),
        title=str(payload.get("title", "")),
        kind=StripKind(payload["kind"]),
        expiry=parse_iso(payload["expiry"]),
        start=parse_iso(payload["start"]) if payload.get("start") else None,
        legs=legs,
        neg_risk=bool(payload.get("neg_risk", False)),
        liquidity_usd=float(payload.get("liquidity_usd", 0.0)),
        volume_24h=float(payload.get("volume_24h", 0.0)),
        fetched_at=parse_iso(payload["fetched_at"]) if payload.get("fetched_at") else utcnow(),
    )
