# Index Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development with user-authorized parallel independent ownership, plus controller integration. Steps use checkbox syntax for tracking.

**Goal:** Extend reviewed local research to the S&P 500/Nasdaq 100 security union with full inspectable evidence, explicit gaps and responsible resumable collection.

**Architecture:** Independent membership, collection and private renderer modules feed the existing pure research policy and prospective record. Optional news context and free-source local imports remain explicit evidence boundaries. The controller owns policy/CLI integration and final verification.

**Tech Stack:** Existing Python/httpx/pytest/exchange-calendars; stdlib HTML/XML parsing, threads and atomic private cache; self-contained HTML/JavaScript.

**Spec:** docs/superpowers/specs/2026-10-04-index-universe-design.md.

## Global Constraints

- Existing isolated worktree/branch from 81d31bdd18c51868bb31c28c37b3ac46e04bdab2; preserve original D:/Print-Money files/HEAD.
- S&P 500 plus Nasdaq 100 security union, never Nasdaq Composite; retain share classes and index tags, no exact membership count assumption.
- No paid providers, signup, credentials, data-display rights, trading, merge, push, deployment or publication assumed.
- FREE-ONLY usable core: publicly released constituent facts/issuer reports/RSS/Atom, attribution and short factual output; respect paywalls/site restrictions and SEC403.
- Snapshot provenance/captured time/version and source completeness are mandatory; uncertified sources and unknown effective dates stay explicit.
- Four workers and one shared outbound request per second by default; no denial retry burst; SEC blocked for broad collection.
- Six-hour per-symbol cache with original clocks, atomic writes, cutoff checks, all partial failures retained and offline/resume support.
- Full HTML/JSON universe with search/filter/sort/details and mutually exclusive field-coverage denominators, not only five highlights.
- Existing annual, event, adjusted-price and prospective integrity gates remain; headlines do not qualify earnings or catalysts.
- All live payloads/export/cache stay in Git-ignored state/research. Fixtures are deterministic generated/minimal samples.

## Review Focus

- Community membership captured after a historical cutoff: rejected, not treated as a historic index basket.
- Class aliases and overlaps: one row per security, GOOG/GOOGL distinct, BRK.B provider alias mapped without CIK deduplication.
- Shared denial/rate gate: at most the permitted requests; one symbol's malformed result cannot remove other rows.
- Cached or imported future/unsafe evidence: explicit gap, never qualifying consideration or current coverage.
- Malicious provider text and XML entities: escaped/no external execution; known values sort before unknowns and filtered counts stay correct.

## Task 1: Dynamic membership and validated snapshots

**Owner/files:** Independent agent; create stockuniverse.py and tests/test_stockuniverse.py only, plus its assigned report.

**Interface:** `resolve_universe(name, now, cache_dir, client=None, offline=False, import_path=None) -> dict`. Snapshot keys: `schema_version`, `name`, `version`, `captured_at`, `verification`, `completeness`, `sources`, `members`, `pending`, `warnings`. Member keys: `symbol`, `provider_symbol`, `name`, `sector`, `industry`, `cik`, `indices`. Names: sp500, nasdaq100, sp500-nasdaq100, starter. Use `https://en.wikipedia.org/wiki/List_of_S%26P_500_companies` and `https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies` (not the overview). Preserve actual revision/last-edited metadata and CC BY-SA4.0 attribution/adaptation notice. Community sources are explicitly uncertified; gated official lists are not bypassed. Optionally reconcile verified effective-dated official notices via an explicit registry; Oct1's S&P notice has future Oct6 changes that remain pending on Oct4. Never enforce exactly503 or deduplicate by CIK.

- [x] Write tests for generated plausible memberships, union overlaps/classes, unsafe/future imports, malformed/empty/conflicting tables, partial source failure, cache and historical cutoff rejection.
- [x] Run focused tests RED, implement stdlib parsers/provenance/private atomic snapshots and import boundary, then GREEN.
- [x] Report exact supported source format and actual fixture results; do not make live requests until controller coordinates them.

