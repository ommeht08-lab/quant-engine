# Independent DCF Validation — V2 (Track A Phase 2C)

V2 is a second-generation independent DCF validation workbook, built to resolve
the specification ambiguity Track A Phase 2B's reconciliation exposed. **V1
(`independent_dcf_validation.xlsx`) is preserved unchanged** — see
[`README.md`](README.md) for V1's own documentation, and
`validation/dcf_reconciliation/history/phase2b_initial_no_go/` for the archived
evidence of V1's reconciliation failure. This file documents V2 only.

## Why V2 exists

`docs/model-specifications/dcf.md`'s prose defined the Revenue CAGR formula but
did not, at the time V1 was built, define `years_elapsed`'s exact day-count
convention. V1 (built without reading `src/dcf_model/dcf.py`, per the
genuine-independence requirement) resolved this as `COUNT(periods) - 1`, a plain
period count. For INTC — whose five frozen fiscal-period-end dates are not evenly
spaced (2021-12-25, 2022-12-31, 2023-12-30, 2024-12-28, 2025-12-27 — 1,463 actual
days, not the 1,461 that `(5 periods − 1) = 4` years would imply) — this diverged
from the codebase's actual-elapsed-calendar-time convention by enough
(`years_elapsed = 4.0` vs. `4.005475701574264`) to move Revenue CAGR by ~0.0124
percentage points, exceeding the documented `±0.01`pp reconciliation tolerance and
producing a Phase 2B **NO-GO**.

Track A Phase 2C resolved the ambiguity by clarifying
`docs/model-specifications/dcf.md` to state `years_elapsed`'s exact definition
(actual elapsed calendar days between the earliest- and latest-dated valid
observations, divided by 365.25 — see `A-028`/`L-019`). V2 is built from that
corrected specification.

## Independence discipline

V2 was built, audited, and hashed **before** production code
(`src/dcf_model/dcf.py`) or the Track A Phase 2B/2C reconciliation
implementation (`validation/dcf_reconciliation/`) was re-inspected in this
phase — the same mechanical-independence discipline V1 was built under. V2's
build scripts (`build_workbook_v2.py`, `shadow_calc_v2.py`) were derived from
V1's own scripts, used as permitted historical validation-tooling artifacts
(explicitly allowed, unlike production code) — the only substantive change is
the `years_elapsed` calculation and the new diagnostic row that displays it
against the naive count for comparison. `xlsx_lite.py` (the stdlib-only OOXML
writer) is reused unmodified — it contains no DCF calculation logic, only
generic spreadsheet-file serialization.

**Honest caveat on session-level independence**: this phase's instructions
required staying blind to `src/dcf_model/dcf.py` and the reconciliation
implementation *during V2's construction*, which this session followed — those
files were not opened or re-inspected between the start of Phase 3 and V2 being
built, audited, and hashed. However, this is the same continuous session that
built and ran Track A Phase 2B's reconciliation (including reading
`src/dcf_model/dcf.py` in full) earlier in this conversation. That prior
exposure cannot be un-known, so V2 does not achieve the stronger guarantee a
genuinely fresh session (no memory of `dcf.py` at all) would provide. What V2
does guarantee: it was derived from the written specification's prose and
equations, its formulas are auditable in the workbook, its CAGR fix was
motivated purely by the specification's own written definition (not by peeking
at a target value to match), and no file governed by this phase's "do not
inspect" list was opened during its construction.

## What changed from V1 (and what didn't)

**Changed:**
- `Inputs_<TICKER>` sheet: "Fiscal Year End" cells now store real Excel date
  serial numbers (with a `yyyy-mm-dd` display format) instead of text labels —
  required so a live formula can subtract two dates.
- `DCF_<TICKER>` sheet, Section 1: "Years elapsed" is now a live formula,
  `(latest_fiscal_date − earliest_fiscal_date) / 365.25`, referencing the
  Inputs sheet's date cells directly — replacing V1's `COUNT(periods)-1`.
