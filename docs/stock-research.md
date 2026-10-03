# On-demand stock research with Sol

Ask for research when you want to review available funds. This command produces conditional evidence, watch conditions, reasons to avoid new exposure and a valid no-trade outcome. It has no validated stock-return track record and does not send orders.

## Install and run

Use an isolated Python environment. Core requirements already include httpx; the stock extra supplies the US exchange calendar. Without that calendar, consideration and prospective comparisons fail closed.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-stock.txt
.\.venv\Scripts\python.exe pm.py research --json
.\.venv\Scripts\python.exe pm.py research --symbols MSFT,AAPL --horizon months-plus --json
.\.venv\Scripts\python.exe pm.py research --symbols MSFT --horizon months-plus --html --lang th
.\.venv\Scripts\python.exe pm.py research-score --json
```

`research --json` writes exactly one JSON document. Successful research and useful degraded/no-trade output return zero; invalid input, unsupported markets or total price failure return nonzero with structured errors. Use `--no-record` to skip a prospective record. Warnings stay in the report, rather than preceding JSON on stdout.

Symbols are optional. The starter universe is **AAPL, MSFT, GOOGL, META, PG, KO, CAT, HON, XOM, CVX, JNJ, MRK**: two established issuers from each of six broad sectors. It is a coverage choice with measured liquidity checks, not a historical winning-stock list or an inference about your portfolio. Supply up to 50 comma-separated tickers to change coverage. At most five cards are highlighted, with a maximum of two per sector; every evaluated symbol remains in JSON.

US-listed USD nonfinancial operating companies with supported annual US-GAAP evidence are the initial capability. ETFs, unsupported listings/accounting and ambiguous multiple share classes cannot qualify issuer consideration. **Thai individual-stock and social feeds are not connected.** `--market th` reports unavailable coverage rather than inferring Thai prices from the legacy THD ETF.

## Reading the labels

- `consider`: all evidence gates passed for the named horizon; a conditional case to study further.
- `watch`: an observed condition or missing evidence must change, with reasons such as `earnings_unknown`, `stale_price` or `valuation_above_screen`.
- `avoid`: verified thin liquidity or non-positive annual income and operating cash flow conflicts with this research policy. It does not predict a fall or issue a sell order.
- `excluded`: unsupported instrument/market or unavailable required price observations.

Cards include the quote date separately from retrieval time, annual period/filing dates, reported metrics, source URLs and lineage, observed risks, review conditions and invalidation. Ranking prioritizes gates, observed liquidity and stable ticker ordering; it does not rank expected returns.

The annual policy uses at least 252 observations, a twenty-session average dollar turnover of USD 5 million, positive compatible annual income/cash flow, nonnegative annual revenue change and positive annual diluted EPS. It displays **raw close / reported annual diluted EPS**, not TTM or forward P/E. The unvalidated P/E cap defaults to 25; change it explicitly with `--pe-cap 20`. Price stretch above two standard deviations of the sixty-session mean or 20% over 21 sessions watches. Annual evidence older than 450 days, future/same-day filings without acceptance timestamps, unknown earnings and unverified split/EPS comparability block consideration.

No issuer earnings calendar or catalyst feed is connected in this release. Consequently real-provider runs can return useful ranked watch research without any consider cases. Imported annual evidence must have compatible USD currency, full-year dates, issuer CIK and filing accession, linked to the official company-facts source. Imported earnings/catalyst evidence must have a verified state, matching symbol, source ID, valid HTTPS official/issuer source and retrieval times at or before cutoff. A truthy flag or date alone cannot qualify. Days/weeks additionally requires that supported dated catalyst within the next 21 calendar days, a conservative subset of the 21-session review window. Intraday requests receive daily context with unsupported entry gates.

Yahoo daily charts are one aggregated price source. SEC ticker/submission/company-fact responses provide annual facts where public access succeeds. HTTP errors retain the gap; nothing replaces missing metrics with zeros or guesses. SEC may reject requests depending on its fair-access controls. An optional `PRINTMONEY_SEC_USER_AGENT` may identify your application/contact according to SEC guidance; it is not a financial account credential. No paid feed or account is needed for the public adapters. The implementation does not bypass denied access.

Unusual adjusted price/volume moves are context for investigation. Splits suppress raw-volume comparisons; missing adjusted closes suppress price-anomaly claims and stretch eligibility. Neither an anomaly nor a repeated claim proves manipulation. Each source exposes single-source corroboration limits. `observed_at` is this retrieval's observation, not an earliest-sighting registry or original publication/author; Yahoo's upstream origin is unverified. External source text is escaped literal content, never instructions or executable HTML. History and rolling metrics use unique exchange-session rows; conflicting duplicates are explicit quality failures.

## Personal context and private files

`--market`, `--horizon`, `--risk`, `--budget`, `--currency` and `--loss-limit` accept your supplied context. Money must be positive and finite. Missing inputs keep the report general. Supplied inputs are presence checks; **allocation, position sizing, suitability and currency conversion are not implemented**. No budget, risk appetite, FX rate, stop level or fractional-share availability is invented. Even a qualifying research case remains conditional, with explicit abstention from automatic allocation.

Records and optional HTML are local, beneath Git-ignored `state/research/`. Default HTML is `state/research/latest.html`; explicit paths must stay beneath that root. Traversal and symlink/junction escapes are rejected. Public `data/`, `reports/`, the existing daily workflow and site publication are unaffected. Records omit budget amounts, loss-limit amounts, profile, holdings and account data. There is no saved user profile in this release.

Records use exclusive creation. Identical evidence/policy/context with changed retrieval or generation time alone retains the earliest record; changed price date, source content, gates, horizon or cap creates a new ID. Implementation and evaluation-definition versions are frozen in each record; unknown definitions remain unscorable rather than being silently reinterpreted. SHA256 checks detect accidental alteration before loading/scoring, not malicious forgery on a locally editable filesystem. Keep private records if you want prospective evaluation; they are not pushed or automatically committed.

## Prospective comparison

`research-score` evaluates those private records separately from the legacy volatility scorecard. A conditional consider case with recorded gates met uses the first exchange open strictly after the **original report cutoff**, then the close of session 21 (`days-weeks`) or 63 (`months-plus`), counting the entry session as one. It uses Yahoo's adjusted return proxy and normalizes the raw opening by the entry day's adjusted/raw close ratio. These are hypothetical comparisons, not fills or personal advice.

Each complete comparison includes gross return, 10bp and 30bp round-trip cost scenarios, zero-interest cash and SPY over the same dates with the same costs. Each entry/exit bar must explicitly retain verified adjusted-price availability; raw fallback prices cannot become scored adjusted returns. Net scenario returns exclude taxes, FX, venue-specific slippage and market impact. Negative results remain visible. Pending, missing-endpoint/benchmark, unknown-calendar and not-entered cases are retained with counts/reasons. Watch/avoid forward observations are separate from performance counts. The aggregate is a pooled diagnostic across policies/horizons; use individual rows for the specific context. Overlapping windows and common market shocks are not independent samples. The policy stays marked `unvalidated`; no accuracy or profitability claim is inferred from the older risk scorecard.

## Verification

Install `requirements.txt`, `requirements-stock.txt` and pytest before running the complete stock tests, including actual-calendar integration tests. The calendar-unavailable fallback has a separate explicit test. Run `python -B -m pytest -q -p no:cacheprovider` in that environment.

Focused tests cover policy gates, provider parsing/HTTP boundaries, actual exchange holidays/early closes, future filings, strict CLI JSON including parser errors, literal HTML, private paths, record idempotence/integrity and prospective entry/cost/benchmark arithmetic. A real Yahoo/SEC smoke checks actual adapter behavior separately from fixtures. See `docs/superpowers/progress/2026-10-03-stock-research.md` for recorded test and review evidence. Report revision links/comparison, grouped performance summaries, excursions/turnover analytics, editable starter-universe configuration and fuller Thai localization remain deferred; this release provides the static Thai introduction and literal evidence fields.
