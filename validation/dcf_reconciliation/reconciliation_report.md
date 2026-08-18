# DCF Codebase-to-Independent-Workbook Reconciliation Report

Track A Phase 2C. Compares `src/dcf_model/dcf.py`'s production output against the independent workbook **V2** (`validation/independent_dcf/independent_dcf_validation_v2.xlsx`), built from the `years_elapsed` specification clarified after Track A Phase 2B's original reconciliation attempt found a genuine, tolerance-exceeding discrepancy against workbook **V1**. V1 (`independent_dcf_validation.xlsx`) is preserved unchanged; its original failing evidence is archived at `validation/dcf_reconciliation/history/phase2b_initial_no_go/` and was never edited. Neither calculation path was modified to force agreement; this report records what was actually found.

- **Branch**: `remediation/valuation-engine-production-quality`
- **Commit**: `776e76f56e6210f1050caa74c60962ed89162496`
- **V1 workbook SHA-256** (preserved, historical): `942b5f096cf546d63487add31eb29b1b690f0819f923f01436093f9bbc75eba1`
- **V2 workbook SHA-256** (reconciliation target): `cd2d225cfe0825ff56aa8f866c3b80b6bec5f87a8b02dc3f76724e4abdd27d6b`
- **Frozen snapshot SHA-256**:
  - `MSFT`: `7c50a3e9fb2d27095a890ad8d11732d4ccc72ef9350998604adb3af7a3c845a8`
  - `CAT`: `651d366c01415c2da33733058dabb9ac6e4f40cdf5b20e743c0b83ebc3a52c93`
  - `INTC`: `d73e4b40bce2442a75affde82aaa563ffdea9c68c7257e70cc325663cff24930`
  - `VZ`: `77b8ea27b8decaff5fe3b4aad857c632a852445eda0b7f2342f594cd3c4b5f3d`

## Overall verdict: GO

- Base-case reconciliation (vs. V2): all pass
- Sensitivity reconciliation (vs. V2): all pass

