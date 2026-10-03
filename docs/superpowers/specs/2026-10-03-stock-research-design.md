# Print-Money: stock research when Few asks Sol

Date: 2026-10-03

Status: architectural design for parent review on Few's behalf. Few's latest instruction delegates design, planning, implementation, and local testing to Sol, without repeated user approval interruptions. The parent requested a technical review of this full text before the implementation plan. No product code, GitHub workflow, publication setting, or financial transaction has been changed yet.

## 1. Agreed purpose

Few wants to return to Sol whenever funds become available and ask which stocks deserve consideration and which to avoid. The system should support repeated questions using current, checkable evidence. A useful answer can contain up to five candidates, a watchlist, observed reasons to avoid new exposure, or a justified decision to wait.

Success means a reader can identify the covered market, the age and limitations of the data, the evidence behind each label, the conditions under which a case becomes actionable, and what would invalidate it. It does not mean finding a trade every day or predicting guaranteed returns.

The approved direction includes evidence, data timestamps, valuation context, catalysts, conditional entry/review/exit conditions, risk gates, and an honest record of outcomes. Private portfolio concentration and cash-needs analysis can follow later. The actual investment horizon, market, budget, currency, and risk tolerance have not been supplied. They remain configurable inputs; Sol asks for the necessary profile when Few actually wants to allocate funds. Repeated scheduled messages and an application chat backend are outside this release.

## 2. Verified current state

Inspection baseline:

- Local repository: `D:/Print-Money`, remote `ayfew/Print-Money`, branch `main`.
- Preserved local HEAD and local `origin/main`: `ad423a130177aadaa74eec1e0061beb3e2478118`. The worktree was clean at the start of exploration.
- Remote main checked through the GitHub API: `a1680f12a1ea6b9f2c89b27f445dba3622ccc82b`, committed 2026-10-03 05:25:17 UTC.
- Remote is 40 commits ahead. The comparison changes only seven generated files: `data/claims.jsonl`, `data/impacts.json`, `data/macro.json`, `data/scorecard.json`, `data/snapshot.json`, `reports/index.html`, and `reports/printmoney.ics`. Application code and tests therefore match the current remote revision.
- No root or tracked `AGENTS.md` or repository `.agents/skills` was found. The available Superpowers `using-superpowers` and `brainstorming` skills were loaded.
- The normal GitHub CLI account is the work account `Napatsakorn-K_tcct`. The repository is public; that account has pull access and no push, maintain, or admin permission. Authentication was checked without exposing tokens or changing accounts.

The code contains two materially different systems. The original engine models BTC prediction markets and has paper/live execution components. The newer `printmoney/research/` system produces a market brief. The stock feature belongs to research and must not call execution or broker modules.

Existing research flow:

```text
CLI daily -> morning.run -> build_brief -> daily OHLC history
                        -> macro readings + event impact tables
                        -> decide -> risk/watch/ignore notes
                        -> risk claim log + latest snapshot
                        -> JSON / Thai or English HTML / iCalendar
```

`research/data.py:264` defines 24 instruments: 22 US-listed ETFs with US and global exposures, plus BTC-USD and ETH-USD. THD is a US-listed Thailand ETF; this is not coverage of individual SET stocks. There are no individual companies in the default universe.

Prices are fetched from Yahoo's daily chart endpoint, with a six-hour cache for the morning brief. Raw OHLC and adjusted closes are retained separately. The `Series` and displayed `MarketLine` do not retain sufficient provider, exchange-session, retrieval, and per-field freshness metadata for a stock decision. The displayed generation time is not the observation time of every price. Coverage of at least half the universe allows a market verdict; that is insufficient as a per-stock eligibility gate.

Macro sources include Treasury, New York Fed, Cboe, TreasuryDirect, and the Fed FOMC calendar. The payroll calendar uses a first-Friday rule, not a fetched BLS schedule. Macro caches can fall back to old readings, while the returned feed loses retrieval/fallback metadata. These sources provide context, not company valuation or a future earnings calendar. SEC exists in the citation registry but no company-filings data adapter exists.

