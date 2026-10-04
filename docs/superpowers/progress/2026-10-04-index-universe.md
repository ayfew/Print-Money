# SDD ledger — plan: docs/superpowers/plans/2026-10-04-index-universe.md

Base: 81d31bdd18c51868bb31c28c37b3ac46e04bdab2. Worktree: print-money-stock-research, branch codex/stock-research. Original D:/Print-Money is preserved.

## Preflight interface/ownership scan

| Tasks | Produced/consumed interface or self-check | Ruling |
|---|---|---|
| 1/5 | resolve_universe snapshot/members consumed by CLI/request | Shared schema and source/cutoff rules in plan; root integrates, no overlapping edits. |
| 2/5 | collect_many shared-gate provider factory -> observations/collection | Explicit signatures, root supplies SEC-disabled provider and final report cutoff. |
| 3/5 | render_universe_html -> existing CLI renderer | New owned module only; old reports handled without membership fields. |
| 4/2/5 | NewsProvider with shared request gate -> bounded registered enrichment | Root composes provider; only two explicit feeds, with unknown issuers making no requests and no qualifying catalyst inference. |
| 4b/2/5 | FinancialReleaseProvider -> reported-period evidence | Four dated official releases, unknown issuers skip requests; no quarter-to-annual ratios or10-K relabeling. |
| 5/6 | source metadata/coverage/records -> tests/review | Full-universe transport smoke distinct from small live sample. |
| 1 | Tests versus membership formats/cache scope | Generated plausible populations avoid exact 500-row assumption/licensed fixture redistribution. |
| 2 | Tests versus cache/concurrency/provider ownership | Shared outbound gate and provider circuit; no provider code touched by other implementers. |
| 3 | Tests versus full table and source escaping | Full evaluated list, controls and explicit unknown values, no external JS. |
| 4 | Tests versus unverified news context | Publication clocks/link validation/entity limits; headlines do not satisfy decision gates. |
| 5 | Tests versus policy/CLI/record scope | Existing gates preserved, named membership capacity separate from explicit ticker limit. |
| 6 | Tests versus real-source claims | No inflated financial accuracy or certified membership claim. |

Ruling: parallel implementation with distinct file ownership — explicitly requested by user — cost if wrong: integration rework, mitigated by frozen interfaces and independent final review.

Ruling: no repeated design/plan authorization prompts — explicitly delegated by parent/user — cost if wrong: reversible local changes, with written spec/plan available for review.

Ruling: FREE-ONLY; no free comprehensive production-ready source is established — latest user constraint/parent research — cost if wrong: field completeness remains limited and visible; no paid dependency.

Ruling: community component snapshots are possible coverage inputs but uncertified; gated official lists are not bypassed — source rights/access constraints — cost if wrong: incomplete/uncertain membership visibly blocks a claim of current official coverage.

Status: design/plan self-review complete; independent implementation tasks ready; no product code changed yet.

## Execution start

Independent owners: index_universe (Task1), index_collection (Task2), index_view (Task3), index_news (Task4), index_financials (Task4b). Briefs/reports are private under state/research/dev/index-universe; controller owns policy/CLI/evidence integration. All are on the existing isolated worktree with no overlapping file edits or agent commits.

Ruling: normal live membership accepts now=None and uses actual response receipt/end clocks; explicit historical cutoffs reject later-observed snapshots — avoids artificial future cutoffs — cost if wrong: strict history cannot use a newly fetched snapshot.

Ruling: financial release facts may carry basis=reported_unspecified when the issuer gives an exact period/number without explicit GAAP wording — preserve observable facts without guessing GAAP — cost if wrong: annual valuation/decision gate remains unavailable; labels remain visible.

## Durable checkpoint — 2026-10-04 05:10 UTC

Implementation is in progress, replacing the earlier execution-start status. The five owned modules and root CLI/evidence/policy integration are implemented locally. All breadth changes remain unstaged on `codex/stock-research`, based on `81d31bdd18c51868bb31c28c37b3ac46e04bdab2`; no breadth commit, push, PR, publication, or financial transaction has occurred. Parent requested this immediate checkpoint before further work.

### Observed live evidence

