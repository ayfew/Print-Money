"""Narrow public issuer-release facts, with explicit period and accounting basis.

These facts are issuer press releases, never annual 10-K evidence. No quarter
annualization, implied missing metrics, recommendations, or article-body output.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

from .stockbatch import RequestGate

RELEASE_URLS = {
    "MSFT": "https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast",
    "NVDA": "https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027",
    "AAPL": "https://www.apple.com/newsroom/2026/07/apple-reports-third-quarter-results/",
    "AMZN": "https://www.aboutamazon.com/news/company-news/amazon-earnings-q2-2026-report",
}
MAX_BYTES = 2_000_000
_DATE = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
_NUMBER = r"-?\d[\d,]*(?:\.\d+)?"


def _clean(text):
    return re.sub(r"\s+", " ", text).strip()


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware cutoff and retrieval clocks required.")
    return value.astimezone(timezone.utc)


def _date(text):
    try:
        return datetime.strptime(_clean(text), "%B %d, %Y").date().isoformat()
    except (ValueError, TypeError):
        return None


class _ReleaseHTML(HTMLParser):
    """Keep temporary visible text and table cells; never return article text."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments = []
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None
        self.skip = 0
        self.dates = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        if self.skip:
            return
        if tag == "meta" and (attrs.get("property", "").lower() in ("article:published_time", "datepublished")
                              or attrs.get("name", "").lower() in ("date", "datepublished", "pubdate")):
            self.dates.append(attrs.get("content", ""))
        if tag == "time" and attrs.get("datetime"):
            self.dates.append(attrs["datetime"])
        if tag == "table" and self.table is None:
            self.table = {"context": _clean(" ".join(self.fragments[-120:]))[-1200:], "rows": [], "caption": ""}
        if tag == "tr" and self.table is not None:
            self.row = []
        if tag in ("td", "th", "caption") and self.table is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag in ("td", "th", "caption") and self.cell is not None:
            text = _clean(" ".join(self.cell))
            if tag == "caption":
                self.table["caption"] = text
            elif self.row is not None:
                self.row.append(text)
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.table["rows"].append(self.row)
            self.row = None
        if tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None

    def handle_data(self, text):
        if self.skip:
            return
        self.fragments.append(text)
        if self.cell is not None:
            self.cell.append(text)


def _numeric(text):
    compact = re.sub(r"[\s$,]", "", text)
    if compact in ("-", "--", "—", "–", ""):
        return None
    if not re.fullmatch(r"(?:-?\d+(?:\.\d+)?|\(\d+(?:\.\d+)?\))", compact):
        return None
    number = -float(compact[1:-1]) if compact.startswith("(") else float(compact)
    return number if math.isfinite(number) else None


def _values(row):
    # Dollar-sign and spacer cells are presentation, whereas dashes preserve
    # missing column positions. Percent changes cannot be financial amounts.
    values = []
    for cell in row[1:]:
        if not cell or cell in ("$", "(", ")"):
            continue
        if cell in ("-", "--", "—", "–") or re.fullmatch(r"[\s$()\d,.-]+", cell):
            values.append(_numeric(cell))
    return values


def _period(kind, end, label, basis):
    return {"kind": kind, "start": None, "end": end, "label": label,
            "currency": "USD", "basis": basis, "metrics": {}}


def _metric(period, key, value, scale, locator):
    if value is None or not math.isfinite(value * scale):
        return
    period["metrics"][key] = {"value": value * scale,
                              "unit": "USD/share" if key == "diluted_eps" else "USD",
                              "basis": period["basis"], "locator": locator[:180]}


def _heading(table):
    return _clean(table["context"] + " " + table["caption"] + " " + " ".join(" ".join(row) for row in table["rows"][:5]))