`research/decide.py` already produces watch, avoid, ignore, changes, and an abstention-style focus. Its own documented purpose explicitly excludes directional buy/sell predictions. The avoid list concerns volatility, not an evaluated investment thesis. No company fundamentals, comparable valuations, issuer catalysts, personalized horizon, or stock entry/invalidation plan is present.

The latest generated report was stamped 2026-10-03 05:18 UTC and had all 24 markets loaded. It reported no actionable focus, no watch/avoid sections, a UUP risk change, macro context, and calm-market notes for IWM, VNQ, and ETH-USD. This is a current generated market report, not a current stock-buy list. Its payload cannot prove that every underlying reading is current.

Existing accuracy evidence measures volatility calls over the next 21 trading days. The remote scorecard, measured 2026-10-01 01:43:28 UTC, reports:

| Evidence | Resolved flags | Hits | Reported rate |
|---|---:|---:|---:|
| Historical replay | 5,219 | 4,221 | 80.88% |
| Recorded live flags | 84 | 38 | 45.24% |
| Live calm subset | 33 | 26 | 78.79% |
| Live elevated subset | 51 | 12 | 23.53% |

The live result has `beats_coin=false`. These figures do not measure stock selection, profitability, or a new policy's predictive ability. Overlapping windows and common market shocks also mean the rows should not be treated as independent evidence. The live brief uses a two-year history; historical scoring and live resolution use expanding history from a ten-year download. Window parity and weekend/holiday claim resolution need investigation before any accuracy claim is reused.

The public risk log contains 355 rows from 2026-08-26 through 2026-10-03. It records date, symbol, risk label, percentile, and record time. It does not preserve an entire stock thesis, input evidence, policy version, entry condition, or outcome after costs. The latest snapshot preserves only one previous generation.

`study.py` measures cost drag and compares mechanical rules with random selection. `indicators.py` applies multiple-testing and turnover checks. The committed indicator artifact covers 2016-08-26 through 2026-08-26, tests 146 rule variants, and has no survivors. These are useful research constraints, not proof that every possible stock investment approach cannot work.

The daily and monthly evidence workflows already generate and commit public artifacts. `state/` is ignored by Git. New on-demand records and personal inputs must use private local state, rather than joining the public workflow's `data/` and `reports/` paths.

The current `daily --json` command can print warnings to stdout before its JSON. Sol should consume a new strict JSON route or the Python result object, rather than assume the existing command always emits one parseable JSON document. The HTML payload also omits `Morning.warnings`, so unavailable optional sections need an explicit status in new research output.

## 3. Approaches and recommendation

1. **Add modular on-demand stock research to the existing research system -- recommended.** Reuse daily-bar parsing, source references, cost arithmetic, localization, and market context. Add issuer evidence, data-quality gates, transparent screening, conditional plans, and a separate prospective record. This directly supports future Sol questions without requiring a service or an AI API subscription. Company sources and comparative accounting still require careful validation.
2. **Add only clearer labels to the current ETF risk brief.** This is smaller and improves market attention guidance, but cannot answer which individual stocks merit consideration or justify valuation and company-specific invalidation. It would be an intermediate scope, not completion of Few's goal.
3. **Build a new news/LLM forecasting platform with many markets and a hosted chat service.** This adds data licensing, infrastructure, evaluation, and operating cost before there is prospective evidence for the policy. It is a possible later project; the current request does not require it.

The recommended release is architectural. It adds issuer-data and recommendation-record subsystems, plus a new result contract. An existing risk decision function does not make these additions a bounded label change.

## 4. Request contract and coverage

Expose a Python entry point and a CLI command. Names below are proposed interfaces, not commands available today:

```text
run_stock_research(request, providers, clock) -> ResearchReport
python pm.py research --symbols MSFT,AAPL --json
python pm.py research --market us --horizon months-plus --risk moderate --json
python pm.py research-score --json
```

`ResearchRequest` accepts optional market, instrument identifiers, horizon, risk tolerance, budget, budget currency, explicit loss limit, and a private profile reference. Budget values use decimal money with an explicit currency. Research policy is configured separately from the original execution/risk configuration.

