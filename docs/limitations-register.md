# Limitations Register

Status: living document. Every known structural limitation of this codebase's
models and data, with severity, affected model(s), mitigation status, and what a
reader should discount or discard when interpreting output because of it. IDs are
stable — do not renumber; mark retired entries as "Retired," don't reuse the
number. Companion to [`docs/assumptions-register.md`](assumptions-register.md) (a
limitation is a constraint the model cannot currently overcome; an assumption is a
stated choice within that constraint).

**Severity scale**: **High** — materially affects whether any output can be trusted
for its stated purpose. **Medium** — affects precision/completeness but the
underlying mechanism is directionally sound. **Low** — a known gap unlikely to
change conclusions materially.

## L-001 — Restatement / look-ahead risk in current fundamentals

- **Severity**: High (for backtesting/research); Low (for live valuation)
- **Affected model(s)**: DCF, backtester, all downstream screens (Altman Z,
  Piotroski, Conviction Score)
- **Description**: yfinance's financial statements reflect the *current, possibly
  restated* view of historical fiscal periods, not what was originally reported at
  the time. A company that later restated a prior period's earnings shows the
  restated figures in today's yfinance data, even for a "historical" backtest
  target date before the restatement happened. This is look-ahead bias: the
  backtester can use information that would not genuinely have been available on
  `as_of_date`.
- **Mitigation**: `STATEMENT_FILING_LAG_DAYS` (90-day conservative buffer) reduces
  — but does not eliminate — the risk of using data before it would plausibly have
  been *filed*; it does nothing about data that was filed on time but later
  *restated*.
- **Status**: Unmitigated for restatement specifically. Full mitigation requires
  Track B item 1 (point-in-time SEC/XBRL fundamentals, which preserve the
  as-originally-filed figures).
- **Consequence for interpretation**: Any backtest result should be treated as
  potentially optimistic to an unknown degree due to this effect — a real,
  point-in-time investor would not have had access to the restated figures this
  backtester silently uses.

## L-002 — Survivorship bias

