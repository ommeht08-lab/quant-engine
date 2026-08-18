# DCF Codebase-to-Independent-Workbook Reconciliation Report

Track A Phase 2B. Compares `src/dcf_model/dcf.py`'s production output against the already-frozen, already-committed independent workbook (`validation/independent_dcf/independent_dcf_validation.xlsx`). Neither calculation path was modified to produce agreement; this report records what was actually found.

- **Branch**: `remediation/valuation-engine-production-quality`
- **Commit**: `776e76f56e6210f1050caa74c60962ed89162496`
- **Original workbook SHA-256**: `942b5f096cf546d63487add31eb29b1b690f0819f923f01436093f9bbc75eba1`
- **Frozen snapshot SHA-256**:
  - `MSFT`: `7c50a3e9fb2d27095a890ad8d11732d4ccc72ef9350998604adb3af7a3c845a8`
  - `CAT`: `651d366c01415c2da33733058dabb9ac6e4f40cdf5b20e743c0b83ebc3a52c93`
  - `INTC`: `d73e4b40bce2442a75affde82aaa563ffdea9c68c7257e70cc325663cff24930`
  - `VZ`: `77b8ea27b8decaff5fe3b4aad857c632a852445eda0b7f2342f594cd3c4b5f3d`

## Overall verdict: NO-GO

- Base-case reconciliation: FAILURES: INTC
- Sensitivity reconciliation: FAILURES present