def _usd_table(table):
    # Registry defaults apply only to the observed US-dollar release formats.
    # A contrary unit declaration is evidence to reject, never to overwrite.
    context = ""
    # A generic caption does not cancel a matching external units declaration.
    # Bind by statement identity so an income title cannot govern a cash table.
    statement = r"INCOME STATEMENTS|CASH FLOWS STATEMENTS"
    local_statements = re.findall(statement, table["caption"], re.I)
    markers = list(re.finditer(statement, table["context"], re.I))
    if not markers and not local_statements:
        markers = list(re.finditer(r"(?<![\w-])(?:Non-GAAP|GAAP)(?![\w-])", table["context"], re.I))
    if markers and (not local_statements or markers[-1].group().upper() == local_statements[-1].upper()):
        context = table["context"][markers[-1].start():]
    header = _clean(context + " " + table["caption"] + " " + " ".join(" ".join(row) for row in table["rows"][:5]))
    if re.search(r"\b(?:EUR|GBP|CAD|AUD|NZD|CHF|CNY|RMB|JPY|HKD|SEK|NOK|DKK|INR|KRW|SGD|BRL|MXN|RUB|euros?|pounds?|yen|yuan)\b|[€£¥]|(?:C|CA|A|AU|HK)\$", header, re.I):
        return False
    # Reject an unrecognized ISO-shaped currency in the units declaration too.
    for declaration in re.findall(r"\([^)]*\bmillions\b[^)]*\)", header, re.I):
        if any(code not in ("USD", "EPS") for code in re.findall(r"\b[A-Z]{3}\b", declaration)):
            return False
    return True


def _msft(parser, end):
    basis = "GAAP" if re.search(r"generally accepted accounting principles\s*\(GAAP\)|reported\s*\(GAAP\)", _clean(" ".join(parser.fragments)), re.I) else "reported_unspecified"
    periods = [_period("quarter", end, "Three months ended " + end, basis),
               _period("annual", end, "Twelve months ended " + end, basis)]
    year = end[:4]
    prior = str(int(year) - 1)
    for table in parser.tables:
        heading = _heading(table)
        if not re.search(r"in millions", heading, re.I) or not _usd_table(table):
            continue
        # Validate this table's own ordered duration/date headers, rather than
        # borrowing a date or duration from the narrative or an earlier table.
        header = _clean(" ".join(" ".join(row) for row in table["rows"][:6]))
        periods_in_header = re.findall(r"\b(Three|Twelve)\s+Months\s+Ended\s+([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?", header, re.I)
        if ([item[0].lower() for item in periods_in_header] != ["three", "twelve"]
                or any(_date(f"{month} {date}, {stated_year or year}") != end
                       for _, month, date, stated_year in periods_in_header)):
            continue
        # The observed statements explicitly order current/prior quarter then
        # current/prior annual. Reject changed/missing year columns.
        years = [c for row in table["rows"][:6] for c in row if re.fullmatch(r"\d{4}", c)]
        if years != [year, prior, year, prior]:
            continue
        statement_labels = re.findall(r"INCOME STATEMENTS|CASH FLOWS STATEMENTS", heading, re.I)
        if not statement_labels:
            continue
        income = statement_labels[-1].upper() == "INCOME STATEMENTS"
        for row in table["rows"]:
            if not row:
                continue
            label = _clean(row[0]).lower()
            key = ({"total revenue": "revenue", "net income": "net_income", "diluted": "diluted_eps"}.get(label) if income
                   else {"net cash from operations": "operating_cashflow"}.get(label))
            if key is None:
                continue
            values = _values(row)
            if len(values) != 4:
                continue
            for index, column in enumerate((0, 2)):
                _metric(periods[index], key, values[column], 1 if key == "diluted_eps" else 1_000_000,
                        ("Income statements" if income else "Cash flows statements") + "; " + row[0] + "; " + periods[index]["label"])
    return [p for p in periods if p["metrics"]]


def _nvda(parser, end):
    periods = []
    for table in parser.tables:
        heading = _heading(table)
        # Order is part of the evidence: the first amount is current Q2 only
        # when this table explicitly places current Q2 in its first column.
        quarter_headers = [cell.upper() for row in table["rows"][:5] for cell in row
                           if re.fullmatch(r"Q\d FY\d{2}", cell, re.I)]
        if (not re.search(r"in millions", heading, re.I) or not _usd_table(table)
                or quarter_headers != ["Q2 FY27", "Q1 FY27", "Q2 FY26"]):
            continue
        basis_labels = re.findall(r"(?<![\w-])(?:Non-GAAP|GAAP)(?![\w-])", table["context"], re.I)
        if not basis_labels:
            continue
        basis = "non-GAAP" if basis_labels[-1].lower() == "non-gaap" else "GAAP"
        period = _period("quarter", end, "Q2 FY27", basis)
        for row in table["rows"]:
            if not row:
                continue
            key = {"revenue": "revenue", "net income": "net_income", "diluted earnings per share": "diluted_eps"}.get(_clean(row[0]).lower())
            values = _values(row)
            if key and len(values) >= 3:
                _metric(period, key, values[0], 1 if key == "diluted_eps" else 1_000_000,
                        "Q2 Fiscal 2027 Summary; " + basis + "; " + row[0] + "; Q2 FY27")
        if period["metrics"]:
            periods.append(period)
    return periods