**Second-reviewer sign-off is PENDING** -- this report alone does not close the checklist in `docs/independent-validation-plan.md`; see the Sign-off section of `dcf_reconciliation.xlsx`.
**Profitability is NOT established by this reconciliation** -- it verifies that two independent calculation paths agree (or documents where/why they don't), not that any company is investable.

## What changed since Phase 2B, and why production needed no change

Phase 2B's original NO-GO was caused by INTC's Revenue CAGR: V1's `DCF_INTC!B5` ("Years elapsed") computed `years_elapsed` as `COUNT(periods)-1` (a plain period count = 4), while `src/dcf_model/dcf.py`'s `calculate_historical_revenue_cagr` computed the ACTUAL elapsed calendar days between the earliest and latest frozen fiscal-period-end dates, divided by 365.25 (= 4.005476 for INTC, whose fiscal year-end dates are not evenly spaced -- 1,463 actual days, not the 1,461 that 4 years at 365.25 days/year would be). `docs/model-specifications/dcf.md`'s prose named `years_elapsed` without specifying its exact day-count convention -- a genuine specification-precision gap, not a numerical bug in either calculation path.

Track A Phase 2C resolved the ambiguity by clarifying the specification (see `A-028` in the assumptions register and `L-019` in the limitations register): `years_elapsed` is now defined as actual elapsed calendar days between the earliest- and latest-dated valid observations, divided by 365.25 -- never a plain period count. **Production code required no change**: `calculate_historical_revenue_cagr`'s existing implementation already computed `years_elapsed` this way; the ambiguity was in the written specification's prose, not in `dcf.py`. A second independent workbook (V2) was built from the corrected specification, blind to the codebase, with a new explicit diagnostic row (in every `DCF_<TICKER>` sheet) that computes and displays the naive `(N periods - 1)` count alongside the actual `years_elapsed`, flagging any company whose fiscal calendar is irregular enough for the two to diverge -- INTC is the only company flagged. **V1's `COUNT-1` convention is not described as having conformed to the now-clarified specification** -- it was a reasonable, independent resolution of a genuine ambiguity that the clarification has since closed, not a defect that was 'fixed' by editing V1 (V1 is preserved unchanged).

## Base-case reconciliation, by company (vs. V2)

### MSFT (Microsoft Corporation)

All 14 required metrics within documented tolerance against V2.

| Metric | Codebase | Workbook (V2) | Abs. Diff | Diff | Tolerance | Result | Workbook Cell |
|---|---|---|---|---|---|---|---|
| Historical Revenue CAGR | 13.741147% | 13.741147% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_MSFT!B9` |
| Historical average Operating Margin | 44.175047% | 44.175047% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_MSFT!B10` |
| WACC (final, clamped [5%,20%]) | 10.000422% | 10.000422% | 0.000000 | 0.0000pp | ±0.0500pp | PASS | `DCF_MSFT!B21` |
| FCF Year 1 | 130,162,813,492.1701 | 130,162,813,492.1701 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_MSFT!C31` |
| FCF Year 2 | 148,048,677,492.9603 | 148,048,677,492.9603 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_MSFT!D31` |
| FCF Year 3 | 168,392,264,421.4593 | 168,392,264,421.4593 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_MSFT!E31` |
| FCF Year 4 | 191,531,293,606.6921 | 191,531,293,606.6921 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_MSFT!F31` |
| FCF Year 5 | 217,849,890,888.3251 | 217,849,890,888.3251 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_MSFT!G31` |
| Terminal Value | 2,977,114,493,450.9736 | 2,977,114,493,450.9736 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_MSFT!B34` |
| PV of Explicit FCF (sum of pv_fcf series) | 633,278,359,490.3066 | 633,278,359,490.3066 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_MSFT!B39` |
| PV of Terminal Value | 1,848,518,446,589.7981 | 1,848,518,446,589.7981 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_MSFT!B40` |
| Enterprise Value | 2,481,796,806,080.1045 | 2,481,796,806,080.1045 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_MSFT!B41` |
| Equity Value | 2,462,437,806,080.1045 | 2,462,437,806,080.1045 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_MSFT!B42` |
| Intrinsic Value per Share | 331.6171 | 331.6171 | 0.000000 | 0.0000% | ±0.5000% | PASS | `DCF_MSFT!B43` |

_Diagnostic only (no codebase field, not part of REQUIRED_METRICS): Raw (pre-clamp) WACC = `10.000422%` at `DCF_MSFT!B20`._

### CAT (Caterpillar Inc.)

All 14 required metrics within documented tolerance against V2.

| Metric | Codebase | Workbook (V2) | Abs. Diff | Diff | Tolerance | Result | Workbook Cell |
|---|---|---|---|---|---|---|---|
| Historical Revenue CAGR | 7.309511% | 7.309511% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_CAT!B9` |
| Historical average Operating Margin | 16.559502% | 16.559502% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_CAT!B10` |
| WACC (final, clamped [5%,20%]) | 12.095875% | 12.095875% | 0.000000 | 0.0000pp | ±0.0500pp | PASS | `DCF_CAT!B21` |
| FCF Year 1 | 8,355,205,000.0888 | 8,355,205,000.0888 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_CAT!C31` |
| FCF Year 2 | 8,965,929,646.9650 | 8,965,929,646.9650 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_CAT!D31` |
| FCF Year 3 | 9,621,295,280.4235 | 9,621,295,280.4235 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_CAT!E31` |
| FCF Year 4 | 10,324,564,938.3870 | 10,324,564,938.3870 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_CAT!F31` |
| FCF Year 5 | 11,079,240,170.9012 | 11,079,240,170.9012 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_CAT!G31` |
| Terminal Value | 118,344,819,774.7289 | 118,344,819,774.7289 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_CAT!B34` |
| PV of Explicit FCF (sum of pv_fcf series) | 34,218,526,042.0224 | 34,218,526,042.0224 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_CAT!B39` |
| PV of Terminal Value | 66,865,345,437.2181 | 66,865,345,437.2181 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_CAT!B40` |
| Enterprise Value | 101,083,871,479.2405 | 101,083,871,479.2405 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_CAT!B41` |
| Equity Value | 74,853,871,479.2405 | 74,853,871,479.2405 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_CAT!B42` |
| Intrinsic Value per Share | 162.8409 | 162.8409 | 0.000000 | 0.0000% | ±0.5000% | PASS | `DCF_CAT!B43` |

_Diagnostic only (no codebase field, not part of REQUIRED_METRICS): Raw (pre-clamp) WACC = `12.095875%` at `DCF_CAT!B20`._

### INTC (Intel Corporation)

All 14 required metrics within documented tolerance against V2.

| Metric | Codebase | Workbook (V2) | Abs. Diff | Diff | Tolerance | Result | Workbook Cell |
|---|---|---|---|---|---|---|---|
| Historical Revenue CAGR | -9.554417% | -9.554417% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_INTC!B9` |
| Historical average Operating Margin | 0.462485% | 0.462485% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_INTC!B10` |
| WACC (final, clamped [5%,20%]) | 15.054645% | 15.054645% | 0.000000 | 0.0000pp | ±0.0500pp | PASS | `DCF_INTC!B21` |
| FCF Year 1 | -423,842,264.7352 | -423,842,264.7352 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_INTC!C31` |
| FCF Year 2 | -383,346,606.0948 | -383,346,606.0948 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_INTC!D31` |
| FCF Year 3 | -346,720,071.6667 | -346,720,071.6667 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_INTC!E31` |
| FCF Year 4 | -313,592,989.1782 | -313,592,989.1782 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_INTC!F31` |
| FCF Year 5 | -283,631,006.3880 | -283,631,006.3880 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_INTC!G31` |
| Terminal Value | -2,315,651,226.8237 | -2,315,651,226.8237 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_INTC!B34` |
| PV of Explicit FCF (sum of pv_fcf series) | -1,205,260,295.0810 | -1,205,260,295.0810 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_INTC!B39` |
| PV of Terminal Value | -1,148,556,522.3442 | -1,148,556,522.3442 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_INTC!B40` |
| Enterprise Value | -2,353,816,817.4252 | -2,353,816,817.4252 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_INTC!B41` |
| Equity Value | -34,673,816,817.4252 | -34,673,816,817.4252 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_INTC!B42` |
| Intrinsic Value per Share | -6.5594 | -6.5594 | 0.000000 | 0.0000% | ±0.5000% | PASS | `DCF_INTC!B43` |

_Diagnostic only (no codebase field, not part of REQUIRED_METRICS): Raw (pre-clamp) WACC = `15.054645%` at `DCF_INTC!B20`._

### VZ (Verizon Communications Inc.)

All 14 required metrics within documented tolerance against V2.

| Metric | Codebase | Workbook (V2) | Abs. Diff | Diff | Tolerance | Result | Workbook Cell |
|---|---|---|---|---|---|---|---|
| Historical Revenue CAGR | 0.845787% | 0.845787% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_VZ!B9` |
| Historical average Operating Margin | 21.216289% | 21.216289% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_VZ!B10` |
| WACC (final, clamped [5%,20%]) | 5.000000% | 5.000000% | 0.000000 | 0.0000pp | ±0.0500pp | PASS | `DCF_VZ!B21` |
| FCF Year 1 | 21,557,635,393.0143 | 21,557,635,393.0143 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_VZ!C31` |
| FCF Year 2 | 21,739,967,146.4382 | 21,739,967,146.4382 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_VZ!D31` |
| FCF Year 3 | 21,923,841,038.7703 | 21,923,841,038.7703 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_VZ!E31` |
| FCF Year 4 | 22,109,270,113.2264 | 22,109,270,113.2264 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_VZ!F31` |
| FCF Year 5 | 22,296,267,523.3400 | 22,296,267,523.3400 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_VZ!G31` |
| Terminal Value | 914,146,968,456.9417 | 914,146,968,456.9417 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_VZ!B34` |
| PV of Explicit FCF (sum of pv_fcf series) | 94,847,570,839.8378 | 94,847,570,839.8378 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_VZ!B39` |
| PV of Terminal Value | 716,258,069,783.8307 | 716,258,069,783.8307 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_VZ!B40` |
| Enterprise Value | 811,105,640,623.6685 | 811,105,640,623.6685 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_VZ!B41` |
| Equity Value | 672,003,640,623.6685 | 672,003,640,623.6685 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_VZ!B42` |
| Intrinsic Value per Share | 161.7425 | 161.7425 | 0.000000 | 0.0000% | ±0.5000% | PASS | `DCF_VZ!B43` |

_Diagnostic only (no codebase field, not part of REQUIRED_METRICS): Raw (pre-clamp) WACC = `4.394939%` at `DCF_VZ!B20`._

## Directional bias across companies

For every required metric, the SIGNED (codebase - workbook V2) difference across all four companies, checking whether within-tolerance noise is randomly distributed or consistently biased in one direction:

| Metric | MSFT | CAT | INTC | VZ | Consistent nonzero bias? |
|---|---|---|---|---|---|
| Historical Revenue CAGR | 0 | 0 | 0 | 0 | no |
| Historical average Operating Margin | 0 | 0 | 0 | 0 | no |
| WACC (final, clamped [5%,20%]) | 0 | 0 | 0 | 0 | no |
| FCF Year 1 | 0 | 0 | 0 | 0 | no |
| FCF Year 2 | 0 | 0 | 0 | 0 | no |
| FCF Year 3 | 0 | 0 | 0 | 0 | no |
| FCF Year 4 | 0 | 0 | 0 | 0 | no |
| FCF Year 5 | 0 | 0 | 0 | 0 | no |
| Terminal Value | 0 | 0 | 0 | 0 | no |
| PV of Explicit FCF (sum of pv_fcf series) | 0 | 0 | 0 | 0 | no |
| PV of Terminal Value | 0 | 0 | 0 | 0 | no |
| Enterprise Value | 0 | 0 | 0 | 0 | no |
| Equity Value | 0 | 0 | 0 | 0 | no |
| Intrinsic Value per Share | 0 | 0 | 0 | 0 | no |

No consistent nonzero directional bias found across the four companies for any required metric.

## Sensitivity reconciliation, by company (vs. V2)

Every numeric grid cell recomputed via the production functions (`project_free_cash_flows`, `calculate_terminal_value`, `discount_to_present_value`, `calculate_intrinsic_value_per_share`) and compared against workbook V2's own cached grid values (Table 1 WACC, Table 2 terminal growth, Table 3 revenue growth, Table 4 operating margin: ±0.5% IVPS / ±0.1% FCF / ±0.2% TV-EV-EqV; Table 5 two-way WACC x g grid: ±0.5% IVPS, exact `n/a` matching for invalid WACC<=g combinations). **227 scalar comparisons per company (908 total across four companies)** -- "Scenario Rows" (how many distinct input values were swept) and "Scalar Comparisons" (scenario rows x output metrics per row) are reported as two distinct numbers below, never conflated under one ambiguous "cells" label. Every one of these 908 comparisons also appears as its own formula-driven row on `dcf_reconciliation.xlsx`'s 'All Sensitivity Comparisons' sheet, not only here or in `reconciliation_results.json`.

### MSFT

| Table | Scenario Rows | Outputs/Row | Scalar Comparisons | All Pass | Workbook Direction | Recomputed Direction | Agree |
|---|---|---|---|---|---|---|---|
| Table 1 -- WACC | 7 | 4 | 28 | PASS | as expected (decreasing) | as expected (decreasing) | yes |
| Table 2 -- Terminal growth | 7 | 4 | 28 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 3 -- Revenue growth | 8 | 9 | 72 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 4 -- Operating margin | 7 | 9 | 63 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 5 -- WACC x g two-way grid | 36/36 | 1 | 36 | PASS | n/a | n/a | n/a |
| **TOTAL (this company)** | | | **227** | | | | |

### CAT

| Table | Scenario Rows | Outputs/Row | Scalar Comparisons | All Pass | Workbook Direction | Recomputed Direction | Agree |
|---|---|---|---|---|---|---|---|
| Table 1 -- WACC | 7 | 4 | 28 | PASS | as expected (decreasing) | as expected (decreasing) | yes |
| Table 2 -- Terminal growth | 7 | 4 | 28 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 3 -- Revenue growth | 8 | 9 | 72 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 4 -- Operating margin | 7 | 9 | 63 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 5 -- WACC x g two-way grid | 36/36 | 1 | 36 | PASS | n/a | n/a | n/a |
| **TOTAL (this company)** | | | **227** | | | | |

### INTC

| Table | Scenario Rows | Outputs/Row | Scalar Comparisons | All Pass | Workbook Direction | Recomputed Direction | Agree |
|---|---|---|---|---|---|---|---|
| Table 1 -- WACC | 7 | 4 | 28 | PASS | INVERTED (decreasing) | INVERTED (decreasing) | yes |
| Table 2 -- Terminal growth | 7 | 4 | 28 | PASS | INVERTED (increasing) | INVERTED (increasing) | yes |
| Table 3 -- Revenue growth | 8 | 9 | 72 | PASS | INVERTED (increasing) | INVERTED (increasing) | yes |
| Table 4 -- Operating margin | 7 | 9 | 63 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 5 -- WACC x g two-way grid | 36/36 | 1 | 36 | PASS | n/a | n/a | n/a |
| **TOTAL (this company)** | | | **227** | | | | |

### VZ

| Table | Scenario Rows | Outputs/Row | Scalar Comparisons | All Pass | Workbook Direction | Recomputed Direction | Agree |
|---|---|---|---|---|---|---|---|
| Table 1 -- WACC | 7 | 4 | 28 | PASS | as expected (decreasing) | as expected (decreasing) | yes |
| Table 2 -- Terminal growth | 7 | 4 | 28 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 3 -- Revenue growth | 8 | 9 | 72 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 4 -- Operating margin | 7 | 9 | 63 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 5 -- WACC x g two-way grid | 36/36 | 1 | 36 | PASS | n/a | n/a | n/a |
| **TOTAL (this company)** | | | **227** | | | | |

### INTC's documented directional exception

INTC's base-case FCF is negative in every projected year (structurally negative unit economics: `margin*(1-tax) + D&A% - CapEx%` per dollar of revenue is negative). Workbook V2's Sensitivity_INTC sheet documents (and this reconciliation's production recompute independently confirms) three inverted directional relationships versus the normal-case expectation: Intrinsic Value per Share *rises* (not falls) as WACC rises (discounting a negative stream less), *falls* (not rises) as terminal growth rises (a more negative terminal value), and *falls* (not rises) as revenue growth rises (compounding the loss). This is mathematically correct DCF behavior given negative cash flows, not a defect in either calculation path, and both V2 and the production recompute agree on every one of these inverted directions -- see the 'Workbook Direction' / 'Recomputed Direction' / 'Agree' columns for INTC above.

## Stop/go decision

**GO** to separate second-reviewer sign-off. All four companies' base-case reconciliations pass against V2, all 908 sensitivity scalar comparisons pass (or are correctly matched `n/a` markers), and negative controls (`tests/validation/test_dcf_reconciliation.py`) prove the reconciliation process is sensitive enough to catch a real defect. This does NOT itself constitute the required second-reviewer sign-off -- see the Sign-off section of `dcf_reconciliation.xlsx`.

Second-reviewer sign-off: **PENDING** (not performed in this session).
Profitability: **NOT established** by this or any prior validation phase.