Two modes are required:

- `general`: missing investment profile is allowed. Return evidence, watch/avoid conditions, missing-input reasons, and conditional research cases. Do not synthesize a budget or claim suitability for Few.
- `profile_review`: used when Few asks what to do with actual funds. Require market, horizon, currency, budget, and stated risk/loss tolerance before returning a personalized allocation scenario. This release can return affordability and risk constraints; it does not execute an allocation.

Horizon accepts `intraday`, `days-weeks`, and `months-plus`. The current verified source capability is daily bars, so intraday requests return `unsupported_horizon` for actionable entries. They may still receive daily research context. Days/weeks and months-plus are explicit scenarios with different review periods. An omitted horizon remains unknown; it is not silently converted to a preferred holding period.

The proposed first issuer-data capability is US-listed, SEC-reporting, non-financial operating companies with comparable supported US-GAAP facts. This choice follows the existing price coverage and the documented SEC API, not an assumption that Few invests in the US. The report must state this limitation prominently. SET stocks, banks, insurers, REITs, ADR accounting variants, and unsupported share/currency structures cannot receive a qualifying issuer label until their appropriate adapters and accounting policies exist.

Few delegated setup choices. With no symbols or watchlist, use a documented US-first starter universe: AAPL, MSFT, GOOGL, META, PG, KO, CAT, HON, XOM, CVX, JNJ, and MRK. This spans six broad sectors with two established US-listed operating companies per sector, selected for coverage/diversification rather than historical return ranking. Apply the measured USD 5 million liquidity gate at every query; membership alone does not prove current liquidity or eligibility. Show the configured constituents, configuration date, selection rules, and US-only coverage in the result. It is general research, not an assumption about Few's desired market or suitability. Symbols and watchlists override this default. The existing ETF universe remains macro context.

Sol should collect the missing actual investment profile at funds-use time, in one concise prompt when practical. No question about current holdings, brokerage access, or private portfolio is required for general research.

## 5. Data and evidence boundaries

Use four separable units:

1. **Collectors:** daily prices, SEC company facts/submissions, and verified issuer events. Reuse the daily-bar decoder; add observation metadata through a stock-specific wrapper or backward-compatible optional fields. Network calls and cache behavior stay out of classification.
2. **Evidence and quality:** normalize units and timestamps, validate issuer/security identity, calculate supported metrics, and return explicit quality and missing-data states.
3. **Policy and conditional plan:** a deterministic pure function receives a validated bundle and request and produces labels, reasons, conditions, and abstention. A prose model cannot promote a failed gate.
4. **Orchestration, output, and record:** collect, evaluate, serialize, and append a local record. The CLI is a thin adapter; Sol and an optional local HTML report use the same result object.

Every decision-relevant observation includes an evidence ID, source URL and source type, provider, instrument/issuer identity, value with units/currency, observation or fiscal-period date, publication/filing time when available, retrieval time, revision/accession identity, and content hash. Derived metrics cite their input evidence IDs and formula. Unavailable metadata is represented as unknown rather than invented.

