# Model Specification: WACC / CAPM

Source: `calculate_wacc`, in [`src/dcf_model/dcf.py`](../../src/dcf_model/dcf.py)
Tests: `TestCalculateWaccBoundaryHardening` in
[`tests/dcf/test_dcf.py`](../../tests/dcf/test_dcf.py) covers numeric/sign
boundary hardening; there is no separate `TestWACC`/`TestCAPM` class for
formula correctness — see "Test coverage" below for what is and isn't
independently verified.
Consumers: every DCF run (`run_dcf_valuation`); the discount-rate leg is fed a live
risk-free rate by [`src/backtesting/historical_tester.py`](../../src/backtesting/historical_tester.py)
and [`src/api/main.py`](../../src/api/main.py) via
[`src/utils/macro.py`](../../src/utils/macro.py)

## Objective

Estimate the Weighted Average Cost of Capital (WACC) — the discount rate applied to
projected Free Cash Flow and terminal value in the DCF — from a company's Capital
Asset Pricing Model (CAPM) cost of equity, after-tax cost of debt, and market-value
capital-structure weights.

## Formula

```
Cost of Equity (Re)        = risk_free_rate + beta * market_risk_premium        (CAPM)
After-Tax Cost of Debt (Rd) = cost_of_debt * (1 - tax_rate)
Weight of Equity (We)       = Market Cap / (Market Cap + Total Debt)
Weight of Debt (Wd)         = Total Debt / (Market Cap + Total Debt)
WACC                        = We * Re + Wd * Rd
```

Market Cap = `current_price * shares_outstanding`. Total Debt is used as a proxy
for the market value of debt (book value, not a separately estimated market value —
see "Known simplifications").

## Calculation sequence

1. Require `current_price` and `shares_outstanding`, both strictly `> 0` (raises
   `ValueError` if either is missing, non-finite, or not strictly positive —
   market cap cannot be meaningfully computed without them; Track A Phase 1.5D
   added the strictly-positive requirement on top of the pre-existing
   missing/finiteness check).
2. Resolve `beta`: if `None`, default to `DEFAULT_BETA = 1.0` (logged warning);
   if present, must be finite (raises `ValueError` otherwise) — no sign bound,
   a negative beta is not rejected.
3. Resolve `cost_of_debt`: if `None`, default to `DEFAULT_COST_OF_DEBT = 5%`
   (logged warning); if present, must be finite and `>= 0` (raises `ValueError`
   otherwise — Track A Phase 1.5D added the non-negativity check).
4. Resolve `tax_rate`: if `None`, default to `DEFAULT_TAX_RATE = 21%` (logged
   warning); if present, must be finite and in `[0, 1)` — raises `ValueError`
   otherwise (Track A Phase 1.5D: an out-of-range present value used to fall
   back to the default; it now raises, since it is a present, economically
   invalid value, not a missing one).
5. Resolve `total_debt`: if `None`, treated as `0.0`; if present, must be
   finite and `>= 0` (raises `ValueError` otherwise — Track A Phase 1.5D added
   the non-negativity check).
6. Compute market cap and total capital; raise `ValueError` if total capital
   (`market_cap + total_debt`) is not positive.
7. Compute capital weights, cost of equity (CAPM), after-tax cost of debt, and the
   weighted sum, verifying each intermediate result is finite.
8. Clamp the result to `[MIN_DISCOUNT_RATE, MAX_DISCOUNT_RATE]` = `[5%, 20%]`.

## Inputs and units

| Input | Units | Source | Default if MISSING (`None`) | Sign/range constraint on a PRESENT value |
|---|---|---|---|---|
| `current_price` | USD/share | yfinance | None — raises `ValueError` (no missing-data fallback exists for this field) | Must be strictly `> 0` (Track A Phase 1.5D) |
| `shares_outstanding` | shares | yfinance | None — raises `ValueError` (no missing-data fallback exists for this field) | Must be strictly `> 0` (Track A Phase 1.5D) |
| `risk_free_rate` | decimal, annual | caller: live 10Y Treasury yield (`src/utils/macro.py`) for live/backtest callers; static `DEFAULT_RISK_FREE_RATE = 4%` otherwise | **No missing-data fallback on this function** — `None` raises `ValueError`, same as any other invalid value (see "Missing vs. malformed" below) | None — a negative risk-free rate (e.g. certain sovereign yields) is not rejected |
| `market_risk_premium` | decimal | caller (default `DEFAULT_MARKET_RISK_PREMIUM = 5.5%`) | **No missing-data fallback on this function** — `None` raises `ValueError`, same as any other invalid value | None |
| `total_debt` | USD | yfinance balance sheet (`Total Debt`) | `0.0` | Must be `>= 0` (Track A Phase 1.5D) |
| `beta` | unitless (levered equity beta) | yfinance `info["beta"]` | `1.0` | None — a negative beta is not rejected |
| `cost_of_debt` | decimal, annual | derived as `\|interest_expense\| / total_debt` from the income statement, or caller override | `5%` | Must be `>= 0` (Track A Phase 1.5D) |
| `tax_rate` | decimal, `[0, 1)` | derived as `tax_provision / pretax_income` (rejected if outside `[0, 1)`), or caller override | `21%` | Must be in `[0, 1)` — raises `ValueError` otherwise (Track A Phase 1.5D; previously fell back to the default) |

