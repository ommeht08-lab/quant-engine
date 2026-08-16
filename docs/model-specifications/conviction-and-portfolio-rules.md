# Model Specification: Conviction Scoring, Screens, and Portfolio Construction

Sources: [`src/backtesting/historical_tester.py`](../../src/backtesting/historical_tester.py)
(sector-relative filter, Conviction Score), [`src/valuation/altman_z.py`](../../src/valuation/altman_z.py),
[`src/valuation/piotroski.py`](../../src/valuation/piotroski.py),
[`src/valuation/technical.py`](../../src/valuation/technical.py),
[`src/trading/alpaca_execution.py`](../../src/trading/alpaca_execution.py) (entry
gates, FCF-yield blend, position sizing, rebalancing)

This document covers every non-DCF, non-VaR decision rule between "we have an
intrinsic value estimate for a ticker" and "we hold a specific dollar amount of
that ticker in the paper account."

## 1. Sector-Relative Valuation Filter

### Objective

Screen for statistically cheap companies **relative to sector peers**, not against
one fixed Price/Intrinsic-Value (P/IV) threshold — different sectors structurally
trade at different multiples for reasons the DCF does not model, so a single
absolute cutoff would systematically exclude entire sectors (e.g. technology)
rather than finding genuinely cheap names within each.

### Calculation sequence (two-pass)

1. **Pass 1** (`compute_valuation`,
   [`historical_tester.py:589`](../../src/backtesting/historical_tester.py)):
   value every ticker in the universe via the DCF, recording its P/IV =
   `historical_price / historical_intrinsic_value` and GICS sector.
2. **Sector medians** (`calculate_sector_median_price_to_intrinsic`,
   [`historical_tester.py:732`](../../src/backtesting/historical_tester.py)): group
   valid P/IV ratios by sector, take each sector's median, within the *current run's
   universe only* (not a fixed historical reference).
3. **Pass 2** (`score_ticker`,
   [`historical_tester.py:809`](../../src/backtesting/historical_tester.py)): a
   ticker is eligible to be scored only if `price_to_intrinsic <= sector_median`.

Sector sample counts (`calculate_sector_sample_counts`,
[`historical_tester.py:753`](../../src/backtesting/historical_tester.py)) are
tracked alongside the medians so a consumer can distinguish a median backed by 20
tickers from one backed by 1.

## 2. Conviction Score

### Formula

```
Eligibility gate: fcf_growth_rate > 0  AND  roic > 0

raw = (fcf_growth_rate * roic) / max(price_to_intrinsic, MIN_PRICE_TO_INTRINSIC_FLOOR)
conviction_score = CONVICTION_RAW_CAP * raw / (raw + CONVICTION_RAW_HALF_SATURATION)
```

with `MIN_PRICE_TO_INTRINSIC_FLOOR = 0.05`, `CONVICTION_RAW_CAP = 2.0`,
`CONVICTION_RAW_HALF_SATURATION = 0.10`
([`historical_tester.py:167-169`](../../src/backtesting/historical_tester.py)).

### Why the eligibility gate exists

Without requiring **both** `fcf_growth_rate > 0` and `roic > 0`, two negative
inputs multiply to a *positive* raw score — a shrinking, capital-destroying company
would read as if it were compounding. The gate is a structural correctness fix, not
merely a filter: a ticker failing it receives a `skip_reason`, never a score
(`score_ticker`, [`historical_tester.py:906-928`](../../src/backtesting/historical_tester.py)).
See `TestNegativeTimesNegativeCannotScorePositive` in
[`tests/backtesting/test_conviction_score.py`](../../tests/backtesting/test_conviction_score.py).

### Why a saturating (not hard-clipped) normalization

The final Michaelis-Menten-style transform maps `raw` (always `> 0` past the
eligibility gate) onto `(0, CONVICTION_RAW_CAP)` — compressing extreme outliers
toward the cap rather than flattening every large value to an identical number the
way a hard clip would, while still guaranteeing the score never exceeds the cap.
This puts the DCF-based component on the same bounded scale as the normalized FCF-
Yield component it is later blended with (§4 below), both capped at 2.0 — a
deliberate correctness requirement, not a cosmetic choice: an unbounded raw score
blended against a bounded term would make a stated blend weighting (e.g. "60/40")
not actually meaningful whenever the unbounded term was large.

### Sub-components

