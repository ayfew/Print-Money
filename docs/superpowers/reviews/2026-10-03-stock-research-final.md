# User-requested independent final gate

The user explicitly requested another independent review after the seven initial corrections. A fresh read-only reviewer inspected `ac3ab65b852dd39064a302dfc72f21c7e3671b47`, focusing on financial integrity, provenance, private records and the CLI contract. The reviewer independently ran the full suite: **537 passed, 1 skipped, 14.37s**. No Critical findings were identified; one Important finding required correction.

## Important finding and correction

The `days-weeks` gate validated a dated catalyst, but the returned card omitted the evidence. JSON, private HTML and saved records consequently lacked the exact date and source condition that qualified the case. The reviewer independently changed only the supported catalyst date from 2026-10-20 to 2026-10-21: both reports remained `consider`, neither report displayed the catalyst, and recording both returned the same ID with one record.

Four focused regression tests confirmed RED before the implementation change (four failures, 0.85s). The smallest correction adds a deep copy of supplied catalyst evidence to each card and renders that field through the existing literal HTML escaping. Records already preserve card fields and hash substantive evidence; no storage redesign was needed. Date, symbol, verification state, source reference and original retrieval time now survive in the report and record. A changed event date creates a separate immutable record. Retrieval-time-only changes keep the first record and original cutoff. Later mutation of the input observation cannot alter the returned catalyst snapshot.

Screening rules remain `annual-research-v2`; the declared implementation version is `stock-research-v3`. Existing records remain loadable with their original frozen definitions. The correction does not connect a catalyst feed or establish independent truth authentication of supplied evidence.

## Fresh verification and assessment

- Implementer affected suite: **72 passed, 1 skipped, 2.12s**.
- Implementer full suite: **541 passed, 1 skipped, 8.15s**, using `.venv/Scripts/python.exe -B -m pytest -q --tb=short -p no:cacheprovider`.
- Reviewer targeted verification: **72 passed, 1 skipped, 1.85s**, plus a separate temporary-directory reproduction of changed-date identity and retrieval-only idempotence.
- Reviewer assessment after the correction: **signed off for local handoff of the reviewed corrected worktree; no remaining material scoped defect identified**.
- All five research/CLI modules compile from source; `git diff --check` passes. The original D:/Print-Money remains clean at `ad423a130177aadaa74eec1e0061beb3e2478118`.
- The skipped test requires Windows symlink privilege. The earlier separately recorded Windows-junction check is supplementary; it was not independently repeated in this final gate.

The reviewer independently exercised parser-level CLI failures before the catalyst correction: research with an invalid P/E cap and research-score with an unknown argument both returned exit 2, one parseable JSON error document and stderr diagnostics. The prior seven corrections and their regressions were verified as present. The final targeted sign-off covers the catalyst correction; the final full-suite execution is explicitly attributed to the implementer.

## Scope and remaining capability limits

The [initial review](2026-10-03-stock-research.md) retains every initial finding and exhaustive scope ruling. This final reviewer confirmed those deferrals:

- Annual 10-K evidence is the approved MVP; TTM, forward/peer/fair-value engines and universal accounting remain outside it.
- Financial issuers, ambiguous share classes, ADR/accounting variants, Thai equities and other markets need supported evidence and separate policies.
- Connected earnings, catalyst, social and independent price feeds, provider redundancy and live SEC validation remain unavailable. SEC HTTP 403 is an externally blocked capability. No live SEC requests, identity changes or attempts to circumvent that restriction occurred during this final gate.
- Allocation, suitability, affordability, sizing, FX, saved profiles, holdings concentration and cash needs remain unavailable and disclosed.
- Intraday entries, numerical targets/stops, custom horizons, actual fills, taxes, venue fees and market impact remain outside the hypothetical daily comparison.
- Grouped performance, report comparison/revision links, excursions, turnover analytics, editable starter configuration and fuller Thai localization remain documented deferrals.
- A persistent earliest-sighting registry, stronger adversarial filesystem defenses and a generic schema framework remain infrastructure deferrals. Immediate-close freshness remains the deliberate conservative rule.
- Historical advice backfilling, legacy-score reuse, profitability claims, policy optimization and unsupported winning-rate/sample-size assertions remain excluded by the prospective unvalidated contract.
- Scheduling, hosted chat, publication, accounts, credentials, brokerage/trading, merge and deployment remain outside authorization.

No local handoff blocker remains. The live SEC and missing-feed limitations above remain visible in reports and continue to block unsupported consideration.