### Missing vs. malformed — the precise policy (Track A Phase 1.5C / 1.5D)

An earlier hardening pass correctly rejected `bool`/non-numeric/non-finite values
at this function's boundary, but incorrectly let `beta`/`cost_of_debt`/`tax_rate`/
`total_debt` silently fall back to their documented default whenever the value was
*malformed*, not just when it was genuinely *missing* (`None`) — e.g.
`total_debt=True` (a `bool`, which Python treats as an `int` subclass) slipped past
a bare `total_debt or 0.0` truthiness check and was silently added to market cap as
if it were `1`. Track A Phase 1.5C corrected the type/finiteness half of this gap.
Track A Phase 1.5D corrects the remaining sign/range half: a *present* value can
be a genuine, well-typed, finite number and still be economically invalid — a
negative debt balance, a negative cost of debt, a non-positive price or share
count, or a tax rate outside `[0, 1)` — and that is now rejected too, not
silently accepted just because it passed the type/finiteness check. This is now
corrected to the precise policy every function in this document follows:

- **`current_price` / `shares_outstanding`** — always required; missing (`None`),
  malformed (`bool`, non-numeric, non-finite), OR present-but-non-positive
  (`<= 0`, Track A Phase 1.5D) all raise `ValueError`. No missing-data fallback
  exists for these two fields at all.
- **`risk_free_rate` / `market_risk_premium`** — these two also have **no
  missing-data fallback on this function** (their module-level defaults apply only
  when the caller omits the keyword argument entirely, via the parameter's own
  Python default value — an explicitly passed `None` is a distinct case with no
  established meaning here). Missing (`None`, if explicitly passed) OR malformed
  both raise `ValueError`. **No sign bound** — a negative risk-free rate or a
  negative market risk premium is not rejected on sign alone.
- **`beta`** — has an established "fall back to `DEFAULT_BETA = 1.0`, logged as a
  warning" contract, but ONLY for genuinely missing (`None`) data. A value that is
  *present* but malformed (`bool`, non-numeric type, or non-finite) raises
  `ValueError` instead of silently falling back. **No sign bound** — a negative
  beta is economically legitimate and is not rejected.
- **`cost_of_debt` / `total_debt`** — each has an established "fall back to a
  documented default/zero, logged as a warning" contract, but ONLY for genuinely
  missing (`None`) data. A value that is *present* but malformed OR present and
  negative (Track A Phase 1.5D) now raises `ValueError` instead of silently
  falling back or being accepted — a negative interest rate or a negative debt
  balance is not a type malformation, but it is still economically nonsensical.
- **`tax_rate`** — has an established "fall back to `DEFAULT_TAX_RATE = 21%`,
  logged as a warning" contract, but ONLY for genuinely missing (`None`) data. A
  value that is *present* and malformed, OR present and outside `[0, 1)`
  (Track A Phase 1.5D, superseding the Phase 1.5C behavior below), now raises
  `ValueError`. **Track A Phase 1.5C had preserved one exception here — a
  well-typed, finite, out-of-range `tax_rate` (e.g. `1.5`) still fell back to the
  default. Track A Phase 1.5D removes that exception**: an out-of-range tax rate
  is economically invalid regardless of its type, and silently substituting the
  21% default for it could produce a plausible-looking WACC from a clearly wrong
  input. It now raises the same as any other invalid present value.

## Live risk-free rate wiring

`calculate_wacc`'s `risk_free_rate` parameter defaults to the static
`DEFAULT_RISK_FREE_RATE` constant, but every production call site overrides it with
a live value from `get_risk_free_rate` in
[`src/utils/macro.py`](../../src/utils/macro.py) — the 10-Year Treasury Note yield
(`^TNX`), most recent close on/before the requested date. This keeps WACC in sync
with the actual macro environment rather than a fixed historical assumption, and is
shared identically by `src/api/main.py` (live), `src/backtesting/historical_tester.py`
(point-in-time, one fetch per whole backtest run — not one per ticker), and
`src/api/sector_medians.py` (cache generation). See the "Risk-free rate" entry in
[`docs/data-dictionary.md`](../data-dictionary.md) for its own missing-data
fallback (`DEFAULT_RISK_FREE_RATE_FALLBACK = 4.2%`, distinct from `dcf.py`'s
`DEFAULT_RISK_FREE_RATE = 4%` — these are two independently-configured constants
that happen to be close; see `L-011` in the limitations register).