- **FCF growth rate**: year-over-year FCF growth between the two most recent
  available periods, `(FCF_t / FCF_{t-1}) - 1`
  (`calculate_fcf_growth_rate`, [`historical_tester.py:497`](../../src/backtesting/historical_tester.py)).
  `None` if fewer than two periods are available or the base period's FCF is not
  positive.
- **ROIC** (Return on Invested Capital):
  ```
  NOPAT            = EBIT * (1 - tax_rate)
  Invested Capital = Total Debt + Stockholders' Equity - Cash & Equivalents
  ROIC             = NOPAT / Invested Capital
  ```
  (`calculate_roic`, [`historical_tester.py:527`](../../src/backtesting/historical_tester.py)).
  `None` if EBIT/equity can't be determined or Invested Capital is not positive.

### Valid range

`conviction_score ∈ (0, 2.0)` for any scored ticker (never exactly 0 or 2.0, since
`raw > 0` strictly past the eligibility gate and the saturating transform
asymptotically approaches but never reaches the cap). `None` for any ticker that
fails the sector filter, the eligibility gate, or an earlier data-availability
check.

### Backtester vs. live: unblended vs. blended

`historical_tester.score_ticker` always returns the **unblended** DCF-based
Conviction Score above — this is what backtests compare across runs, kept stable
regardless of live-trading refinements. The live/paper trading engine
(`src/trading/alpaca_execution.py`) blends this with a normalized FCF Yield before
using it for ranking/sizing (§4).

## 3. Entry Gates (live/paper trading only)

Applied **after** every ticker has already been valued and has already contributed
to the sector median — never before, and never in a way that changes which tickers
other tickers are benchmarked against
(`_entry_gate_failure_reason`, [`alpaca_execution.py:598`](../../src/trading/alpaca_execution.py)).
Purely a Top-N *eligibility* filter. All four fail safe (reject) on missing/
unusable data — none of them treats "I couldn't compute this" as "this passed."

### 3a. Altman Z-Score (distress filter)

```
Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5

X1 = (Current Assets - Current Liabilities) / Total Assets   (working capital)
X2 = Retained Earnings / Total Assets
X3 = EBIT / Total Assets
X4 = (Shares Outstanding * Current Price) / Total Liabilities  (market value of equity / total liabilities)
X5 = Total Revenue / Total Assets
```

(`calculate_altman_z`, [`src/valuation/altman_z.py:42`](../../src/valuation/altman_z.py)).
Gate: reject if `Z < 1.8` (`ALTMAN_Z_DISTRESS_THRESHOLD`) or `Z` is unavailable.
**Excluded sectors**: Financial Services, Financials, Utilities, Real Estate
(`ALTMAN_Z_EXCLUDED_SECTORS`) — the original 1968 formula's working-capital and
asset-turnover terms are not meaningful for these sectors' balance-sheet
structures, so the gate is marked not-applicable (neither pass nor fail) rather
than misapplied. Missing any of the 9 required line items → `None` → treated as a
failure everywhere except the excluded sectors.

### 3b. 200-Day SMA Trend Filter ("value trap protection")

`current_price >= TREND_SMA_TOLERANCE (98%) * SMA_200`, where `SMA_200` is the
simple mean of the most recent 200 valid daily closes
(`check_trend_filter`, [`alpaca_execution.py:532`](../../src/trading/alpaca_execution.py)).
Requires at least `TREND_SMA_WINDOW_DAYS` (200) valid closes — fewer fails safe
(treated as a fail, not a shorter-window approximation), the rationale being that a
"200-day SMA" computed over a shorter window is not actually the 200-day average.

### 3c. Piotroski F-Score (fundamental quality)

9-point score comparing the most recent period (t) against the prior period (t-1):

| # | Category | Factor |
|---|---|---|
| 1 | Profitability | ROA (Net Income / Total Assets) `> 0` |
| 2 | Profitability | CFO (Operating Cash Flow) `> 0` |
| 3 | Profitability | CFO `>` Net Income (earnings quality) |
| 4 | Profitability | ΔROA `> 0` |
| 5 | Leverage | ΔLeverage (LT Debt / Assets) `< 0` |
| 6 | Liquidity | ΔCurrent Ratio `> 0` |
| 7 | Dilution | Shares issued: `Shares_t <= Shares_{t-1}` |
| 8 | Efficiency | ΔGross Margin `> 0` |
| 9 | Efficiency | ΔAsset Turnover (Revenue / Assets) `> 0` |

