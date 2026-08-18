# Assumptions Register

Status: living document. Every modeling assumption embedded in this codebase's
default configuration, with its rationale, evidence/source, and what would change
if it turned out to be wrong. IDs are stable — do not renumber an existing entry
even if a later one is removed; mark it "Retired" instead. See
[`docs/model-development-roadmap.md`](model-development-roadmap.md) for the Track A
requirement this register satisfies, and
[`docs/limitations-register.md`](limitations-register.md) for the companion list of
structural limitations (an assumption is a stated choice; a limitation is a
constraint the model cannot currently overcome).

| Field | Meaning |
|---|---|
| Assumption | What is assumed |
| Model affected | Which model/module |
| Current value / policy | The literal constant or rule in the code |
| Rationale | Why this value/policy was chosen |
| Evidence / source | What (if anything) backs the choice |
| Sensitivity required | Whether/how this should be stress-tested (Track A) |
| Validation status | Whether this has been independently checked |
| If wrong | What output changes if this assumption is incorrect |

## DCF / WACC

### A-001 — Risk-free rate source
- **Model affected**: WACC (`calculate_wacc`, [`dcf.py`](../src/dcf_model/dcf.py))
- **Current value/policy**: 10-Year Treasury Note yield (`^TNX`), live for current
  valuations, historical close on/before `as_of_date` for backtests
  (`src/utils/macro.py`)
- **Rationale**: Standard CAPM risk-free proxy; a live/point-in-time value keeps
  WACC synchronized with the actual macro environment rather than a static
  assumption
- **Evidence/source**: Standard finance convention; yfinance `^TNX` quote
- **Sensitivity required**: Yes — DCF sensitivity to risk-free rate is part of
  Track A's required sensitivity analysis (not yet performed)
- **Validation status**: Not independently validated
- **If wrong**: Directly shifts WACC and therefore every discounted value and
  terminal value; a systematically stale or wrong risk-free quote biases every
  valuation in the same direction

### A-002 — Historical revenue growth: capped above, unbounded below
- **Model affected**: DCF revenue projection (`extract_valuation_inputs`, [`dcf.py:277`](../src/dcf_model/dcf.py))
- **Current value/policy**: Historical CAGR capped at `MAX_REVENUE_GROWTH_RATE =
  25%`; **not** floored below — a genuinely shrinking company's negative CAGR is
  used as-is
- **Rationale**: A hyper-growth anomaly must not blow up the terminal-value math
  (`WACC − g` must stay positive); a shrinking company is a legitimate real-world
  case the model must still be able to value, so no artificial floor is applied
- **Evidence/source**: Engineering judgment, not empirically derived
- **Sensitivity required**: Yes — Track A sensitivity analysis on revenue growth
- **Validation status**: Not independently validated
- **If wrong**: An uncapped extreme growth outlier (if the cap were removed) could
  produce an economically meaningless valuation; an inappropriately capped genuine
  high-growth company would be systematically undervalued

### A-003 — Terminal growth rate policy default
- **Model affected**: DCF terminal value
- **Current value/policy**: Default `2.5%`, bounded `[0%, 5%]`
  (`MIN/MAX_EXPLICIT_TERMINAL_GROWTH_RATE`)
- **Rationale**: Roughly tracks long-run nominal GDP growth expectations; bounded
  well under typical WACC values so the perpetuity formula stays well-behaved
- **Evidence/source**: Standard finance convention (terminal growth ≈ long-run
  economy-wide growth), not a live macro input
- **Sensitivity required**: Yes — explicitly named in the roadmap's Track A
  sensitivity-analysis item
- **Validation status**: Not independently validated
- **If wrong**: Terminal value is highly sensitive to this input (see `L-005`) —
  even a 50bps error compounds materially into intrinsic value

### A-004 — D&A / CapEx / ΔNWC as constant percentages of revenue
- **Model affected**: DCF FCF projection
- **Current value/policy**: `DEFAULT_DA_PCT_REVENUE = 3%`,
  `DEFAULT_CAPEX_PCT_REVENUE = 4%`, `DEFAULT_NWC_PCT_REVENUE_CHANGE = 1%` — fixed
  across every company and every projection year