## Task 2: Resumable bounded collection and provider safety

**Owner/files:** Independent agent; create stockbatch.py, tests/test_stockbatch.py; modify stockdata.py and tests/test_stockdata.py only, plus assigned report.

**Interfaces:** `RequestGate(interval=1.0, clock=None, sleeper=None)` and `collect_many(symbols, provider_factory, now, cache_dir, workers=4, interval=1.0, offline=False, refresh=False, request_gate=None) -> dict` returning `observations` and `collection`. Factory receives a shared gate and returns a provider supporting collect/close. An optional supplied gate preserves membership-phase pacing and denial state. Collection counts: requested, cached, attempted, completed, failed, offline, workers, request_interval_seconds. Extend `StockProvider(..., request_gate=None, sec_enabled=True, clock=None)` while preserving existing call sites. Gate every actual GET, chart alias BRK.B/BF.B, retain canonical identity, preserve actual live receipt time, and share a provider-wide denial circuit. Controller disables SEC in broad CLI until normal authorized access is available.

- [x] Write RED tests for bounded concurrency/global gate, partial malformed/error observations, successful resume, atomic/corrupt/future cache, offline missing rows, source denial circuit and provider alias mapping.
- [x] Implement minimal collection/cache and provider extensions; run new and existing stockdata tests GREEN.
- [x] Report exact clocks/circuit behavior and tests; no live SEC/network probes or global configuration edits.

## Task 3: Complete private table and evidence details

**Owner/files:** Independent agent; create stockview.py and tests/test_stockview.py only, plus its assigned report. Controller connects existing render_html to this module.

**Interface:** `render_universe_html(report:dict, lang='th')->str`. Consume `evaluated` full cards and optional `membership`, `collection`, `coverage_metrics`, `data_quality`, `news`; gracefully handle earlier reports. Every symbol appears in the table and has inspectable details. Controls search ticker/name, filter status/index/sector/data gaps, sort known numerics before unknown, and show filtered/total counts. Existing field/source/catalyst escaping remains mandatory.

- [x] Write RED structural/adversarial tests for 500-plus rows, classes, missing fields, malicious source/name/event text, safe links and controls/counts; verify sort/filter logic where execution tools permit.
- [x] Implement self-contained accessible HTML/JS without external dependencies/requests, then GREEN.
- [x] Save any visual evidence privately; report actual browser validation or its exact limitation. No publication.

## Task 4: Optional headline context

**Owner/files:** Independent agent; create stocknews.py and tests/test_stocknews.py only, plus assigned report.

**Interfaces:** `parse_news_xml(xml, symbol, now, source_url, retrieved_at=None)->dict` with status/items/sources/warnings; `NewsProvider(client=None, request_gate=None, clock=None).collect(symbol,now)->dict`, close(). Explicit initial registry: AAPL `https://www.apple.com/newsroom/rss-feed.rss` (Atom despite extension) and NVDA `https://nvidianews.nvidia.com/releases.xml` (RSS); both returned200 in parent research. Unknown symbols are unsupported without requests. YahooRSS404/MSFTblog403 are unavailable; no invented URL or auth bypass. At most twenty headlines/short factual summaries; HTTPS links, timezone-aware dates at/before cutoff, source/retrieval clocks; reject DTD/entity/oversized/malformed payloads. Never verification=verified for issuer catalyst/earnings. Root may enable the two verified feeds by default using the shared gate.

- [x] Write RED fixtures for RSS/Atom, future/unknown dates, unsafe links, entity/malformed/oversized input, partial provider denial and literal content.
- [x] Implement parser/provider with same request gate; GREEN. No live calls until controller coordinates sample.

## Task 4b: Free reported financial-release facts

**Owner/files:** Independent agent; create stockfinancials.py and tests/test_stockfinancials.py only, plus assigned report.

