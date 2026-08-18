# Error-Injection Test Evidence — V2 (Track A Phase 2C)

Per `docs/independent-validation-plan.md` §"Deliberate error-injection checks", this
test confirms the reconciliation process built into V2 of this workbook (the
documented tolerances in `docs/independent-validation-plan.md`
§"Expected comparison tolerances", reused verbatim on the Summary & Reconciliation
sheet) is sensitive enough to actually catch a real discrepancy — not passing
merely because both sides agree by construction. This is V2's own error-injection
run, distinct from and additional to V1's (preserved at
[`error_injection_evidence.md`](error_injection_evidence.md)).

**Script:** [`error_injection_test_v2.py`](error_injection_test_v2.py) (re-runnable;
run again any time via `python3 error_injection_test_v2.py`).

## What was injected

Target: `DCF_MSFT!B34`, the Terminal Value formula (Section 4 — Gordon Growth,
per `docs/model-specifications/dcf.md` §"Terminal value"). The row number
(`B34`) differs from V1's `B32` because V2 inserted two additional rows in
Section 1 (the "Years elapsed" formula row and the new irregular-fiscal-calendar
diagnostic row) — every downstream reference shifted accordingly, exactly as the
row-cursor-based workbook generator is designed to handle.

- **Correct formula:** `G31*(1+'Inputs_MSFT'!$B$29)/(B21-'Inputs_MSFT'!$B$29)`
  — i.e. `FCF_Y5 × (1+g) / (WACC − g)`.
- **Injected error:** `G31*(1+'Inputs_MSFT'!$B$29)/(B21)` — i.e. `FCF_Y5 × (1+g) / WACC`,
  replacing `WACC − g` with `WACC` alone in the denominator. This is one of the two
  example errors the task instructions name explicitly.
- The four downstream cached values (PV(Terminal Value), Enterprise Value, Equity
  Value, Intrinsic Value/Share) were also patched to what a spreadsheet engine
  would show after recalculating the corrupted formula chain, using the same
  independent Python arithmetic (`shadow_calc_v2.py`) as everywhere else in this
  validation.
- Operates on a TEMPORARY copy (`_TEMP_error_injection_copy_v2.xlsx`), deleted at
  the end of the run. `independent_dcf_validation_v2.xlsx` itself was never opened
  for writing.

## Detection result

| Intermediate value | Correct | Corrupted (injected) | % difference | Documented tolerance | Detected? |
|---|---|---|---|---|---|
| Terminal Value | 2,977,114,493,450.9736 | 2,232,867,246,644.7080 | 25.00% | 0.20% | YES — FAIL flagged |
| Enterprise Value | 2,481,796,806,080.1045 | 2,019,686,676,432.2783 | 18.62% | 0.20% | YES — FAIL flagged |
| Equity Value | 2,462,437,806,080.1045 | 2,000,327,676,432.2783 | 18.77% | 0.20% | YES — FAIL flagged |
| Intrinsic Value per Share | 331.6171 | 269.3846 | 18.77% | 0.50% | YES — FAIL flagged |

**First intermediate value where the reconciliation process would flag a failure: Terminal Value.**

**All four downstream intermediate values detected beyond tolerance: True.**

Temporary copy confirmed deleted after the run; only
`independent_dcf_validation_v2.xlsx` (the correct, unmodified workbook) is
retained.

## Conclusion

V2's reconciliation tolerances are genuinely sensitive to a real formula defect,
not passing merely by construction — the same property V1 demonstrated for its
own build. This check is orthogonal to V2's actual purpose (correcting the
`years_elapsed` convention): it validates the *process*, not the specific fix.
