# Broad index-universe research design

## Intent and authorization

Few explicitly requested serious coverage of stocks in the S&P and Nasdaq, the information needed to decide with real money, multitasking when collection is slow, and code quality. The parent stated S&P 500 plus Nasdaq 100 as the working interpretation and is collecting any correction. Nasdaq 100 is not the Composite or all Nasdaq listings.

This is architectural: membership, bounded collection and a full-universe interface extend the existing reviewed research workflow. User-directed parallel execution and the parent's explicit instruction to proceed without repeated internal design approvals govern this cycle. This written design and plan are the reviewable execution artifacts. Work continues in the existing isolated codex/stock-research worktree from 81d31bdd18c51868bb31c28c37b3ac46e04bdab2; the earlier implementation is retained.

## Alternatives and selected approach

1. **Selected: evidence-aware broad collection with interchangeable membership/evidence boundaries.** Public accessible component tables may supply an attributed community snapshot, explicitly uncertified. Actual public daily prices are collected conservatively, with bounded registered headline context and explicit free-source local evidence imports. Missing fields remain explicit. This is immediately useful without pretending a complete production feed exists.
2. **Licensed consolidated feed, rejected for this release.** The user explicitly requires free publicly released data with no budget. The usable core cannot depend on FMP, EODHD, signup or new credentials. Optional import boundaries do not assume paid data-display rights.
3. **ETF holdings or a static starter list.** Official holdings are useful corroboration but are not certified index membership; the earlier twelve stocks do not meet the user's breadth requirement. Holdings are not silently substituted for constituent lists, and starter coverage is only an explicitly selected legacy option.

## Membership and provenance

Default CLI research selects the security-level union of S&P 500 and Nasdaq 100. No exact row count is hardcoded: indices may have multiple share classes. Keep GOOG and GOOGL distinct. Normalize provider aliases for BRK.B/BRK-B and BF.B/BF-B without deduplicating by CIK. Each member has a canonical symbol, provider symbol, name, sector/industry where supplied, CIK if known, and index tags.

Snapshots carry schema/name, content version, captured_at, source URL/retrieval/content hash/license, source-reported as_of/effective dates if actually available, verification and caveats. Unknown effective dates stay unknown. A community source is never relabeled official or certified current. Reject malformed, implausibly small, future and conflicting snapshots; preserve pending future-effective changes separately. Sources are independently available/unavailable: partial membership is disclosed, never called a complete union. Membership imports use the same schema and checks, with their actual provenance. Cached membership and its observation time are preserved; a historical cutoff cannot use a later-observed snapshot. Current membership cannot be replayed as an unbiased historical universe.

Live downloads and full constituent payloads remain in private Git-ignored state/research. Deterministic test fixtures contain generated symbols/minimal examples and attribution; they do not redistribute a licensed live dataset.

## Collection and field evidence

New bounded collection uses at most four workers by default and one shared outbound request per second. Each actual provider request acquires the same gate; worker count does not multiply the rate. No immediate retries for 401/403/429. A provider-wide denial opens a circuit; it must not become hundreds of denied requests. SEC remains blocked in this environment and is disabled for broad live collection. An explicit normal SEC option can support a future authorized working environment; it does not rotate identities or bypass restrictions.

Per-symbol successful observations are cached atomically for six hours, with original retrieval timestamps and source evidence. Reuse is incremental and resumable after interruption. Invalid/future cache entries are ignored; offline mode emits every member with cached data or an explicit gap. Failed symbols do not disappear. Cache does not turn a stale quote into current data. Only completed unique exchange sessions enter daily metrics; split/adjustment validity and all existing annual/event gates remain intact.

Public chart data can support price, dated volume, return/stretch/liquidity and contextual risk. No complete free fundamental, earnings or news capability is promised. Explicit issuer RSS/Atom headlines are unverified context with publication/retrieval clocks, literal content and safe links; they do not qualify catalysts or earnings. DTD/entity payloads, unsafe URLs, malformed dates and future headlines cannot become executable or qualifying content. Provider credentials, subscriptions and entitlements are never inferred from accessible documentation.

An explicit local evidence import boundary retains compatible source-backed fundamentals/valuation/earnings/news for inspection; unsupported accounting or units are labeled. It cannot silently promote an unverified provider ratio into the annual-10-K decision gate. Banks, insurers, REITs and ambiguous classes remain visible with unsupported accounting/valuation reasons. Existing annual P/E is not treated as universal fair value.

Verified free initial sources are the public S&P 500 and Nasdaq 100 company tables on Wikipedia (CC BY-SA4.0 with attribution, revision and adapted-content notice), Apple Newsroom Atom and NVIDIA News RSS, and dated official public financial-result releases for MSFT/NVDA/AAPL/AMZN. Yahoo RSS returned404 in the parent's live probe and is not presumed supported. Explicit issuer source registries avoid invented feed URLs. Public financial adapters retain exact quarter/annual/year-to-date periods and GAAP/non-GAAP units; incompatible quarter EPS cannot become annual valuation. News exposes facts/short summaries/headline links, never full articles.

Parent primary-source research identified an official S&P notice dated October1: ADD VYLR October1; DELETE CTVA October6; ADD TWLO/DELETE WBD October6. Verify and parse the actual table before reconciliation. On October4, future changes remain pending and only validated effective changes apply. Temporary extra membership is valid. This notice is additional provenance, not certification that every intervening change is covered.

## Full-universe interface

Private HTML includes every evaluated security in a searchable, sortable and filterable table; five highlights remain a convenience. Columns include index tags, name/sector, consideration state, dated price, returns/liquidity, annual operating evidence/valuation where supported, earnings, source/coverage status and reasons. Details expose original dates, source lineage, news, risk flags, invalidation and unknowns. Unknown is displayed explicitly and sorts after known values. Search/filter state must not hide the total or filtered denominators.

Coverage diagnostics count every requested member once for each field group: price, fundamentals, valuation, earnings, news and risk. Status buckets are mutually exclusive (available, missing, stale, unsupported, blocked, unverified, not_requested) and sum to the universe denominator. Separate membership completeness/certification from data-field completeness. Tests passing, many rows and price availability are not evidence of recommendation accuracy or financial completeness.

The renderer is self-contained, uses escaped literal content, safe HTTPS links and local search/filter/sort controls. There are no external scripts, network requests or public hosting. JSON contains the full universe and snapshot/version, collection counts and every coverage gap. Prospective records freeze those inputs with the original cutoff; scoring remains limited to the supported forward observation contract, never historical membership backfill.

## Validation and limits

Each independent stream follows RED/GREEN with deterministic transport fixtures. Integration covers a realistic 500-plus security snapshot with overlaps/share classes, partial provider denial, resume, cutoff leakage, cache poisoning, unknown accounting and malicious HTML/XML. A small explicitly counted live sample verifies reachable public adapters without request bursts; a full-universe offline/transport smoke verifies scale and completeness without rate abuse. A fresh independent final review follows integration, with findings fixed and verified.

All work and exports stay local to the existing isolated branch/private state. No merge, push, deployment, publication, transaction, brokerage access, account change, paid provider or credential setup is authorized by this implementation. Free-only is explicit; public availability does not grant full-article redistribution rights. SEC403 and membership/provider gaps remain stated blockers to data completeness.