**Interfaces:** `parse_release(html, symbol, source_url, now, retrieved_at=None)->dict` and `FinancialReleaseProvider(client=None, request_gate=None, clock=None).collect(symbol,now)->dict`, close(). Result includes status, periods/reported facts, sources and warnings. Explicit registry: MSFT `https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast`; NVDA `https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027`; AAPL `https://www.apple.com/newsroom/2026/07/apple-reports-third-quarter-results/`; AMZN `https://www.aboutamazon.com/news/company-news/amazon-earnings-q2-2026-report`. Actual source-shaped fixtures must establish exact period, GAAP basis and currency/scale for each retained metric. Unavailable fields remain unknown. No synthetic annualization/TTM, press-release fact masquerading as10-K, recommendation or full article body. Unknown issuers make zero requests.

- [x] Read four primary pages and record extractable facts/limitations, then write RED fixtures for periods/units/GAAP versus non-GAAP, missing metrics, dynamic shells, future/unsafe source evidence and malformed payloads.
- [x] Implement small source-specific factual adapters using existing dependencies/stdlib; GREEN. Shared gate, bounded requests, no paid provider/signup/credentials/live SEC.
- [x] Report live feasibility separately from fixtures; facts/short summaries with links, no full copyrighted articles.

## Task 5: Integration, field coverage and local evidence imports

**Owner/files:** Controller; stocks.py, stockcli.py, stockhistory.py, new stockevidence.py; associated existing tests and test_stockbreadth.py/test_stockevidence.py; docs/stock-research.md and this plan/ledger.

- [x] RED: default CLI index union, explicit custom/starter compatibility, all requested members including unsupported accounting, strict structured membership errors, coherent snapshot/version and original cutoff in records.
- [x] RED: field statuses/denominators sum to every member, price availability is separate from supported fundamental/valuation/news/earnings evidence; unsafe/future local imports do not promote cases.
- [x] Integrate resolver/batch/news/rendering, named-universe capacity up to 1000 versus explicit custom cap 50, private CLI import/cache/offline controls, original source clocks and full membership/collection metadata in records. Record version changes reflect semantics.
- [x] GREEN all focused new and existing research suites; use generated 500-plus transport smoke with partial failures/resume and zero actual external calls.

## Task 6: Source smoke, integrated review and local handoff

**Owner/files:** Controller/independent reviewer; durable progress/review docs only except material scoped fixes.

- [x] Small counted, rate-limited normal public membership/price/news live sample; distinguish the six-security sample from subsequently observed full 519-price collection. Report exact counts/statuses/as-of without implying complete financial/news coverage. No SEC bypass, paid plan or credential calls.
- [x] Full pytest suite, syntax/whitespace checks, actual CLI private JSON/HTML and offline resume validation.
- [x] Fresh independent whole-branch review against 81d31bd; fix material issues via RED/GREEN and targeted review. Distinguish test correctness from financial accuracy/source completeness.
- [x] Commit reviewed changes locally on codex/stock-research, verify clean isolated/original checkouts, provide commands/deliverables and remaining provider/setup blockers.

## Execution rulings

Final acceptance evidence and source/visual limits are in `docs/superpowers/reviews/2026-10-04-index-universe-final.md` and the progress ledger. Completion is limited to the reviewed local feature; no push or publication is authorized.

- Explicit user multitasking request overrides the subagent skill's serial-only dispatch guidance for independent file ownership. Agents do not commit or modify each other's files; controller owns integration and commits.
- Native Windows durable ledger/brief files replace bash-only skill helper scripts. This plan is new; no prior task completion is redispatched.
- User authorization to proceed without repeated internal design gates overrides additional spec/plan permission prompts. Free-only is confirmed; no paid source is part of the usable core. Imports do not assume credentials/display rights.
- Public community membership is attributed and uncertified; unknown official current completeness is a coverage gap. Private use is separate from redistribution/publication rights.