## Valid ranges

| Parameter | Range | Enforcement |
|---|---|---|
| Final WACC | `[5%, 20%]` | Hard clamp — a degenerate beta/rate combination cannot produce an unclamped discount rate |
| `tax_rate` | `[0, 1)` | Rejected — raises `ValueError` — both when derived and when explicit (Track A Phase 1.5D; a genuinely missing `tax_rate`, i.e. `None`, still falls back to the 21% default) |
| `current_price`, `shares_outstanding` | must be strictly `> 0` | Raises `ValueError` otherwise (Track A Phase 1.5D) |
| `cost_of_debt`, `total_debt` | must be `>= 0` when present | Raises `ValueError` otherwise (Track A Phase 1.5D); `None` still falls back to the documented default/zero |
| `beta` | must be finite when present; no sign bound | `None` → falls back to `1.0`; present non-finite → raises `ValueError`; a negative value is not rejected |
| Total capital | must be `> 0` | Raises `ValueError` otherwise |

## Missing-data / failure behavior

| Condition | Behavior |
|---|---|
| `current_price` or `shares_outstanding` missing, a `bool`, a non-numeric type, non-finite, or `<= 0` | `ValueError` — fatal, market cap cannot be meaningfully computed, and there is no fallback for either field (the `<= 0` case was added in Track A Phase 1.5D) |
| `risk_free_rate` or `market_risk_premium` missing (`None`), a `bool`, a non-numeric type, or non-finite | `ValueError` — no missing-data fallback exists for either field on this function. No sign bound — a negative value is not rejected. |
| `beta` missing (`None`) | Falls back to `1.0`, logged as a warning |
| `beta` PRESENT but a `bool`, a non-numeric type, or non-finite | `ValueError` — a malformed present value is a data-integrity problem, not an absence. No sign bound — a negative beta is not rejected. |
| `cost_of_debt` missing (`None`) | Falls back to `5%`, logged as a warning |
| `cost_of_debt` PRESENT but malformed, or PRESENT and negative | `ValueError` (negative case added in Track A Phase 1.5D) |
| `tax_rate` missing (`None`) | Falls back to `21%`, logged as a warning |
| `tax_rate` PRESENT, well-typed, finite, but outside `[0, 1)` | `ValueError` (Track A Phase 1.5D — this used to fall back to `21%`, logged as a warning; it now raises, since a present out-of-range tax rate is an economically invalid value, not a missing one) |
| `tax_rate` PRESENT but a `bool`, a non-numeric type, or non-finite | `ValueError` |
| `total_debt` missing (`None`) | Treated as `0.0` (equivalent to an all-equity capital structure) |
| `total_debt` PRESENT but malformed, or PRESENT and negative | `ValueError` (negative case added in Track A Phase 1.5D) |
| Any intermediate quantity (`market_cap`, `total_capital`, `cost_of_equity`, `after_tax_cost_of_debt`, or the final `wacc`) is not a finite number | `ValueError` — e.g. from an arithmetic overflow combining a technically-finite-but-astronomically-large `int` (Python integers are arbitrary-precision and always finite by definition, but multiplying/adding one with a `float` can still overflow the `float` side of that specific operation) with an ordinary value. This function never returns or clamps a non-finite result into an apparently-valid WACC. |
| Total capital ≤ 0 | `ValueError` |

Every fallback is logged at `WARNING` level, not silently applied — a caller
reading logs can tell when WACC was computed from real company-specific inputs
versus generic defaults. Every raised case surfaces as this function's own clean
`ValueError`, never a raw internal `TypeError`/`OverflowError`.

## Numerical safeguards

- WACC's `[5%, 20%]` clamp prevents an extreme beta (e.g. a data error reporting
  beta = 20) or an extreme risk-free-rate spike from producing an economically
  nonsensical discount rate that would, in turn, produce a nonsensical terminal
  value (`WACC − g` in the denominator of the Gordon Growth formula).
- `tax_rate` derived from statement data is explicitly range-checked
  (`0 <= computed_rate < 1`) before use — a corrupted or unusual Tax Provision /
  Pretax Income ratio (e.g. from a one-time tax item producing a rate > 100% or
  negative) is rejected rather than propagated. `calculate_wacc` itself applies
  the same `[0, 1)` range check to any *explicitly supplied* `tax_rate` and, as
  of Track A Phase 1.5D, raises `ValueError` for a present out-of-range value
  rather than silently substituting the 21% default.
