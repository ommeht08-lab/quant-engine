# DCF Codebase-to-Independent-Workbook Reconciliation — Track A Phase 2C

This directory is the output of the reconciliation phase described in
`docs/independent-validation-plan.md`: comparing `src/dcf_model/dcf.py`'s
production DCF output against an already-frozen, already-committed
independent workbook in `validation/independent_dcf/`. As of Track A Phase
2C, the reconciliation TARGET is **V2**
(`independent_dcf_validation_v2.xlsx`), built from the `years_elapsed`
specification clarified after Phase 2B's original attempt against V1 found a
genuine, tolerance-exceeding discrepancy for INTC. **V1
(`independent_dcf_validation.xlsx`) is preserved unchanged** — see
`validation/independent_dcf/README.md` (V1) and
`validation/independent_dcf/README_v2.md` (V2) for how each was built.

**This directory never modifies `validation/independent_dcf/`.** Every file
here is new, and both `independent_dcf_validation.xlsx` (V1) and
`independent_dcf_validation_v2.xlsx` (V2) are read only through
`xlsx_reader.py`'s read-only OOXML parser — never written to, never
recomputed via `shadow_calc.py`/`shadow_calc_v2.py`.

## What this is

A per-metric, per-company, per-sensitivity-cell reconciliation of two
mechanically independent DCF calculations for MSFT, CAT, INTC, and VZ:

1. The production codebase (`run_dcf_valuation`, called with
   `DCFAssumptions()` defaults, against `financial_data` built exclusively
   from the frozen `validation/independent_dcf/snapshots/*.json` files).
2. The independent workbook V2's own cached formula cells (`DCF_<TICKER>` and
   `Sensitivity_<TICKER>` sheets), read structurally from the finished
   `.xlsx`, never recomputed by any Python reimplementation of the spec.

## What this is NOT

- Not a modification of either calculation path to force agreement. A
  discrepancy found here is reported, not "fixed," in this session. Phase
  2B's V1-vs-codebase discrepancy was resolved by clarifying the written
  *specification* (`docs/model-specifications/dcf.md`, `A-028`, `L-019`) and
  building a new independent workbook (V2) from it — not by editing V1 or
  `src/dcf_model/dcf.py` to force agreement.