- **Rationale**: Simplifying assumption; avoids requiring a full working-capital/
  capex forecast model
- **Evidence/source**: Not derived from each company's own historical D&A/CapEx/NWC
  ratios (unlike revenue growth and operating margin, which are)
- **Sensitivity required**: Yes — not yet performed
- **Validation status**: Not independently validated
- **If wrong**: A capital-intensive company (e.g. a utility or manufacturer) with
  real CapEx well above 4% of revenue would have its FCF, and therefore intrinsic
  value, systematically overstated by this model

### A-005 — Default tax rate
- **Model affected**: DCF, ROIC
- **Current value/policy**: `DEFAULT_TAX_RATE = 21%` (U.S. federal statutory
  corporate rate) when a company-specific effective rate cannot be derived
- **Rationale**: Statutory rate as a conservative, well-known default
- **Evidence/source**: U.S. tax code (effective rates commonly differ from
  statutory due to credits/deductions/foreign mix)
- **Sensitivity required**: Low priority (a secondary lever compared to growth/
  margin/WACC)
- **Validation status**: Not independently validated
- **If wrong**: NOPAT and ROIC shift proportionally; likely a smaller effect than
  growth/margin/discount-rate assumptions

### A-006 — WACC clamp
- **Model affected**: WACC
- **Current value/policy**: Final WACC clamped to `[5%, 20%]`
- **Rationale**: Prevents a degenerate beta/rate combination from producing an
  economically nonsensical discount rate
- **Evidence/source**: Engineering judgment
- **Sensitivity required**: Document how often real companies actually hit this
  clamp (not yet measured)
- **Validation status**: Not independently validated
- **If wrong**: A company whose "true" cost of capital genuinely falls outside this
  band (e.g. a distressed micro-cap with cost of equity well above 20%) is
  systematically mis-discounted

### A-007 — Default beta
- **Model affected**: WACC, inverse-beta position sizing
- **Current value/policy**: `DEFAULT_BETA = 1.0` when yfinance beta is genuinely
  missing (`None`). A *present* beta that is non-finite (or otherwise malformed)
  is rejected with a `ValueError` rather than silently defaulted (Track A Phase
  1.5C) — see [`docs/model-specifications/wacc-capm.md`](model-specifications/wacc-capm.md).
- **Rationale**: Market-average beta as a neutral default
- **Evidence/source**: Standard convention
- **Sensitivity required**: Low
- **Validation status**: Not independently validated
- **If wrong**: A missing-beta company defaults to market-average risk treatment in
  both discount rate and position sizing, which may over- or under-state its actual
  risk

### A-008 — Default cost of debt
- **Model affected**: WACC
- **Current value/policy**: `DEFAULT_COST_OF_DEBT = 5%` when not derivable from
  interest expense / total debt
- **Rationale**: Reasonable investment-grade approximation
- **Evidence/source**: Not empirically calibrated
- **Sensitivity required**: Low (debt weight in WACC is often small relative to
  equity weight for the large-cap universe this project uses)
- **Validation status**: Not independently validated
- **If wrong**: Minor WACC effect for low-leverage companies; larger for
  high-leverage ones

### A-009 — Market risk premium
- **Model affected**: WACC (CAPM cost of equity)
- **Current value/policy**: Fixed `DEFAULT_MARKET_RISK_PREMIUM = 5.5%`, not live or
  regime-varying
- **Rationale**: A commonly cited long-run U.S. equity risk premium estimate
- **Evidence/source**: Standard finance convention
- **Sensitivity required**: Yes
- **Validation status**: Not independently validated
- **If wrong**: A systematic bias in cost of equity, and therefore WACC, across
  every valuation in the same direction and magnitude

### A-028 — Revenue-CAGR `years_elapsed` convention: actual elapsed calendar time, not period count
- **Model affected**: DCF historical revenue CAGR
  (`calculate_historical_revenue_cagr`, [`dcf.py`](../src/dcf_model/dcf.py)); see
  `docs/model-specifications/dcf.md`'s "Historical revenue-growth and
  operating-margin derivation" section for the full authoritative definition
