"""Bounded public issuer RSS/Atom context, never earnings/catalyst evidence."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

import httpx

MAX_XML_BYTES = 1024 * 1024
MAX_ITEMS = 20
FEEDS = {
    "AAPL": ("Apple Newsroom", "https://www.apple.com/newsroom/rss-feed.rss"),
    "NVDA": ("NVIDIA Newsroom", "https://nvidianews.nvidia.com/releases.xml"),
}
CONTEXT_WARNING = "Issuer headlines are context only; they do not verify earnings or catalysts."
ATOM = "{http://www.w3.org/2005/Atom}"


def _date(value):
    try:
        if isinstance(value, datetime):
            stamp = value
        else:
            value = str(value or "").strip()
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None


def _https(url):
    if not isinstance(url, str) or not url:
        return False
    decoded = unquote(url)
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in decoded) or "\\" in decoded:
        return False
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and bool(parts.hostname)
                and parts.username is None and parts.password is None
                and parts.port in (None, 443))
    except ValueError:
        return False


def _result(symbol, source_url, receipt, status="unavailable", warning=None):
    stamp = _date(receipt)
    source = {
        "source": FEEDS.get(symbol, ("Issuer feed", None))[0],
        "url": source_url if _https(source_url) else None,
        "status": status, "verification": "context_only", "published_at": None, "updated_at": None,
        "retrieved_at": stamp.isoformat() if stamp else None,
        "as_of": stamp.isoformat() if stamp else None,
    }
    return {"symbol": symbol, "status": status, "items": [], "sources": [source],
            "warnings": [CONTEXT_WARNING] + ([warning] if warning else [])}


def _text(element):
    return "".join(element.itertext()).strip() if element is not None else ""


class _ShortText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _summary(value):
    parser = _ShortText()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())[:280]


def _xml_text(xml):
    if isinstance(xml, str):
        if len(xml.encode("utf-8")) > MAX_XML_BYTES:
            raise ValueError("Oversized XML feed rejected.")
        return xml
    if not isinstance(xml, bytes):
        raise ValueError("Malformed XML input type.")
    if len(xml) > MAX_XML_BYTES:
        raise ValueError("Oversized XML feed rejected.")
    # Decode before checking declarations; UTF-16/32 NUL bytes cannot hide a DTD.
    encoding = "utf-8-sig"
    if xml.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encoding = "utf-32"
    elif xml.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    return xml.decode(encoding)


def parse_news_xml(xml, symbol, now, source_url, retrieved_at=None):
    """Parse short context with original clocks; all retained dates meet cutoff.

    Receipt defaults to the explicit caller cutoff for deterministic imports.
    Live callers must supply their actual receipt time. HTML title text remains
    literal, so downstream renderers must escape it like every external string.
    """
    symbol = str(symbol).strip().upper()
    receipt = now if retrieved_at is None else retrieved_at
    result = _result(symbol, source_url, receipt)
    cutoff, received = _date(now), _date(receipt)
    if not _https(source_url):
        result["warnings"].append("Unsafe source URL rejected.")
        return result
    if cutoff is None or received is None or received > cutoff:
        result["warnings"].append("Unknown, naive or future retrieval/cutoff date rejected.")
        return result
    evidence_cutoff = min(cutoff, received)
    try:
        text = _xml_text(xml)
        if "\x00" in text:
            raise ValueError("NUL/interleaved XML encoding rejected before entity processing.")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
            raise ValueError("DTD/entity declarations are prohibited.")
        root = ET.fromstring(text)
    except (ValueError, ET.ParseError, UnicodeError) as exc:
        result["warnings"].append(str(exc)[:180])
        return result
    if root.tag == ATOM + "feed":
        entries = root.findall(ATOM + "entry")
        feed_dates = {"published_at": _text(root.find(ATOM + "published")),
                      "updated_at": _text(root.find(ATOM + "updated"))}
        kind = "atom"
    elif root.tag == "rss" and root.find("channel") is not None:
        channel = root.find("channel")
        entries = channel.findall("item")
        feed_dates = {"published_at": _text(channel.find("pubDate")),
                      "updated_at": _text(channel.find("lastBuildDate"))}
        kind = "rss"
    else:
        result["warnings"].append("Unsupported or malformed RSS/Atom feed.")
        return result
    for field, feed_date in feed_dates.items():
        if not feed_date:
            continue
        stamp = _date(feed_date)
        if stamp is None or stamp > evidence_cutoff:
            result["warnings"].append("Unknown or future feed publication date rejected.")
            return result
        result["sources"][0][field] = stamp.isoformat()
    rejected = 0
    for entry in entries:
        prefix = ATOM if kind == "atom" else ""
        title = _text(entry.find(prefix + "title"))
        published_text = _text(entry.find(prefix + ("published" if kind == "atom" else "pubDate")))
        published = _date(published_text)
        update_only = False
        if kind == "atom":
            links = entry.findall(ATOM + "link")
            url = next((link.get("href", "") for link in links
                        if link.get("rel", "alternate") == "alternate"), "")
            summary = _text(entry.find(ATOM + "summary"))
            updated_text = _text(entry.find(ATOM + "updated"))
            updated = _date(updated_text) if updated_text else published
            update_only = not published_text and bool(updated_text)
        else:
            url = _text(entry.find("link"))
            summary = _text(entry.find("description"))
            updated = published
        if ((published is None and not update_only)
                or (published is not None and published > evidence_cutoff) or updated is None
                or updated > evidence_cutoff):
            result["warnings"].append("Entry excluded: unknown, naive or future publication date.")
            rejected += 1
            continue
        if not title or not _https(url):
            result["warnings"].append("Entry excluded: missing title or unsafe HTTPS link.")
            rejected += 1
            continue
        if update_only:
            result["warnings"].append("Atom entry retained as updated-only context: original publication unknown.")
            rejected += 1
        result["items"].append({
            "symbol": symbol, "title": title[:240], "summary": _summary(summary), "url": url,
            "published_at": published.isoformat() if published else None, "retrieved_at": received.isoformat(),
            "updated_at": updated.isoformat(),
            "publication_basis": "updated_only; original publication unknown" if update_only else "published",
            "source_url": source_url, "verification": "context_only",
        })
    result["items"].sort(key=lambda item: item["published_at"] or item["updated_at"], reverse=True)
    if len(result["items"]) > MAX_ITEMS:
        result["warnings"].append("Context capped at twenty headlines; feed coverage is incomplete.")
        result["items"] = result["items"][:MAX_ITEMS]
        rejected += 1
    if result["items"]:
        result["status"] = "partial" if rejected else "available"
    else:
        result["warnings"].append("No qualifying dated HTTPS headlines available.")
    result["sources"][0]["status"] = result["status"]
    result["sources"][0]["format"] = kind
    return result


class NewsProvider:
    """Only the explicitly registered, parent-checked free issuer feeds."""
    def __init__(self, client=None, request_gate=None, clock=None):
        self._owns_client = client is None
        self._live_cutoff = client is None and clock is None
        self.client = client if client is not None else httpx.Client(
            timeout=15, follow_redirects=False,
            headers={"User-Agent": "PrintMoneyResearch/0.1 (issuer headline context)"})
        self.request_gate = request_gate
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._blocked = {}

    def collect(self, symbol, now):
        symbol = str(symbol).strip().upper()
        if symbol not in FEEDS:
            return {"symbol": symbol, "status": "unsupported", "items": [], "sources": [],
                    "warnings": [CONTEXT_WARNING, "No supported free issuer feed is registered."]}
        url = FEEDS[symbol][1]
        gate = self.request_gate
        if url in self._blocked or (gate is not None and gate.is_blocked(url)):
            result = _result(symbol, url, None, "blocked", "Source blocked; no request attempted.")
            if url in self._blocked:
                result["sources"][0]["http_status"] = self._blocked[url]
            return result
        try:
            if gate is not None:
                try:
                    gate.acquire(url)
                except ValueError:
                    return _result(symbol, url, None, "blocked", "Shared request gate blocked this source.")
            # No redirect requests or retries: every actual GET passes the gate.
            with self.client.stream("GET", url, follow_redirects=False) as response:
                status = response.status_code
                body = bytearray()
                if status == 200:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if len(body) > MAX_XML_BYTES:
                            break
                receipt = self.clock()
            if status != 200:
                blocked = status in (401, 403, 429)
                if blocked:
                    self._blocked[url] = status
                    if gate is not None:
                        gate.block(url, status)
                result = _result(symbol, url, receipt, "blocked" if blocked else "unavailable",
                                 f"Issuer feed HTTP {status}; no retry attempted.")
            else:
                cutoff = receipt if self._live_cutoff else now
                result = parse_news_xml(bytes(body), symbol, cutoff, url, receipt)
            result["sources"][0]["http_status"] = status
            return result
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return _result(symbol, url, self.clock(), warning=f"Issuer feed failed: {type(exc).__name__}.")

    def close(self):
        if self._owns_client:
            self.client.close()