- `current_price`/`shares_outstanding` must be strictly positive, and a present
  `total_debt`/`cost_of_debt` must be non-negative (Track A Phase 1.5D) — a
  zero/negative price or share count, or a negative debt balance or cost of
  debt, is economically meaningless and is rejected rather than silently fed
  into the market-cap/capital-weight arithmetic.
- Every intermediate arithmetic result (`market_cap`, `total_capital`,
  `cost_of_equity`, `after_tax_cost_of_debt`, `wacc` itself) is explicitly checked
  finite immediately after it's computed, and the whole computational core is
  wrapped in a `try`/`except (ArithmeticError, OverflowError)` that re-raises as
  this function's own `ValueError` — covering both the "silent `inf`" failure mode
  (ordinary `float`-only overflow, which doesn't raise) and the "raw
  `OverflowError`" failure mode (converting an astronomically large `int` to
  `float` during a mixed int/float operation, which does raise).

## Known simplifications

- **Single-factor CAPM.** Cost of equity uses only market beta — no
  size, value, momentum, or other multi-factor model (e.g. Fama-French) is
  implemented. See `L-004` in the limitations register.
- **Book value of debt as a market-value proxy.** `total_debt` (book value from the
  balance sheet) stands in for the market value of debt in the capital-weight
  calculation — no separate market-value-of-debt estimation exists.
- **Static levered beta.** Beta is the current value reported by Yahoo Finance,
  used identically for live valuations and for every historical `as_of_date` in a
  backtest (yfinance exposes no historical beta endpoint) — see the
  "current-day-proxy approximations" note in
  [`docs/model-specifications/backtesting.md`](backtesting.md).
- **Constant market risk premium.** `market_risk_premium` is a fixed assumption
  (5.5%), not derived live or varied by market conditions.
- **No explicit preferred equity or other capital-structure component** — only
  common equity and total debt are modeled.

## Test coverage

`calculate_wacc` has a dedicated test class, `TestCalculateWaccBoundaryHardening`
in [`tests/dcf/test_dcf.py`](../../tests/dcf/test_dcf.py) — but its scope is
numeric/sign boundary hardening, not independent CAPM-formula validation (see
below). It verifies the full missing-vs-malformed-vs-invalid-range policy
documented above:

- **Track A Phase 1.5C** (type/finiteness): `current_price`/`shares_outstanding`/
  `risk_free_rate`/`market_risk_premium` raise a clean `ValueError` for `None`, a
  `bool`, a non-numeric type, NaN, or +/-infinity (no fallback exists for any of
  these four); `beta`/`cost_of_debt`/`tax_rate`/`total_debt` fall back to their
  documented default ONLY for genuine `None` (verified with dedicated
  `test_none_*_still_falls_back_*` cases), and raise `ValueError` for the SAME
  adversarial set when the value is *present* (verified with dedicated
  `test_malformed_present_*_raises` cases) — e.g. `total_debt=True` previously
  slipped past a bare `total_debt or 0.0` truthiness check and was silently
  added to market cap as if it were `1`; it now raises instead. Two further
  cases prove the arithmetic-overflow guard: an astronomically large but
  technically-finite `float` `beta` combined with an extreme
  `market_risk_premium`, and a `10**10000` Python `int` `current_price`
  (arbitrary-precision, always finite by definition, but overflows the moment
  it's multiplied against a `float`) — both must raise this function's own
  clean `ValueError`, never a raw `OverflowError`.
- **Track A Phase 1.5D** (sign/range): dedicated tests verify `current_price`/
  `shares_outstanding` reject `0.0` and negative values; `total_debt` and
  `cost_of_debt` reject a negative present value; a present `tax_rate` outside
  `[0, 1)` (e.g. `-0.2`, `1.5`, `1.0`) now raises `ValueError` instead of
  falling back — replacing the earlier `test_out_of_range_but_genuinely_numeric_tax_rate_still_falls_back`
  test, which asserted the opposite (now-superseded) behavior; and dedicated
  tests confirm a negative `beta` and a negative `risk_free_rate` are each
  accepted, not rejected on sign alone.

WACC is also still exercised indirectly wherever a full `run_dcf_valuation` call
is tested (e.g. `TestTaxRateOverridePrecedence`, which asserts an explicit
`tax_rate` correctly flows through into the WACC calculation's after-tax cost of
debt). The `[5%, 20%]` clamp's own behavior and the CAPM formula's numerical
correctness itself — i.e. independently verifying the WACC this function computes
against a second, independently-built calculation for a real company — are still
**not** independently unit-tested in isolation as of this writing; boundary/sign
validation correctness (what this test class covers) is a distinct question from
formula correctness (what it does not cover). This remains recorded as a
(partial, not total) test-coverage gap — see `L-013` in the limitations register,
and [`docs/independent-validation-plan.md`](../independent-validation-plan.md)
for the plan to close it.