- **Current value/policy**: `years_elapsed = (latest_fiscal_period_end −
  earliest_fiscal_period_end).days / 365.25`, where the earliest/latest periods
  are found by dropping periods with no reported revenue, sorting the remainder
  by actual fiscal-period-end date, and taking the first/last of that sorted set
  (subject to both having strictly positive revenue). This was already the
  codebase's implemented behavior; Track A Phase 2C made it the documented,
  authoritative convention after the written specification's silence on the
  point produced a real, tolerance-exceeding independent-validation discrepancy
  for a company on an irregular fiscal calendar (see "If wrong" below)
- **Rationale**: A fiscal calendar is not guaranteed to recur on the same
  calendar day every year (52/53-week fiscal calendars, occasional calendar
  shifts). `(number of periods − 1)` implicitly assumes perfectly even annual
  spacing; actual elapsed calendar time does not, and is the economically
  correct denominator for an annualized (CAGR) growth rate regardless of how a
  company's fiscal periods happen to be spaced
- **Evidence/source**: Track A Phase 2B's independent-validation reconciliation
  (`validation/dcf_reconciliation/`) found this exact ambiguity: Intel
  Corporation's (`INTC`) five frozen fiscal-period-end dates (2021-12-25,
  2022-12-31, 2023-12-30, 2024-12-28, 2025-12-27) span 1,463 actual days, not
  the 1,461 days (`4 × 365.25`) that a `(5 periods − 1) = 4`-year assumption
  implies — a difference large enough to move INTC's Revenue CAGR by ~0.0124
  percentage points, exceeding the documented `±0.01`pp reconciliation
  tolerance. See `docs/model-change-log.md`'s Phase 2C entry and
  `validation/dcf_reconciliation/history/phase2b_initial_no_go/` for the
  original failing evidence
- **Sensitivity required**: No — this is a definitional/date-arithmetic
  convention, not a tunable parameter; there is nothing to sensitivity-sweep
- **Validation status**: Independently re-validated as of Track A Phase 2C: a
  second, independently-built workbook (V2), built from this clarified
  specification blind to the codebase, was reconciled against production —
  all four companies (MSFT, CAT, INTC, VZ) pass, a **GO** result — see
  `validation/independent_dcf/README_v2.md` and
  `validation/dcf_reconciliation/reconciliation_report.md`. Second-reviewer
  sign-off (a separate checklist item in
  `docs/independent-validation-plan.md`) is still PENDING
- **If wrong**: For a company whose fiscal-period-end dates are evenly spaced
  (recur on the same calendar day every year), this convention and the naive
  `(periods − 1)` count produce an identical result, so there is no practical
  difference to be "wrong" about. For a company on an irregular or 52/53-week
  fiscal calendar, using `(periods − 1)` instead would misstate `years_elapsed`
  and therefore the annualized CAGR — as it did for INTC in Phase 2B

## Screens and Conviction Score

### A-010 — Altman Z-Score distress threshold and sector exclusions
- **Model affected**: Entry gate (Altman Z)
- **Current value/policy**: Reject if `Z < 1.8`; excluded entirely for Financial
  Services, Financials, Utilities, Real Estate
- **Rationale**: `1.8` is Altman's own original "Distress Zone" cutoff; the
  excluded sectors' balance-sheet structures make the original formula's
  working-capital/asset-turnover terms not meaningful
- **Evidence/source**: Altman (1968) original publication
- **Sensitivity required**: Not yet performed
- **Validation status**: Formula itself not independently re-derived/checked (see
  `L-013`)
- **If wrong**: A false-positive distress signal excludes a genuinely healthy
  company from the Top-N candidate set; a false negative admits a genuinely
  distressed one

### A-011 — Piotroski minimum F-Score
- **Model affected**: Entry gate (fundamental quality)
- **Current value/policy**: Reject if F-Score `< 5` (of 9)
- **Rationale**: The commonly cited threshold distinguishing "healthy" from
  "unhealthy" in Piotroski's original research
