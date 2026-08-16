# Independent Validation Plan

Status: **plan only — not yet executed.** This document specifies the next phase's
independent DCF validation; it does not implement it. Per
[`docs/model-development-roadmap.md`](model-development-roadmap.md), Track A
requires this validation to exist before Track B (performance research) may begin,
"specifically to catch a systematic bug that the same code checking itself against
itself could never catch." See `L-012` in
[`docs/limitations-register.md`](limitations-register.md).

## Purpose

The existing test suite ([`tests/dcf/test_dcf.py`](../tests/dcf/test_dcf.py) and
others) verifies internal consistency: boundary conditions, override precedence,
input validation. It cannot catch a bug that is consistent with itself — a formula
implemented incorrectly, with tests written to match that same incorrect
implementation, would pass every existing test while still being wrong. The only
way to catch that class of error is a **second, independently built calculation
path** that was never derived from, and never calls, this codebase's source.

## Design principle: genuine independence

The validation workbook must be built from
[`docs/model-specifications/dcf.md`](model-specifications/dcf.md) and
[`docs/model-specifications/wacc-capm.md`](model-specifications/wacc-capm.md) —
the **written specification** — not from reading `src/dcf_model/dcf.py`'s source
code, and never by calling into this codebase (no importing `run_dcf_valuation`,
no copy-pasting a formula string from the `.py` file into a spreadsheet cell). If
the builder finds themselves needing to open `dcf.py` to resolve an ambiguity, that
ambiguity is itself a defect in the written specification and should be fixed
there first, then the workbook continued from the corrected spec — never resolved
by directly copying the implementation's exact code.

**Formulas must be entered independently, cell by cell, from the spec's prose and
equations** — not copied from any Python expression. The specific arithmetic
sequence (e.g. whether an intermediate value is rounded, in what order operations
are applied) may differ between the workbook and the codebase as long as both
implement the same specified formula; a divergence here is itself informative (see
"Reconciliation format" below).

## Workbook structure

An independently constructed spreadsheet workbook (one company per sheet, plus a
shared assumptions/summary sheet), computing, per company:

1. Historical Revenue CAGR and average Operating Margin from raw statement inputs
   (entered manually from the frozen snapshot, not pulled live).
2. WACC via CAPM (cost of equity, after-tax cost of debt, market-value capital
   weights).
3. 5-year FCF projection (Revenue → EBIT → NOPAT → +D&A → −CapEx → −ΔNWC → FCF).
4. Terminal value (Gordon Growth).
5. Present value of explicit FCF + terminal value → Enterprise Value.
6. Enterprise Value → Equity Value → Intrinsic Value per Share.

Each cell should carry a comment or adjacent label naming which spec section and
equation it implements, so a reviewer can check the workbook against the spec
independently of checking it against this codebase's output.

## Validation companies

At least **three** companies with materially different financial profiles, so the
validation exercises the model's behavior across genuinely different regimes, not
three similar large-cap technology names:

