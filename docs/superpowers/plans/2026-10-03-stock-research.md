# Stock Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver runnable on-demand US stock research, source quality and contextual anomaly flags, abstention and private prospective records.

**Architecture:** New research modules collect public daily prices and supported reported annual SEC facts; a pure screening function handles gates and attention ranking. A thin CLI emits strict JSON or private local HTML and a separate evaluator resolves local records. Existing daily reports and trading modules are outside the write scope.

**Tech Stack:** Python, existing httpx/PyYAML, optional exchange-calendars for completed US sessions, pytest and HTTP transport fixtures.

**Spec:** `docs/superpowers/specs/2026-10-03-stock-research-design.md` (parent-approved annual-metrics MVP).

## Global Constraints

- Original D:/Print-Money files and HEAD remain preserved; branch codex/stock-research starts at a1680f1.
- US-first starter universe: AAPL, MSFT, GOOGL, META, PG, KO, CAT, HON, XOM, CVX, JNJ, MRK; disclose coverage and no personal suitability assumption.
- At most five highlights; no quota, forecasts, transactions, publishing, paid feeds or new persistent credentials.
- At least 252 completed observations; twenty-session average USD dollar turnover at least 5 million; price stretch above two standard deviations or 20% over 21 sessions watches.
- Reported annual 10-K metrics only; 450-day filing-age limit; annual-earnings P/E research cap 25. No synthetic TTM or peer engine.
- Future/ambiguous facts and unknown earnings cannot qualify consideration; earnings blackout seven calendar days before through the completed post-release session.
- Missing investment profile yields useful general research; no invented budget/FX conversion/stop prices.
- Price/volume anomaly flags are contextual, never allegations of manipulation. Social and Thai-stock feeds are not connected.
- Private state and export paths under state/research; pure JSON stdout and stderr diagnostics.

## Review Focus

- A split/dividend must not fabricate a price/volume anomaly: provider fixture with adjusted/raw prices and a recent split.
- Future publication dates and malformed units must not enter metrics: annual-fact fixtures with future filings and cumulative/interim periods.
- Source text must never become instructions or HTML execution: malicious text/URL fixture and escaped rendering.
- Unknown symbol/market must not appear to be a negative investment thesis: provider/type fixtures and excluded result.
- Output traversal/symlinks must not write private research into public artifacts: real temporary-directory path tests.

## Task 1: Pure request/policy and source-risk output

**Files:** Create `printmoney/research/stocks.py`, `tests/test_stocks.py`.
**Interfaces:** `ResearchRequest` validates optional profile and symbols; `screen(request, observations, now) -> dict` produces schema-versioned JSON-safe research; `risk_context(observation) -> dict` gives anomaly/source limits.

- [x] Write failing behavior tests for no-symbol defaults, unknown profile, consider/watch/avoid gates, delayed/future observations, unsupported symbols, price/volume anomalies, single-source corroboration and malicious source data.
- [x] Run `python -B -m pytest tests/test_stocks.py -q -p no:cacheprovider`; expect feature-missing failures.
- [x] Implement minimal request, pure gates, descriptive annual metrics, attention ranking and evidence fields.
- [x] Run the focused tests; expect all pass. Inspect scope.

## Task 2: Actual public providers

**Files:** Create `printmoney/research/stockdata.py`, `tests/test_stockdata.py`, `requirements-stock.txt`.
**Interfaces:** `StockProvider.collect(symbol, now) -> dict`, `parse_chart(payload, symbol, now, calendar) -> dict`, `annual_facts(payload, cutoff) -> dict`; these dictionaries are Task 1 observations.

- [x] Write failing real-parser/HTTP-transport tests for completed-session prices, delayed data, split handling, future filings, compatible full-year EPS/income/cash-flow and unsupported exchange/security.
- [x] Run focused tests and verify expected missing-feature failures.
- [x] Implement bounded HTTP GETs, optional exchange calendar, public SEC ticker mapping/company facts, explicit provider errors, and unknown earnings. Preserve daily-bar helpers' raw/adjusted conventions.
- [x] Run focused tests; expect pass. Install calendar into this worktree's venv if available; absence must fail consideration closed.

## Task 3: On-demand integration, CLI, private HTML and record

**Files:** Create `printmoney/research/stockhistory.py`, `printmoney/research/stockcli.py`, `tests/test_stockcli.py`, `tests/test_stockhistory.py`; modify `printmoney/cli.py` only for parser registration.
**Interfaces:** `run_stock_research(request, provider=None, now=None, record_dir=None) -> dict`; `record(report, directory) -> str`; `render_html(report, lang) -> str`; `private_path(path, root) -> Path`.

- [x] Write failing tests for real orchestration, one-document JSON on errors/success, default useful research, source warnings, source-content escaping, private path traversal/symlinks and idempotent records with private amounts excluded.
- [x] Verify focused RED, implement thin adapters and application-immutable records, then focused GREEN.
- [x] Real-provider smoke run for one starter symbol with no recording; verify data or explicit unavailable status, never invented facts. No public artifacts are written.

## Task 4: Prospective evaluation, baseline contract and documentation

**Files:** Modify `stockhistory.py` and `stockcli.py`; extend their tests; update `tests/test_integration.py` only after independent headline selection checks; add `docs/stock-research.md` and README entry.
**Interfaces:** `evaluate(records, price_series) -> dict` retains pending/missing/not-entered cases and measures 21/63-session scenarios, same-window SPY and explicit 10/30bp costs.

- [x] Write failing tests for pending maturity, negative return, next-open entry, weekend dates, modified records, absent outcomes and benchmark alignment.
- [x] Verify RED, implement minimal evaluator and research-score CLI, verify GREEN.
- [x] Independently demonstrate headline selects live at n>=30 and backtest otherwise. Correct baseline integration assertions to match selected committed evidence, including losses; do not change published numbers or force historical selection.
- [x] Document installation, actual commands, default universe, annual metric/earnings/data limitations, source-lineage limits, social/Thai feeds absent and private output behavior.
- [x] Run full `python -B -m pytest -q -p no:cacheprovider`, compile checks, actual CLI JSON and private HTML smoke. Expect green suite and parseable output.
- [x] Commit only the isolated feature branch locally; dispatch one fresh code reviewer, fix Important/Critical findings via RED/GREEN, verify final suite; leave branch/worktree available without merge/push/deploy.

## Execution decisions

- Few delegated setup and execution; parent approved this revised design and inline method. No further internal design approval gate.
- Baseline before feature: 439 passed, one failing stale headline expectation (requires n>=1000 and rate>0.5 despite live n=84/rate=0.4524). Treat it as a test-contract defect only after independent verification.
- Native PowerShell ledger replaces the skill's bash helper scripts in this Windows environment. Record RED/GREEN commands, deviations and final review here; preserve the ledger for handoff.