- Real membership snapshot captured at `2026-10-04T04:36:13.639380+00:00`: **519 securities = 504 S&P 500 + 101 Nasdaq-100 − 86 overlap**. Distinct GOOG/GOOGL and BRK.B/BF.B rows are retained. These are community tables with source revision/receipt/license metadata, plus a bounded official October 1 notice; current official completeness is **uncertified**. S&P source revision `1376729338` was edited September 25; Nasdaq source revision `1375648703` September 19. Four pending October 6 changes remain explicitly future events.
- Live six-security CLI sample (MSFT, NVDA, AAPL, AMZN, BRK.B, JPM), final cutoff `2026-10-04T04:08:09.141069+00:00`: **6/6 prices**, each dated October 2; **4/4 registered issuer releases** fetched successfully, containing **6 reported periods and 19 financial metric values**; **2/2 registered feeds** returned **40 headlines**. Nvidia's 20 entries have publication times; Apple's 20 entries have update times only, so its news coverage is partial/unverified. The other four symbols have no registered issuer feed and make no feed requests.
- In that sample, annual valuation and verified earnings coverage were **0/6**. Fixed quarterly release evidence is context, not qualifying annual fundamentals or earnings verification. Current policy does not turn those headlines into catalysts. The source receipt/cutoff values above are original observations, not refreshed claims.
- Full **519-price live collection has not run**. Existing generated 505-row transport/table tests exercise breadth without representing live coverage.
- Private raw evidence: `state/research/source-probes/membership-snapshot.json`, `membership-summary.json`, `six-securities.json`, `six-securities-summary.json`, `six-securities.html`; raw source/cache files remain ignored and must not be staged.

### Verification and review state

- Root's last passing affected suite: **109 passed, 1 skipped in 4.68s** (before the latest actual-schema CIK regressions).
- Independent final review ran eleven affected suites: **384 passed, 1 skipped in 13.12s**. This is an interim result, not final acceptance or the whole repository suite.
- Latest focused RED result: **4 failed, 33 passed in 2.08s** in `tests/test_stockevidence.py`. Failures reproduce actual zero-padded string CIKs bypassing mismatch detection/import binding, mixed string/integer share-class counting, and imported identity canonicalization.
- Seven original stream specification defects and both redirected-request quality defects were independently closed. Final review remains open on the CIK boundary above and an additional evidence-clock boundary: imported reported-period end/annual period end or filing date may be later than the claimed supporting source receipt. Impossible imported actuals must be rejected or remain unverified, with precision-aware same-day handling.
- Full repository tests have **not** been rerun after breadth implementation. No final review approval is claimed. Review evidence is private in `state/research/dev/index-universe/final-review.md`, `stream-spec-review.md`, and `stream-quality-review.md`.
- Actual Node execution verifies table filtering/sorting. Windows Chrome failed during startup; no browser screenshot or visual-render acceptance is claimed.

### Exact continuation order

1. In `printmoney/research/stockevidence.py`, fix `_cik` to validate/canonicalize digit strings and numeric identities; use normalized identities in share-class counts and imported issuer identity. Preserve raw membership CIK provenance and all security rows. Run the four RED regressions above to GREEN.
2. Add focused RED tests for reported/annual facts whose supporting source receipt predates their period end or filing. Fix import/coverage/decision validation without inventing publication or acceptance times. Ask the existing final reviewer to close these boundaries independently.
3. Run fresh root affected checks, then the complete repository suite and `git diff --check`; record exact results. Prior passing snapshots do not substitute for this acceptance run.
4. Preserve the original frozen 519 snapshot. Membership owner now computes semantic versions without receipt clocks. Its original snapshot version (`27572fcd17b63d8152b0b5633e44e5dfca96860ad34293f2922cf4aa9d544c3c`) uses the prior clock-inclusive algorithm. Validate the old artifact, write a separate semantic-version copy with only its top-level version changed, then validate it and atomically migrate the private product cache. Keep source clocks/content unchanged; do not refetch or delete the original evidence.
5. Run bounded full-union live CLI collection (at least one second between all outbound starts, at most four workers, shared denial circuit, SEC disabled) and retain every row/gap. Generate private JSON/HTML artifacts, then verify cache/offline reuse preserves original evidence clocks. Report observed field denominators rather than assumed complete financial/news coverage.
6. Obtain independent final review closure, update plan/ledger with exact final evidence, and create an authorized local breadth commit only. Preserve original `D:/Print-Money`; no merge/push/PR/deploy/publication is authorized.

### Source and acceptance limits

SEC access was previously 403 and remains disabled: **no new SEC requests**. Official complete membership lists are gated; no access restriction was bypassed. Only four fixed dated issuer releases and two registered free feeds are supported, so broad financial/news gaps remain explicit. No paid provider, subscription, signup, new credential, brokerage connection, or portfolio publication is part of this work. General research with justified watch/no-trade remains appropriate until qualifying evidence and user horizon/profile are supplied.

## Resumed acceptance — 2026-10-04

Parent explicitly authorized completing the saved remainder, including conservative resumable 519-price collection, final independent review and a local commit. No push, deployment or account change is authorized.

