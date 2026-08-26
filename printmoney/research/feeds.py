"""Official daily readings that explain what the price table cannot.

The market table says gold moved. It does not say *why*, and "why" is most of
what makes a brief worth reading twice. These feeds carry the handful of numbers
that actually drive the assets in the universe, taken from the bodies that
publish them rather than from anyone's summary of them:

    US Treasury    the nominal yield curve, and the real (TIPS) curve.  The
                   10-year real yield is the single number gold trades against,
                   and the 2s10s spread is the market's own read on the cycle.
    NY Fed         the effective federal funds rate, i.e. where policy actually
                   is rather than where a headline says it is.
    CBOE           VIX, SKEW and VVIX, straight from the exchange that computes
                   them, with history back to 1990 for VIX.

Two rules carried over from the rest of the project.

*Nothing here is a forecast.*  A real yield is a fact about today; the brief may
use it to explain a move that already happened, never to call the next one.

*Nothing ships unmeasured.*  Every series here is put through
:mod:`printmoney.research.macro` before the brief is allowed to mention it, and
the ones that turn out to say nothing are reported as saying nothing.

What is deliberately absent, having been checked and found unavailable rather
than merely skipped:

    CME FedWatch          rate-cut probabilities.  The page is JavaScript and
                          the underlying futures settlements are a paid feed.
                          The 2-year Treasury yield is the free stand-in and it
                          is not a bad one - repricing the Fed path *is* what
                          the 2-year does.
    CBOE put/call ratio   the CSVs return 403 to anything but a browser.
    SPDR GLD tonnage      State Street moved the file; the old URL 404s.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from ..util import STATE_DIR, read_json, retry, write_json

log = logging.getLogger("printmoney.feeds")

#: A contactable agent, because two of these hosts ask for one and all of them
#: deserve one.
UA = "printmoney/1.0 daily brief (https://github.com/ayfew/Print-Money)"

TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/"
            "interest-rates/daily-treasury-rates.csv/{year}/all"
            "?type=daily_treasury_{kind}&field_tdr_date_value={year}&page&_format=csv")
CBOE = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"
NYFED = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/{n}.json"
SOMA = "https://markets.newyorkfed.org/api/soma/summary.json"
AUCTIONS = ("https://www.treasurydirect.gov/TA_WS/securities/auctioned"
            "?format=json&pagesize={n}")

CACHE_DIR = STATE_DIR / "feeds"

#: These publish once a day, after the close. Six hours keeps a morning run from
#: re-downloading on every invocation without ever serving yesterday's number as
#: though it were today's.
CACHE_HOURS = 6.0

#: Bumped when the *meaning* of a cached row changes rather than its shape.
#: Price bars already learned this the expensive way: a cache written before the
#: switch to adjusted closes read back without complaint and served unadjusted
#: prices to code that assumed otherwise. Nothing failed, which was the problem.
SCHEMA = 1



@dataclass
class Line:
    """One daily observation of one named series."""

    day: str          # YYYY-MM-DD
    value: float


@dataclass
class Feed:
    """A named daily series, newest last, with the source it came from."""

    key: str
    name: str
    source: str       # id in sources.REGISTRY
    unit: str
    lines: list[Line] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.lines)

    @property
    def latest(self) -> Line | None:
        return self.lines[-1] if self.lines else None

    def by_day(self) -> dict[str, float]:
        return {l.day: l.value for l in self.lines}

    def change(self, back: int = 1) -> float | None:
        """Move over the last ``back`` observations, in the series' own unit."""
        if len(self.lines) <= back:
            return None
        return self.lines[-1].value - self.lines[-1 - back].value

    def percentile(self, lookback: int = 504) -> float | None:
        """Where the latest reading sits in its own recent history."""
        window = [l.value for l in self.lines[-lookback:]]
        if len(window) < 30:
            return None
        return sum(1 for v in window if v <= window[-1]) / len(window)

    def to_dict(self) -> dict[str, Any]:
        latest = self.latest
        return {
            "key": self.key, "name": self.name, "source": self.source,
            "unit": self.unit, "n": len(self.lines),
            "day": latest.day if latest else None,
            "value": round(latest.value, 4) if latest else None,
            "change_1d": (round(c, 4) if (c := self.change(1)) is not None else None),
            "change_5d": (round(c, 4) if (c := self.change(5)) is not None else None),
            "percentile": (round(p, 3) if (p := self.percentile()) is not None else None),
        }


# --------------------------------------------------------------------------- #
def _get(url: str, *, what: str) -> str:
    def once() -> str:
        r = httpx.get(url, timeout=30.0, headers={"User-Agent": UA},
                      follow_redirects=True)
        r.raise_for_status()
        return r.text

    return retry(once, attempts=3, what=what)


