"""Attributed, uncertified security snapshots; never a historical index backfill.

Only the two public Wikipedia constituent tables are downloaded. A narrowly
reviewed public S&P announcement supplements the community baseline; it cannot
certify all intervening changes. Callers keep snapshots under state/research.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

URLS = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "nasdaq100": "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
}
NOTICE_URL = "https://press.spglobal.com/2026-10-01-Vylor-Added-to-the-S-P-500-Twilio-Set-to-Join-S-P-500-Others-to-Join-S-P-MidCap-400-and-S-P-SmallCap-600"
# Actual read-only review time, rather than treating announcement date as receipt.
NOTICE_OBSERVED_AT = "2026-10-04T03:16:10+00:00"
NOTICE_CHANGES = (
    {"symbol": "VYLR", "action": "addition", "name": "Vylor", "sector": None, "effective_at": "2026-10-01T13:30:00+00:00"},
    {"symbol": "CTVA", "action": "deletion", "name": "Corteva", "sector": "Materials", "effective_at": "2026-10-06T13:30:00+00:00"},
    {"symbol": "TWLO", "action": "addition", "name": "Twilio", "sector": "Information Technology", "effective_at": "2026-10-06T13:30:00+00:00"},
    {"symbol": "WBD", "action": "deletion", "name": "Warner Bros. Discovery", "sector": "Communication Services", "effective_at": "2026-10-06T13:30:00+00:00"},
    {"symbol": "VYLR", "action": "sector_change", "name": "Vylor", "sector": "Consumer Staples", "effective_at": "2026-10-06T13:30:00+00:00"},
)
SCHEMA_VERSION = 1
MAX_BYTES = 3_000_000
MIN_ROWS = {"sp500": 450, "nasdaq100": 90}
SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}\Z")


def _date(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timezone-aware membership clock required.")
    return result.astimezone(timezone.utc)


def _optional_source_clock(value, field, latest):
    """Keep unknowns/day precision; compare aware instants without inventing time.

    ISO YYYY-MM-DD values only establish a UTC calendar day and are compared
    against the receipt/snapshot/cutoff day. They never acquire a midnight
    instant. Other supplied values must be timezone-aware timestamps.
    """
    if value is None:
        return
    try:
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            future = date.fromisoformat(value) > latest.date()
        else:
            future = _date(value) > latest
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid optional membership source {field} clock.") from exc
    if future:
        raise ValueError(f"Membership source {field} after receipt/snapshot/cutoff at supplied precision.")


def _canonical(symbol):
    return {"BRK-B": "BRK.B", "BF-B": "BF.B"}.get(symbol, symbol)


def _provider(symbol):
    return {"BRK.B": "BRK-B", "BF.B": "BF-B"}.get(symbol, symbol)


def _safe_url(value):
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _version(snapshot):
    """Identify semantic evidence without changing its original receipt clocks.

    Only observation captured_at/retrieved_at keys are excluded recursively.
    Publication/as-of/effective dates, revisions, content hashes, membership,
    pending changes and all other metadata still distinguish the evidence.
    Legacy clock-inclusive hashes require an explicit validated migration.
    """
    # Validate the full artifact before excluding observation keys from identity;
    # otherwise NaN hidden in a clock-named metadata field could reach export.
    json.dumps(snapshot, allow_nan=False)
    def identity(value):
        if isinstance(value, dict):
            return {key: identity(child) for key, child in value.items() if key not in ("captured_at", "retrieved_at")}
        if isinstance(value, list):
            return [identity(child) for child in value]
        return value
    return _hash(identity({key: value for key, value in snapshot.items() if key != "version"}))


class _Tables(HTMLParser):
    """Read flat source tables and visible footer text, ignoring citation marks."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.links, self.text = [], [], []
        self.elements = []
        self.scripts, self.current_script, self.root_about = [], None, None
        self.depth = 0
        self.table = self.row = self.cell = None
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "html" and not self.elements:
            self.root_about = attr.get("about")
        if tag == "script":
            self.current_script = []
            self.scripts.append(self.current_script)
        if tag == "a" and attr.get("href"):
            current_context = any(
                attributes.get("id") == "t-permalink" or "printfooter" in (attributes.get("class") or "").split()
                for _, attributes in self.elements
            )
            self.links.append((attr["href"], current_context))
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"):
            self.elements.append((tag, attr))
        if tag in ("sup", "script", "style"):
            self.ignored += 1
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.table = []
        if self.depth == 1:
            if tag == "tr":
                self.row = []
            if tag in ("td", "th"):
                self.cell = []

    def handle_data(self, data):
        if self.current_script is not None:
            self.current_script.append(data)
        if self.ignored:
            return
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.current_script = None
        for position in range(len(self.elements) - 1, -1, -1):
            if self.elements[position][0] == tag:
                del self.elements[position:]
                break
        if tag in ("sup", "script", "style") and self.ignored:
            self.ignored -= 1
        if self.depth == 1:
            if tag in ("td", "th") and self.cell is not None:
                if self.row is not None:
                    self.row.append(" ".join("".join(self.cell).split()))
                self.cell = None
            if tag == "tr" and self.row is not None:
                self.table.append(self.row)
                self.row = None
        if tag == "table" and self.depth:
            if self.depth == 1:
                self.tables.append(self.table)
                self.table = None
            self.depth -= 1


