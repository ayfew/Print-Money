# Fresh review and local resolution

The initial independent read-only reviewer inspected `db842fa4e9a04afd27815740a4f1469a80b27462` against `a1680f12a1ea6b9f2c89b27f445dba3622ccc82b`. Reviewer independently ran the full suite: 505 passed, one Windows symlink-privilege skip, 13.91s. Initial assessment requested changes, with no Critical and seven Important findings. The later user-requested independent final gate and catalyst correction are recorded in [the final review](2026-10-03-stock-research-final.md).

## Important findings and resolution

| Finding | Locally reproduced RED | Implemented correction and GREEN |
|---|---|---|
| Raw fallback prices scored as adjusted returns | Three failures for missing stock/SPY adjustment and parser metadata | Each exported bar retains an explicit adjusted-price validity flag. Both stock and benchmark endpoints must be verified; otherwise unscorable. Provider/evaluation suite: 28 passed. |
| Missing/nonfinite stretch metrics passed as zero | Three failures | Both stretch metrics must be finite and have adjusted basis; otherwise explicit watch. Stock suite: 28 passed. |
| Imported annual/event evidence bypassed validation | Ten failures | Validate annual currency, full-year dates, filing chronology, CIK, accession and official source. Earnings/catalyst require matching symbol, supported source and cutoff-valid retrieval; truthy flags cannot qualify. Combined affected suite: 54 passed, one skip. |
| Revenue-growth denominator mixed filing versions | Two failures, including a sign reversal | Use comparative prior revenue from the current accession/tag; retain denominator value, dates and accession. Missing/ambiguous compatible denominator stays unavailable. Provider suite: 18 passed. |
| Duplicate/non-session rows inflated history | Three failures | Check exchange sessions and deduplicate before rolling metrics. Conflicting duplicates fail quality explicitly. Provider suite: 21 passed. |
| Malformed payload escaped isolation/JSON | Five failures, one existing passing malformed case | Validate JSON container types and representable finite timestamps; retain per-symbol unavailable results while other symbols continue. Provider/CLI suite: 34 passed. |
| Parser-level errors produced no JSON | Four failures | Research parse errors now preserve normal stderr diagnostics and return exactly one structured JSON document on stdout. Other CLI paths retain their parsing behavior. CLI suite: 12 passed. |

Additional record-version/provenance tests first failed twice, then passed in the affected suite (18 passed, one skip). Records freeze implementation and evaluation definitions; unknown definitions are unscorable. At this checkpoint, policy/implementation versions were `annual-research-v2` / `stock-research-v2`.

Full suite after these corrections: **537 passed, 1 skipped, 8.99s**. This checkpoint was fresh local verification of the fixes, before the later user-requested independent final gate. Changed modules compile and diff whitespace checks pass. The preserved original checkout remains clean at `ad423a1`.

## Minor rulings and cost if wrong

- Misleading first-seen field: fixed by naming `observed_at` as this retrieval, explicitly not an earliest-sighting registry. Cost if wrong: no persistent first-seen provenance is available, exposed in output.
- Pooled performance: retain a clearly labeled pooled diagnostic and individual policy/horizon rows; grouped summaries are deferred. Cost if wrong: readers must use per-case context rather than infer one strategy estimate.
- Optional-calendar tests: documentation now explicitly requires the stock extra for complete integration testing. Calendar-unavailable behavior is tested explicitly. Cost if wrong: the complete suite needs that documented dependency; runtime absence still blocks consideration/scoring.
- Report-to-report comparison/revision links, excursions/turnover analytics, editable starter configuration and fuller Thai localization are deferred and documented. Starter coverage is defined by this dated policy/code version. Cost if wrong: less presentation/history tooling; evidence and original decisions remain inspectable.
- Record code/evaluation-definition versioning: implemented with explicit versions and a frozen definition, without pretending the artifact can contain its own Git commit hash. The local commits identify source revisions. Cost if wrong: manual source-to-record revision mapping uses that declared implementation version.

## Exhaustive set-aside scope rulings

These are deliberate deferrals, not unresolved Important findings:

- TTM/forward earnings, peer/fair-value engines and universal accounting: superseded by the parent-approved annual MVP; limited valuation coverage is disclosed.
- Banks/insurers/REITs, ambiguous share classes, ADR/accounting variants, Thai equities and other markets: require their own evidence/policies; no coverage inferred.
- Earnings/catalyst/social/independent price feeds: not connected; retain useful watch output and single-source limits. No paid feeds or denied-access bypass.
- Allocation/suitability/affordability, sizing/FX, saved profiles, holdings concentration and cash needs: deferred; no personal amount or risk model invented.
- Intraday entries, numerical targets/stops, custom horizons, real fills, taxes/venue fees/market impact: outside daily conditional research comparisons; assumptions are explicit.
- Excursions and turnover analytics: deferred endpoint-return expansion; no claim these statistics are implemented.
- Publication/scheduling/hosted chat, accounts/credentials/brokerage/trading, merge/deploy: outside authorization and untouched.
- Historical advice backfilling, old volatility-score reuse, profitability claims and policy optimization: excluded from this prospective unvalidated contract.
- Provider redundancy, persistent earliest-sighting database, stronger adversarial filesystem defenses and a generic schema framework: unnecessary expansion for this MVP; targeted data/path checks remain.
- Sixty-minute close grace: conservative immediate-close rule stays per recorded ruling; may temporarily watch delayed bars.
- Restoring a winning-rate/sample-size floor in the baseline test: rejected; independently verified committed live evidence selects n=84/hits=38/rate=0.4524 with beats_coin=false.
- Extra broad re-testing after final green checks: not needed without new changes/failures; final suite covers the corrected tree.

Remaining external limitation: public SEC endpoints returned HTTP 403 in real smoke tests. Yahoo prices were reachable; missing annual/earnings evidence stays visible and cannot qualify consideration. No permission blocker remains for local handoff.