def _headlines(text, symbol, end):
    period = _period("quarter", end, "Fiscal Q3 2026" if symbol == "AAPL" else "Q2 2026", "reported_unspecified")
    if symbol == "AAPL":
        patterns = {"revenue": r"posted quarterly revenue of\s*\$(" + _NUMBER + r")\s+(billion|million)",
                    "diluted_eps": r"Diluted earnings per share was\s*\$(" + _NUMBER + r")"}
    else:
        # Stop before guidance, and require the total-company quarter clauses.
        text = re.split(r"Financial Guidance", text, flags=re.I)[0]
        patterns = {"revenue": r"Net sales increased\s+[\d.]+%\s+to\s*\$(" + _NUMBER + r")\s+(billion|million)\s+in the second quarter",
                    "net_income": r"Net income increased to\s*\$(" + _NUMBER + r")\s+(billion|million)\s+in the second quarter",
                    "diluted_eps": r"Net income increased to\s*\$" + _NUMBER + r"\s+(?:billion|million)\s+in the second quarter,\s+or\s*\$(" + _NUMBER + r")\s+per diluted share"}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match:
            scale = 1 if key == "diluted_eps" else {"billion": 1_000_000_000, "million": 1_000_000}[match.group(2).lower()]
            _metric(period, key, _numeric(match.group(1)), scale,
                    "Quarter results opening paragraph; " + key + "; " + end)
    return [period] if period["metrics"] else []


def _publication(parser, text, symbol):
    for candidate in parser.dates:
        try:
            published = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if published.tzinfo is not None:
                return {"published_at": published.astimezone(timezone.utc).isoformat(), "publication_time_precision": "timestamp"}
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
                return {"published_day": published.date().isoformat(), "publication_time_precision": "day"}
        except (ValueError, TypeError):
            continue
    if symbol != "AMZN":
        # A fiscal period end in the opening results paragraph is not a
        # publication date. Observed datelines precede that paragraph.
        text = re.split(r"quarter\s+ended", text, maxsplit=1, flags=re.I)[0]
    pattern = r"as of\s+(" + _DATE + ")" if symbol == "AMZN" else "(" + _DATE + ")"
    match = re.search(pattern, text, re.I)
    day = _date(match.group(1)) if match else None
    return {"published_day": day, "publication_time_precision": "day"} if day else {}


