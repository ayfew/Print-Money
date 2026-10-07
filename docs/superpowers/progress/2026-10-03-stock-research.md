# Inline execution ledger

Plan: docs/superpowers/plans/2026-10-03-stock-research.md.
Base: a1680f12a1ea6b9f2c89b27f445dba3622ccc82b, branch codex/stock-research.
Original D:/Print-Money remains clean at ad423a130177aadaa74eec1e0061beb3e2478118.

## Decisions

- Annual 10-K metrics supersede the earlier TTM/peer engine, following the parent-approved smaller MVP. Cost if wrong: limited accounting/valuation coverage, exposed in results.
- Inline Windows execution uses this native ledger instead of bash helper scripts. Cost if wrong: less automated bookkeeping, mitigated by fresh tests and independent review.
- Profile values are validated presence/context inputs; allocation, suitability, risk sizing and FX conversion remain explicitly unavailable. Consider cases are always conditional with allocation abstention. Cost if wrong: no personalized allocation; no invented amounts or risk model.
- SEC access denial remains missing evidence; no fabricated fundamentals or access-control bypass. Cost if wrong: fewer qualifying cases, with ranked watch output retained.
- Calendar close is required immediately, without a sixty-minute publication grace; delayed closing bars watch until available. Cost if wrong: conservative temporary watch after close.
- Source origin is not inferred from retrieval/local first-seen; anomaly flags are context only. Social and Thai-stock feeds are not connected.

## Test evidence

Commands use the isolated worktree venv, python -B -m pytest ... -q -p no:cacheprovider.

- Baseline before feature: 439 passed, 1 failed, 193.04s. The existing headline test assumed n>=1000 and a winning rate, although committed live evidence is n=84, hits=38, rate=0.4524.
- Task 1: 19 feature-missing RED failures, then 19 passed, 0.06s.
- Task 2: initial 11 feature-missing RED failures; combined policy/provider/session suite 34 passed, 2.17s. exchange-calendars 4.13.2 installed only in this worktree venv.
- Task 3: 12 feature-missing RED failures, then combined Task 1-3 45 passed, 1 skipped, 1.46s. Skip: Windows process lacks symlink privilege.
- Task 4: evaluator 10 expected RED failures; independent live/backtest threshold/losing-live contract 3 passed while original stale integration assertion still failed. Evaluation and corrected assertion then 25 passed, 1 skipped, 2.67s. Published evidence unchanged.
- Additional source/clock/split-EPS/configuration cases: 7 expected RED failures, then combined new tests 65 passed, 1 skipped, 2.07s.
- Full suite before fresh review: 505 passed, 1 skipped, 14.52s; no failures.
- Changed modules compile from source; git diff --check passes.

## Real adapter and CLI evidence

- Actual Yahoo fetch: MSFT quote day 2026-10-02, raw close 517.530029296875 USD, retrieved 2026-10-03. Status watch/degraded because annual evidence and earnings are unavailable. This is smoke evidence, not a recommendation.
- Public SEC probes returned HTTP 403 at company_tickers, submissions/CIK0000789019 and companyfacts/CIK0000789019. Live annual facts cannot be confirmed in this environment; parser/arithmetic are tested separately with provider-shaped fixtures.
- Actual pm.py research --symbols MSFT --horizon months-plus --json --html: exit 0, one JSON document, empty stderr, private HTML and exclusive record. Record a6b63c6ae8524442188a9f25f96f9372.
- Actual pm.py research-score --json: exit 0, n=0, one not_entered case, no invented performance.
- Actual Windows junction escaping the private root was created in ignored test state and rejected by private_path, supplementing the skipped symlink fixture.

## Review and final verification

- One fresh independent reviewer inspected local commit db842fa against a1680f1. Independent suite: 505 passed, one skip, 13.91s. No Critical findings; seven Important findings reproduced and fixed via focused RED/GREEN. See docs/superpowers/reviews/2026-10-03-stock-research.md for every finding and all deferred-scope rulings.
- All new policy/provider/CLI/history/evaluation tests after the seven fixes: 95 passed, one skip, 2.63s.
- Record provenance/version corrections: two RED failures, then 18 passed, one skip, 1.73s. Source observed_at is this retrieval, not an earliest-sighting claim. Implementation/evaluation definitions are frozen; unknown definitions remain unscorable.
- Full suite after the initial review fixes: **537 passed, 1 skipped, 8.99s**. Policy annual-research-v2, implementation stock-research-v2. At this checkpoint, verification was local and only the initial independent review had occurred.
- Pooled summaries explicitly identify their diagnostic scope. Grouped performance, revision-link/report comparison, excursions/turnover, editable starter configuration and fuller Thai localization remain deferred; documentation states these limits.

## User-requested independent final gate

- A fresh reviewer inspected `ac3ab65b852dd39064a302dfc72f21c7e3671b47` after the initial seven corrections. Independent full suite: 537 passed, one skip, 14.37s. No Critical findings; one Important finding: qualifying catalyst evidence disappeared from JSON/HTML and record identity.
- Four corrected regression tests failed before implementation: missing card catalyst, missing HTML catalyst, unchanged record identity after a catalyst-date change, and missing saved catalyst evidence. RED: four failures, 0.85s.
- Minimal correction preserves a deep copy of supplied catalyst evidence in the card and renders it through existing HTML escaping. Existing record canonicalization now freezes that evidence and includes substantive changes in identity. Screening policy stays annual-research-v2; implementation becomes stock-research-v3.
- Affected suite after correction: 72 passed, one skip, 2.12s. Final full suite: **541 passed, 1 skipped, 8.15s**, using `.venv/Scripts/python.exe -B -m pytest -q --tb=short -p no:cacheprovider`.
- The reviewer independently verified the correction with the affected suite (72 passed, one skip, 1.85s) and a separate temporary-directory reproduction, then explicitly signed off the corrected worktree for local handoff. The full-suite result above is the implementer's fresh execution, not an independently repeated full run after the catalyst fix.
- All five research/CLI modules compile from source; diff whitespace checks pass. Original D:/Print-Money remains clean at ad423a130177aadaa74eec1e0061beb3e2478118. No additional network calls or SEC identity changes occurred during this final gate.
- Complete finding, resolution, evidence and deferred-scope context: `docs/superpowers/reviews/2026-10-03-stock-research-final.md`.

Branch/worktree are preserved for local handoff. No merge, push, PR, deployment, publication, account change or transaction performed. SEC HTTP403 and missing earnings/catalyst/social/Thai feeds are exposed capability limits, not hidden success claims.