(`calculate_f_score`, [`src/valuation/piotroski.py:139`](../../src/valuation/piotroski.py)).
Each factor independently defaults to `False` (0) if its required line item or
prior-period comparison is unavailable, rather than aborting the whole score — a
company with an incomplete statement still gets a best-effort score. Gate: reject
if `F-Score < PIOTROSKI_MIN_F_SCORE = 5`.

### 3d. RSI Micro-Dip Filter

14-day Relative Strength Index via Wilder's Smoothing Method (the original RSI
formulation — an EMA of gains/losses seeded by a simple average of the first
`period` observations, not a plain rolling average):

```
avg_gain_0 = mean(gains[:period])        avg_loss_0 = mean(losses[:period])
avg_gain_i = (avg_gain_{i-1} * (period-1) + gain_i) / period      (i > 0, analogous for loss)
RS  = avg_gain / avg_loss
RSI = 100 - 100 / (1 + RS)
```

(`calculate_rsi`, [`src/valuation/technical.py:48`](../../src/valuation/technical.py)).
Gate: only enter while `RSI < RSI_MAX_ENTRY_THRESHOLD = 45` (a technical cooling-
off/oversold state, not while the stock is "hot"). A period of zero average loss
(uninterrupted gains) returns exactly `100.0`. Requires `period + 1` (15) closes;
insufficient history or a non-finite result returns `None`, treated as a gate
failure.

## 4. FCF Yield Blend (live/paper trading only)

```
normalized_fcf   = clamp(fcf_yield * FCF_YIELD_NORMALIZATION_FACTOR, 0.0, FCF_YIELD_NORMALIZED_CAP)
final_conviction = (dcf_conviction * DCF_CONVICTION_BLEND_WEIGHT) + (normalized_fcf * FCF_YIELD_BLEND_WEIGHT)
```

with `FCF_YIELD_NORMALIZATION_FACTOR = 10` (a 10% FCF yield maps to a normalized
multiplier of 1.0), `FCF_YIELD_NORMALIZED_CAP = 2.0`,
`DCF_CONVICTION_BLEND_WEIGHT = 0.60`, `FCF_YIELD_BLEND_WEIGHT = 0.40`
(`_blend_conviction_with_fcf_yield`, [`alpaca_execution.py:499`](../../src/trading/alpaca_execution.py)).
A missing FCF Yield contributes `0` to the blend (not a rejection) — the DCF
Conviction Score alone still carries 60% of the final score. This blended score is
used only for live/paper ranking and sizing; the backtester's `TickerAnalysis`
scores are never blended, so historical results stay comparable across runs. See
`TestBlendWeightingBehavesAsDocumented` in
[`tests/trading/test_rebalance.py`](../../tests/trading/test_rebalance.py).

## 5. Position Sizing — Inverse Volatility (Beta) Weighting

### Formula

```
raw_weight_i = 1 / max(beta_i, MIN_BETA_FLOOR)          MIN_BETA_FLOOR = 0.5
weight_i     = raw_weight_i / sum(raw_weight_j for all picks j)
```

(`_inverse_risk`, [`alpaca_execution.py:960`](../../src/trading/alpaca_execution.py);
`calculate_inverse_beta_weights`, [`alpaca_execution.py:1010`](../../src/trading/alpaca_execution.py)).
Beta is floored at 0.5 before inverting so an artificially low-beta anomaly cannot
dominate the allocation. Missing beta falls back to `DEFAULT_BETA = 1.0` (same
fallback `calculate_wacc` uses, for consistency).

### Risk caps

Two caps are applied on top of the raw inverse-beta weights, iteratively, until
stable (`_CAP_ITERATION_LIMIT = 20` rounds):

- **`MAX_POSITION_WEIGHT = 15%`**: no single position may exceed this share of
  equity.
- **`MAX_SECTOR_WEIGHT = 25%`**: no single GICS sector's combined weight (summed
  across every Top-N pick in that sector) may exceed this.

Excess weight from either cap is redistributed pro-rata to positions/sectors still
under their own cap (`_distribute_capped`,
[`alpaca_execution.py:967`](../../src/trading/alpaca_execution.py)), itself always
bounded by `MAX_POSITION_WEIGHT` so the two caps cannot fight each other
indefinitely. If the remaining candidate set cannot absorb the excess even
respecting both caps, the shortfall is left unallocated (sits in cash) rather than
breaching either cap. Weights are therefore **not guaranteed to sum to 1.0**.