SEC submissions and XBRL APIs are documented, publicly accessible without API keys, and supply filing history and financial-statement facts. They do not establish a future earnings calendar. Their runtime reachability has not yet been tested in this repository. Use caching, an appropriately configured identifying user agent, and the documented fair-access limits. See [SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and [SEC developer resources](https://www.sec.gov/about/developer-resources).

Keep the MVP accounting scope small: use compatible reported full-year facts from supported US-GAAP 10-K filings, with periods, units, currency, accession and filing dates retained. Compare revenue only across compatible full-year periods. Distinguish duration facts from instant balances and reject ambiguous units/share structures and future-filed facts. Do not build synthetic TTM values, automatic peer normalization or a universal accounting engine in this release. Any later TTM support must prove non-overlapping periods and cutoff discipline first.

Show reported annual net income, operating cash flow, revenue changes and diluted EPS where supported, with cash/debt as optional comparable context. Price divided by positive reported annual diluted EPS is explicitly named annual-earnings P/E, not TTM or forward P/E. Show missing or non-comparable values explicitly. Do not report a modeled fair price when the model and its assumptions do not exist.

Future earnings/catalysts need a dated official issuer announcement or a verified provider observation. A generic SEC filing history, first-Friday rule, guessed quarterly date, or missing event is not confirmation that no event is near. Initial event-adapter coverage can be limited to verified sources. A missing future event date blocks new-entry consideration and returns a watch condition. Sol can supplement a request with source-backed evidence; imported evidence has the same identity, date, and validation requirements and is never accepted as executable instructions.

## 6. Freshness and eligibility policy

These are initial screening defaults for review, not measured investment skill. Put them in a versioned research-policy configuration.

| Check | Initial rule | Result when unmet |
|---|---|---|
| Identity and market | Verified symbol, listing, currency and supported accounting | Excluded from issuer selection; explain coverage |
| Completed daily prices | Latest completed exchange session, allowing 60 minutes for post-close publication | Block new-entry consideration |
| Session calendar | Verified exchange/timezone calendar; weekends and holidays do not create a new observation | Unknown calendar blocks consideration |
| History | At least 252 valid completed sessions for risk context | Watch with insufficient-history reason |
| Liquidity | Twenty-session average raw-close dollar turnover at least USD 5 million for the initial US capability | Avoid new exposure under this liquidity policy |
| Report currency | Quote and fundamental inputs compatible; explicit verified FX observation needed for a cross-currency budget scenario | No converted sizing or affordability claim |
| Earnings | Confirmed next date; blackout from seven calendar days before through one full completed session after release | Watch until release and updated evidence; unknown date also watches |
| Filing age | Supported annual filing no older than 450 days; disclose newer interim evidence when available without synthesizing TTM | Watch until refreshed |
| Operating evidence | Positive compatible reported annual earnings and operating cash flow, and non-negative compatible annual revenue change | Missing/non-comparable facts or mixed results watch; both earnings and operating cash flow non-positive avoid under this policy |
| Valuation context | Positive compatible reported annual diluted EPS; annual-earnings P/E at or below a configurable research cap, initially 25 | Watch when unavailable, ambiguous or above the cap; this unvalidated attention rule is not a fair-value model |
| Price run-up | More than two standard deviations above the 60-session mean or more than 20% over 21 sessions | Watch for chasing risk; never predict a fall from this fact |

All price/return statistics are computed from completed observations. Use raw prices for displayed quote, turnover, and hypothetical entry arithmetic; total-return/appropriately adjusted data for return comparisons. Corporate actions must be handled explicitly. A refresh of an old cache does not make an old observation current.

The annual-earnings P/E cap is a disclosed configurable screening heuristic, not proof of cheapness, skill or future returns. Preserve the numerator, denominator and fiscal-period date. Negative denominators, incompatible accounting/share classes and unresolved exceptional earnings cannot qualify. Peer comparison and synthetic TTM are deferred. For a days/weeks case, additionally require either a verified dated catalyst within the stated review period or an explicit evidence-backed entry condition; otherwise return watch. Presence of a catalyst is not proof of positive returns.

Legacy volatility percentiles may be shown descriptively. The latest live performance does not qualify them as a validated stock-selection forecast. Macro relationships must be described as associations and context, not evidence that an individual stock must rise.

## 6a. Source provenance and manipulation-risk context

The MVP reports unusual adjusted price moves, abnormal volume and measured liquidity as contextual flags. They are not allegations of manipulation or proof that a claim is true. Corporate actions and unavailable adjusted prices limit anomaly inference. Corroboration distinguishes a single aggregated provider from a single official source; neither implies independent verification.

Preserve source IDs/URLs, content hashes, retrieval time, local first-seen scope and extraction/derivation lineage. A local first sighting does not establish the original publication, author or market origin. External text remains untrusted literal content. Social and Thai-stock feeds are explicitly not connected. Unknown origin/corroboration stays unknown rather than becoming a manipulation verdict.

## 7. Labels, plans, and no-trade output

Labels have the following meanings:

- `consider`: a case worth further purchase consideration under the named horizon/policy, with a stated thesis and valuation condition, and all required evidence/eligibility gates satisfied. This is not a forecast or an order. Unknown personal inputs keep its `actionability` conditional.
- `watch`: a concrete condition or missing evidence must change before consideration. Examples are approaching earnings, missing comparable facts, stretched price, or an unspecified horizon.
- `avoid`: verified evidence conflicts with the stated research/risk policy for new exposure. Include the specific observable reason and the condition that would permit reassessment. It does not mean the stock will fall or that an existing holding must be sold.
- `excluded`: unsupported instruments or unavailable required observations, visible separately from negative investment conclusions.

Show at most five highlighted research cards total, not a quota of purchases. Keep all evaluated instruments and category counts in JSON. Rank by verified evidence completeness, qualifying operating/valuation context, measured liquidity, and stable symbol tie-breaks; allow at most two highlighted names from one sector while other sectors have equally qualified candidates. Disclose this attention order, never call it a return forecast. Missing earnings may leave every eligible instrument on watch, but useful ranked facts, risks and missing conditions still appear.

A card contains the facts, the conditional thesis, why it is relevant to this request, valuation context, observed risks, source IDs, timestamps, uncertainty, entry/review conditions, horizon, and invalidation. Missing valuation or catalyst information is visible. A verified catalyst can strengthen the explanation; an invented catalyst cannot substitute for any gate.

Entry conditions refer to a qualifying evidence/policy state and optionally a disclosed price bound derived from a valid valuation scenario. Exit/review conditions include a new filing contradicting the thesis, a material issuer event, a changed risk/valuation gate, or reaching the selected horizon. Numerical price targets and stop levels are omitted unless their explicit basis exists. Any supplied loss/stop scenario states gap and execution limitations; no stop is described as a guaranteed loss cap.

An absent budget or currency prevents personal position sizing. An absent loss tolerance prevents a personal risk plan. Affordability can be computed only for a compatible known currency and stated whole/fractional-share assumption; fractions are not presumed available from a broker. No funds are deployed by the system.

The report separately returns `system_status` (`ok`, `degraded`, or `unavailable`) and an `abstention` object. Reasons distinguish no qualifying case, missing user context, stale data, unsupported coverage, upcoming events, and insufficient evidence. Data failure must never masquerade as a quiet market day. Every degraded section appears in JSON and HTML, including optional providers and retained stale context.

## 8. Output and repeated Sol use

`ResearchReport` is schema-versioned and includes report ID, UTC generation time, coverage, request mode, required inputs, per-provider/field quality, the evaluated universe, consider/watch/avoid/excluded cards, source observations, policy version, abstention, and machine-readable errors with remedy text.

`research --json` emits one JSON document to stdout. Warnings and progress go to stderr. Success is zero, including a valid no-trade decision; an unavailable requested scope or total required-data failure is nonzero with a structured JSON error. A partially useful report is `degraded`, with blocked actionability where required evidence is missing. The internal Python entry point returns the same contract and does not invoke CLI presentation.

Sol reads the report, states the supported market and data times, answers Few's actual question, and asks only for material missing inputs. Repeated queries refresh only data due for refresh and compare with the previous comparable report. A changed profile is shown as a changed assumption, rather than being mislabeled a changed market.

Optional Thai/English local HTML uses this same object. Its default destination is private local state. Do not default to the existing public `reports/index.html`, and do not embed personal inputs in any public output. A static report is sufficient; no new HTTP chat endpoint, connector, scheduled user notification, or site publication is part of this design.

## 9. Prospective record and evaluation

New records live under Git-ignored `state/research/`. Each completed report creates an application-immutable record with report and decision IDs, observation cutoff, evaluated universe, non-sensitive policy context, policy/code versions, evidence hashes, exact statuses and conditions, and documented evaluation definitions. Budget, cash needs, holdings, and account details are not written into recommendation evidence or export payloads. Private profile settings, if saved, live separately.

Use exclusive creation and atomic persistence. A repeated identical request against identical evidence and effective session cutoff is idempotent; a later cutoff or changed policy produces a linked new record. Identity uses instrument IDs, observation/publication versions and content hashes, policy version, non-sensitive horizon/risk scenario, and resulting gates. Generation/retrieval times alone do not inflate the count. A revision references the original rather than replacing it. Integrity checking can detect altered local records; it must not claim to make a locally editable filesystem tamper-proof. No new local records are automatically committed or pushed publicly.

Evaluation is a separate research function, never a simulated call into the live broker. Keep the legacy volatility scorecard separate from new stock results. Start a prospective stock record with this release; do not backfill LLM historical advice and call it live performance.

At creation, freeze the evaluation horizon and hypothetical entry semantics. For a consider case, hypothetical entry is the next completed session's open after the evidence cutoff and only when the declared entry conditions can be evaluated as met with data available then. An unevaluable or unmet condition is `not_entered`, not a losing/winning trade. Evaluate days/weeks at 21 exchange sessions and months-plus at 63 exchange sessions as comparison windows, clearly distinguishing these from Few's eventual chosen holding period. Additional custom dates can be supplied explicitly.

Measure market-data outcomes for watch/avoid cases separately as observations, not P&L of positions that were never recommended. For consider cases report gross total return, net scenario return, losses, adverse/favorable excursions where split-consistent OHLC exists, turnover, and the same-window benchmark. Use the relevant configured market benchmark (SPY only for the supported US capability); benchmark and cash comparisons must share the cutoff and eligible calendar. The default cash comparison is explicitly assumed zero nominal interest, unless a verified alternative is supplied; it is not a claim about Few's bank account return.

Without an actual venue cost schedule, display 10bp and 30bp round-trip cost scenarios inherited from the research cost study, explicitly labeled assumptions. Include verified extra FX/fees when supplied. Exclude taxes from net claims unless supplied; identify the omission. Do not claim the scenarios equal a real broker fill or Few's realized return.

Count cases by decision ID and actual exchange session, including weekends, holidays, delistings, missing outcomes, and not-entered cases. Display unresolved and unscorable cases with reasons; do not silently discard them. Report sample size and evaluation time, split by market, horizon and policy. Until there is adequate prospective evidence, show `unvalidated`; any statistical comparison must account for overlapping horizons and correlated instruments, rather than treating daily rows as independent coin flips.

## 10. Scope and validation for the later plan

Likely product boundaries are new research request/evidence types, issuer collectors, quality/policy functions, on-demand orchestration, a local recommendation store/evaluator, and thin CLI/render adapters. Reuse existing research helpers where their assumptions fit; avoid growing `cli.py` into another large business-logic module. The implementation plan will choose exact files after written-spec approval.

Required meaningful verification includes:

- Price-session freshness across holidays, weekends, a live partial session, source delay, and stale-cache fallback; correct raw/adjusted units around splits/dividends.
- Company facts with fiscal offsets, overlapping cumulative periods, amendments, missing tags, negative earnings, incompatible currencies/share classes, and future-filed evidence excluded from historical cutoffs.
- Earnings known/unknown states, blackout boundaries, insufficient peers, thin liquidity, chasing conditions, and unsupported markets/horizons; no failed evidence gate can yield actionable consideration.
- Missing profile produces useful general research without a synthetic budget; changes of personal assumptions are distinguished from changes in data.
- One parseable JSON stdout document on success/degradation/error, and the same statuses/evidence in Thai/English local HTML.
- Records are idempotent, preserve prior decisions, detect integrity problems, resolve only after their evaluation horizon, and retain losses, missing/delisted outcomes, and unentered cases.
- Budgets/holdings never reach tracked artifacts; every relevant result carries sources and timestamp limitations; the stock path has no broker/execution call.
- Baseline and focused regression checks for the reused market-data/localization/research helpers, with temporary storage so tests cannot overwrite real `data/` or `state/` records.

Tests were inspected during the initial read-only exploration. After Few delegated implementation, the existing pytest suite ran in the isolated worktree with cache writing disabled: 439 passed and one pre-existing test failed in 193.04 seconds. `tests/test_integration.py::TestPublishedEvidenceStaysConsistent::test_the_scorecard_headline_matches_the_committed_summary` assumes the headline has at least 1,000 observations; the current headline correctly selects the live summary with 84 observations. Record this baseline incompatibility for the plan; no production or test code has been changed yet. Existing CLI commands can write caches, logs, claims, snapshots, and reports, so they were not executed in the preserved repository. No dependencies were installed.

## 11. Review and handoff

This design is stored in the writable task workspace at `docs/superpowers/specs/2026-10-03-stock-research-design.md`. It has not been committed into `D:/Print-Money`, because the parent requested preserving that checkout's HEAD and dirty state. An isolated linked worktree has now been created at `C:/Users/napatsakorn.k/Documents/Codex/2026-10-03/task-3/print-money-stock-research`, branch `codex/stock-research`, starting at current remote commit `a1680f12a1ea6b9f2c89b27f445dba3622ccc82b`. Fetch updated `origin/main`; the original checkout's files and HEAD remain preserved at `ad423a1`. Product work and the design/plan documents will live in the isolated worktree.

Review attention: explicitly labeled US-first default research coverage; simple reported annual metrics and configurable unvalidated screening thresholds; ranked useful watch output despite unknown earnings; prospective 21/63-session comparison windows; and private on-demand output. None of these infers Few's actual investment profile. Parent's requested delayed-price, future-evidence, unsupported-symbol, malicious-source-content and private-output-path fixtures are mandatory.

After the parent's technical review, use Superpowers `writing-plans`, then execute inline with TDD and one fresh final code review. Few explicitly delegated these internal design/plan/execution choices, so additional user approval gates are waived. Document choices and verification. The previous Pages/publication repair remains a separate task with its existing permission blocker. User authorization includes local feature work and tests; it excludes publishing, deployment, paid subscriptions, new persistent credentials/scopes, brokerage access, trading, merging, or changes to accounts.

## Evidence links

- [Latest remote commit](https://github.com/ayfew/Print-Money/commit/a1680f12a1ea6b9f2c89b27f445dba3622ccc82b)
- [Local baseline versus remote comparison](https://github.com/ayfew/Print-Money/compare/ad423a130177aadaa74eec1e0061beb3e2478118...a1680f12a1ea6b9f2c89b27f445dba3622ccc82b)
- [Universe and daily data](https://github.com/ayfew/Print-Money/blob/a1680f12a1ea6b9f2c89b27f445dba3622ccc82b/printmoney/research/data.py#L264)
- [Existing decision semantics](https://github.com/ayfew/Print-Money/blob/a1680f12a1ea6b9f2c89b27f445dba3622ccc82b/printmoney/research/decide.py#L1)
- [Morning generation and persistence](https://github.com/ayfew/Print-Money/blob/a1680f12a1ea6b9f2c89b27f445dba3622ccc82b/printmoney/research/morning.py#L87)
- [Current generated snapshot](https://github.com/ayfew/Print-Money/blob/a1680f12a1ea6b9f2c89b27f445dba3622ccc82b/data/snapshot.json)
- [Current scorecard](https://github.com/ayfew/Print-Money/blob/a1680f12a1ea6b9f2c89b27f445dba3622ccc82b/data/scorecard.json)
- [Historical risk scoring and live resolution](https://github.com/ayfew/Print-Money/blob/a1680f12a1ea6b9f2c89b27f445dba3622ccc82b/printmoney/research/scorecard.py#L207)
- [CLI JSON path](https://github.com/ayfew/Print-Money/blob/a1680f12a1ea6b9f2c89b27f445dba3622ccc82b/printmoney/cli.py#L560)
- [Daily public-artifact workflow](https://github.com/ayfew/Print-Money/blob/a1680f12a1ea6b9f2c89b27f445dba3622ccc82b/.github/workflows/daily-brief.yml)
- [Git-ignored local state](https://github.com/ayfew/Print-Money/blob/a1680f12a1ea6b9f2c89b27f445dba3622ccc82b/.gitignore)
