# Experimental intraday momentum scanner

This on-demand experiment preserves long-term `research`, `research-score` and
the daily brief. No orders, notifications, credentials or schedules are added.

## Run and evidence

Install the existing `requirements.txt` and `requirements-stock.txt` dependencies.

```
python pm.py intraday --symbols AAPL,NVDA --json
python pm.py intraday --input observations.json --as-of 2026-10-06T14:10:00Z --json
```

Live collection accepts at most five explicit symbols. It reuses public Yahoo
charts and registered issuer RSS, with shared pacing, no retries and host circuit
breaking on 401/403/429. There is no trial, paid service or access-control bypass.
RSS is context, not verified catalyst evidence. A chart response does not attest
complete premarket volume or corporate-action comparability, so these remain
unknown in live mode. Imported reviewed evidence can satisfy the contract; its
attestations are not independently verified by PrintMoney. Use only sources you
are permitted to use. No real-time coverage or redistribution rights are implied.

Each run creates unique timestamp/UUID JSON and HTML files beneath ignored
`state/research/intraday/runs/`. They retain inputs, source timestamps, exclusions,
zero-result runs, strategy version and policy. Pages publishes only `reports/`.
Exit 2 means a symbol cannot be evaluated. Waiting/closed-market/zero-match runs
return 0; this does not certify source coverage. Every row shows its state.

## Input contract

Input is a JSON list, at most 50 unique symbols. Each observation supplies:

| Field | Required semantics |
|---|---|
| symbol, currency, instrument_type | Plain ticker, USD, EQUITY |
| source_url, retrieved_at | HTTPS evidence URL, timezone-aware receipt |
| premarket_complete | Reviewed attestation of complete 04:00-09:30 ET coverage |
| corporate_actions_verified | Daily/intraday raw prices comparable; no unhandled split |
| daily | 200 consecutive completed XNYS sessions: session, close, high |
| bars | Five-minute bars: aware start, open, high, low, close, volume |
| catalyst | status=verified, source, HTTPS url, description, published_at, observed_at, verified_at |

News must be published, observed and verified in order before the signal bar ends,
within the preceding 24 hours. Historical imports must retain actual knowledge
times. Live requests reject `--as-of`: today's data cannot reconstruct past
knowledge. The synthetic `tests/test_intraday.py::sample` illustrates the schema;
it is not market evidence.

## Stages

XNYS sessions and America/New_York handle DST, holidays and early closes. 04:00 is
the consolidated extended-hours research window, not every listing venue's open.
Completed bars before 09:30 determine premarket last/high/volume; those freeze at
the open. Changed evidence after an accepted freeze causes abstention.

After 10:00 ET and before session close, require strictly:

1. Frozen premarket last / previous regular close minus one > 5%.
2. Premarket and current price > $3; premarket volume > 50,000.
3. A verified dated catalyst, independently of fundamental coverage.
4. Previous close > its 200-session SMA.
5. Latest completed five-minute close > previous-day high, frozen premarket high,
   and all **earlier** regular bars' highs. The current bar is excluded from HOD.

Future/incomplete bars never contribute. Full regular-session history is required
for prior HOD. Missing/invalid/stale evidence means `cannot_evaluate`; failed
thresholds mean `watch`; early runs are `waiting`; closed sessions are
`outside_session`. A passing row is `experimental_signal`, never a buy instruction.
One new signal per symbol/session/policy is persisted. Repeat runs retain evidence
with `new_signal=false`. A writer lock prevents races; corrupt history fails closed.
After a killed writer, inspect records before manually removing a stale lock.
CLI thresholds are configurable and included in policy identity.

## Evaluation is unvalidated

The report exposes a hypothetical next-bar-open entry, 2% stop, 4% target,
stop-first ambiguous bars, worse opening fills on gaps and session-close exit.
Costs default to 30 bps round trip, with 10/30/60 bps sensitivity required for future
evaluation. They are experiment assumptions, not user risk tolerance or reliable
thin-stock execution estimates. No position size is inferred.

There is no verified point-in-time intraday/news/universe archive here. Sample
count is zero; expectancy, win rate, mean win/loss, drawdown and matched-period SPY
return are null. This increment does not implement a fill simulator or claim a
historical strategy result. Before validation: freeze the policy, collect a
prospective untouched chronological holdout, develop only with walk-forward
splits, retain delisted names and contemporaneous membership/news, include net
costs, all exclusions and exposure assumptions, and compare SPY at exactly matching
entry/exit times. Do not tune on the holdout or substitute today's index members.

## Existing 45% number

`data/scorecard.json` measured 2026-10-01 has 38 hits / 84 resolved live volatility
calls = 45.24%, Wilson lower bound 34.84%. `scorecard.Resolved.hit` checks whether
next-21-session volatility lands above/below its historical median as predicted.
This is not portfolio return or an intraday win rate. `10y over 24 markets` is the
historical scoring span, not the date range of the 84 live calls. Correlated markets
and overlapping outcomes also limit independent-binomial interpretation. The
scanner does not alter this record or optimize to inflate it.

## Pages diagnosis and verified recovery

On 2026-10-07, run 37579708327 failed at configure-pages with Not Found;
repository metadata returned `has_pages=false`. Daily brief 37579086114 succeeded.
The workflow adds read-only preflight diagnostics and main-only publication. It
does not enable Pages, suppress failure, change permissions or rerun deployment.

After owner approval on 2026-10-07, Pages Source was set to GitHub Actions through
the existing browser session. [Run 37579708327, attempt 2](https://github.com/ayfew/Print-Money/actions/runs/37579708327/attempts/2)
succeeded, publishing main commit `3153bd838040eafbea0654752f9dbbbde8f56333`.
Both [the public brief](https://ayfew.github.io/Print-Money/) and its calendar
returned HTTP 200 and matched the audited local files byte for byte. The public
site remains the daily brief; this scanner's local reports were not published.

The preflight changes in this branch have passed YAML parsing but have not run in
GitHub Actions. Pushing the new code is blocked by authentication in this execution
context. A noninteractive push dry-run returned 128: Git Credential Manager could
not persist credentials with `wincredman`, then could not get a password. This does
not establish that the user lacks repository write permission. The CLI's cached
account is `Napatsakorn-K_tcct` with an invalid token; the connector authenticates as
that same account but its create-branch/permission endpoint requests return 403
`Resource not accessible by integration`. Its exact OAuth scope is not exposed.
The already-authorized browser is signed in as repository owner `ayfew` and can
access Pages settings. No credential was extracted or transferred between routes,
and no new token or permission was created. Merge is still pending. Existing cadence is
unchanged; a future scanner cadence/request budget needs separate review.

The local scorecard UI now names the volatility classification hit rate, separates
live from backtest, and displays hits/sample count plus summary measurement time.
Legacy call-date ranges remain explicitly unknown. Future saved summaries include
the first and last actual scored call dates; the summary's timestamp or the
backtest's `10y` label is never substituted for those dates.

Sources: [GitHub Pages configuration](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site),
[NYSE calendar](https://www.nyse.com/trade/hours-calendars).