def _cached(key: str, fetch, *, cache_hours: float = CACHE_HOURS,
            offline: bool = False) -> list[dict[str, Any]]:
    """Fetch-or-reuse, with a stale copy always preferred over nothing.

    A feed that cannot be reached should cost the brief one line of context, not
    the whole morning - so every failure path here ends in yesterday's numbers
    plus a warning, never in an exception reaching the caller.
    """
    path = CACHE_DIR / f"{key}.json"
    blob = read_json(path, default=None)
    if blob and blob.get("schema") != SCHEMA:
        blob = None                     # written by an older parser; refetch
    fresh = bool(blob) and blob.get("fetched_at", 0.0) > (
        datetime.now(timezone.utc).timestamp() - cache_hours * 3600)
    if blob and (fresh or offline):
        return blob["rows"]
    if offline:
        return []
    try:
        rows = fetch()
    except Exception as exc:                       # noqa: BLE001 - never fatal
        log.warning("feed %s unavailable (%s)", key, exc)
        return blob["rows"] if blob else []
    write_json(path, {"schema": SCHEMA,
                      "fetched_at": datetime.now(timezone.utc).timestamp(),
                      "rows": rows})
    return rows


def _to_lines(rows: Iterable[dict[str, Any]]) -> list[Line]:
    return sorted((Line(day=r["day"], value=float(r["value"])) for r in rows),
                  key=lambda l: l.day)


def _iso(us_date: str) -> str | None:
    """MM/DD/YYYY, which is what both Treasury and CBOE hand back."""
    try:
        return datetime.strptime(us_date.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
def parse_treasury(text: str, column: str) -> list[dict[str, Any]]:
    """One named column out of a Treasury daily-rates CSV."""
    out: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(text)):
        day = _iso(row.get("Date", ""))
        raw = (row.get(column) or "").strip()
        if day and raw:
            try:
                out.append({"day": day, "value": float(raw)})
            except ValueError:
                continue
    return out


def _treasury_rows(kind: str, column: str, years: int = 3) -> list[dict[str, Any]]:
    """One column of one Treasury curve, over the last few calendar years."""
    now = datetime.now(timezone.utc).year
    out: list[dict[str, Any]] = []
    for year in range(now - years + 1, now + 1):
        text = _get(TREASURY.format(year=year, kind=kind), what=f"treasury {kind}")
        out += parse_treasury(text, column)
    if not out:
        raise ValueError(f"treasury {kind}/{column} parsed to nothing")
    return out


def parse_cboe(text: str) -> list[dict[str, Any]]:
    """A Cboe daily-price CSV, taking the last column as the close.

    VIX_History carries OPEN/HIGH/LOW/CLOSE while SKEW and VVIX carry a single
    value, so the close is located by position rather than by name - the one
    thing both layouts agree on.
    """
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return []
    close = len(header) - 1
    out = []
    for row in reader:
        if len(row) <= close:
            continue
        day = _iso(row[0])
        if not day:
            continue
        try:
            out.append({"day": day, "value": float(row[close])})
        except ValueError:
            continue
    return out


def _cboe_rows(name: str) -> list[dict[str, Any]]:
    rows = parse_cboe(_get(CBOE.format(name=name), what=f"cboe {name}"))
    if not rows:
        raise ValueError(f"cboe {name} parsed to nothing")
    return rows


