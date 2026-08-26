# Model Specification: Discounted Cash Flow (DCF) Valuation

Source: [`src/dcf_model/dcf.py`](../../src/dcf_model/dcf.py)
Tests: [`tests/dcf/test_dcf.py`](../../tests/dcf/test_dcf.py) — see "Test coverage" below for
the current test-function/collected-case counts (counted, not hand-maintained, to avoid drift).
Consumers: [`src/api/main.py`](../../src/api/main.py) (single-ticker live API),
[`src/backtesting/historical_tester.py`](../../src/backtesting/historical_tester.py)
(point-in-time backtesting), [`src/api/sector_medians.py`](../../src/api/sector_medians.py)
(sector median snapshot generation — published to a Supabase-backed store for the live
API to read; see [L-008](../limitations-register.md#l-008--sector-median-and-price-history-cache-limitations)
in the limitations register)

## Objective

Estimate a company's intrinsic value per share from its own financial statements,
via a standard unlevered Free Cash Flow (FCF) Discounted Cash Flow model: project
FCF for an explicit forecast window, apply a Gordon Growth terminal value beyond
that window, discount both back to the present at the company's Weighted Average
Cost of Capital (WACC), and bridge the resulting Enterprise Value to an Equity
Value per share.

## Calculation sequence

1. Extract raw statement line items (`extract_valuation_inputs`, in
   [`dcf.py`](../../src/dcf_model/dcf.py)).
2. Validate capital-structure inputs are finite and non-negative
   (`_validate_capital_inputs`, in [`dcf.py`](../../src/dcf_model/dcf.py)).
3. Resolve `revenue_growth_rate` and `operating_margin` — explicit override, else
   historically-derived, else conservative fallback (see "Historical derivation"
   below).
4. Compute WACC (`calculate_wacc`, in [`dcf.py`](../../src/dcf_model/dcf.py)).
5. Project FCF for `projection_years` (`project_free_cash_flows`, in
   [`dcf.py`](../../src/dcf_model/dcf.py)).
6. Compute terminal value (`calculate_terminal_value`, in
   [`dcf.py`](../../src/dcf_model/dcf.py)).
7. Discount projected FCF and terminal value to present value
   (`discount_to_present_value`, in [`dcf.py`](../../src/dcf_model/dcf.py)).
8. Bridge Enterprise Value to intrinsic value per share
   (`calculate_intrinsic_value_per_share`, in [`dcf.py`](../../src/dcf_model/dcf.py)).
9. Separately, compute a market-based Enterprise Value and FCF Yield as a
   valuation cross-check (`calculate_enterprise_value`/`calculate_fcf_yield`, in
   [`dcf.py`](../../src/dcf_model/dcf.py)).

The orchestrator `run_dcf_valuation` (in [`dcf.py`](../../src/dcf_model/dcf.py))
wires steps 1–9 together; every function is independently callable and tested.
Function names, not line numbers, are used throughout this document as
references into the source — line numbers drift on every edit and had already
gone stale prior to this revision.

## Historical revenue-growth and operating-margin derivation

By default (`DCFAssumptions.revenue_growth_rate = None` /
`operating_margin = None`), this model does **not** apply one generic assumption to
every company — it derives each company's own historical figures:

- **Revenue growth** = Compound Annual Growth Rate (CAGR) of Total Revenue across
  every period available in the income statement:
  `CAGR = (Revenue_latest / Revenue_earliest) ** (1 / years_elapsed) - 1`
  (`calculate_historical_revenue_cagr`, in [`dcf.py`](../../src/dcf_model/dcf.py)).
  Requires at least two periods of positive revenue; returns `None` otherwise.
  Capped at `MAX_REVENUE_GROWTH_RATE` (25%) so a hyper-growth anomaly cannot make
  `WACC − g` in the terminal-value formula degenerate. **Not** floored below —
  a genuinely shrinking company's negative CAGR is used as-is, a deliberate
  modeling choice (see `A-002` in the assumptions register).

  **`years_elapsed` — authoritative definition (Track A Phase 2C; previously
  ambiguous, see `L-019` in the limitations register, `A-028` in the
  assumptions register, and the Phase 2C entry in
  `docs/model-change-log.md`):**

  ```
  years_elapsed = (latest_fiscal_period_end − earliest_fiscal_period_end).days / 365.25
  ```

  `earliest_fiscal_period_end` and `latest_fiscal_period_end` are the fiscal
  period end **dates** of the earliest and latest usable observations, found
  by: (1) dropping any period with no reported revenue value; (2) sorting the
  remaining periods **by their actual fiscal-period-end date**, not by
  column order or by row position; (3) taking the resulting first
  (earliest-dated) and last (latest-dated) periods, subject to both having
  strictly positive revenue — if either the earliest- or latest-dated
  period's revenue is not strictly positive, historical CAGR is not derived
  (returns `None`, falling through to the documented fallback/override
  precedence) rather than substituting a different period.

  `years_elapsed` is **actual elapsed calendar time between those two
  dates**, expressed as a fraction of an average Gregorian year (365.25
  days) — it is explicitly **not** `(number of periods − 1)`. The two are
  easy to conflate for a company that reports on a strict, unchanging
  fiscal calendar (e.g. every fiscal year ending exactly one calendar year
  after the last, to the day), where they coincidentally produce the same
  number. They diverge for a company on an irregular or 52/53-week fiscal
  calendar, where consecutive fiscal-period-end dates are not exactly 365
  or 366 days apart — `(number of periods − 1)` silently assumes perfectly
  equal annual spacing and misstates the actual elapsed time in that case.
  This is not a hypothetical: Intel Corporation's (`INTC`) five most recent
  frozen fiscal-period-end dates are 2021-12-25, 2022-12-31, 2023-12-30,
  2024-12-28, and 2025-12-27 — 1,463 actual elapsed days between the first
  and last, not the 1,461 days (`4 × 365.25`) that `(5 periods − 1) = 4`
  years would imply. Using the wrong convention for INTC changes
  `years_elapsed` from `4.0` to `4.005475701574264`, which — compounded
  through a negative CAGR base — was large enough to fail Track A Phase
  2B's independent-validation reconciliation at its documented `±0.01`
  percentage-point tolerance (see `docs/independent-validation-plan.md`'s
  Phase 2B/2C history and `validation/dcf_reconciliation/history/`).
- **Operating margin** = simple average of EBIT / Revenue across every period with
  both a usable EBIT (or Operating Income, as a fallback label) and positive
  revenue (`calculate_historical_average_operating_margin`, in
  [`dcf.py`](../../src/dcf_model/dcf.py)).

If historical derivation fails (e.g. a company with only one period of statement
data), the model falls back to `DEFAULT_REVENUE_GROWTH_RATE_FALLBACK` (8%) /
`DEFAULT_OPERATING_MARGIN_FALLBACK` (15%), logged as a warning. An explicit
caller-supplied value (e.g. a dashboard slider) always overrides both the
historical derivation and the fallback.

## Free Cash Flow projection

```
Revenue_t     = Revenue_{t-1} * (1 + revenue_growth_rate)
EBIT_t        = Revenue_t * operating_margin
NOPAT_t       = EBIT_t * (1 - tax_rate)
D&A_t         = Revenue_t * da_pct_revenue
CapEx_t       = Revenue_t * capex_pct_revenue
ΔNWC_t        = nwc_pct_revenue_change * (Revenue_t - Revenue_{t-1})
FCF_t         = NOPAT_t + D&A_t - CapEx_t - ΔNWC_t
```

(`project_free_cash_flows`, in [`dcf.py`](../../src/dcf_model/dcf.py)). Revenue
growth and operating margin are held **constant** across the projection window —
there is no glide path or fade toward a terminal growth rate within the explicit
forecast period; the transition happens at the terminal-value step, not gradually
within the projection.

D&A and CapEx are modeled as constant percentages of projected revenue
(`DEFAULT_DA_PCT_REVENUE = 3%`, `DEFAULT_CAPEX_PCT_REVENUE = 4%`). Change in Net
Working Capital is modeled as a percentage of the **change** in revenue
(`DEFAULT_NWC_PCT_REVENUE_CHANGE = 1%`), not of revenue itself — a deliberate
choice documented in the source (`project_free_cash_flows` docstring) to avoid
double-counting NWC as if the business rebuilt its entire working-capital base
from zero every year.

## Terminal value

Gordon Growth (perpetuity growth) method:

```
TV = FCF_n * (1 + g) / (WACC - g)
```

where `FCF_n` is the final explicit projection year's FCF and `g` is
`terminal_growth_rate` (default 2.5%, bounded to
`[MIN_EXPLICIT_TERMINAL_GROWTH_RATE, MAX_EXPLICIT_TERMINAL_GROWTH_RATE]` = `[0%,
5%]`). Raises `ValueError` if `WACC <= g`, since the perpetuity series otherwise
diverges (`calculate_terminal_value`, in [`dcf.py`](../../src/dcf_model/dcf.py)).
Tested at the WACC-equals-terminal-growth boundary by `TestTerminalValueBoundary`
in [`tests/dcf/test_dcf.py`](../../tests/dcf/test_dcf.py).

## Discounting and the Enterprise-to-Equity bridge

```
PV(FCF_t)      = FCF_t / (1 + WACC)^t
PV(TV)         = TV / (1 + WACC)^n
Enterprise Value = sum(PV(FCF_t)) + PV(TV)
Equity Value      = Enterprise Value - Total Debt + Cash & Equivalents
Intrinsic Value / Share = Equity Value / Shares Outstanding
```

(`discount_to_present_value` and `calculate_intrinsic_value_per_share`, both in
[`dcf.py`](../../src/dcf_model/dcf.py)). `shares_outstanding` must be a
positive, present value — raises `ValueError` otherwise. `total_debt` /
`cash_and_equivalents` default to 0 if missing, and a present value must be
non-negative — raises `ValueError` otherwise (Track A Phase 1.5D).

## FCF Yield (market-based cross-check)

Distinct from the DCF-derived Enterprise Value above — this uses the **market-
observed** Enterprise Value (`current_price * shares_outstanding + total_debt -
cash_and_equivalents`) to compute:

```
FCF Yield = (Operating Cash Flow - |CapEx|) / Market Enterprise Value
```

(`calculate_fcf_yield`, in [`dcf.py`](../../src/dcf_model/dcf.py); yfinance
reports CapEx as a negative outflow, so it is added rather than subtracted — see
the inline comment). Returns `None` if any input is unavailable or the market EV
is not positive. This feeds the Conviction Score blend in
[`docs/model-specifications/conviction-and-portfolio-rules.md`](conviction-and-portfolio-rules.md),
not the DCF pipeline itself.

## Inputs

| Name | Units | Source | Notes |
|---|---|---|---|
| `income_statement`, `balance_sheet`, `cash_flow` | pandas DataFrame | yfinance, via `src/data_ingestion/fetch_financials.py` | Rows = line items, columns = fiscal periods |
| `current_price` | USD/share | yfinance `fast_info`/`info` | Required; must be strictly positive |
| `shares_outstanding` | shares | yfinance `fast_info`/`info` | Required, must be strictly positive |
| `beta` | unitless (levered equity beta) | yfinance `info["beta"]` | Defaults to `DEFAULT_BETA = 1.0` only if genuinely missing (`None`); a present but non-finite/bool/non-numeric value raises `ValueError` instead of defaulting (Track A Phase 1.5C). No sign bound — a negative beta is economically legitimate. |
| `revenue_growth_rate` | decimal, annual | historical CAGR or caller override | `None` = derive from historicals |
| `operating_margin` | decimal | historical average or caller override | `None` = derive from historicals |
| `terminal_growth_rate` | decimal, annual | caller (default 2.5%) | Always explicit, no historical mode |
| `tax_rate` | decimal, [0, 1) | derived from Pretax Income / Tax Provision, or caller override, or `DEFAULT_TAX_RATE = 21%` | A present value outside `[0, 1)` raises `ValueError` (Track A Phase 1.5D); only a genuinely missing (`None`) tax rate falls back to the default |
| `risk_free_rate` | decimal, annual | caller; live callers pass `src/utils/macro.py`'s 10Y Treasury yield | Default constant `DEFAULT_RISK_FREE_RATE = 4%`; no missing-data fallback on `calculate_wacc` itself. No sign bound — a negative risk-free rate can be legitimate. |
| `market_risk_premium` | decimal | caller (default `DEFAULT_MARKET_RISK_PREMIUM = 5.5%`) | |
| `cost_of_debt` | decimal | derived (`|interest_expense| / total_debt`) or `DEFAULT_COST_OF_DEBT = 5%` | A present value must be non-negative — raises `ValueError` otherwise (Track A Phase 1.5D) |
| `total_debt` | USD | derived from balance sheet | Missing (`None`) treated as 0; a present value must be non-negative — raises `ValueError` otherwise (Track A Phase 1.5D) |
| `da_pct_revenue`, `capex_pct_revenue`, `nwc_pct_revenue_change` | decimal, % of revenue | caller (defaults 3%, 4%, 1%) | |
| `projection_years` | integer, ≥ 1 | caller (default 5) | |

## Outputs

`run_dcf_valuation` returns a dict with: `wacc`, `revenue_growth_rate`,
`operating_margin` (the *actually used* values, not the raw request params —
important when the caller passed `None`), `fcf_projection` (DataFrame),
`terminal_value`, `pv_fcf`, `pv_terminal_value`, `enterprise_value`,
`equity_value`, `intrinsic_value_per_share`, `current_market_price`,
`market_enterprise_value`, `fcf_yield`.

## Valid ranges and numerical safeguards

| Assumption | Bound (explicit override only) | Enforced in |
|---|---|---|
| `revenue_growth_rate` | `[-10%, 40%]` | `DCFAssumptions.__post_init__`, in [`dcf.py`](../../src/dcf_model/dcf.py) |
| `operating_margin` | `[0%, 60%]` | same |
| `terminal_growth_rate` | `[0%, 5%]` (always, not just explicit) | same |
| Historically-derived revenue growth | capped at 25% (`MAX_REVENUE_GROWTH_RATE`), unbounded below | `extract_valuation_inputs` |
| WACC | clamped to `[5%, 20%]` | `calculate_wacc` |
| `tax_rate` | `[0, 1)` — a present out-of-range value now raises `ValueError` (Track A Phase 1.5D); only genuinely missing (`None`) falls back to the default | validated at construction and in `calculate_wacc` |
| `current_price`, `shares_outstanding` (in `calculate_wacc` / `calculate_enterprise_value`) | finite, non-bool, strictly `> 0` | `calculate_wacc` raises `ValueError`; `calculate_enterprise_value` degrades to `None` (Track A Phase 1.5D — both now enforce this directly, not just via `_validate_capital_inputs`'s upstream non-negative pre-check) |
| `total_debt`, `cost_of_debt` (in `calculate_wacc`); `total_debt`, `cash_and_equivalents` (in `calculate_intrinsic_value_per_share` / `calculate_enterprise_value`) | finite, non-bool, `>= 0` when present (`None` still uses the documented zero/default fallback) | Track A Phase 1.5D — raises `ValueError` in the two raising functions, degrades to `None` in `calculate_enterprise_value` |
| `current_price`, `shares_outstanding`, `total_debt`, `cash_and_equivalents` (orchestration-path pre-check) | finite, non-bool, non-negative | `_validate_capital_inputs` — a present value is validated via `_require_finite_numeric` (rejecting a `bool`, a non-numeric type, or NaN/infinity, with a clean `ValueError` rather than a raw `TypeError`); a Python `int`, however large, is recognized as finite by definition (arbitrary precision — see "Astronomically large integers" below) and is not rejected on magnitude alone here; `None` is left alone (a separate, already-handled "unavailable" case elsewhere in the pipeline). This is a pipeline-level pre-check — the direct public functions above independently enforce their own, sometimes stricter (e.g. strictly-positive price/shares), sign rules, so calling them directly is still safe. |
| `base_revenue` (in `project_free_cash_flows`) | finite, non-bool, `> 0` | `project_free_cash_flows` — rejects `inf`/`nan`/`bool`/non-numeric types, not just non-positive values; a prior gap let `base_revenue=inf` silently produce a DataFrame full of `inf`/`NaN`, and separately let `base_revenue=True` be silently projected as if it were `1` |
| `revenue_growth_rate`, `operating_margin`, `tax_rate`, `da_pct_revenue`, `capex_pct_revenue`, `nwc_pct_revenue_change` (in `project_free_cash_flows`) | finite, non-bool | `project_free_cash_flows` |
| `years` / `DCFAssumptions.projection_years` | genuine positive whole number (an `int`, or a whole-number `float` like `5.0`) | `project_free_cash_flows` and `DCFAssumptions.__post_init__` both reject a fractional value (e.g. `2.5`) or a `bool` (Python treats `bool` as an `int` subclass) via `_coerce_positive_int` — a fractional `years` previously leaked an internal `range()` `TypeError` instead of a clean `ValueError` |

These bounds mirror the dashboard's own slider ranges
(`frontend/src/app/page.tsx`) specifically so the API cannot be made to accept an
economically meaningless value (e.g. a 5000%-compounded growth assumption) that
the UI itself would never offer — see the `MIN_EXPLICIT_*`/`MAX_EXPLICIT_*`
module-level constants near the top of [`dcf.py`](../../src/dcf_model/dcf.py).

### Astronomically large integers

Python `int` is arbitrary-precision and always finite by definition — it has
no NaN/infinity representation — so `_is_valid_finite_number`/
`_require_finite_numeric` (in [`dcf.py`](../../src/dcf_model/dcf.py))
deliberately treat an `int` like `10**10000` as finite and do **not** call
`math.isfinite` on it (that call would itself raise `OverflowError` while
converting the `int` to a C `double`, defeating the purpose of a "genuinely
non-raising" check). This means such a value passes input-level validation.
Downstream arithmetic that combines it with a `float` (e.g.
`current_price * shares_outstanding`) can still overflow — every function
that does this arithmetic wraps it in `try/except (ArithmeticError,
OverflowError)` and re-raises (or, for graceful-degradation functions,
returns `None`) rather than leaking the raw `OverflowError`. This is
distinct from — and layered on top of — the ordinary finite-result checks
that catch pure-`float` overflow (which silently saturates to `inf` without
raising at all).

## Missing-data behavior

- Missing `beta` → default 1.0 (logged warning). A *present* non-finite/bool/
  non-numeric value raises `ValueError` instead — the fallback applies only to
  genuinely missing (`None`) data (Track A Phase 1.5C).
- Missing `cost_of_debt` → default 5% (logged warning). A present negative or
  malformed value raises `ValueError` instead.
- Missing `tax_rate` (neither derivable nor explicit) → default 21% (logged
  warning). A present value outside `[0, 1)` — including a well-typed, finite
  one like `-0.2` or `1.5` — raises `ValueError` instead of falling back
  (Track A Phase 1.5D).
- Missing `total_debt` / `cash_and_equivalents` → treated as 0. A present
  negative value raises `ValueError` (`calculate_wacc`,
  `calculate_intrinsic_value_per_share`) or degrades to `None`
  (`calculate_enterprise_value`) instead of being silently accepted.
- Missing base revenue → `ValueError` (cannot run DCF without it).
- Missing, zero, or negative `current_price` or `shares_outstanding` →
  `ValueError` in `calculate_wacc`/`calculate_intrinsic_value_per_share`, or
  `None` in `calculate_enterprise_value` (market cap cannot be meaningfully
  computed without a genuine positive price/share count).
- A statement line item tried under multiple candidate labels (yfinance labels the
  same line item differently across versions/tickers, e.g. `"Total Revenue"` vs.
  `"TotalRevenue"`) — `_get_row_value`, in [`dcf.py`](../../src/dcf_model/dcf.py),
  tries each in priority order and returns the first match.

## Failure behavior

Every input-level failure raises `ValueError` with a specific message (not a
generic exception) — no silent fallback to a meaningless valuation. `_get_row_value`
never raises for a missing/unparseable individual cell; it returns `None` and lets
the caller decide (fallback or hard failure) at the appropriate level. See
`TestMissingData` and `TestCapitalInputValidation` in
[`tests/dcf/test_dcf.py`](../../tests/dcf/test_dcf.py).

Numeric boundary validation is deliberately layered: `DCFAssumptions.__post_init__`
validates every publicly-settable assumption (including `projection_years`) at
construction time, and `project_free_cash_flows` independently re-validates
`base_revenue`, `years`, and every rate/percentage parameter, since it can be
called directly without going through `DCFAssumptions` at all. Both layers reject
non-finite values (NaN/infinity) and, for whole-number fields, a fractional value
or a `bool` — see `TestNumericBoundaryHardening` in
[`tests/dcf/test_dcf.py`](../../tests/dcf/test_dcf.py).

**Booleans and non-numeric values (strings, `None` where prohibited, lists, etc.)
are rejected with a clean, documented `ValueError` at every public DCF boundary**
— never a raw `TypeError`/`AttributeError`/`OverflowError` leaked from a bare
comparison or from `math.isfinite` on a non-numeric type or an astronomically
large `int`, and never a `bool` silently accepted as `0`/`1` because Python treats
`bool` as an `int` subclass (the reproduced defects:
`project_free_cash_flows(base_revenue=True, ...)` was previously accepted and
projected as if `base_revenue` were `1`; `DCFAssumptions(risk_free_rate=True)` was
previously accepted as a 100% risk-free rate). This is enforced via shared
internal helpers, `_require_finite_numeric`/`_require_finite_numeric_or_none`/
`_is_valid_finite_number` ([`dcf.py`](../../src/dcf_model/dcf.py)), applied at
every one of the following boundaries.

### The precise missing-versus-malformed policy (Track A Phase 1.5C / 1.5D)

**Missing data (`None`) may invoke an existing, explicitly documented fallback.
Malformed PRESENT data (a `bool`, a non-numeric type, a non-finite value, or —
as of Track A Phase 1.5D — a present value that is finite and well-typed but
economically invalid on sign or range, e.g. a negative debt balance or a
tax rate outside `[0, 1)`) never silently becomes that same fallback or a
plausible-looking default — it is rejected or causes graceful refusal, per
each function's own established contract.** An earlier hardening pass
conflated these two cases for several fields (treating a malformed-but-present
value the same as a missing one); Phase 1.5C closed that gap for type/
finiteness. Phase 1.5D closes the remaining gap for sign/range: a present
value that is technically a finite, correctly-typed number can still be
economically nonsensical (negative debt, a >100% tax rate, a zero share
count), and is now rejected the same way rather than silently accepted.

| Function | Fields | Missing (`None`) | Present but malformed / economically invalid |
|---|---|---|---|
| `project_free_cash_flows` | `base_revenue`, `revenue_growth_rate`, `operating_margin`, `tax_rate`, `da_pct_revenue`, `capex_pct_revenue`, `nwc_pct_revenue_change`, `years` | Not accepted — all required | Raises `ValueError` |
| `DCFAssumptions.__post_init__` | `revenue_growth_rate`, `operating_margin`, `tax_rate` | `None` = "derive from historicals" (preserved) | Raises `ValueError` at construction time |
| `DCFAssumptions.__post_init__` | `risk_free_rate`, `market_risk_premium`, `da_pct_revenue`, `capex_pct_revenue`, `nwc_pct_revenue_change`, `projection_years`, `terminal_growth_rate` | Not accepted — all required (never legitimately `None`) | Raises `ValueError` at construction time |
| `calculate_wacc` | `current_price`, `shares_outstanding` | Not accepted — required | Raises `ValueError` — including a present value that is `<= 0` (Phase 1.5D; a zero/negative price or share count is economically meaningless, not just a type problem) |
| `calculate_wacc` | `risk_free_rate`, `market_risk_premium` | **No missing-data fallback on this function** — raises `ValueError` the same as a malformed value | Raises `ValueError` for type/finiteness only — **no sign bound**; a negative risk-free rate or premium is not rejected |
| `calculate_wacc` | `beta` | Falls back to `DEFAULT_BETA = 1.0`, logged as a warning (unchanged) | Raises `ValueError` for type/finiteness — **no sign bound**; a negative beta is not rejected |
| `calculate_wacc` | `cost_of_debt`, `total_debt` | Falls back to the documented default/zero, logged as a warning (unchanged) | Raises `ValueError` for type/finiteness, **and now (Phase 1.5D) for a present negative value too** — e.g. `total_debt=True` previously slipped past `total_debt or 0.0`'s truthiness check and was silently added to market cap as if it were `1`; `total_debt=-500` is now rejected the same way a malformed type is, since negative debt is economically nonsensical |
| `calculate_wacc` | `tax_rate` | Falls back to `DEFAULT_TAX_RATE = 21%`, logged as a warning (unchanged) | Raises `ValueError` for type/finiteness, **and (Phase 1.5D, superseding Phase 1.5C) for any present value outside `[0, 1)`** — e.g. `tax_rate=1.5` previously fell back to the 21% default; it now raises instead, since a >100%/negative tax rate is not a "missing data" case |
| `calculate_terminal_value` | `final_year_fcf`, `wacc`, `terminal_growth_rate` | Not accepted — all required | Raises `ValueError` |
| `discount_to_present_value` | `terminal_value`, `wacc` | Not accepted — required | Raises `ValueError` |
| `discount_to_present_value` | `fcf_projection` (DataFrame boundary) | Not accepted — required | Raises `ValueError` if not a non-empty DataFrame, missing the `"fcf"` column, or containing a non-finite/malformed index or `"fcf"` value |
| `calculate_intrinsic_value_per_share` | `enterprise_value` | Not accepted — required | Raises `ValueError` for type/finiteness only — **no sign bound**; a negative enterprise value (a legitimate cash-rich-company case) is not rejected |
| `calculate_intrinsic_value_per_share` | `shares_outstanding` | Not accepted — required | Raises `ValueError` — including a present value that is `<= 0` |
| `calculate_intrinsic_value_per_share` | `total_debt`, `cash_and_equivalents` | Treated as `0.0` (unchanged) | Raises `ValueError` for type/finiteness, **and (Phase 1.5D) for a present negative value too** |
| `calculate_enterprise_value` | `current_price`, `shares_outstanding` | Returns `None` (this function's established graceful-degradation contract; never raises) | Returns `None` — including a present value that is `<= 0` (Phase 1.5D); a malformed or non-positive value is refused the same way a missing one is, NOT silently treated as `0`/priced as `$1` |
| `calculate_enterprise_value` | `total_debt`, `cash_and_equivalents` | Treated as `0.0` (unchanged) | Returns `None` for type/finiteness, **and (Phase 1.5D) for a present negative value too**. The *final computed* Enterprise Value is deliberately NOT sign-checked — a cash-rich company can legitimately have a negative market EV, and only malformed/nonfinite arithmetic is rejected there. |
| `calculate_fcf_yield` | `operating_cash_flow`, `capital_expenditures`, `enterprise_value` | Returns `None` (established graceful-degradation contract; never raises) | Returns `None` |

Additionally, **every function above now rejects a non-finite ARITHMETIC RESULT**,
not just a non-finite input — e.g. two individually-valid, finite inputs whose
product or quotient overflows `float` range (`1e308 * 1e300`), or a technically-
finite-by-definition Python `int` (arbitrary precision, e.g. `10**10000`) that
overflows the moment it's combined with a `float`. Raising functions surface this
as their own `ValueError` (never a raw `OverflowError`); graceful-degradation
functions (`calculate_enterprise_value`, `calculate_fcf_yield`) return `None`.
`discount_to_present_value` additionally scopes a narrow `numpy.errstate`
context around its vectorized discounting arithmetic (Track A Phase 1.5D) so
that a numpy-level floating-point overflow/invalid/divide-by-zero condition
raises `FloatingPointError` (caught by the same `except (ArithmeticError,
OverflowError)` clause) instead of emitting an unhandled `RuntimeWarning` —
this changes only how the deliberate-overflow case is *reported*, not the
resulting `ValueError` it was already raising. **No public DCF function
returns NaN or infinity as if it were a legitimate valuation result.**

See `TestBooleanAndNonnumericRejectedAtProjectFCF`,
`TestBooleanAndNonnumericRejectedAtDCFAssumptions`,
`TestCalculateWaccBoundaryHardening`, `TestCalculateTerminalValueBoundaryHardening`,
`TestDiscountToPresentValueBoundaryHardening`,
`TestCalculateIntrinsicValuePerShareBoundaryHardening`,
`TestCalculateEnterpriseValueBoundaryHardening`,
`TestCalculateFcfYieldBoundaryHardening`, `TestCapitalInputValidation`, and
`TestNumericBoundaryHardening` in
[`tests/dcf/test_dcf.py`](../../tests/dcf/test_dcf.py) for the full adversarial
matrix (`True`/`False`, a numeric-looking string, an arbitrary string, `None`
where prohibited, NaN, +/-infinity, and an astronomically large Python `int`)
exercised against every field above, plus dedicated `test_none_*_still_falls_back_*`
cases proving the genuine missing-data contracts are entirely unaffected, and
(Phase 1.5D) dedicated sign/range-invariant tests within
`TestCalculateWaccBoundaryHardening`, `TestCalculateIntrinsicValuePerShareBoundaryHardening`,
and `TestCalculateEnterpriseValueBoundaryHardening`.

## Timing / point-in-time assumptions

`run_dcf_valuation` itself has no notion of "as of" — it operates on whatever
statement data it is handed. Point-in-time correctness (restricting statement
columns to periods that would plausibly have been publicly filed by a target date)
is the caller's responsibility, implemented in
[`src/backtesting/historical_tester.py`](../../src/backtesting/historical_tester.py)
(`_columns_on_or_before`) — see
[`docs/model-specifications/backtesting.md`](backtesting.md).

## Known simplifications

- No glide path: growth/margin are constant within the explicit forecast window,
  not fading toward the terminal growth rate.
- D&A, CapEx, and NWC-change are modeled as simple percentages of revenue/Δrevenue,
  not derived from the company's own historical D&A/CapEx/NWC ratios the way
  revenue growth and operating margin are.
- Single-stage terminal value (Gordon Growth) — no two-stage or fade-based terminal
  value alternative is implemented.
- Tax rate is a single constant applied uniformly across the projection window, not
  varied by projected income level or jurisdiction.
- No explicit modeling of share buybacks/dilution within the projection window
  beyond whatever `shares_outstanding` is at valuation time.

## Test coverage

`tests/dcf/test_dcf.py`: **135 test functions across 16 test classes; 442
pytest-collected cases** once parametrization is expanded (counted directly
from the file via `grep -c "    def test_"` / `grep -c "^class Test"` and
`pytest --collect-only`, respectively — verify against the current file
rather than trusting this figure indefinitely, since it will drift again as
tests are added). Classes: `TestTaxRateOverridePrecedence`,
`TestNWCCalculation`, `TestTerminalValueBoundary`, `TestAssumptionValidation`,
`TestExplicitAssumptionEconomicBounds`, `TestCapitalInputValidation`,
`TestMissingData`, `TestNumericBoundaryHardening` (adversarial `base_revenue`/
`years`/rate-field inputs — non-finite, fractional, bool, plus (Track A Phase
1.5C) arithmetic-overflow and astronomically-large-`int` cases), plus (Track A
Phase 1.5B/1.5C) `TestBooleanAndNonnumericRejectedAtProjectFCF`,
`TestBooleanAndNonnumericRejectedAtDCFAssumptions`,
`TestCalculateWaccBoundaryHardening` (dedicated
`test_none_*_still_falls_back_*` / `test_malformed_present_*_raises` pairs
proving the missing-vs-malformed distinction, overflow cases, and — Track A
Phase 1.5D — dedicated sign/range-invariant tests for non-positive
price/shares, negative debt/cost-of-debt, and out-of-range tax rate),
`TestCalculateTerminalValueBoundaryHardening`,
`TestDiscountToPresentValueBoundaryHardening` (DataFrame-shape validation,
arithmetic-overflow cases, and — Track A Phase 1.5D — a dedicated test
proving the deliberate-overflow case raises `ValueError` without an unhandled
`RuntimeWarning`), `TestCalculateIntrinsicValuePerShareBoundaryHardening`
(plus Phase 1.5D negative-debt/cash tests), `TestCalculateEnterpriseValueBoundaryHardening`
(plus Phase 1.5D non-positive-price/shares, negative-debt/cash, and
legitimate-negative-EV tests), `TestCalculateFcfYieldBoundaryHardening` (the
full boolean/nonnumeric/non-finite adversarial matrix, parametrized across
every hardened field, plus the missing-vs-malformed distinction and
arithmetic-overflow/huge-`int` cases). This covers input validation,
boundary conditions, and override precedence; it does not (and does not claim to)
independently verify that the DCF's numerical output matches a second,
independently-built calculation for a real company — that is the explicit purpose
of [`docs/independent-validation-plan.md`](../independent-validation-plan.md),
which has not yet been executed.
