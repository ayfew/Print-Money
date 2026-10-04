# On-demand stock research with Sol

Ask for research when you want to review available funds. This command produces conditional evidence, watch conditions, reasons to avoid new exposure and a valid no-trade outcome. It has no validated stock-return track record and does not send orders.

## Install and run

Use an isolated Python environment. Core requirements already include httpx; the stock extra supplies the US exchange calendar. Without that calendar, consideration and prospective comparisons fail closed.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-stock.txt
.\.venv\Scripts\python.exe pm.py research --json
.\.venv\Scripts\python.exe pm.py research --universe sp500-nasdaq100 --html --json
.\.venv\Scripts\python.exe pm.py research --offline --html --json
.\.venv\Scripts\python.exe pm.py research --symbols MSFT,AAPL --horizon months-plus --json
.\.venv\Scripts\python.exe pm.py research --symbols MSFT --horizon months-plus --html --lang th
.\.venv\Scripts\python.exe pm.py research-score --json
```

`research --json` writes exactly one JSON document. Successful research and useful degraded/no-trade output return zero; invalid input, unsupported markets or total price failure return nonzero with structured errors. Use `--no-record` to skip a prospective record. Warnings stay in the report, rather than preceding JSON on stdout.

Symbols are optional. The default is the **security union of S&P 500 and Nasdaq 100**, not Nasdaq Composite or every Nasdaq listing. `--universe sp500` or `--universe nasdaq100` narrows that selection. At most 1,000 resolved index securities are supported; explicit `--symbols` stays limited to 50. GOOG/GOOGL remain distinct, while BRK.B/BRK-B and BF.B/BF-B aliases deduplicate. The old twelve-stock list is available only through `--universe starter`.

Every evaluated security appears in JSON and the private HTML table, including failed requests and unsupported accounting. Search/filter by ticker/name, state, index, sector and evidence gap; sort dated metrics with unknown values last. Up to five highlights remain a convenience. Each row expands source evidence, reported periods, news context, risk, invalidation and unknowns. Field counts use the entire evaluated denominator for price, fundamentals, valuation, earnings, news and risk; an available field does not imply complete financial coverage or recommendation accuracy.

Membership is an attributed **uncertified community snapshot** from the two public Wikipedia constituent tables, with source URL, actual receipt, revision where genuinely available, content hash and CC BY-SA 4.0 attribution/adaptation notice. A narrowly reviewed public S&P change notice supplements that baseline with known effective and pending dates; it does not certify all intervening changes. Snapshot capture/version and completeness stay visible. No hardcoded count or issuer-level deduplication is used. Missing membership returns an explicit error, with no silent starter fallback; partial source coverage remains labeled. A current list cannot be reused as unbiased historical membership.

Collection uses up to four workers but one shared outbound request start per second, caches usable observations privately for six hours and preserves original clocks on resume. 401/403/429 stop subsequent calls to that host for the run; no immediate retries or redirect workarounds occur. `--offline` makes no source requests and emits cached eligible rows or gaps. `--refresh` skips observation cache; `--workers` accepts 1–4 and `--request-interval` has a minimum of one second. `--no-news` and `--no-financials` skip new context requests; cached context can still appear.

US-listed USD nonfinancial operating companies with supported annual US-GAAP evidence are the initial capability. ETFs, unsupported listings/accounting and ambiguous multiple share classes cannot qualify issuer consideration. **Thai individual-stock and social feeds are not connected.** `--market th` reports unavailable coverage rather than inferring Thai prices from the legacy THD ETF.

## Reading the labels

- `consider`: all evidence gates passed for the named horizon; a conditional case to study further.
- `watch`: an observed condition or missing evidence must change, with reasons such as `earnings_unknown`, `stale_price` or `valuation_above_screen`.
- `avoid`: verified thin liquidity or non-positive annual income and operating cash flow conflicts with this research policy. It does not predict a fall or issue a sell order.
- `excluded`: unsupported instrument/market or unavailable required price observations.

Cards include the quote date separately from retrieval time, annual period/filing dates, reported metrics, source URLs and lineage, observed risks, review conditions and invalidation. Ranking prioritizes gates, observed liquidity and stable ticker ordering; it does not rank expected returns.

The exact catalyst evidence used for a days/weeks case is retained in each card's `catalyst` field, private HTML and prospective record, including the date, symbol, verification state, source reference and retrieval time. Changing substantive catalyst evidence creates a distinct record; retrieving the same evidence again preserves the original record and cutoff.

The annual policy uses at least 252 observations, a twenty-session average dollar turnover of USD 5 million, positive compatible annual income/cash flow, nonnegative annual revenue change and positive annual diluted EPS. It displays **raw close / reported annual diluted EPS**, not TTM or forward P/E. The unvalidated P/E cap defaults to 25; change it explicitly with `--pe-cap 20`. Price stretch above two standard deviations of the sixty-session mean or 20% over 21 sessions watches. Annual evidence older than 450 days, future/same-day filings without acceptance timestamps, unknown earnings and unverified split/EPS comparability block consideration.

No issuer earnings calendar or catalyst feed is connected in this release. Consequently real-provider runs can return useful ranked watch research without any consider cases. Imported annual evidence must have compatible USD currency, full-year dates, issuer CIK and filing accession, linked to the official company-facts source. Imported earnings/catalyst evidence must have a verified state, matching symbol, source ID, valid HTTPS official/issuer source and retrieval times at or before cutoff. A truthy flag or date alone cannot qualify. Days/weeks additionally requires that supported dated catalyst within the next 21 calendar days, a conservative subset of the 21-session review window. Intraday requests receive daily context with unsupported entry gates.

The usable core is **free-only**, without signup, credentials or subscriptions. Yahoo daily charts provide one aggregated price source. Broad CLI collection disables SEC requests because this environment's prior requests were denied; it does not retry with a different identity. The lower-level StockProvider preserves its existing optional SEC capability for compatible environments, but the broad research/scoring commands do not invoke it. HTTP errors retain the gap; missing values are not replaced with zeros.

Two registered free issuer feeds provide headline context: [Apple Newsroom](https://www.apple.com/newsroom/rss-feed.rss) is Atom despite its filename, and [NVIDIA News](https://nvidianews.nvidia.com/releases.xml) is RSS. Other issuers make no invented feed requests. Publication and update clocks remain distinct: updated-only Apple entries are retained as partial context with original publication unknown. Headlines and short summaries do not qualify earnings/catalysts; full articles are not exported.

Four **fixed dated** official release adapters retain observable metrics for [Microsoft FY26 Q4](https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast), [NVIDIA Q2 FY27](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027), [Apple fiscal Q3 2026](https://www.apple.com/newsroom/2026/07/apple-reports-third-quarter-results/), and [Amazon Q2 2026](https://www.aboutamazon.com/news/company-news/amazon-earnings-q2-2026-report). These are not rolling release discovery or every-issuer financial coverage. Quarter/annual/YTD labels, dates, USD scale, GAAP/non-GAAP and `reported_unspecified` remain separate; an unstated accounting basis or fiscal start is not guessed. No quarterly EPS annualization, TTM synthesis or 10-K relabeling is performed. Apple linked PDFs and Amazon's full IR release remain unfetched; omitted metrics stay unknown.

Reported financial context and valuation eligibility have separate meanings. A release can expose several dated periods/metrics and count as available financial context while supplying no qualifying annual filing or P/E denominator. Raw extraction counts describe companies, periods and metric values; qualified annual facts and qualified annual valuation counts must be reported separately.

`--evidence state/research/evidence.json` loads an explicit schema1 `observations` list for compatible source-backed context. `--membership-file` accepts a provenance-bearing snapshot of the same validated format. Both paths must remain private. Imports reject nonfinite numbers, unsafe links, future/naive known clocks, duplicate/conflicting identities and price overrides. Manual evidence still needs independent factual review; structured validation is not source-authenticity proof. Neither imports nor accessible documentation imply paid data entitlements.

CIKs are compared as validated canonical identities, including zero-padded digit strings; the original membership value remains inspectable. A conflicting member/provider/imported identity cannot qualify annual evidence. Actual financial period ends cannot postdate their supporting source receipt or known publication day. Annual filing dates must precede the source's receipt day because a date alone supplies no intraday acceptance timestamp. Day-only publication remains a calendar day, with no invented midnight; unknown original news publication remains unknown even when an update time exists.

Unusual adjusted price/volume moves are context for investigation. Splits suppress raw-volume comparisons; missing adjusted closes suppress price-anomaly claims and stretch eligibility. Neither an anomaly nor a repeated claim proves manipulation. Each source exposes single-source corroboration limits. `observed_at` is this retrieval's observation, not an earliest-sighting registry or original publication/author; Yahoo's upstream origin is unverified. External source text is escaped literal content, never instructions or executable HTML. History and rolling metrics use unique exchange-session rows; conflicting duplicates are explicit quality failures.

## Personal context and private files

`--market`, `--horizon`, `--risk`, `--budget`, `--currency` and `--loss-limit` accept your supplied context. Money must be positive and finite. Missing inputs keep the report general. Supplied inputs are presence checks; **allocation, position sizing, suitability and currency conversion are not implemented**. No budget, risk appetite, FX rate, stop level or fractional-share availability is invented. Even a qualifying research case remains conditional, with explicit abstention from automatic allocation.

Records and optional HTML are local, beneath Git-ignored `state/research/`. Default HTML is `state/research/latest.html`; explicit paths must stay beneath that root. Traversal and symlink/junction escapes are rejected. Public `data/`, `reports/`, the existing daily workflow and site publication are unaffected. Records omit budget amounts, loss-limit amounts, profile, holdings and account data. There is no saved user profile in this release.

Records use exclusive creation. Identical evidence/policy/context with changed retrieval or generation time alone retains the earliest record; changed price date, source content, gates, horizon or cap creates a new ID. Implementation and evaluation-definition versions are frozen in each record; unknown definitions remain unscorable rather than being silently reinterpreted. SHA256 checks detect accidental alteration before loading/scoring, not malicious forgery on a locally editable filesystem. Keep private records if you want prospective evaluation; they are not pushed or automatically committed.

Membership versions identify semantic content, retaining publication/effective dates, revisions, hashes and member/pending-change content while excluding receipt/capture clocks. Cache validation still enforces the original clocks. A trusted older clock-inclusive snapshot needs an explicit validated version-only migration; original artifacts and source clocks remain unchanged. Cached prices are rechecked against the expected completed session at report time, including market-close boundaries.

## Prospective comparison

`research-score` evaluates those private records separately from the legacy volatility scorecard. A conditional consider case with recorded gates met uses the first exchange open strictly after the **original report cutoff**, then the close of session 21 (`days-weeks`) or 63 (`months-plus`), counting the entry session as one. It uses Yahoo's adjusted return proxy and normalizes the raw opening by the entry day's adjusted/raw close ratio. These are hypothetical comparisons, not fills or personal advice.

Each complete comparison includes gross return, 10bp and 30bp round-trip cost scenarios, zero-interest cash and SPY over the same dates with the same costs. Each entry/exit bar must explicitly retain verified adjusted-price availability; raw fallback prices cannot become scored adjusted returns. Net scenario returns exclude taxes, FX, venue-specific slippage and market impact. Negative results remain visible. Pending, missing-endpoint/benchmark, unknown-calendar and not-entered cases are retained with counts/reasons. Watch/avoid forward observations are separate from performance counts. The aggregate is a pooled diagnostic across policies/horizons; use individual rows for the specific context. Overlapping windows and common market shocks are not independent samples. The policy stays marked `unvalidated`; no accuracy or profitability claim is inferred from the older risk scorecard.

## Verification

Install `requirements.txt`, `requirements-stock.txt` and pytest before running the complete stock tests, including actual-calendar integration tests. The calendar-unavailable fallback has a separate explicit test. Run `python -B -m pytest -q -p no:cacheprovider` in that environment.

Focused tests cover policy gates, membership/share classes/clocks, provider parsing/HTTP boundaries, actual exchange holidays/early closes, cache/resume/denial behavior, future evidence, strict CLI JSON including parser errors, literal HTML/XML, private paths, record idempotence/integrity and prospective entry/cost/benchmark arithmetic. Real free-source ingestion is checked separately from generated full-universe tests; no new SEC calls are made. See `docs/superpowers/progress/2026-10-04-index-universe.md` for counts, exact commands and independent review evidence. Actual browser rendering remains limited by the local Chrome GPU/CDP failure; executed Node tests verify the filter/sort rules without claiming a browser screenshot pass. Complete issuer fundamentals/news/earnings, rolling financial-release discovery, Thai equities, allocation and stock-return validation remain gaps.