def parse_effr(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    return [{"day": r["effectiveDate"], "value": float(r["percentRate"])}
            for r in payload.get("refRates", [])
            if r.get("percentRate") is not None and r.get("effectiveDate")]


def _effr_rows(n: int = 250) -> list[dict[str, Any]]:
    rows = parse_effr(_get(NYFED.format(n=n), what="nyfed effr"))
    if not rows:
        raise ValueError("nyfed effr parsed to nothing")
    return rows


def parse_auctions(text: str, field: str) -> list[dict[str, Any]]:
    """One demand statistic per coupon auction, keyed by auction date.

    Bills are excluded. They are rolled constantly by money-market funds and
    their demand statistics say almost nothing about appetite for US duration,
    which is the question these numbers are being asked.

    Three fields matter, and they are the measurable form of "could the Treasury
    sell it":

        ``bid_to_cover``  total bids over amount sold. Higher is more demand.
        ``dealer``        the share taken by primary dealers, who are obliged to
                          bid and therefore absorb whatever nobody else wanted.
                          A *high* number means weak demand, not strong.
        ``indirect``      the share taken by indirect bidders, largely foreign
                          central banks. Higher means more foreign appetite.
    """
    rows: list[dict[str, Any]] = []
    for a in json.loads(text):
        if a.get("securityType") not in ("Note", "Bond"):
            continue
        day = (a.get("auctionDate") or "")[:10]
        total = _num(a.get("totalAccepted"))
        if not day or not total:
            continue
        if field == "bid_to_cover":
            value = _num(a.get("bidToCoverRatio"))
        elif field == "dealer":
            value = 100.0 * (_num(a.get("primaryDealerAccepted")) or 0.0) / total
        elif field == "indirect":
            value = 100.0 * (_num(a.get("indirectBidderAccepted")) or 0.0) / total
        else:
            value = None
        if value:
            rows.append({"day": day, "value": value})
    # Several tenors are auctioned on the same day; average them so the series
    # stays one observation per day and cannot double-count a single session.
    merged: dict[str, list[float]] = {}
    for r in rows:
        merged.setdefault(r["day"], []).append(r["value"])
    return [{"day": d, "value": sum(v) / len(v)} for d, v in sorted(merged.items())]


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _auction_rows(field: str, n: int = 400) -> list[dict[str, Any]]:
    rows = parse_auctions(_get(AUCTIONS.format(n=n), what="treasury auctions"), field)
    if not rows:
        raise ValueError(f"treasury auctions/{field} parsed to nothing")
    return rows


def parse_soma(text: str) -> list[dict[str, Any]]:
    """The Fed's own holdings, in trillions. Weekly."""
    payload = json.loads(text)
    out = []
    for row in payload.get("soma", {}).get("summary", []):
        total = _num(row.get("total"))
        if row.get("asOfDate") and total:
            out.append({"day": row["asOfDate"], "value": total / 1e12})
    return out


def _soma_rows() -> list[dict[str, Any]]:
    rows = parse_soma(_get(SOMA, what="nyfed soma"))
    if not rows:
        raise ValueError("nyfed soma parsed to nothing")
    return rows


# --------------------------------------------------------------------------- #
#: Everything the brief is allowed to read, keyed the way it is referred to.
#: ``source`` points at :mod:`sources`, so a feed cannot appear in the brief
#: without a citation any more than a note can.
SPECS: dict[str, dict[str, Any]] = {
    "ust10y": dict(name="10-year Treasury yield", source="treasury", unit="%",
                   fetch=lambda: _treasury_rows("yield_curve", "10 Yr")),
    "ust2y": dict(name="2-year Treasury yield", source="treasury", unit="%",
                  fetch=lambda: _treasury_rows("yield_curve", "2 Yr")),
    "real10y": dict(name="10-year real yield (TIPS)", source="treasury", unit="%",
                    fetch=lambda: _treasury_rows("real_yield_curve", "10 YR")),
    "effr": dict(name="effective fed funds rate", source="nyfed", unit="%",
                 fetch=_effr_rows),
    "vix": dict(name="VIX", source="cboe", unit="pts",
                fetch=lambda: _cboe_rows("VIX")),
    "skew": dict(name="CBOE SKEW", source="cboe", unit="pts",
                 fetch=lambda: _cboe_rows("SKEW")),
    "vvix": dict(name="VVIX", source="cboe", unit="pts",
                 fetch=lambda: _cboe_rows("VVIX")),
    "auction_btc": dict(name="auction bid-to-cover", source="treasurydirect",
                        unit="x", fetch=lambda: _auction_rows("bid_to_cover")),
    "auction_dealer": dict(name="primary dealer take-up", source="treasurydirect",
                           unit="%", fetch=lambda: _auction_rows("dealer")),
    "auction_indirect": dict(name="foreign/indirect take-up",
                             source="treasurydirect", unit="%",
                             fetch=lambda: _auction_rows("indirect")),
    "soma": dict(name="Fed balance sheet (SOMA)", source="nyfed", unit="$T",
                 fetch=_soma_rows),
}


def load(keys: Iterable[str] | None = None, *, cache_hours: float = CACHE_HOURS,
         offline: bool = False) -> dict[str, Feed]:
    """Every requested feed that could be read, keyed by name.

    Missing feeds are simply absent from the result rather than present and
    empty, so a caller that iterates cannot accidentally render a blank reading
    as though it were a zero.
    """
    out: dict[str, Feed] = {}
    for key in (keys if keys is not None else SPECS):
        spec = SPECS.get(key)
        if spec is None:
            continue
        rows = _cached(key, spec["fetch"], cache_hours=cache_hours, offline=offline)
        if not rows:
            continue
        out[key] = Feed(key=key, name=spec["name"], source=spec["source"],
                        unit=spec["unit"], lines=_to_lines(rows))
    return out


def curve_spread(feeds: dict[str, Feed]) -> Feed | None:
    """2s10s: the ten-year minus the two-year, aligned on shared days.

    Derived rather than published, so it is tier 3 and says so. Negative is an
    inverted curve, which is the reading everyone quotes and almost nobody can
    trade on - it has led recessions by a year or more, which is the wrong
    horizon for a note about today.
    """
    ten, two = feeds.get("ust10y"), feeds.get("ust2y")
    if not ten or not two:
        return None
    a, b = ten.by_day(), two.by_day()
    shared = sorted(set(a) & set(b))
    if not shared:
        return None
    return Feed(key="curve", name="2s10s spread", source="curve", unit="%",
                lines=[Line(day=d, value=a[d] - b[d]) for d in shared])