- **Evidence/source**: Piotroski (2000) original publication
- **Sensitivity required**: Not yet performed
- **Validation status**: Formula itself not independently re-derived/checked (see
  `L-013`)
- **If wrong**: Threshold too strict excludes viable candidates; too loose admits
  weak-quality companies

### A-012 — RSI entry threshold
- **Model affected**: Entry gate (technical micro-dip timing)
- **Current value/policy**: Only enter while `RSI < 45`
- **Rationale**: A conventional "cooling off, not yet oversold" zone — avoids
  buying into a stock that is technically hot
- **Evidence/source**: Common technical-analysis convention, not statistically
  derived for this strategy's universe
- **Sensitivity required**: Yes — this threshold has not been tested against
  alternative values
- **Validation status**: Not independently validated
- **If wrong**: Too strict a threshold delays/blocks otherwise-good entries; too
  loose admits entries with no real timing benefit

### A-013 — 200-day SMA trend tolerance
- **Model affected**: Entry gate (value trap protection)
- **Current value/policy**: Require `current_price >= 98% * SMA_200`
- **Rationale**: A stock meaningfully below its own long-term trend is judged more
  likely a value trap than a bargain
- **Evidence/source**: Common technical-analysis heuristic
- **Sensitivity required**: Yes
- **Validation status**: Not independently validated
- **If wrong**: Excludes genuinely cheap turnaround candidates, or admits stocks in
  a real structural decline

### A-014 — Conviction Score compounder eligibility gate
- **Model affected**: Conviction Score
- **Current value/policy**: Requires both `fcf_growth_rate > 0` and `roic > 0`
- **Rationale**: Prevents two negative inputs multiplying to a spurious positive
  score (a correctness fix, not a tunable parameter)
- **Evidence/source**: Internal logical requirement of the scoring formula
- **Sensitivity required**: No — this is a structural correctness constraint, not a
  policy lever
- **Validation status**: Verified by `TestNegativeTimesNegativeCannotScorePositive`
- **If wrong**: N/A — this is a mathematical necessity of the formula, not a
  discretionary choice

### A-015 — FCF Yield blend weights
- **Model affected**: Live/paper Conviction Score (not backtester)
- **Current value/policy**: 60% DCF Conviction Score / 40% normalized FCF Yield;
  10% FCF yield → 1.0 normalized multiplier; capped at 2.0
- **Rationale**: Blends the DCF's forward-looking estimate with a
  simpler, harder-to-manipulate trailing cash-yield metric
- **Evidence/source**: Not empirically optimized — a stated design choice
- **Sensitivity required**: Yes
- **Validation status**: Not independently validated
- **If wrong**: Over/under-weights one signal relative to the other; affects
  live-trading ranking only, never backtester comparability

## Portfolio construction

### A-016 — Inverse-beta position sizing with beta floor
- **Model affected**: Position sizing
- **Current value/policy**: `weight ∝ 1 / max(beta, 0.5)`
- **Rationale**: Lower-volatility picks receive proportionally more capital; the
  0.5 floor prevents an anomalously low beta from dominating the allocation
- **Evidence/source**: Standard risk-parity-style heuristic, not empirically tuned
  for this universe
- **Sensitivity required**: Yes
- **Validation status**: Not independently validated
- **If wrong**: Misallocates risk if beta itself is a poor forward-risk proxy for a
  given name (see `L-004`, single-factor CAPM limitation)

### A-017 — Position and sector caps
- **Model affected**: Position sizing
- **Current value/policy**: `MAX_POSITION_WEIGHT = 15%`, `MAX_SECTOR_WEIGHT = 25%`
- **Rationale**: Conventional concentration-risk limits for a retail-scale
  portfolio
- **Evidence/source**: Not derived from this project's own risk analysis
- **Sensitivity required**: Should be checked against realized portfolio VaR over
  time (Track A "ongoing monitoring")
- **Validation status**: Not independently validated
- **If wrong**: Too loose allows excess concentration risk; too tight forces
  cash drag when few candidates pass all gates