- CIK regression GREEN: all four actual digit-string/mixed-representation cases closed; canonical validated identities drive comparison/counting/imports while raw membership values stay inspectable.
- Financial clock RED: **9 failed, 38 passed in 4.45s**. Regressions reproduced period ends after source receipt/publication, annual filing after receipt, and a same-day date-only filing being promoted without acceptance time. `_actuals_observed` now binds those facts to their supporting clocks; imported impossible actuals reject, raw API evidence remains unverified, and P/E/consideration is withheld. Day precision is retained, and unknown original news publication is never filled from update time.
- Root affected GREEN: **123 passed, 1 skipped in 9.21s**. Command: `.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_stockevidence.py tests/test_stockbreadth.py tests/test_stocks.py tests/test_stockcli.py tests/test_stockhistory.py -q --tb=short -p no:cacheprovider`.
- Fresh complete repository suite: **854 passed, 1 skipped in 28.57s**. Command: `.venv/Scripts/python.exe -X utf8 -B -m pytest -q --tb=short -p no:cacheprovider`. The skip is the documented Windows symlink capability test, not a missing market-data assertion.
- Final independent reviewer replayed all seven initial integration findings and both follow-up boundaries, plus saved exact counterexamples and baseline record compatibility. Fresh independent affected run: **123 passed, 1 skipped in 6.89s**. No material implementation finding remains open. Private `final-review.md` supersedes its earlier interim failure; browser and complete live issuer-financial coverage remain limitations.
- Frozen membership migration completed at `2026-10-04T05:25:18.991195+00:00`, making **zero requests**. Original SHA256 `28f28a152fb515ebe6ecb35e53908671bee6a7712e62a81180d7029f3ff7e1ac` and every original byte/source clock were preserved. New semantic version: `3a3e32573c5b8d4640d6480bfc2987c04deda70edf7f7fc318fda8f3258239cd`. Legacy digest verification, only-version-changed equality, strict snapshot validation and atomic private cache/readback all passed. Private migration summary: `state/research/source-probes/membership-version-migration.json`.
- Full live collection is running under the native CLI through a private audit harness, with one-second shared pacing, four workers, eligible cache reuse and SEC disabled. First 100 new observations have usable prices; no 401/403/429 denial has been observed at this milestone. Final full-universe counts and offline/record acceptance are still pending.

This section supersedes the checkpoint's open CIK/clock findings and missing full-suite run. It does not claim completed live collection, final artifacts, browser rendering, or a local commit.

## Full live and offline acceptance

Native full CLI collection completed at `2026-10-04T05:36:17.102428+00:00`; original report cutoff is `2026-10-04T05:36:16.437258+00:00`. **519 requested = 6 cached + 513 newly completed + 0 failed**. All 519 prices are dated `2026-10-02`, the expected completed session. Audit: 513 starts on the price host, minimum measured start gap **1.0000468000071123 seconds**, four workers, no 401/403/429 denials and **zero SEC requests**. Original source clocks were preserved. Private harness: `.venv/Scripts/python.exe -X utf8 -B state/research/source-probes/index-universe-live-smoke.py`.

Every row contributes to field denominators of 519: price available519; financial context available4/unsupported114/blocked401; annual valuation available0/missing405/unsupported114; verified earnings available0/missing519; news available1/unverified1/unsupported517; price/volume risk context available517/unverified2. Overall: **519 watch; zero consider/avoid/excluded**. All these counts are observed ingestion/evidence states, not recommendation accuracy.

Financial extraction is **4 companies / 6 reported periods / 19 metric values**, separately from **0 qualified annual-filing companies / 0 qualified annual-valuation companies**. Raw quarter/annual release context is never relabeled as qualifying10-K evidence or synthesized TTM. News is **40 headlines / 2 companies**, with **20 original-publication-unknown Apple items** retained as partial context (`published_at=null`); update timestamps do not pass publication freshness or event/catalyst qualification.

Native offline assertion harness passed: **519 cached, zero attempted/completed/failed new observations and zero HTTP calls**. Original evidence clocks/content, membership and field coverage are identical to the live report. It saved the full prospective record once and re-recorded the offline report to the same ID, retaining the original cutoff. Two existing records were verified unchanged by byte hash. Private evidence: `index-universe-offline-summary.json`; command: `.venv/Scripts/python.exe -X utf8 -B state/research/source-probes/index-universe-offline-smoke.py`.

Deliverables: private `state/research/latest.html`, frozen `state/research/source-probes/index-universe-live.json`, `index-universe-live.html`, live/offline summaries, source snapshot/migration and request audit. All raw payloads/cache/records remain ignored. Durable public engineering evidence: `docs/superpowers/reviews/2026-10-04-index-universe-final.md`. Syntax parsing covered22 module/test files; root whitespace check passed. Browser rendering remains unverified due the documented Windows Chrome startup failure. Local staging/commit and clean-checkout handoff remain pending at this entry.