- A new diagnostic row immediately below it, for every company, explicitly
  computing and displaying the naive `(N periods − 1)` count alongside the
  actual `years_elapsed`, flagging **IRREGULAR** (red) if they differ beyond
  floating-point noise or **REGULAR** (green) if they coincide. **INTC is the
  only company flagged IRREGULAR** — MSFT, CAT, and VZ are flagged REGULAR
  (their fiscal calendars recur on the same calendar day every year, so both
  conventions coincidentally agree for them).
- `historical_annual_data` periods are explicitly sorted by
  `fiscal_period_end` date before use (both in the workbook builder and in
  `shadow_calc_v2.py`), rather than trusting snapshot-file order — a no-op for
  these four companies' already-chronological snapshots, but now a genuine
  guarantee rather than an unstated assumption.
- Every hardcoded/cached Revenue CAGR figure in the workbook (README sheet's
  company table, DCF sheet cached values, Summary sheet) reflects the
  corrected calculation.

**Unchanged (specification did not change anywhere else):**
- WACC/CAPM formula and clamp.
- FCF projection formula (Revenue → EBIT → NOPAT → +D&A −CapEx −ΔNWC).
- Terminal value (Gordon Growth) formula.
- Discounting and Enterprise-to-Equity bridge.
- All five sensitivity tables' formula structure.
- Frozen snapshot data (`snapshots/*.json` — byte-for-byte identical; V2 reads
  the same files V1 did, never modifying them).
- Operating margin averaging (a simple per-period average, never tied to
  elapsed time).

## Files

| File | Contents |
|---|---|
| `shadow_calc_v2.py` | Independent Python re-implementation, V2 — corrected `historical_revenue_cagr` (date-based `years_elapsed`), plus `years_elapsed_actual()` and `excel_serial_date()` helpers. |
| `build_workbook_v2.py` | Workbook generator, V2. |
| `independent_dcf_validation_v2.xlsx` | The V2 workbook. |
| `error_injection_test_v2.py` / `error_injection_evidence_v2.md` | V2's own deliberate-error-injection check and result (Terminal Value formula, DCF_MSFT — same technique as V1, re-run against V2). |
| `README_v2.md` | This file. |

## Verification performed before freezing V2's hash

- `formula_audit.py independent_dcf_validation_v2.xlsx` → **PASS** (144/144 Table 5 cells, all base/sensitivity formula-text checks; reused unmodified — it locates every cell from the workbook's own labels, never from generating-code assumptions, so it needed no V2-specific changes).
- `error_injection_test_v2.py` → **PASS** — a deliberately corrupted Terminal Value formula (temporary copy only) is correctly flagged at the first intermediate value (Terminal Value), with all four downstream values also flagged, and the temporary copy confirmed deleted.
- Structural checks: zip integrity (`zipfile.testzip()`), zero formula-error-marker cells, all 18 expected sheets present.
- Determinism: re-running `build_workbook_v2.py` from the unchanged frozen snapshots produces a byte-identical file.
- Cross-check: V2's independently-derived Revenue CAGR for all four companies (MSFT `13.741147%`, CAT `7.309511%`, INTC `-9.554417%`, VZ `0.845787%`) and `years_elapsed` values (MSFT/CAT/VZ = `4.0` exactly, INTC = `4.005475701574264`) match to full floating-point precision — computed independently from the corrected specification and the frozen snapshot dates, with no dependency on the production codebase.

## V2 workbook SHA-256 (frozen)

```
cd2d225cfe0825ff56aa8f866c3b80b6bec5f87a8b02dc3f76724e4abdd27d6b
```

Frozen at the end of Track A Phase 3 (this phase), before production code or
the reconciliation implementation was re-inspected. Recheck this hash before
trusting any later reconciliation result against V2.

## Status

- V2 workbook construction: **COMPLETE**
- V2 formula/sensitivity/error-injection audits: **COMPLETE, PASS**
- Codebase-to-V2-workbook reconciliation: pending Track A Phase 2C's Phase 4 (this session, next step)
- Second-reviewer sign-off: **PENDING**
- Profitability evidence: **NOT ESTABLISHED**