| # | Profile | Rationale | Candidate (illustrative — confirm data availability at execution time) |
|---|---|---|---|
| 1 | Large-cap, capital-light, high-margin | Tests the "clean" case: strong historical growth, high operating margin, low leverage | e.g. a mega-cap software/platform company |
| 2 | Capital-intensive, moderate leverage | Tests D&A/CapEx assumptions against a company where those percentages are actually large relative to revenue (stresses `A-004`'s fixed-percentage simplification) | e.g. an industrial or utility-adjacent name (noting the Altman-Z sector-exclusion list, `A-010`, does not apply to the DCF itself) |
| 3 | Negative or near-zero historical revenue growth | Tests the "shrinking company" path — `A-002`'s deliberate choice to leave negative historical growth unbounded below | e.g. a mature company with a declining core business line |

A fourth company with meaningful debt (to stress-test the WACC capital-weight and
Equity Value bridge) is recommended but not required for the minimum validation
pass.

## Frozen input snapshots

For each validation company, the exact income statement, balance sheet, cash flow
statement, current price, shares outstanding, and beta used must be **captured and
frozen** (e.g. saved as a dated CSV/JSON snapshot alongside the workbook) at the
moment validation begins — not re-fetched live at comparison time. This is required
because:

- yfinance data can change between when the workbook is built and when the
  comparison is run (a later statement revision, a stock split, a fresh price).
- Without a frozen snapshot, a discrepancy between the workbook and the codebase
  could be caused by the two simply having pulled data at different moments, which
  would be indistinguishable from a genuine calculation error.

This also anticipates Track A's "Immutable input snapshots" requirement
([`docs/model-development-roadmap.md`](model-development-roadmap.md)) — the same
snapshot discipline this validation needs is the discipline Track B will need at
scale.

## Comparison procedure — without one calling the other

1. **Run the codebase** (`run_dcf_valuation`) against the frozen snapshot data for
   each validation company, capturing every intermediate output (WACC, each
   projected year's FCF, terminal value, PV of FCF, PV of terminal value,
   Enterprise Value, Equity Value, intrinsic value per share) — not just the final
   number.
2. **Complete the workbook** independently against the same frozen snapshot data,
   producing the same set of intermediate outputs.
3. **Reconcile** the two sets of intermediate outputs side by side (see
   "Reconciliation format" below) — never by having the workbook call the codebase
   or vice versa. The two computations must remain mechanically independent
   throughout; only their *outputs* are compared, and only after both are complete.
4. Any discrepancy is investigated at the **first intermediate value** where the
   two diverge, not just at the final intrinsic value — this localizes whether a
   discrepancy originates in, e.g., the CAGR calculation, the WACC formula, the FCF
   projection, or the terminal value/discounting step.

## Expected comparison tolerances

| Value | Tolerance | Rationale |
|---|---|---|
| Historical Revenue CAGR, Operating Margin | Exact match (± 0.01 percentage point) | Both are simple, unambiguous arithmetic from the same frozen inputs — any divergence beyond floating-point noise indicates a real discrepancy |
| WACC | ± 0.05 percentage point | Allows for minor rounding-order differences (e.g. whether cost of equity is rounded before or after weighting) |
| Each projected year's FCF | ± 0.1% of that year's value | Compounding of the growth-rate/margin rounding tolerance across years |
| Terminal value | ± 0.2% | Sensitive to WACC/terminal-growth rounding (see `L-005`) |
| Enterprise Value, Equity Value | ± 0.2% | Sum of the above tolerances |
| Intrinsic Value per Share | ± 0.5% | Final compounded tolerance |

A discrepancy **larger** than these tolerances is a validation failure requiring
investigation before this company's result is accepted. A discrepancy **within**
tolerance but consistently in the same direction across all three companies is
still worth investigating — a systematic small bias is more concerning than random
noise of the same magnitude.

## Reconciliation format

For each validation company, a reconciliation table:

| Intermediate value | Codebase output | Workbook output | Absolute difference | Within tolerance? | Notes |
|---|---|---|---|---|---|
| Revenue CAGR | | | | | |
| Operating Margin | | | | | |
| WACC | | | | | |
| FCF Year 1 | | | | | |
| FCF Year 2 | | | | | |
| ... | | | | | |
| Terminal Value | | | | | |
| Enterprise Value | | | | | |
| Equity Value | | | | | |
| Intrinsic Value / Share | | | | | |

## Sensitivity tables

For each validation company, in the workbook, produce a sensitivity table varying
one input at a time while holding others at their base-case value, over a
reasonable grid (e.g. 5–7 points spanning each variable's valid range from
[`docs/model-specifications/dcf.md`](model-specifications/dcf.md)):

- **WACC** sensitivity: intrinsic value per share across a range of WACC values
  spanning `[5%, 20%]` (the codebase's own clamp range).
- **Terminal growth rate** sensitivity: across `[0%, 5%]`.
- **Revenue growth rate** sensitivity: across `[-10%, 40%]` (the explicit-override
  bounds).
- **Operating margin** sensitivity: across `[0%, 60%]`.

These tables both satisfy Track A's separately-required sensitivity-analysis
deliverable (referenced throughout
[`docs/assumptions-register.md`](assumptions-register.md), e.g. `A-003`, `A-005`)
and give the reconciliation exercise a way to check that the codebase's *behavior*
across a range — not just its output at one specific input set — matches the
specification.

## Deliberate error-injection checks

To confirm the reconciliation process itself is sensitive enough to catch a real
discrepancy (not just passing because both sides happen to agree by construction),
deliberately introduce at least one intentional, known error into a **copy** of the
workbook (e.g. flip a sign in the terminal value formula, or use `WACC` in place of
`WACC − g` in the denominator) and confirm the reconciliation table correctly
flags a tolerance failure at the expected intermediate value. This copy is
discarded after the check; only the correct workbook is retained. Document that
this check was performed and its result.

## Review / sign-off checklist

Before a validation company's result is accepted as "independently validated":

- [ ] Input snapshot is frozen, dated, and stored alongside the workbook.
- [ ] Every workbook formula cell is traceable to a specific
      [`docs/model-specifications/dcf.md`](model-specifications/dcf.md) /
      [`docs/model-specifications/wacc-capm.md`](model-specifications/wacc-capm.md)
      section, not to `dcf.py`'s source.
- [ ] The workbook was built without importing or calling any code from this
      repository.
- [ ] The full intermediate-value reconciliation table is complete for this
      company, with every row within its documented tolerance or an investigated
      and resolved discrepancy noted.
- [ ] The sensitivity tables (WACC, terminal growth, revenue growth, margin) are
      complete for this company.
- [ ] The deliberate error-injection check has been performed at least once for
      this validation pass (not necessarily once per company) and its result
      documented.
- [ ] A second reviewer (not the workbook's builder) has independently checked at
      least the final reconciliation table and one sensitivity table.
- [ ] Any discrepancy beyond tolerance has either been resolved (with the fix
      recorded in [`docs/model-change-log.md`](model-change-log.md) if it required
      a code change) or explicitly documented as an accepted, understood
      divergence with its cause.

## Stop/go criteria before Track B begins

Per [`docs/model-development-roadmap.md`](model-development-roadmap.md)'s Track A
stop/go gate, Track B (performance research) may not proceed on a given model until
that model's independent validation is complete. Specifically, for the DCF model:

- **Go**: All three (minimum) validation companies pass reconciliation within
  tolerance, the sensitivity tables are complete and behave as the specification
  describes (e.g. intrinsic value rises monotonically as WACC falls, holding other
  inputs fixed), and the review/sign-off checklist above is fully checked.
- **No-go**: Any validation company shows an unresolved discrepancy beyond
  tolerance, the error-injection check fails to catch the deliberately introduced
  error (indicating the reconciliation process itself is not sensitive enough to
  trust), or the sign-off checklist has unchecked items. In this case, Track B work
  depending on the DCF model does not begin, and the discrepancy is investigated
  and either fixed (with a `docs/model-change-log.md` entry) or documented as an
  accepted limitation before re-attempting validation.

This same structure (spec-derived independent workbook, frozen snapshots,
intermediate-value reconciliation with tolerances, sensitivity tables, error
injection, sign-off checklist, stop/go gate) should be reused for any other model
Track B comes to depend on (e.g. the Monte Carlo VaR engine, once Track B item 10's
risk-model comparison begins) — this document's structure, not just its DCF-
specific content, is the reusable template.