- Not evidence that either implementation is correct in an absolute sense —
  only that the two independent paths agree (or don't) on the same frozen
  inputs.
- Not investment advice, and not a claim of profitability. See the
  reconciliation workbook's Sign-off sheet.

## Files

| File | Contents |
|---|---|
| `adapter.py` | Deterministically converts each frozen `validation/independent_dcf/snapshots/*.json` into the `financial_data` dict `run_dcf_valuation` expects. Every value is asserted equal to its snapshot source immediately after construction. No network access; does not import `src/data_ingestion/fetch_financials.py`. |
| `xlsx_reader.py` | Minimal, read-only, stdlib-only OOXML parser (independently implemented, not imported from `validation/independent_dcf/`). Locates cells by the workbook's own row/column labels — works against V1 and V2 without modification. |
| `xlsx_writer.py` | Minimal, dependency-free `.xlsx` writer for the reconciliation workbook (independently implemented, not imported from `validation/independent_dcf/xlsx_lite.py`). Deterministic output (no wall-clock content). Track A Phase 2C fixed a number-format-ID collision bug here (Finding 2) — see the module docstring on `_build_styles_xml`. |
| `cell_map.py` | Phase 1 comparison contract: the exact codebase-field <-> workbook-cell mapping and documented tolerances for the 14 base metrics, located in `DCF_<TICKER>` by label, never a hardcoded row number (works unmodified against V2's shifted row numbers). |
| `sensitivity_map.py` | Locates the five sensitivity tables in `Sensitivity_<TICKER>` by their own section-title/header labels and reads cached grid values (including `n/a` cells). |
| `coverage.py` | Single source of truth for sensitivity-table shape and scalar-comparison counts (Finding 3: 28+28+72+63+36 = 227 per company, 908 total) — shared by `report.py` and `workbook_builder.py` so the two narratives can't drift apart. |
| `recompute.py` | Recomputes every sensitivity scenario using the PRODUCTION DCF functions themselves (`project_free_cash_flows`, `calculate_terminal_value`, `discount_to_present_value`, `calculate_intrinsic_value_per_share`) — one variable swept at a time, every other input held at the actual base-case value. |
| `compare.py` | Pure tolerance-math functions (percentage-point vs. relative-fraction, with a documented zero-denominator carve-out), independently unit-tested. |
| `capture.py` | Runs `run_dcf_valuation` for all four companies and captures every intermediate at full precision -> `codebase_outputs.json`. Also guards V1's and V2's SHA-256 against unexpected drift before every run. |
| `report.py` | Renders `reconciliation_results.json` into `reconciliation_report.md`. |
| `workbook_builder.py` | Builds `dcf_reconciliation.xlsx` (a **separate** workbook from either independent workbook) with live spreadsheet formulas for every Absolute Difference / Relative-or-pp Difference / Tolerance / PASS-FAIL cell, semantically correct number formats (Finding 2), accurate scenario-row-vs-scalar-comparison coverage counts (Finding 3), full formula-driven detail for every one of the 908 Table 1-5 scalar comparisons (Finding 4, on the "All Sensitivity Comparisons" sheet), and (Finding 5 / audit finding M-1) a Python-computed, non-empty cached PASS/FAIL value alongside every live PASS/FAIL formula. |
| `reconcile.py` | Main orchestrator — `python3 -m validation.dcf_reconciliation.reconcile` runs the full pipeline and writes every artifact below. |
| `codebase_outputs.json` | Every captured production intermediate, full precision, plus commit/branch/hash/assumption metadata and an explicit no-network statement. |
| `reconciliation_results.json` | The full base-case + sensitivity comparison, every metric, every company, every sensitivity cell. |
| `reconciliation_report.md` | Human-readable summary: results tables, first-divergence analysis, directional-bias analysis, sensitivity coverage, stop/go decision. |
| `dcf_reconciliation.xlsx` | The reconciliation workbook (README/methodology, Tolerances, Base Reconciliation, one `Detail_<TICKER>` sheet per company, Sensitivity Reconciliation summary, **All Sensitivity Comparisons** full detail, Findings, Sign-off). |
| `reconciliation_manifest.json` | SHA-256 of every artifact this directory produced (except itself), plus V1's, V2's, and the frozen snapshots' SHA-256 for provenance. |
| `history/phase2b_initial_no_go/` | Frozen, never-edited archive of Phase 2B's original reconciliation artifacts (against V1) and the NO-GO verdict they produced. See its own `README.md`. |

## What "PASS/FAIL" means at each layer -- three distinct, non-substitutable claims

An independent second-reviewer audit of Track A Phase 2C (recorded below,
"Findings H-1/M-1") found that this distinction had collapsed into a single
loosely-used "PASS" in earlier documentation. It does not:

1. **Structurally verified** -- `validation/independent_dcf/formula_audit.py`
   and this directory's own OOXML inspection confirm that a formula cell
   exists, its `<f>` text references the cells it is supposed to (by reading
   the workbook's own labels, not by trusting the generating code), and no
   `#REF!`/`#VALUE!`/etc. error marker is present anywhere. This says the
   formula is *well-formed and points at the right inputs* -- it says
   nothing about what the formula would evaluate to.
2. **Python-computed cached verdict** -- as of the M-1 remediation, every
   PASS/FAIL formula cell's `<v>` (the value a structural/XML-only reader
   sees before any recalculation) is the actual "PASS" or "FAIL" string,
   computed in Python by `workbook_builder.py`'s `_comparison_row` from
   exactly the codebase value, workbook value, and tolerance written into
   that same row, via the identical `compare_rate`/`compare_monetary`
   functions (`compare.py`) that produce `reconciliation_results.json`'s
   verdicts. This is a real, independently-computed answer -- not a guess,
   not empty, not copied between rows -- but it is **still a Python
   computation standing in for the formula's own evaluation**, not proof
   that a spreadsheet engine evaluated `IF(D5<=F5,"PASS","FAIL")` and got
   the same answer.
3. **Actual spreadsheet recalculation** -- `xlsx_writer.py` sets
   `<calcPr calcId="999999" fullCalcOnLoad="1"/>`, which *requests* that a
   real spreadsheet application (Excel, LibreOffice, Numbers) recalculate
   every formula the moment the file is opened. **This metadata is a
   request, not evidence that recalculation happened.** No spreadsheet
   engine is available anywhere in this repository's tooling or CI, and
   none was used to build, fix, or verify `dcf_reconciliation.xlsx` at any
   point, including during the M-1 remediation. Anyone who needs tier-3
   confidence must open the file in a real spreadsheet application
   themselves and visually confirm the recalculated values match the
   cached ones.

Tiers 1 and 2 are both satisfied and machine-checked (see
`tests/validation/test_dcf_reconciliation.py`'s `TestNumberFormatAllocator`
and the PASS/FAIL cached-value tests). Tier 3 remains, and will always
remain, outside what this repository's own tooling can demonstrate without
a spreadsheet engine being introduced as a dependency.

## Reproducing

```bash
python3 -m validation.dcf_reconciliation.reconcile
```

Deterministic: re-running against an unchanged repository produces
byte-identical `codebase_outputs.json`, `reconciliation_results.json`,
`reconciliation_report.md`, and `dcf_reconciliation.xlsx` (no wall-clock
content is embedded in any of them).

## Result summary (see `reconciliation_report.md` for full detail)

- **All four companies (MSFT, CAT, INTC, VZ)**: all 14 required base metrics
  and all five sensitivity tables (908 scalar comparisons total) match
  workbook V2 to full floating-point precision or well within documented
  tolerance.
- **INTC**, previously the sole failure against V1 (Phase 2B), now passes
  against V2 starting from Revenue CAGR (previously the first divergence)
  through every downstream base and sensitivity comparison. The root cause —
  V1's `years_elapsed = COUNT(periods)-1` vs. the specification's actual
  elapsed-calendar-time convention — is now resolved by V2's construction,
  not by any change to `src/dcf_model/dcf.py` (which already implemented the
  clarified convention).
- **Overall verdict**: **GO** to separate second-reviewer sign-off. See
  `reconciliation_report.md`'s "What changed since Phase 2B" section for the
  full explanation, and `validation/dcf_reconciliation/history/phase2b_initial_no_go/`
  for the original, unedited failing evidence.

## Durable evidence and `.gitignore` (audit finding H-1, remediated)

The repository's `.gitignore` excludes `*.xlsx` generally (raw/interim
financial data pulls should not be committed). An independent audit found
that this blanket rule also silently excluded three workbooks that are not
data pulls but are themselves the **evidence** this validation phase exists
to produce: `dcf_reconciliation.xlsx` (this directory), its frozen copy at
`history/phase2b_initial_no_go/dcf_reconciliation.xlsx`, and
`validation/independent_dcf/independent_dcf_validation_v2.xlsx` (V2). Left
ignored, none of the three would survive a `git clean` or be present after a
fresh clone, with no git history to recover them from -- directly
undermining the archive's own "must not be silently erased" claim.
`.gitignore` now carries three narrow, exact-path `!`-exceptions for these
files only (see `.gitignore`'s own comment block) -- the general `*.xlsx`
rule is otherwise unchanged and still applies to every other workbook in the
repository, including any future one. `independent_dcf_validation.xlsx` (V1)
needed no exception; it was already tracked from an earlier force-add.
`tests/validation/test_dcf_reconciliation.py`'s
`test_required_evidence_paths_are_not_git_ignored` and its companion tests
verify this holds (and stays narrow) on every test run.

## Status

- Base-case reconciliation (vs. V2): **COMPLETE**, all four companies pass
- Sensitivity reconciliation (vs. V2): **COMPLETE**, all 908 scalar comparisons pass
- Negative controls (reconciliation logic correctly detects injected defects): see `tests/validation/test_dcf_reconciliation.py`
- Durable evidence preservation (H-1): **REMEDIATED** -- see above
- Cached PASS/FAIL verdicts on formula cells (M-1): **REMEDIATED** -- see "What 'PASS/FAIL' means at each layer" above
- Second-reviewer sign-off: **PENDING**
- Profitability evidence: **NOT ESTABLISHED**