- **Severity**: High (for backtesting); N/A (for live trading, which only needs
  today's universe)
- **Affected model(s)**: Backtester, sector-median cache generation
- **Description**: `DEFAULT_SP500_TOP_100_TICKERS` reflects *today's* top-100
  market-cap ranking, applied uniformly to every `as_of_date`. Companies that
  would have genuinely qualified on a historical date but have since been
  delisted, acquired, gone bankrupt, or shrunk out of the ranking are structurally
  excluded from every backtest's universe.
- **Mitigation**: `run_backtest`'s `tickers` parameter is fully injectable — a
  caller can supply a historically-accurate universe. `BacktestResult.universe_note`
  always records this limitation regardless of which universe was used, so it
  cannot be silently forgotten.
- **Status**: Unmitigated by default. Full mitigation is Track B item 2
  (survivorship-bias-free historical universe) — not yet built.
- **Consequence for interpretation**: A default-universe backtest's return is
  systematically biased upward relative to what a real historical investor,
  choosing from the real historical universe, could have achieved — bankruptcies
  and delistings (which would have hurt real historical performance) are invisible
  to it.

## L-003 — Corporate-action risk beyond stock splits

- **Severity**: Medium
- **Affected model(s)**: Backtester (historical share count/price reconstruction)
- **Description**: `_cumulative_split_factor_since` handles stock splits. Spin-offs,
  mergers, and ticker symbol changes are **not** specifically detected or
  corrected for anywhere in the codebase.
- **Mitigation**: None currently implemented.
- **Status**: Unmitigated. Track B item 3.
- **Consequence for interpretation**: A ticker that underwent a spin-off or symbol
  change within a backtest's window may show a corrupted or misleading price/share
  series without any explicit warning.

## L-004 — Single-factor CAPM

- **Severity**: Medium
- **Affected model(s)**: WACC, inverse-beta position sizing
- **Description**: Cost of equity uses only market beta (a single-factor CAPM) —
  no size, value, momentum, quality, or other risk-factor exposure (e.g.
  Fama-French three/five-factor) is modeled anywhere in this codebase.
- **Mitigation**: None.
- **Status**: Unmitigated, documented as an accepted simplification.
- **Consequence for interpretation**: WACC (and therefore intrinsic value) may
  understate or overstate a company's true cost of capital to the extent its risk
  is driven by factors other than market beta — a known, long-studied limitation
  of single-factor CAPM in general, not specific to this implementation.

## L-005 — Terminal-value sensitivity

- **Severity**: High
- **Affected model(s)**: DCF
- **Description**: The Gordon Growth terminal value formula
  (`TV = FCF_n * (1+g) / (WACC - g)`) is highly sensitive to the `WACC − g`
  denominator — a small change in either input can produce a large change in
  terminal value, which typically dominates total intrinsic value in a 5-year
  explicit-projection DCF.
- **Mitigation**: `terminal_growth_rate` is bounded to `[0%, 5%]` and WACC is
  clamped to `[5%, 20%]`, preventing the denominator from approaching zero or going
  negative — but this bounds the *failure mode*, not the underlying sensitivity.
- **Status**: Partially mitigated (numerical safety only). Full mitigation requires
  the Track A sensitivity-analysis deliverable (WACC/terminal-growth/margin
  independently varied and reported) — not yet built.
- **Consequence for interpretation**: A single point-estimate intrinsic value
  should not be read as a precise number; without an accompanying sensitivity
  table, its implied precision is misleading.

## L-006 — Normal-distribution Monte Carlo VaR assumption

- **Severity**: Medium-High
- **Affected model(s)**: Portfolio VaR/CVaR, SPY hedge sizing (which is driven by
  VaR's dollar output)
- **Description**: `calculate_portfolio_var` simulates returns from a
  (multivariate) normal distribution fit to historical mean/covariance. Real
  equity returns are well documented to exhibit fatter tails, skew, and volatility
  clustering that a normal distribution does not capture.
- **Mitigation**: None currently — no alternative risk-model family
  (historical-simulation, bootstrap, or a fat-tailed parametric distribution) is
  implemented.
- **Status**: Unmitigated. Track B item 10 (and cross-listed as a Track A
  sensitivity item) calls for comparing this VaR against historical-simulation and
  bootstrap alternatives — not yet built.
- **Consequence for interpretation**: This VaR/CVaR likely **understates** true
  tail risk, particularly during stressed/high-volatility regimes — the exact
  conditions where an accurate risk estimate matters most.

## L-007 — Sector-classification and current-day proxy limitations

- **Severity**: Medium
- **Affected model(s)**: Sector-relative filter, Altman Z sector exclusions, sector
  position-sizing caps
- **Description**: GICS sector is fetched live from yfinance and used identically
  for every historical `as_of_date` in a backtest — yfinance has no historical
  sector-classification endpoint. A company that changed sector classification (or
  business mix) over time is misrepresented in any backtest predating that change.
  Sector is also a single categorical label with no sub-industry granularity, and
  companies genuinely spanning multiple sectors are forced into one bucket.
- **Mitigation**: Recorded in each backtest result's `approximations` list
  (`"sector"`), so a consumer can identify affected tickers.
- **Status**: Unmitigated beyond disclosure.
- **Consequence for interpretation**: A ticker whose sector reclassified over time
  may be compared against the wrong peer group for any pre-reclassification
  backtest date, and its Altman Z sector-exclusion eligibility may be misapplied.

## L-008 — Sector-median and price-history cache limitations

- **Severity**: Low-Medium
- **Affected model(s)**: Live sector-relative comparison (`src.api.sector_medians`),
  every module using `src.utils.cache`'s Redis caching layer
- **Description**: The live API's sector-median comparison depends on a
  periodically (manually) regenerated cache with explicit staleness (48h),
  minimum-sample-size (3), and minimum-overall-coverage (50%) refusal rules — a
  comparison can go silently unavailable (with an explicit reason, not a wrong
  number) if the cache hasn't been regenerated recently enough. Separately, most
  yfinance fetches across the codebase are cached with TTLs ranging from 1 hour
  (RSI, risk-free rate) to 24 hours (statements, price history) — a genuine
  same-day market move can be invisible to a cached read within that window.
- **Mitigation**: The sector-median cache (`get_sector_median_price_to_intrinsic`,
  [`src/api/sector_medians.py`](../src/api/sector_medians.py)) fails closed —
  refuses rather than serves a stale/thin/discount-rate-incompatible/malformed
  comparison:
  - A check against the cache's own generation-time risk-free rate (`A-027`,
    closed after being found missing entirely).
  - The cache file is read/written defensively: corrupt JSON or an unreadable file
    degrades to "unavailable" rather than a 500; a timezone-naive generation
    timestamp is refused rather than crashing on an aware-vs-naive datetime
    subtraction; writes are atomic via a temp-file-plus-`os.replace` so a
    concurrent reader can never observe a partially-written file.
  - **(Track A Phase 1.5B)** Valid JSON whose TOP-LEVEL value isn't a JSON object
    (`[]`, `null`, a bare string/number/bool) is refused with an explicit
    "malformed" reason — distinct from "not been generated yet" — instead of
    reaching a `.get(...)` call and leaking a raw `AttributeError`. Every NESTED
    container the lookup touches is also validated before use: `sector_medians`/
    `sector_sample_counts` must be dicts, `assumptions` must be a dict when
    present, `universe_size`/`tickers_used`/the returned median value/a sector's
    sample count must each be a genuine finite non-boolean number (a string,
    bool, list, or NaN/infinity in any of these positions is refused, not
    silently coerced or left to raise deep in a comparison or division).
  - Cache TTLs are individually tuned per data type's actual volatility (annual
    statements: 24h; intraday momentum signal: 1h).