### Post-fill correction

Because a market order's exact fill price/notional cannot be perfectly predicted,
`_check_post_fill_caps` ([`alpaca_execution.py:1903`](../../src/trading/alpaca_execution.py))
recomputes *actual* post-fill weights against both caps. A single position over
`MAX_POSITION_WEIGHT` triggers an immediate trim-to-cap sell
(`_trim_position_to_cap`, [`alpaca_execution.py:1807`](../../src/trading/alpaca_execution.py)).
A sector over `MAX_SECTOR_WEIGHT` is **not** auto-corrected (choosing which of
several tickers to cut is a policy decision this function is not positioned to make
safely) — it is logged and the rebalance is reported incomplete.

**The trim is only reported complete if the CONFIRMED fill actually restored the
position to at/under the cap — not merely because the trim order received any
positive fill.** `_trim_position_to_cap` returns the trim's confirmed filled
notional (`filled_qty * filled_avg_price`), and `_check_post_fill_caps` recomputes
the resulting dollar value from that confirmed value
(`remaining_market_value = market_value - trimmed_notional`), checked against the
cap's dollar value (`maximum_allowed_value = MAX_POSITION_WEIGHT * equity`) with a
small, fixed **dollar** tolerance:

```
remaining_market_value <= maximum_allowed_value + POST_TRIM_NOTIONAL_TOLERANCE_DOLLARS
```

`POST_TRIM_NOTIONAL_TOLERANCE_DOLLARS = 0.01 + 1e-6` — one cent, plus a small
floating-point-representation epsilon. This is deliberately a fixed dollar amount,
**not** a weight-fraction percentage: a fixed percentage tolerance scales with
account size (an earlier version of this check used `0.1` percentage point, which
is $100 of slack on a $100,000 account and $10,000 on a $10,000,000 one — far more
slack than the rounding it was meant to absorb actually requires, and wide enough
to silently accept a genuine, actionable $100 remaining breach as "close enough").
The only real source of residual "shortfall" this needs to cover — cent-level
notional rounding (`round(excess_value, 2)` at order-submission time) and ordinary
floating-point noise in the `market_value`/`equity` arithmetic — is on the order of
a single cent regardless of account size, so a fixed one-cent-plus-epsilon dollar
tolerance covers it exactly without ever being wide enough to paper over a real
shortfall.

A tiny partial fill — e.g. a $20,000 position needing a $5,000 trim to reach a
$15,000 (15%) cap, but only $100 actually fills — leaves the remaining value at
$19,900, nowhere near the cap, and is correctly reported incomplete; a genuine $1,
$50, or $100 residual is likewise always reported incomplete (each is far outside
the one-cent tolerance). Only a partial (or full) fill whose confirmed notional
actually closes the gap to within a cent of the cap is reported complete. A
pending order that never resolves within the polling window, or one whose status
cannot be confirmed at all, is likewise reported incomplete rather than assumed
resolved. The proven (not assumed) remaining weight also feeds directly into the
per-sector weight total below — an insufficiently-trimmed position's actual,
elevated weight is what gets summed into its sector's total, never the weight it
would have had if the trim were (wrongly) assumed to have fully restored it to the
cap. See `TestPostFillCapEnforcement` and `TestPostFillCapNotionalTolerance` in
[`tests/trading/test_rebalance.py`](../../tests/trading/test_rebalance.py),
specifically `test_tiny_partial_fill_remains_over_cap_and_reports_incomplete`,
`test_sufficient_partial_fill_reaches_the_cap`,
`test_pending_trim_order_reports_incomplete`, and
`test_unconfirmed_trim_order_reports_incomplete`.

## 6. Rebalancing Rules

- **Drift threshold** (`DRIFT_THRESHOLD = 3%`): an already-held Top-N pick is only
  rebalanced (bought or sold) if its current weight has drifted from its freshly
  computed target by more than 3 percentage points, in either direction — avoids
  generating a stream of tiny, cost-and-slippage-only trades. Bidirectional: an
  overweight pick is sold down toward target, not merely left alone (a prior-
  version limitation this codebase's tests explicitly guard against — see
  `TestBidirectionalRebalance`).