def parse_release(html, symbol, source_url, now, retrieved_at=None):
    """Extract registered release facts; absent or unsafe evidence stays missing."""
    result = {"status": "missing", "periods": [], "sources": [], "warnings": []}
    try:
        cutoff = _utc(now)
        receipt = _utc(retrieved_at if retrieved_at is not None else now)
        if receipt > cutoff:
            raise ValueError("Retrieval clock is after the cutoff.")
        if symbol not in RELEASE_URLS or source_url != RELEASE_URLS[symbol]:
            raise ValueError("Only an exact registered official release URL is supported.")
        if isinstance(html, bytes):
            if len(html) > MAX_BYTES:
                raise ValueError("Release payload exceeds the size limit.")
            html = html.decode("utf-8", errors="strict")
        if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_BYTES or not html.strip():
            raise ValueError("Missing, malformed, or oversized HTML release payload.")
        if not re.search(r"<(?:html|article|main|div|p|table|h1)\b", html, re.I):
            raise ValueError("An HTML financial release is required.")
        parser = _ReleaseHTML()
        parser.feed(html)
        parser.close()
        text = _clean(" ".join(parser.fragments))
        publication = _publication(parser, text, symbol)
        source = {"id": "financial_release_" + symbol, "url": source_url, "origin_url": source_url,
                  "kind": "issuer", "type": "issuer_financial_release", "retrieved_at": receipt.isoformat(),
                  **publication, "content_hash": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                  "corroboration": "single_official_source", "lineage": [source_url, "Source-specific financial release extraction"]}
        result["sources"].append(source)
        published_at = datetime.fromisoformat(publication["published_at"]) if publication.get("published_at") else None
        publication_day = published_at.date().isoformat() if published_at else publication.get("published_day")
        if (not publication_day or publication_day > cutoff.date().isoformat() or publication_day > receipt.date().isoformat()
                or (published_at is not None and (published_at > cutoff or published_at > receipt))):
            raise ValueError("Missing or future release publication date.")
        identity = {"MSFT": "Microsoft", "NVDA": "NVIDIA", "AAPL": "Apple", "AMZN": "Amazon.com"}[symbol]
        if identity.lower() not in text.lower():
            raise ValueError("Issuer identity is missing from the release.")
        match = re.search(r"(?:quarter ended|quarter\s+ended)\s+(" + _DATE + ")", text, re.I)
        end = _date(match.group(1)) if match else None
        if not end or end > publication_day or end > cutoff.date().isoformat():
            raise ValueError("Missing or future explicitly reported fiscal period end.")
        result["periods"] = _msft(parser, end) if symbol == "MSFT" else (_nvda(parser, end) if symbol == "NVDA" else _headlines(text, symbol, end))
        if not result["periods"]:
            raise ValueError("No usable reported facts in the supported visible release format; dynamic shells are not evidence.")
        result["status"] = "available"
        result["warnings"].append("Issuer financial release facts only; not annual 10-K evidence. Unknown fiscal starts and missing metrics remain unknown.")
        if any(p["basis"] == "reported_unspecified" for p in result["periods"]):
            result["warnings"].append("Accounting basis is not explicitly stated in the retained release facts; reported_unspecified is not GAAP verification.")
        if symbol == "AMZN":
            result["warnings"].append("Trailing twelve months cash flow and annualized run rates are omitted; they are not quarter or fiscal annual facts.")
        if symbol == "AAPL":
            result["warnings"].append("Linked PDF statements were not fetched; HTML net income and numeric cash flow coverage is unavailable.")
    except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
        result["periods"] = []
        result["status"] = "missing"
        result["warnings"].append(str(exc))
    return result


class FinancialReleaseProvider:
    """One bounded request per supported issuer; shared gate and no denial retry."""
    def __init__(self, client=None, request_gate=None, clock=None):
        self._owns_client = client is None
        self.client = client if client is not None else httpx.Client(timeout=15, follow_redirects=False, headers={
            "User-Agent": "Print-Money research/1.0 (https://github.com/ayfew/Print-Money)"})
        self.request_gate = request_gate if request_gate is not None else RequestGate()
        self.clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self._live_receipt_cutoff = client is None and clock is None

    def collect(self, symbol, now):
        result = {"status": "missing", "periods": [], "sources": [], "warnings": []}
        url = RELEASE_URLS.get(symbol)
        if not url:
            result["warnings"].append("No registered free official financial release for this issuer.")
            return result
        try:
            _utc(now)
            if self.request_gate.is_blocked(url):
                raise ValueError("Official source is blocked for this run.")
            self.request_gate.acquire(url)
        except ValueError as exc:
            result["status"] = "blocked" if "blocked" in str(exc).lower() else "missing"
            result["warnings"].append(str(exc))
            return result
        try:
            with self.client.stream("GET", url, follow_redirects=False) as response:
                receipt = _utc(self.clock())
                result["sources"].append({"id": "financial_release_" + symbol, "url": url, "kind": "issuer",
                                           "type": "issuer_financial_release", "retrieved_at": receipt.isoformat(),
                                           "http_status": response.status_code})
                if response.status_code in (401, 403, 429):
                    self.request_gate.block(url, response.status_code)
                    result["status"] = "blocked"
                    result["warnings"].append("Official release access denied (HTTP " + str(response.status_code) + "); no retry.")
                    return result
                if response.status_code != 200:
                    result["warnings"].append("Official release HTTP " + str(response.status_code) + "; redirects and retries are disabled.")
                    return result
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in ("text/html", "application/xhtml+xml", ""):
                    raise ValueError("Official source did not return HTML.")
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_BYTES:
                        raise ValueError("Release payload exceeds the size limit.")
                # Receipt is completion time, not the request-start cutoff.
                receipt = _utc(self.clock())
                cutoff = receipt if self._live_receipt_cutoff else now
                parsed = parse_release(bytes(payload), symbol, url, cutoff, receipt)
                if parsed["sources"]:
                    parsed["sources"][0]["http_status"] = response.status_code
                else:
                    parsed["sources"] = result["sources"]
                return parsed
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            result["warnings"].append("Official financial release fetch failed: " + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
            return result

    def close(self):
        if self._owns_client:
            self.client.close()