### A-018 — Rebalance drift threshold
- **Model affected**: Rebalancing
- **Current value/policy**: `DRIFT_THRESHOLD = 3%`
- **Rationale**: Avoids generating a stream of tiny, cost-and-slippage-only trades
  for positions already close to target
- **Evidence/source**: Engineering judgment
- **Sensitivity required**: Should be revisited once a real cost model exists
  (Track B item 6) — the right threshold depends on actual trading costs, which
  are not yet modeled
- **Validation status**: Not independently validated
- **If wrong**: Too tight generates excess turnover/cost; too loose leaves the
  portfolio meaningfully off its risk-adjusted target for longer

## Risk models

### A-019 — Monte Carlo VaR: normal-distribution assumption, simulation parameters
- **Model affected**: Portfolio VaR/CVaR
- **Current value/policy**: Multivariate normal simulation, `10,000` paths, `21`
  trading-day horizon, 95% confidence (5th percentile tail)
- **Rationale**: Standard Monte Carlo VaR construction; parameters chosen for
  computational tractability and a roughly monthly risk horizon
- **Evidence/source**: Historical mean/covariance of the portfolio's own daily log
  returns (252-day lookback) — the *distributional family* (normal) is an
  assumption, not derived from the data
- **Sensitivity required**: Yes — explicitly named in the roadmap (Track B item 10:
  compare against historical-simulation and bootstrap VaR)
- **Validation status**: Not yet compared against alternative risk-model families
- **If wrong**: Real equity returns exhibit fatter tails than normal — this VaR/
  CVaR likely **understates** true tail risk (see `L-006`)

### A-020 — VaR minimum portfolio coverage fraction
- **Model affected**: Portfolio VaR
- **Current value/policy**: `MIN_PORTFOLIO_COVERAGE_FRACTION = 50%` — below this,
  refuse to compute rather than renormalize an unrepresentative remnant
- **Rationale**: A result computed from less than half the portfolio's weight is
  judged not meaningful
- **Evidence/source**: Engineering judgment; mirrors the identical policy in
  `src.api.sector_medians.MIN_OVERALL_COVERAGE_FRACTION`
- **Sensitivity required**: Low priority
- **Validation status**: Not independently validated
- **If wrong**: Too strict a floor produces more "insufficient_data" results than
  necessary; too loose risks a misleading VaR from a small unrepresentative subset

### A-021 — SPY hedge stress scenario and assumed implied volatility
- **Model affected**: SPY put hedge sizing
- **Current value/policy**: `stress_move_fraction = 7%`, `implied_vol = 15%` (both
  fixed assumptions, not live/derived)
- **Rationale**: A plausible single-scenario equity drawdown and a plausible
  baseline SPY implied volatility level
- **Evidence/source**: Not derived from the actual VaR horizon/confidence level
  being hedged, and not the contract's real live implied volatility (Alpaca's
  trading-only client does not reliably expose it)
- **Sensitivity required**: Yes
- **Validation status**: Not independently validated
- **If wrong**: Hedge could be under- or over-sized relative to the actual realized
  stress scenario or actual market-implied volatility

### A-022 — Hedge budget and contract caps
- **Model affected**: SPY put hedge sizing
- **Current value/policy**: `HEDGE_BUDGET_FRACTION_OF_EQUITY = 2%`,
  `HEDGE_MAX_CONTRACTS = 50`
- **Rationale**: Bounds hedge premium spend as a conventional small fraction of
  equity
- **Evidence/source**: Engineering judgment
- **Sensitivity required**: Low priority
- **Validation status**: Not independently validated
- **If wrong**: Too tight a budget leaves VaR under-hedged; too loose risks
  excessive premium spend on protection

## Backtesting

### A-023 — Statement filing lag (backtester)
- **Model affected**: Point-in-time statement selection
- **Current value/policy**: `STATEMENT_FILING_LAG_DAYS = 90`, deliberately the
  longest realistic SEC deadline