def _parse_table(html, index, receipt):
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Constituent HTML missing or oversized.")
    parser = _Tables()
    parser.feed(html)
    parser.close()
    required = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "CIK"] if index == "sp500" else ["Ticker", "Company", "ICB Industry", "ICB Subsector"]
    candidates = [table for table in parser.tables if table and set(required).issubset(table[0])]
    if len(candidates) != 1 or parser.depth:
        raise ValueError("One complete, unambiguous constituent table required.")
    headers, *rows = candidates[0]
    if len(set(headers)) != len(headers):
        raise ValueError("Conflicting constituent headers.")
    if not MIN_ROWS[index] <= len(rows) <= 1000:
        raise ValueError("Implausibly small, empty or oversized constituent table.")
    members, seen = [], set()
    for cells in rows:
        if len(cells) != len(headers):
            raise ValueError("Malformed constituent row width.")
        row = dict(zip(headers, cells))
        symbol = _canonical(row[required[0]].strip().upper())
        if not SYMBOL.fullmatch(symbol) or symbol in seen or not row[required[1]]:
            raise ValueError("Invalid or duplicate security identity.")
        seen.add(symbol)
        cik = row.get("CIK") or None
        if cik and not re.fullmatch(r"\d{1,10}", cik):
            raise ValueError("Invalid constituent CIK.")
        members.append({"symbol": symbol, "provider_symbol": _provider(symbol), "name": row[required[1]],
                        "sector": row[required[2]] or None, "industry": row[required[3]] or None,
                        "cik": cik.zfill(10) if cik else None, "indices": [index]})
    match = re.search(r'"wgRevisionId"\s*:\s*(\d+)', " ".join("".join(script) for script in parser.scripts))
    revision = match[1] if match else None
    if revision is None:
        match = re.fullmatch(r'https://en\.wikipedia\.org/(?:wiki/Special:Redirect/revision|revision)/(\d+)', parser.root_about or "")
        revision = match[1] if match else None
    if revision is None:
        # Arbitrary history/archive links cannot identify the rendered revision.
        expected_title = "List_of_S&P_500_companies" if index == "sp500" else "List_of_NASDAQ-100_companies"
        revisions = set()
        for link, current_context in parser.links:
            parsed = urlsplit(link)
            query = parse_qs(parsed.query)
            title = query.get("title", [])
            oldid = query.get("oldid", [])
            if (current_context and parsed.scheme in ("", "https") and parsed.netloc in ("", "en.wikipedia.org")
                    and parsed.path == "/w/index.php" and title == [expected_title] and "diff" not in query
                    and "action" not in query and len(oldid) == 1 and oldid[0].isdigit()):
                revisions.add(oldid[0])
        revision = next(iter(revisions)) if len(revisions) == 1 else None
    visible = " ".join(" ".join(parser.text).split())
    match = re.search(r"This page was last edited on (\d{1,2} \w+ \d{4}), at (\d{2}:\d{2})\s*\(UTC\)", visible)
    edited = datetime.strptime(" ".join(match.groups()), "%d %B %Y %H:%M").replace(tzinfo=timezone.utc) if match else None
    if edited and edited > receipt:
        raise ValueError("Source last-edited metadata is after receipt cutoff.")
    warnings = []
    if not revision or not edited:
        warnings.append(f"{index}: actual revision or last-edited metadata unavailable; retained as unknown.")
    source = {"id": "wikipedia-" + index, "index": index, "url": URLS[index], "kind": "community",
              "status": "available", "captured_at": receipt.isoformat(), "retrieved_at": receipt.isoformat(),
              "revision": revision, "last_edited_at": edited.isoformat() if edited else None,
              "effective_at": None, "content_hash": hashlib.sha256(html.encode("utf-8")).hexdigest(),
              "license": "CC BY-SA 4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
              "attribution": "Wikipedia contributors, " + URLS[index] + (f" (revision {revision})" if revision else " (revision unknown)"),
              "adaptation_notice": "Adapted constituent table: selected fields, normalized symbols and combined index tags; CC BY-SA 4.0.",
              "verification": "community_uncertified", "row_count": len(members)}
    return members, source, warnings