- **Status**: Mitigated for every currently-identified failure mode (never serves a
  misleadingly-labeled stale, thin-sample, discount-rate-incompatible, or
  malformed comparison; never crashes on a corrupt file, a naive timestamp, a
  wrong-shaped top-level payload, or a wrong-shaped nested container; never risks a
  torn write). Not mitigated for the underlying staleness itself, which requires
  periodic manual cache regeneration (`python -m src.api.sector_medians`) that is
  not currently automated/scheduled.
- **Consequence for interpretation**: A "sector median unavailable" response on the
  live dashboard may simply mean the cache needs regenerating, not that the
  comparison is fundamentally impossible.

## L-009 — No liquidity, slippage, or transaction-cost model

- **Severity**: High (for any profitability claim); Low (for the current, no-claim
  state of the project)
- **Affected model(s)**: Backtester equity curve, live rebalancing
- **Description**: No component of this codebase models bid-ask spread, price
  impact/slippage, or per-trade transaction cost. The backtester's equity curve
  assumes frictionless fills at recorded close prices; the live paper-trading
  engine's rebalancing similarly does not adjust sizing or drift thresholds for
  expected trading costs.
- **Mitigation**: None currently.
- **Status**: Unmitigated. Track B item 6, explicitly required before any
  profitability claim can be made — see
  [`docs/research-overview.md`](research-overview.md)'s definition of a successful
  result.
- **Consequence for interpretation**: Any return figure produced today (backtest or
  paper-trading) is a **gross**, not net, figure. It cannot be compared to a real
  achievable return without a cost model.

## L-010 — Benchmark choice (SPY only)

- **Severity**: Medium
- **Affected model(s)**: Backtester, live risk reporting
- **Description**: SPY is the only benchmark implemented. No comparison exists
  against simpler baselines (equal-weight, a single-factor version of the same
  signal) that Track B item 7 explicitly requires before a performance claim is
  meaningful — a complex strategy that doesn't beat a simple one net of costs has
  not demonstrated an edge worth the complexity.
- **Mitigation**: None currently.
- **Status**: Unmitigated. Track B item 7.
- **Consequence for interpretation**: A positive SPY-relative return alone is
  insufficient evidence of a real edge from this strategy's specific complexity
  (sector-relative filtering, screens, conviction scoring, risk-adjusted sizing)
  versus a much simpler approach.

## L-011 — Parameter-selection and multiple-testing risk

- **Severity**: Medium-High
- **Affected model(s)**: All screens and thresholds (Altman Z threshold, Piotroski
  minimum, RSI threshold, trend tolerance, drift threshold, position/sector caps,
  FCF-yield blend weights, VaR simulation parameters, hedge stress scenario)
