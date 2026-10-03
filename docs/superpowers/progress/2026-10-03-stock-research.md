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

Fresh review and final verification pending. No merge, push, PR, deployment, publication, account change or transaction performed.