def _snapshot(name, clock, members=None, sources=None, pending=None, warnings=None):
    members, sources = members or [], sources or []
    expected = list(URLS) if name == "sp500-nasdaq100" else ([name] if name in URLS else [])
    available = [index for index in expected if any(s.get("index") == index and s.get("status") == "available" for s in sources)]
    missing = [index for index in expected if index not in available]
    status = "complete" if not missing else "partial" if available else "unavailable"
    result = {"schema_version": SCHEMA_VERSION, "name": name, "captured_at": clock.isoformat(),
              "verification": "local_starter" if name == "starter" else "community_uncertified",
              "completeness": {"status": status, "expected_indices": expected, "available_indices": available,
                               "missing_indices": missing, "member_count": len(members), "certified_current": False},
              "sources": sources, "members": sorted(members, key=lambda m: m["symbol"]),
              "pending": pending or [], "warnings": warnings or []}
    result["version"] = _version(result)
    return result


def _notice(members, sources, cutoff, warnings):
    if _date(NOTICE_OBSERVED_AT) > cutoff:
        warnings.append("Reviewed S&P notice was observed after cutoff and was excluded.")
        return []
    evidence = {"url": NOTICE_URL, "published_at": "2026-10-01", "changes": NOTICE_CHANGES}
    sources.append({"id": "sp-notice-2026-10-01", "url": NOTICE_URL, "kind": "official_notice",
                    "status": "available", "captured_at": NOTICE_OBSERVED_AT, "retrieved_at": NOTICE_OBSERVED_AT,
                    "published_at": "2026-10-01", "verification": "reviewed_effective_dated_notice",
                    "content_hash": _hash(evidence), "hash_scope": "reviewed factual registry, not full page",
                    "attribution": "S&P Dow Jones Indices, public announcement dated October 1, 2026"})
    warnings.append("Community baseline plus one reviewed S&P notice; intervening changes and certified current membership remain unverified. Effective dates in community tables are unknown.")
    pending = []
    for change in NOTICE_CHANGES:
        entry = dict(change, index="sp500", source_url=NOTICE_URL, source_id="sp-notice-2026-10-01")
        member = next((m for m in members if m["symbol"] == change["symbol"]), None)
        if _date(change["effective_at"]) > cutoff:
            pending.append(entry)
            # A table may anticipate a announced replacement. Preserve the
            # effective-dated current security facts, not that premature basket.
            if change["action"] == "addition" and member and "sp500" in member["indices"]:
                member["indices"].remove("sp500")
                if not member["indices"]:
                    members.remove(member)
            elif change["action"] == "deletion":
                if member:
                    if "sp500" not in member["indices"]:
                        member["indices"].insert(0, "sp500")
                else:
                    members.append({"symbol": change["symbol"], "provider_symbol": _provider(change["symbol"]),
                                    "name": change["name"], "sector": change["sector"], "industry": None,
                                    "cik": None, "indices": ["sp500"]})
            elif change["action"] == "sector_change" and member and "sp500" in member["indices"]:
                member["sector"] = None
            continue
        if change["action"] == "deletion":
            if member and "sp500" in member["indices"]:
                member["indices"].remove("sp500")
                if not member["indices"]:
                    members.remove(member)
        elif change["action"] == "addition":
            if member:
                if "sp500" not in member["indices"]:
                    member["indices"].insert(0, "sp500")
            else:
                members.append({"symbol": change["symbol"], "provider_symbol": _provider(change["symbol"]),
                                "name": change["name"], "sector": change["sector"], "industry": None,
                                "cik": None, "indices": ["sp500"]})
        elif member and "sp500" in member["indices"]:
            member["sector"] = change["sector"]
    return pending