- **Rationale**: Errs toward treating a period as available *later* than it might
  really have been — judged a safer failure mode than look-ahead bias
- **Evidence/source**: SEC 10-K filing deadline schedule (60/75/90 days by filer
  category)
- **Sensitivity required**: Should be tested per filer-category once Track B's
  point-in-time SEC/XBRL data exists
- **Validation status**: `TestDefaultFilingLagIsConservative` verifies the constant
  itself; not validated against real historical filing dates
- **If wrong**: Too short a lag risks genuine look-ahead bias; too long
  (unnecessarily) discards otherwise-usable recent data

### A-024 — Sector-relative filter as the screening mechanism
- **Model affected**: Sector-relative valuation filter
- **Current value/policy**: A ticker is eligible only if its P/IV ≤ its sector's
  median P/IV *within the current run's universe*
- **Rationale**: Avoids uniformly excluding entire sectors that structurally trade
  at higher multiples
- **Evidence/source**: Common equity-research practice (sector-relative rather than
  absolute-value screening); not statistically validated for this project's
  specific universe/formula combination
- **Sensitivity required**: Yes — sensitivity to universe composition is not yet
  measured
- **Validation status**: Not independently validated
- **If wrong**: A small or unusual universe composition can shift a sector's
  median substantially, changing which tickers pass the filter run to run

### A-025 — Benchmark choice
- **Model affected**: Backtesting, live risk reporting
- **Current value/policy**: SPY (S&P 500 ETF) as the default and only benchmark
- **Rationale**: The most conventional U.S. large-cap passive benchmark
- **Evidence/source**: Convention
- **Sensitivity required**: Track B item 7 explicitly requires comparison against
  *additional* simpler baselines (equal-weight, single-factor), not SPY alone —
  not yet built
- **Validation status**: N/A (a data choice, not a model to validate)
- **If wrong**: A single-benchmark comparison could overstate an edge that doesn't
  hold against a more appropriate or more granular baseline

### A-026 — Default universe (current top-100 by market cap)
- **Model affected**: Backtesting, live scan
- **Current value/policy**: `DEFAULT_SP500_TOP_100_TICKERS` — a hardcoded list of
  today's 100 largest S&P 500 constituents by market cap
- **Rationale**: A representative, liquid large-cap universe for both live trading
  and backtesting
- **Evidence/source**: Current market-cap ranking, not a historically accurate
  point-in-time universe
- **Sensitivity required**: N/A — this is a documented limitation (`L-002`,
  survivorship bias), not a lever to sensitivity-test
- **Validation status**: N/A
- **If wrong**: See `L-002` — this is a known, structural, currently-unaddressed
  bias, not an uncertain assumption

### A-027 — Sector-median cache health thresholds
- **Model affected**: Live API sector comparison (`src.api.sector_medians`)
- **Current value/policy**: `CACHE_MAX_STALENESS = 48h`,
  `MIN_SECTOR_SAMPLE_SIZE = 3`, `MIN_OVERALL_COVERAGE_FRACTION = 50%`,
  `RISK_FREE_RATE_COMPARISON_TOLERANCE = 5bps` — a comparison is refused if the
  caller's actual risk-free rate (the same rate it fed into its own `calculate_wacc`
  call) differs from the cache's own generation-time risk-free rate by more than
  this tolerance, in addition to the assumption-match check
  (`_serialize_comparable_assumptions`). Fixed a real gap: the cache always
  recorded its own `risk_free_rate`, but this was never actually checked against
  the caller's rate — a cache generated near a 1% risk-free rate would previously
  have been silently accepted for a request valued at 10%, even though the two
  P/IV ratios were computed under materially different discount-rate regimes.
- **Rationale**: Refuses a comparison rather than silently trusting a stale,
  thin-sample, systemically-degraded, or discount-rate-incompatible cache
- **Evidence/source**: Engineering judgment
- **Sensitivity required**: Low priority
- **Validation status**: Not independently validated
- **If wrong**: Too strict refuses comparisons unnecessarily; too loose risks
  trusting a stale, unrepresentative, or discount-rate-incompatible median