- **Full liquidations are never subject to the drift threshold** — a ticker leaving
  the Top N, or a profit-taking exit, always executes regardless of drift size.
- **Profit-taking**: any held equity position whose current market price has risen
  to or above its own DCF intrinsic value is liquidated
  (`_is_profit_take_candidate`, [`alpaca_execution.py:805`](../../src/trading/alpaca_execution.py))
  — once price catches up to fair value, it is no longer a margin-of-safety
  opportunity, regardless of Conviction Score rank.
- **Buying-power constraint**: the aggregate of every planned buy is scaled
  proportionally (never order-dependent) to fit available buying power, then
  allocated via a `Decimal`/`ROUND_DOWN` cent-exact procedure
  (`_allocate_cent_safe_buy_notionals`, [`alpaca_execution.py:1099`](../../src/trading/alpaca_execution.py))
  so `sum(submitted buy notionals) <= buying_power` is an exact invariant, not
  merely true up to independent per-order rounding.
- **Order confirmation**: no order is assumed filled just because the submission
  call returned — every order is polled (`_await_order_resolution`) until a
  terminal status or a bounded number of polls elapses; only the *actual confirmed
  filled quantity/price* is logged to `trade_logs`.
- **Idempotency**: `_open_order_symbols` is checked before every submission this
  run, updated in-memory as orders are submitted/resolved, so a still-open order
  from earlier in the same run (or a prior run) is never duplicated.

## Known simplifications

- The compounder eligibility gate (`fcf_growth_rate > 0 AND roic > 0`) is a hard
  binary cutoff — a company at `fcf_growth_rate = -0.001%` is treated identically
  to one at -50%, both excluded, with no graded scoring near the boundary.
- Sector medians are computed from the *current run's universe only* — a small or
  unusual universe composition can shift a sector's median substantially run to
  run.
- The FCF Yield blend weights (60/40) and normalization factor (10%) are fixed,
  stated assumptions, not derived or optimized from data.
- Inverse-beta weighting uses the same current-day beta approximation the WACC
  model uses (see [`docs/model-specifications/wacc-capm.md`](wacc-capm.md)) — no
  historical beta is available for backtesting position sizing either.

## Test coverage

Conviction Score: [`tests/backtesting/test_conviction_score.py`](../../tests/backtesting/test_conviction_score.py)
(9 methods) — `TestPriceToIntrinsicFloorBoundsBlowUp`,
`TestNegativeTimesNegativeCannotScorePositive`. Portfolio construction and
rebalancing: [`tests/trading/test_rebalance.py`](../../tests/trading/test_rebalance.py)
(63 test functions; no parametrization, so 63 collected cases too) —
`TestBidirectionalRebalance`, `TestSectorCapAndCash`,
`TestCentSafeBuyAllocation`, `TestBuyingPowerConstraint`,
`TestBlendWeightingBehavesAsDocumented`, `TestHedgeContractNonFungibility`,
`TestPostFillCapEnforcement`, `TestPostFillCapNotionalTolerance` (Track A Phase
1.5B: the exact-cap/sub-cent/one-cent/$1/$50/$100-residual matrix, plus the
sector-total-uses-proven-weight regression), `TestCapTrimDuplicateOrderProtection`,
`TestMarketClockRecheckedPerOrder`, and more. Safety/idempotency:
[`tests/trading/test_safety.py`](../../tests/trading/test_safety.py) (37 methods) —
`TestPaperOnlyEnforcement`, `TestIdempotency`, `TestDryRunNeverMutatesExternalState`,
`TestMarketClosesMidRun`.

**Coverage gap**: as of this writing, there is **no dedicated unit test file** for
`src/valuation/altman_z.py`, `src/valuation/piotroski.py`, or
`src/valuation/technical.py` (RSI) — grepping the test suite for
`calculate_altman_z`, `calculate_f_score`, and `calculate_rsi` finds no direct
references. `tests/trading/test_main_integration.py` monkeypatches
`run_todays_scan` wholesale rather than exercising the real entry-gate calculation
path end to end. This means the Altman Z-Score formula, the Piotroski F-Score's 9
factors, and the Wilder's-Smoothing RSI calculation are exercised only via manual
`__main__` smoke tests in their own source files, not by the automated test suite.
Recorded as `L-013` in the limitations register — closing this gap is a Track A
prerequisite, not yet complete.