**Second-reviewer sign-off is PENDING** -- this report alone does not close the checklist in `docs/independent-validation-plan.md`; see the Sign-off section of `dcf_reconciliation.xlsx`.
**Profitability is NOT established by this reconciliation** -- it verifies that two independent calculation paths agree (or documents where/why they don't), not that any company is investable.

## Base-case reconciliation, by company

### MSFT (Microsoft Corporation)

All 14 required metrics within documented tolerance.

| Metric | Codebase | Workbook | Abs. Diff | Diff | Tolerance | Result | Workbook Cell |
|---|---|---|---|---|---|---|---|
| Historical Revenue CAGR | 13.741147% | 13.741147% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_MSFT!B7` |
| Historical average Operating Margin | 44.175047% | 44.175047% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_MSFT!B8` |
| WACC (final, clamped [5%,20%]) | 10.000422% | 10.000422% | 0.000000 | 0.0000pp | ±0.0500pp | PASS | `DCF_MSFT!B19` |
| FCF Year 1 | 130,162,813,492.1701 | 130,162,813,492.1701 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_MSFT!C29` |
| FCF Year 2 | 148,048,677,492.9603 | 148,048,677,492.9603 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_MSFT!D29` |
| FCF Year 3 | 168,392,264,421.4593 | 168,392,264,421.4593 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_MSFT!E29` |
| FCF Year 4 | 191,531,293,606.6921 | 191,531,293,606.6921 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_MSFT!F29` |
| FCF Year 5 | 217,849,890,888.3251 | 217,849,890,888.3251 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_MSFT!G29` |
| Terminal Value | 2,977,114,493,450.9736 | 2,977,114,493,450.9736 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_MSFT!B32` |
| PV of Explicit FCF (sum of pv_fcf series) | 633,278,359,490.3066 | 633,278,359,490.3066 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_MSFT!B37` |
| PV of Terminal Value | 1,848,518,446,589.7981 | 1,848,518,446,589.7981 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_MSFT!B38` |
| Enterprise Value | 2,481,796,806,080.1045 | 2,481,796,806,080.1045 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_MSFT!B39` |
| Equity Value | 2,462,437,806,080.1045 | 2,462,437,806,080.1045 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_MSFT!B40` |
| Intrinsic Value per Share | 331.6171 | 331.6171 | 0.000000 | 0.0000% | ±0.5000% | PASS | `DCF_MSFT!B41` |

_Diagnostic only (no codebase field, not part of REQUIRED_METRICS): Raw (pre-clamp) WACC = `10.000422%` at `DCF_MSFT!B18`._

### CAT (Caterpillar Inc.)

All 14 required metrics within documented tolerance.

| Metric | Codebase | Workbook | Abs. Diff | Diff | Tolerance | Result | Workbook Cell |
|---|---|---|---|---|---|---|---|
| Historical Revenue CAGR | 7.309511% | 7.309511% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_CAT!B7` |
| Historical average Operating Margin | 16.559502% | 16.559502% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_CAT!B8` |
| WACC (final, clamped [5%,20%]) | 12.095875% | 12.095875% | 0.000000 | 0.0000pp | ±0.0500pp | PASS | `DCF_CAT!B19` |
| FCF Year 1 | 8,355,205,000.0888 | 8,355,205,000.0888 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_CAT!C29` |
| FCF Year 2 | 8,965,929,646.9650 | 8,965,929,646.9650 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_CAT!D29` |
| FCF Year 3 | 9,621,295,280.4235 | 9,621,295,280.4235 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_CAT!E29` |
| FCF Year 4 | 10,324,564,938.3870 | 10,324,564,938.3870 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_CAT!F29` |
| FCF Year 5 | 11,079,240,170.9012 | 11,079,240,170.9012 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_CAT!G29` |
| Terminal Value | 118,344,819,774.7289 | 118,344,819,774.7289 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_CAT!B32` |
| PV of Explicit FCF (sum of pv_fcf series) | 34,218,526,042.0224 | 34,218,526,042.0224 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_CAT!B37` |
| PV of Terminal Value | 66,865,345,437.2181 | 66,865,345,437.2181 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_CAT!B38` |
| Enterprise Value | 101,083,871,479.2405 | 101,083,871,479.2405 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_CAT!B39` |
| Equity Value | 74,853,871,479.2405 | 74,853,871,479.2405 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_CAT!B40` |
| Intrinsic Value per Share | 162.8409 | 162.8409 | 0.000000 | 0.0000% | ±0.5000% | PASS | `DCF_CAT!B41` |

_Diagnostic only (no codebase field, not part of REQUIRED_METRICS): Raw (pre-clamp) WACC = `12.095875%` at `DCF_CAT!B18`._

### INTC (Intel Corporation)

**First divergence: `Historical Revenue CAGR`** (first metric, in comparison order, outside its documented tolerance).

| Metric | Codebase | Workbook | Abs. Diff | Diff | Tolerance | Result | Workbook Cell |
|---|---|---|---|---|---|---|---|
| Historical Revenue CAGR | -9.554417% | -9.566850% | 0.000124 | 0.0124pp | ±0.0100pp | **FAIL** | `DCF_INTC!B7` |
| Historical average Operating Margin | 0.462485% | 0.462485% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_INTC!B8` |
| WACC (final, clamped [5%,20%]) | 15.054645% | 15.054645% | 0.000000 | 0.0000pp | ±0.0500pp | PASS | `DCF_INTC!B19` |
| FCF Year 1 | -423,842,264.7352 | -423,711,351.1482 | 130,913.586955 | 0.0309% | ±0.1000% | PASS | `DCF_INTC!C29` |
| FCF Year 2 | -383,346,606.0948 | -383,175,521.7904 | 171,084.304373 | 0.0446% | ±0.1000% | PASS | `DCF_INTC!D29` |
| FCF Year 3 | -346,720,071.6667 | -346,517,694.4197 | 202,377.247039 | 0.0584% | ±0.1000% | PASS | `DCF_INTC!E29` |
| FCF Year 4 | -313,592,989.1782 | -313,366,866.4034 | 226,122.774830 | 0.0722% | ±0.1000% | PASS | `DCF_INTC!F29` |
| FCF Year 5 | -283,631,006.3880 | -283,387,528.3741 | 243,478.013856 | 0.0859% | ±0.1000% | PASS | `DCF_INTC!G29` |
| Terminal Value | -2,315,651,226.8237 | -2,313,663,397.0421 | 1,987,829.781625 | 0.0859% | ±0.2000% | PASS | `DCF_INTC!B32` |
| PV of Explicit FCF (sum of pv_fcf series) | -1,205,260,295.0810 | -1,204,634,587.5651 | 625,707.515918 | 0.0519% | ±0.2000% | PASS | `DCF_INTC!B37` |
| PV of Terminal Value | -1,148,556,522.3442 | -1,147,570,564.3404 | 985,958.003757 | 0.0859% | ±0.2000% | PASS | `DCF_INTC!B38` |
| Enterprise Value | -2,353,816,817.4252 | -2,352,205,151.9055 | 1,611,665.519674 | 0.0685% | ±0.2000% | PASS | `DCF_INTC!B39` |
| Equity Value | -34,673,816,817.4252 | -34,672,205,151.9055 | 1,611,665.519676 | 0.0046% | ±0.2000% | PASS | `DCF_INTC!B40` |
| Intrinsic Value per Share | -6.5594 | -6.5591 | 0.000305 | 0.0046% | ±0.5000% | PASS | `DCF_INTC!B41` |

_Diagnostic only (no codebase field, not part of REQUIRED_METRICS): Raw (pre-clamp) WACC = `15.054645%` at `DCF_INTC!B18`._

### VZ (Verizon Communications Inc.)

All 14 required metrics within documented tolerance.

| Metric | Codebase | Workbook | Abs. Diff | Diff | Tolerance | Result | Workbook Cell |
|---|---|---|---|---|---|---|---|
| Historical Revenue CAGR | 0.845787% | 0.845787% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_VZ!B7` |
| Historical average Operating Margin | 21.216289% | 21.216289% | 0.000000 | 0.0000pp | ±0.0100pp | PASS | `DCF_VZ!B8` |
| WACC (final, clamped [5%,20%]) | 5.000000% | 5.000000% | 0.000000 | 0.0000pp | ±0.0500pp | PASS | `DCF_VZ!B19` |
| FCF Year 1 | 21,557,635,393.0143 | 21,557,635,393.0143 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_VZ!C29` |
| FCF Year 2 | 21,739,967,146.4382 | 21,739,967,146.4382 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_VZ!D29` |
| FCF Year 3 | 21,923,841,038.7703 | 21,923,841,038.7703 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_VZ!E29` |
| FCF Year 4 | 22,109,270,113.2264 | 22,109,270,113.2264 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_VZ!F29` |
| FCF Year 5 | 22,296,267,523.3400 | 22,296,267,523.3400 | 0.000000 | 0.0000% | ±0.1000% | PASS | `DCF_VZ!G29` |
| Terminal Value | 914,146,968,456.9417 | 914,146,968,456.9417 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_VZ!B32` |
| PV of Explicit FCF (sum of pv_fcf series) | 94,847,570,839.8378 | 94,847,570,839.8378 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_VZ!B37` |
| PV of Terminal Value | 716,258,069,783.8307 | 716,258,069,783.8307 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_VZ!B38` |
| Enterprise Value | 811,105,640,623.6685 | 811,105,640,623.6685 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_VZ!B39` |
| Equity Value | 672,003,640,623.6685 | 672,003,640,623.6685 | 0.000000 | 0.0000% | ±0.2000% | PASS | `DCF_VZ!B40` |
| Intrinsic Value per Share | 161.7425 | 161.7425 | 0.000000 | 0.0000% | ±0.5000% | PASS | `DCF_VZ!B41` |

_Diagnostic only (no codebase field, not part of REQUIRED_METRICS): Raw (pre-clamp) WACC = `4.394939%` at `DCF_VZ!B18`._

## Root-cause note: INTC Revenue CAGR divergence

INTC's Revenue CAGR is the first (and root-cause) divergence. `docs/model-specifications/dcf.md` documents the formula as `CAGR = (Revenue_latest / Revenue_earliest) ** (1 / years_elapsed) - 1`, and `calculate_historical_revenue_cagr` (`src/dcf_model/dcf.py`) computes `years_elapsed` as the ACTUAL number of calendar days between the earliest and latest frozen fiscal-period-end dates, divided by 365.25. The independent workbook's `DCF_INTC!B5` ("Years elapsed (N periods - 1)") instead computes `COUNT(periods) - 1` -- a plain count of periods, not actual elapsed calendar time. For MSFT, CAT, and VZ, whose five frozen fiscal-period-end dates recur on the same calendar month/day every year, both methods happen to produce exactly 4.0 (their total elapsed days divide out to an exact multiple of 365.25), so the two calculation paths agree to full floating-point precision. INTC's five fiscal-period-end dates are NOT evenly spaced (2021-12-25, 2022-12-31, 2023-12-30, 2024-12-28, 2025-12-27 -- 1,463 actual days, not 1,461), so `years_elapsed` = 4.005476 (codebase/spec) vs. 4.0 (workbook), producing a genuine ~0.0124 percentage-point Revenue CAGR divergence that exceeds the documented ±0.01pp tolerance. This is a pre-existing ambiguity in how precisely `docs/model-specifications/dcf.md`'s prose specifies `years_elapsed` (it names the quantity but not, in prose, the exact day-count method), which the independent workbook builder resolved differently (and reasonably) from the codebase's specific implementation. It is not a defect being 'fixed' here -- per this phase's instructions, neither calculation path was altered.

This single-metric divergence cascades downstream through every subsequent INTC metric (FCF Years 1-5, Terminal Value, PV of Explicit FCF, PV of Terminal Value, Enterprise Value, Equity Value, Intrinsic Value per Share, and every INTC sensitivity-table cell), because `revenue_growth_rate` is an input to `project_free_cash_flows` and everything downstream of it. The magnitude of each downstream difference is consistent with propagation from this one root cause, not independent errors at each step.

## Directional bias across companies

For every required metric, the SIGNED (codebase - workbook) difference across all four companies, checking whether within-tolerance noise is randomly distributed or consistently biased in one direction:

| Metric | MSFT | CAT | INTC | VZ | Consistent nonzero bias? |
|---|---|---|---|---|---|
| Historical Revenue CAGR | 0 | 0 | 0.000124327 | 0 | no |
| Historical average Operating Margin | 0 | 0 | 0 | 0 | no |
| WACC (final, clamped [5%,20%]) | 0 | 0 | 0 | 0 | no |
| FCF Year 1 | 0 | 0 | -130914 | 0 | no |
| FCF Year 2 | 0 | 0 | -171084 | 0 | no |
| FCF Year 3 | 0 | 0 | -202377 | 0 | no |
| FCF Year 4 | 0 | 0 | -226123 | 0 | no |
| FCF Year 5 | 0 | 0 | -243478 | 0 | no |
| Terminal Value | 0 | 0 | -1.98783e+06 | 0 | no |
| PV of Explicit FCF (sum of pv_fcf series) | 0 | 0 | -625708 | 0 | no |
| PV of Terminal Value | 0 | 0 | -985958 | 0 | no |
| Enterprise Value | 0 | 0 | -1.61167e+06 | 0 | no |
| Equity Value | 0 | 0 | -1.61167e+06 | 0 | no |
| Intrinsic Value per Share | 0 | 0 | -0.000304887 | 0 | no |

MSFT, CAT, and VZ show exactly zero signed difference (bit-for-bit floating-point agreement) on every required metric -- there is no directional bias to speak of among them. INTC's nonzero differences are fully explained by the single Revenue CAGR root cause above, not an independent systematic bias.

## Sensitivity reconciliation, by company

Every numeric grid cell recomputed via the production functions (`project_free_cash_flows`, `calculate_terminal_value`, `discount_to_present_value`, `calculate_intrinsic_value_per_share`) and compared against the workbook's own cached grid values (Table 1 WACC, Table 2 terminal growth, Table 3 revenue growth, Table 4 operating margin: ±0.5% IVPS / ±0.1% FCF / ±0.2% TV-EV-EqV; Table 5 two-way WACC x g grid: ±0.5% IVPS, exact `n/a` matching for invalid WACC<=g combinations).

### MSFT

| Table | Cells | All Pass | Workbook Direction | Recomputed Direction | Agree |
|---|---|---|---|---|---|
| Table 1 -- WACC | 7 | PASS | as expected (decreasing) | as expected (decreasing) | yes |
| Table 2 -- Terminal growth | 7 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 3 -- Revenue growth | 8 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 4 -- Operating margin | 7 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 5 -- WACC x g (two-way) | 36/36 | PASS | n/a | n/a | n/a |

### CAT

| Table | Cells | All Pass | Workbook Direction | Recomputed Direction | Agree |
|---|---|---|---|---|---|
| Table 1 -- WACC | 7 | PASS | as expected (decreasing) | as expected (decreasing) | yes |
| Table 2 -- Terminal growth | 7 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 3 -- Revenue growth | 8 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 4 -- Operating margin | 7 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 5 -- WACC x g (two-way) | 36/36 | PASS | n/a | n/a | n/a |

### INTC

| Table | Cells | All Pass | Workbook Direction | Recomputed Direction | Agree |
|---|---|---|---|---|---|
| Table 1 -- WACC | 7 | PASS | INVERTED (decreasing) | INVERTED (decreasing) | yes |
| Table 2 -- Terminal growth | 7 | PASS | INVERTED (increasing) | INVERTED (increasing) | yes |
| Table 3 -- Revenue growth | 8 | PASS | INVERTED (increasing) | INVERTED (increasing) | yes |
| Table 4 -- Operating margin | 7 | **FAIL** | as expected (increasing) | as expected (increasing) | yes |
| Table 5 -- WACC x g (two-way) | 36/36 | PASS | n/a | n/a | n/a |

### VZ

| Table | Cells | All Pass | Workbook Direction | Recomputed Direction | Agree |
|---|---|---|---|---|---|
| Table 1 -- WACC | 7 | PASS | as expected (decreasing) | as expected (decreasing) | yes |
| Table 2 -- Terminal growth | 7 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 3 -- Revenue growth | 8 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 4 -- Operating margin | 7 | PASS | as expected (increasing) | as expected (increasing) | yes |
| Table 5 -- WACC x g (two-way) | 36/36 | PASS | n/a | n/a | n/a |

### INTC's documented directional exception

INTC's base-case FCF is negative in every projected year (structurally negative unit economics: `margin*(1-tax) + D&A% - CapEx%` per dollar of revenue is negative). The workbook's own Sensitivity_INTC sheet documents (and this reconciliation's production recompute independently confirms) three inverted directional relationships versus the normal-case expectation: Intrinsic Value per Share *rises* (not falls) as WACC rises (discounting a negative stream less), *falls* (not rises) as terminal growth rises (a more negative terminal value), and *falls* (not rises) as revenue growth rises (compounding the loss). This is mathematically correct DCF behavior given negative cash flows, not a defect in either calculation path, and both the workbook and the production recompute agree on every one of these inverted directions -- see the 'Workbook Direction' / 'Recomputed Direction' / 'Agree' columns for INTC above.

## Stop/go decision

**NO-GO.** Base-case divergence(s) outside documented tolerance for: INTC. Sensitivity reconciliation has failing cells. Per the task's NO-GO protocol: no source code or independent-workbook edits were made in response to this finding; the discrepancy is reported here for explicit human approval before any remediation is attempted.

Second-reviewer sign-off: **PENDING** (not performed in this session).
Profitability: **NOT established** by this or any prior validation phase.