def _validate(snapshot, name, cutoff):
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SCHEMA_VERSION or snapshot.get("name") != name:
        raise ValueError("Membership snapshot schema/name mismatch.")
    if _date(snapshot.get("captured_at")) > cutoff:
        raise ValueError("Membership snapshot observed after historical cutoff.")
    if snapshot.get("version") != _version(snapshot):
        raise ValueError("Membership snapshot version/content mismatch.")
    expected = list(URLS) if name == "sp500-nasdaq100" else [name]
    if snapshot.get("verification") != ("local_starter" if name == "starter" else "community_uncertified"):
        raise ValueError("Membership certification is unsupported.")
    members, sources, completeness = snapshot.get("members"), snapshot.get("sources"), snapshot.get("completeness")
    if not isinstance(members, list) or not isinstance(sources, list) or not isinstance(completeness, dict) or completeness.get("certified_current") is not False:
        raise ValueError("Structured membership/source completeness required.")
    if len(members) > 1000:
        raise ValueError("Membership capacity exceeded.")
    seen = set()
    for member in members:
        if not isinstance(member, dict) or not set(("symbol", "provider_symbol", "name", "sector", "industry", "cik", "indices")).issubset(member):
            raise ValueError("Membership row schema required.")
        symbol, indices = member["symbol"], member["indices"]
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or _canonical(symbol) != symbol or symbol in seen or member["provider_symbol"] != _provider(symbol):
            raise ValueError("Membership security identity conflict.")
        if not isinstance(indices, list) or not indices or len(set(indices)) != len(indices) or any(index not in expected for index in indices):
            raise ValueError("Unexpected membership index tags.")
        if not isinstance(member["name"], str) or not member["name"] or any(member[key] is not None and not isinstance(member[key], str) for key in ("sector", "industry", "cik")):
            raise ValueError("Invalid member metadata.")
        if member["cik"] and not re.fullmatch(r"\d{10}", member["cik"]):
            raise ValueError("Invalid member CIK.")
        seen.add(symbol)
    available = []
    for source in sources:
        if not isinstance(source, dict) or not _safe_url(source.get("url")) or source.get("status") not in ("available", "unavailable"):
            raise ValueError("Unsafe or malformed membership source.")
        stamp = _date(source.get("captured_at"))
        excluded_diagnostic = source.get("status") == "unavailable" and source.get("excluded_after_cutoff") is True
        if (stamp > cutoff or stamp > _date(snapshot["captured_at"])) and not excluded_diagnostic:
            raise ValueError("Membership source observed after snapshot/cutoff.")
        if source.get("status") == "available":
            if _date(source.get("retrieved_at")) != stamp:
                raise ValueError("Membership retrieval and captured clocks conflict.")
            latest = min(stamp, _date(snapshot["captured_at"]), cutoff)
            for field in ("published_at", "as_of"):
                _optional_source_clock(source.get(field), field, latest)
            if source.get("effective_at") and _date(source["effective_at"]) > cutoff:
                raise ValueError("Membership source effective after cutoff.")
            if not re.fullmatch(r"[a-f0-9]{64}", source.get("content_hash", "")):
                raise ValueError("Membership content hash required.")
            index = source.get("index")
            if index in expected:
                if index in available or source.get("url") != URLS.get(index) or source.get("license") != "CC BY-SA 4.0" or not source.get("attribution") or not source.get("adaptation_notice"):
                    raise ValueError("Constituent attribution/source conflict.")
                edited = source.get("last_edited_at")
                if edited and _date(edited) > stamp:
                    raise ValueError("Membership revision after receipt cutoff.")
                count = sum(index in member["indices"] for member in members)
                if count < MIN_ROWS[index]:
                    raise ValueError("Implausibly small imported membership.")
                available.append(index)
    if name != "starter":
        actual = [index for index in expected if index in available]
        missing = [index for index in expected if index not in available]
        status = "complete" if not missing else "partial" if available else "unavailable"
        if completeness.get("available_indices") != actual or completeness.get("missing_indices") != missing or completeness.get("status") != status or completeness.get("expected_indices") != expected:
            raise ValueError("Membership source completeness conflict.")
        if any(index not in available for member in members for index in member["indices"]):
            raise ValueError("Membership lacks supporting constituent source.")
    if completeness.get("member_count") != len(members):
        raise ValueError("Membership denominator mismatch.")
    if not isinstance(snapshot.get("pending"), list) or not isinstance(snapshot.get("warnings"), list):
        raise ValueError("Membership pending/warnings schema required.")
    for change in snapshot["pending"]:
        if not isinstance(change, dict) or change.get("index") not in expected or change.get("action") not in ("addition", "deletion", "sector_change") or not _safe_url(change.get("source_url")) or not SYMBOL.fullmatch(change.get("symbol", "")) or _date(change.get("effective_at")) <= _date(snapshot["captured_at"]):
            raise ValueError("Invalid future membership change.")
        if not any(s.get("status") == "available" and s.get("id") == change.get("source_id") and s.get("url") == change["source_url"] for s in sources):
            raise ValueError("Pending membership change lacks source provenance.")
    return snapshot