- **Description**: Most numeric thresholds in this codebase (see the assumptions
  register) are fixed, stated engineering choices — not derived through a
  disciplined, pre-registered parameter search with an out-of-sample check. As a
  concrete example of the risk this creates: `src/dcf_model/dcf.py`'s
  `DEFAULT_RISK_FREE_RATE = 4%` and `src/utils/macro.py`'s
  `DEFAULT_RISK_FREE_RATE_FALLBACK = 4.2%` are two independently-configured
  constants intended to serve the same conceptual role but never reconciled to a
  single source of truth — a small, benign illustration of how ad hoc constants
  can drift out of sync across a codebase without a systematic process catching it.
  At larger scale, if any of these thresholds were ever tuned by observing backtest
  results and adjusting until performance looked better, that would constitute
  exactly the "seeking a return before the infrastructure that could catch a wrong
  or overfit answer exists" failure mode
  [`docs/model-development-roadmap.md`](model-development-roadmap.md) is
  structured to prevent.
- **Mitigation**: Track B item 8 (an experiment registry recording every attempted
  configuration, including ones that didn't work) is designed to make this
  auditable once Track B begins. Not yet built.
- **Status**: Unmitigated. As of this writing, every threshold in this codebase
  should be read as "a stated, reasoned engineering choice," never as "a value
  empirically optimized for this strategy's performance."
- **Consequence for interpretation**: Any backtest run using the current default
  thresholds should not be treated as evidence that those specific threshold
  values are individually optimal — only that they are the values currently in use.

## L-012 — Limited independent validation

- **Severity**: High
- **Affected model(s)**: DCF (primarily); by extension, everything built on top of
  it
- **Description**: The test suite verifies internal consistency, boundary
  conditions, and override precedence — it cannot catch a systematic bug that
  both the implementation and its own tests share (e.g. a formula both were
  written to agree with, even if that formula itself has an error). A
  genuinely independent second implementation is required to catch that class
  of error.
- **Mitigation**: [`docs/independent-validation-plan.md`](independent-validation-plan.md)
  specifies exactly this validation. Track A Phase 2A built the first
  independent workbook (V1); Phase 2B ran the reconciliation and found a
  genuine, tolerance-exceeding discrepancy for INTC, traced to an undocumented
  `years_elapsed` convention (see `L-019`, resolved), not a codebase defect —
  `src/dcf_model/dcf.py` required no change. Phase 2C clarified the
  specification (`A-028`), built a second independent workbook (V2) from it,
  and reran the full reconciliation against V2: all four companies (MSFT,
  CAT, INTC, VZ) now pass base-case reconciliation and all 908 sensitivity
  scalar comparisons — see
  `validation/dcf_reconciliation/reconciliation_report.md`.
- **Status**: Reconciliation reached a **GO** verdict as of Track A Phase 2C.
  This is one of the named Track A stop/go gate items, and a GO verdict is a
  precondition for Track B to proceed on this model — but the
  `docs/independent-validation-plan.md` sign-off checklist's **second-reviewer
  requirement is still PENDING** (not performed in any session to date), so
  this limitation is not yet fully closed. Track B should not treat this
  entry as closed until that second review is recorded.
- **Consequence for interpretation**: DCF output from this codebase has now
  been reconciled against an independently-built second implementation for
  four companies spanning different profiles (large-cap capital-light,
  capital-intensive, negative-growth, and leveraged), with a GO result — but
  this remains a single-reviewer (the building session's own) result until
  second-reviewer sign-off is recorded.

## L-013 — Test coverage gaps

- **Severity**: Medium
- **Affected model(s)**: WACC/CAPM (`calculate_wacc`), Altman Z-Score, Piotroski
  F-Score, RSI (Wilder's Smoothing)
- **Description**: `calculate_wacc` has a dedicated test class as of Track A Phase
  1.5B, extended in Phase 1.5C and 1.5D (`TestCalculateWaccBoundaryHardening`,
  [`tests/dcf/test_dcf.py`](../tests/dcf/test_dcf.py)) — but that class covers only
  numeric/sign-boundary hardening: bool/non-finite/non-numeric rejection, the
  missing-versus-malformed distinction (a genuinely missing `beta`/`cost_of_debt`/
  `tax_rate`/`total_debt` still falls back to its documented default; the SAME
  field present but malformed raises `ValueError` instead), the sign/range
  invariants added in Phase 1.5D (`current_price`/`shares_outstanding` must be
  strictly positive; a present `total_debt`/`cost_of_debt` must be non-negative;
  a present `tax_rate` outside `[0, 1)` now raises rather than falling back to
  the default), and arithmetic-overflow guards — not the CAPM formula's
  correctness or the `[5%, 20%]` clamp's own behavior in isolation — those
  remain exercised only indirectly through full `run_dcf_valuation` calls.
  `src/valuation/altman_z.py`,
  `src/valuation/piotroski.py`, and `src/valuation/technical.py` (RSI) still have
  **no dedicated test file at all** — grepping the test suite for
  `calculate_altman_z`, `calculate_f_score`, and `calculate_rsi` finds no
  references, and `tests/trading/test_main_integration.py` monkeypatches
  `run_todays_scan` wholesale rather than exercising the real entry-gate
  calculation path.
- **Mitigation**: Partial — `calculate_wacc`'s boundary/type-safety behavior is now
  covered (see above). The CAPM formula itself and the Altman Z/Piotroski/RSI gap
  remain unmitigated.
- **Status**: Partially mitigated, tracked here explicitly per this document's
  quality rule against implying coverage that doesn't exist.
- **Consequence for interpretation**: The Altman Z-Score, Piotroski F-Score, and
  RSI formulas as actually implemented in this codebase are exercised only by
  manual `__main__` smoke tests in their own source files — a regression in any of
  the three would not be caught by the automated test suite. Closing this gap
  should precede any Track B work that depends on these screens' correctness.

## L-014 — Paper trading only; results do not reflect real market impact

- **Severity**: Low-Medium (for interpretation, given the project's explicit
  non-claim); would be High if ever misread as a live track record
- **Affected model(s)**: Live execution engine (`src.trading.alpaca_execution`)
- **Description**: This project trades exclusively against Alpaca's paper (simulated
  money) environment, enforced fail-closed with no bypass of any kind (see
  [`docs/security-threat-model.md`](security-threat-model.md)). Paper-trading fills
  do not necessarily reflect real market impact, real liquidity constraints at the
  moment of a real order, or real broker-side execution quality.
- **Mitigation**: This is a deliberate, permanent design choice, not a gap to be
  closed — see the project [README](../README.md)'s Disclaimer.
- **Status**: By design; not a target for "mitigation" in the sense of the other
  entries here.
- **Consequence for interpretation**: Any trade log or realized paper P&L from this
  project is not a track record and must never be presented as one. It is useful
  only for verifying the execution engine's own logic (order sizing, gating,
  idempotency), not for estimating real-world tradeable returns.

## L-015 — Data-provider revisions, quirks, and outages

- **Severity**: Medium
- **Affected model(s)**: Every model consuming yfinance data
- **Description**: yfinance is an unofficial, community-maintained wrapper around
  Yahoo Finance's own (undocumented, subject-to-change) endpoints. Statement row
  labels have changed across yfinance versions (handled defensively via multiple
  candidate labels — see [`docs/data-dictionary.md`](data-dictionary.md)), Yahoo
  can rate-limit or reshape endpoints without notice (explicitly called out in
  `src/valuation/macro_sentiment.py`'s docstring), and a `NaN` Close on an
  unsettled session is a documented, recurring quirk handled defensively throughout
  the codebase.
- **Mitigation**: Every fetch function degrades to `None`/an empty result rather
  than raising, and multiple candidate row labels are tried per line item. Redis
  caching (`src.utils.cache`) reduces — but does not eliminate — exposure to a
  transient outage during a single run.
- **Status**: Mitigated defensively at the code level; not eliminated as a source
  of missing/incorrect data, since this project has no alternative or fallback data
  provider.
- **Consequence for interpretation**: A `None`/missing value or a skipped ticker
  anywhere in this system's output may reflect a genuine data gap for that company,
  or a transient provider issue — the two are not currently distinguishable from
  the output alone.

## L-016 — SPY hedge budget is a theoretical premium ceiling, not an enforceable actual-spend ceiling

- **Severity**: High
- **Affected model(s)**: SPY put hedge sizing (`src.risk.hedging.calculate_spy_hedge`),
  live hedge execution (`src.trading.alpaca_execution.execute_spy_var_hedge`)
- **Description**: `hedge_budget_dollars` caps the sized number of contracts
  against this module's own BSM-modeled `current_put_price` — a theoretical price
  computed from `HEDGE_IMPLIED_VOL` (a fixed, assumed volatility, not the
  contract's real live IV) and the selected contract's strike/expiry, with no live
  market quote involved. The order this sizing feeds is a real Alpaca MARKET
  order, which fills at whatever the option's real bid/ask happens to be at
  submission time. A real fill can cost more (or less) per contract than the
  modeled price the budget check used, so actual premium spent is not guaranteed
  to stay within `HEDGE_BUDGET_FRACTION_OF_EQUITY` of equity — only the *modeled
  estimate* is bounded by it.
- **Mitigation**: Documented explicitly, in this codebase's own docstrings
  (`src/risk/hedging.py` module docstring and `calculate_spy_hedge`'s
  `hedge_budget_dollars` parameter doc; `execute_spy_var_hedge`'s docstring) and
  here, rather than left implicit behind a parameter name that reads as a hard
  cap. No code-level enforcement of actual spend exists. **Distinct from, and not
  fixed by,** Track A Phase 1.5B's separate numeric-robustness hardening of
  `calculate_spy_hedge` (extreme-but-finite `implied_vol`/`risk_free_rate` no
  longer raise `OverflowError`) — that pass made the function's SIZING
  computation itself robust to adversarial inputs; it does nothing to make the
  resulting `hedge_budget_dollars` figure an enforceable ceiling on what the
  live MARKET order actually pays, which is what this entry tracks.
- **Status**: Unmitigated at the enforcement level; a genuine fix requires
  fetching a live option quote (bid/ask) and submitting a LIMIT order bounded by
  it, replacing the current MARKET order. Not implemented in this pass: no
  option-quote data client exists anywhere in this codebase today (only
  `alpaca.trading.client.TradingClient`, which does not expose live option
  quotes), and adding one is a genuine scope expansion — a new data-client
  integration and its own test-fixture surface — not a bounded correctness fix.
  Proposed as the next bounded task.
- **Consequence for interpretation**: A logged hedge "budget" figure describes a
  modeled ceiling, not a guarantee. Actual premium spend on a given hedge run
  should be independently checked against `trade_logs` after the fact, not assumed
  to equal the pre-trade budget calculation.

## L-017 — Backtest equity-curve entry date is not guaranteed to align with the nominal `as_of_date`

- **Severity**: High
- **Affected model(s)**: Backtester equity curve (`build_equity_curve`,
  `_monthly_close_series`, [`historical_tester.py`](../src/backtesting/historical_tester.py))
- **Description**: `build_equity_curve`'s `portfolio_return`/`benchmark_return`/
  `alpha` are computed from the FIRST COMMON MONTHLY date every Top-N pick and the
  benchmark all share a close for — not from `as_of_date` itself.
  `_monthly_close_series` requests `interval="1mo"` price history starting at
  `as_of_ts`; yfinance's monthly bars are aligned to calendar-month boundaries
  (not to the literal request start date), so if `as_of_date` falls mid-month, the
  earliest monthly bar actually returned — and therefore the entry point every
  return figure is measured from — can land days to several weeks after the
  nominal `as_of_date`, silently. This is distinct from, and in addition to, the
  documented normalization behavior (every series is aligned to the SAME first
  common date so the strategy and benchmark curves start at the same value) —
  that normalization is internally consistent, but the date it normalizes to is
  not guaranteed to be `as_of_date` itself.
- **Mitigation**: None currently. The gap is structural to using monthly-interval
  history requested from an arbitrary start date, not a bug in the normalization
  logic itself.
- **Status**: Unmitigated. A fix would need to either fetch daily history and
  resample to the nearest trading day on/after `as_of_date`, or explicitly record
  and surface the actual first-common-date used as a distinct field from
  `as_of_date`, so a consumer can see when the two diverge.
- **Consequence for interpretation**: A reported backtest return should be read as
  measured from "the first monthly date the whole portfolio has data for on or
  after `as_of_date`," not from `as_of_date` itself — the two can differ by up to
  several weeks, which matters for a strategy whose thesis includes short-horizon
  entry timing.

## L-018 — Delisted/unavailable Top-N picks are dropped from the equity curve, not modeled as a loss

- **Severity**: High
- **Affected model(s)**: Backtester equity curve (`build_equity_curve`,
  [`historical_tester.py`](../src/backtesting/historical_tester.py))
- **Description**: A Top-N pick whose price history cannot be fetched at all
  (e.g. because it was delisted, acquired, or renamed between `as_of_date` and
  today) is removed from the equity curve, and the capital notionally allocated to
  it is redistributed equally among the surviving picks (`dropped_tickers`,
  recorded on the result but not reflected in the return calculation). This is
  distinct from `L-002` (universe-level survivorship bias, which is about which
  tickers are even considered as candidates): this is outcome-level — a pick that
  WAS selected and then suffered a bad outcome (bankruptcy, a going-private
  acquisition at a loss, etc.) severe enough to break its price history is treated
  as though it had simply never been part of the portfolio, rather than as a
  realized loss (potentially a total one) a real investor holding it would have
  experienced.
- **Mitigation**: `dropped_tickers` is recorded and surfaced on the
  `BacktestResult`, so the omission is at least visible and auditable — a consumer
  can see which tickers were dropped, even though the return figure itself doesn't
  account for their outcome.
- **Status**: Unmitigated at the calculation level. A fix requires attributing a
  realized outcome (ideally the actual delisting/acquisition terms, or
  conservatively a total loss) to a dropped ticker's notional weight, rather than
  silently redistributing it to survivors.
- **Consequence for interpretation**: A backtest's reported `portfolio_return` is
  systematically biased upward whenever any Top-N pick's price history breaks
  during the holding period — the worse the actual outcome for a dropped pick, the
  larger the resulting overstatement, since the omission (rather than a loss) is
  what gets applied.

## L-019 — Revenue-CAGR `years_elapsed` was an undocumented convention (resolved Track A Phase 2C)

- **Severity**: Medium (resolved as a documentation defect; the underlying
  arithmetic discrepancy it exposed was small in absolute terms but exceeded a
  documented validation tolerance)
- **Affected model(s)**: DCF historical revenue CAGR
  (`calculate_historical_revenue_cagr`, [`dcf.py`](../src/dcf_model/dcf.py))
- **Description**: Prior to Track A Phase 2C, `docs/model-specifications/dcf.md`
  stated the CAGR formula (`CAGR = (Revenue_latest / Revenue_earliest) **
  (1 / years_elapsed) - 1`) but did not define, in prose, exactly how
  `years_elapsed` is computed. The codebase computes it as actual elapsed
  calendar days between the earliest and latest fiscal-period-end dates,
  divided by 365.25 — but a from-spec-only reader could just as reasonably
  read `years_elapsed` as "(number of periods − 1)," a common textbook CAGR
  shortcut for evenly-spaced annual observations. Track A Phase 2B's
  independent-validation workbook (V1), built without reading `dcf.py`, chose
  the latter interpretation. For MSFT, CAT, and VZ — whose frozen
  fiscal-period-end dates recur on the same calendar day every year — the two
  interpretations coincidentally produce an identical `years_elapsed` (exactly
  `4.0`), so V1 matched the codebase to full floating-point precision on those
  three companies' Revenue CAGR. For INTC, whose fiscal-period-end dates are
  not evenly spaced (2021-12-25, 2022-12-31, 2023-12-30, 2024-12-28,
  2025-12-27 — 1,463 actual days, not 1,461), the two interpretations diverge
  (`4.005475701574264` vs. `4.0`), producing a genuine ~0.0124 percentage-point
  Revenue CAGR discrepancy that exceeded the documented `±0.01`pp
  reconciliation tolerance and produced a Phase 2B NO-GO. See
  `validation/dcf_reconciliation/history/phase2b_initial_no_go/` for the
  original failing evidence.
- **Mitigation**: `docs/model-specifications/dcf.md` now states
  `years_elapsed`'s exact definition (actual elapsed calendar days between the
  earliest- and latest-dated valid observations, divided by 365.25), formalized
  as `A-028` in the assumptions register. The codebase's existing behavior
  already conformed to this clarified convention and required no change. A
  second independent workbook (V2), built from the corrected specification, is
  used to re-validate under Track A Phase 2C.
- **Status**: Specification ambiguity resolved (Phase 2C). See
  `docs/model-change-log.md`'s Phase 2C entry and `A-028` for the
  authoritative convention and its independent re-validation status.
- **Consequence for interpretation**: For any company with evenly-spaced
  fiscal-period-end dates, this had no numerical effect. For a company on an
  irregular or 52/53-week fiscal calendar (as INTC's frozen snapshot happens to
  be), a reconciliation performed against a `(periods − 1)`-based independent
  calculation would show a small but real Revenue CAGR discrepancy that is not
  a codebase defect — it is a resolved specification-precision gap.