def _read(path, name, cutoff):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Membership import oversized.")
    def reject_constant(value):
        raise ValueError(f"Non-finite membership JSON constant: {value}")
    return _validate(json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant), name, cutoff)


def _write(path, snapshot):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolve_universe(name, now, cache_dir, client=None, offline=False, import_path=None):
    """Return a validated complete/partial snapshot, retaining failed source gaps.

    ``now=None`` is a normal live run, frozen at real collection end. An explicit
    aware clock is a strict cutoff; client.clock() may inject deterministic
    receipt timestamps in fixtures. There are no retries or gated-list bypasses.
    Supplied clients must accept and honor get(url, follow_redirects=False),
    and are responsible for a shared global request gate and denial circuit.
    The owned client shares one RequestGate across both membership URLs.
    """
    if name not in (*URLS, "sp500-nasdaq100", "starter"):
        raise ValueError("Choose sp500, nasdaq100, sp500-nasdaq100 or starter.")
    cutoff = _date(now) if now is not None else datetime.now(timezone.utc)
    path = Path(cache_dir) / (name + ".json")
    warnings = []
    if import_path is not None:
        try:
            return _read(Path(import_path), name, cutoff)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            return _snapshot(name, cutoff, warnings=[f"Membership import rejected: {exc}"])
    if name == "starter":
        from .stocks import STARTER
        return _snapshot(name, cutoff, members=[{"symbol": symbol, "provider_symbol": _provider(symbol), "name": symbol,
                                              "sector": sector, "industry": None, "cik": None, "indices": ["starter"]}
                                             for symbol, sector in STARTER.items()], warnings=["Explicit local starter scope; not index membership."])
    if path.exists():
        try:
            cached = _read(path, name, cutoff)
            pending_due = any(_date(change["effective_at"]) <= cutoff for change in cached["pending"])
            if offline or (cached["completeness"]["status"] == "complete" and not pending_due and cutoff - _date(cached["captured_at"]) <= timedelta(hours=6)):
                return cached
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            warnings.append(f"Membership cache rejected: {exc}")
    if offline:
        return _snapshot(name, cutoff, warnings=warnings + ["Offline membership snapshot unavailable; no requests made."])
    own_client = client is None
    request_gate = None
    if own_client:
        import httpx
        from .stockbatch import RequestGate
        request_gate = RequestGate()
        client = httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": "Print-Money research/1.0 (https://github.com/ayfew/Print-Money)"})
    members, sources = [], []
    expected = list(URLS) if name == "sp500-nasdaq100" else [name]
    try:
        for index in expected:
            receipt = None
            try:
                if request_gate is not None:
                    request_gate.acquire(URLS[index])
                # Never let a client's redirect default make hidden GETs outside
                # the gate or attribute another origin's body to Wikipedia.
                response = client.get(URLS[index], follow_redirects=False)
                if request_gate is not None:
                    request_gate.block(URLS[index], response.status_code)
                receipt = _date(client.clock()) if callable(getattr(client, "clock", None)) else datetime.now(timezone.utc)
                if now is not None and receipt > cutoff:
                    raise ValueError("Membership source observed after historical cutoff.")
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}; no retries.")
                rows, source, notices = _parse_table(response.text, index, receipt)
                sources.append(source)
                warnings.extend(notices)
                for row in rows:
                    previous = next((m for m in members if m["symbol"] == row["symbol"]), None)
                    if previous:
                        if previous["cik"] and row["cik"] and previous["cik"] != row["cik"]:
                            raise ValueError("Cross-index security CIK conflict.")
                        previous["indices"].append(index)
                        if previous["name"] != row["name"] or previous["sector"] != row["sector"] or previous["industry"] != row["industry"]:
                            warnings.append(f"{row['symbol']}: index source naming/classification differ; first source fields retained (GICS/ICB taxonomies differ).")
                    else:
                        members.append(row)
            except Exception as exc:
                # Remove any partially merged failed source atomically at index level.
                for member in members[:]:
                    if index in member["indices"]:
                        member["indices"].remove(index)
                    if not member["indices"]:
                        members.remove(member)
                sources = [s for s in sources if s.get("index") != index]
                clock = receipt or datetime.now(timezone.utc)
                sources.append({"id": "wikipedia-" + index, "index": index, "url": URLS[index], "kind": "community",
                                "status": "unavailable", "captured_at": clock.isoformat(),
                                "retrieved_at": receipt.isoformat() if receipt else None,
                                "excluded_after_cutoff": now is not None and clock > cutoff, "error": str(exc)[:400]})
                warnings.append(f"{index} membership unavailable: {str(exc)[:400]}")
    finally:
        if own_client:
            client.close()
    final_cutoff = cutoff if now is not None else datetime.now(timezone.utc)
    accepted_clocks = [_date(s["captured_at"]) for s in sources if _date(s["captured_at"]) <= final_cutoff]
    captured = max(accepted_clocks) if accepted_clocks else final_cutoff
    pending = _notice(members, sources, final_cutoff, warnings) if any(s.get("index") == "sp500" and s["status"] == "available" for s in sources) else []
    captured = max([captured] + [_date(s["captured_at"]) for s in sources if _date(s["captured_at"]) <= final_cutoff])
    warnings.append("Membership is an observed community snapshot; official current completeness is uncertified and effective dates are unknown except the explicit notice registry.")
    result = _snapshot(name, captured, members, sources, pending, warnings)
    try:
        _validate(result, name, final_cutoff)
        _write(path, result)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        warnings.append(f"Membership snapshot/cache validation failed: {exc}")
        # Validation failures must not leak unsafe members. Filesystem failures
        # alone preserve the validated result for this run.
        if not isinstance(exc, OSError):
            return _snapshot(name, final_cutoff, warnings=warnings)
        result["version"] = _version(result)
    return result
