"""Phase 7 (revised Track A Phase 2C): renders reconciliation_results.json
into a human-readable reconciliation_report.md. Pure function of its input
dict -- deterministic."""
from validation.dcf_reconciliation.cell_map import METRIC_LABELS, REQUIRED_METRICS
from validation.dcf_reconciliation.adapter import TICKERS
from validation.dcf_reconciliation.coverage import (
    EXPECTED_SCALAR_COMPARISONS_PER_COMPANY,
    EXPECTED_SCALAR_COMPARISONS_TOTAL,
    sensitivity_coverage_rows,
)

ENTITY_NAMES = {
    "MSFT": "Microsoft Corporation",
    "CAT": "Caterpillar Inc.",
    "INTC": "Intel Corporation",
    "VZ": "Verizon Communications Inc.",
}


def _fmt(v, metric=None):
    if isinstance(v, str):
        return v
    if metric in ("revenue_cagr", "operating_margin", "wacc"):
        return f"{v:.6%}"
    return f"{v:,.4f}"


def _pass_fail(passed):
    return "PASS" if passed else "**FAIL**"


def _base_table_for_company(ticker, comparisons):
    lines = [
        "| Metric | Codebase | Workbook (V2) | Abs. Diff | Diff | Tolerance | Result | Workbook Cell |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in comparisons:
        metric = row["metric"]
        label = row["label"]
        diff_str = (
            f"{row['difference_value']:.4f}pp" if row["difference_kind"] == "percentage_points"
            else f"{row['difference_value']:.4%}" if row["difference_kind"] == "relative_fraction"
            else f"${row['difference_value']:.8f} (abs, zero-carveout)"
        )
        tol_str = (
            f"±{row['tolerance_value']:.4f}pp" if row["tolerance_kind"] == "percentage_points"
            else f"±{row['tolerance_value']:.4%}" if row["tolerance_kind"] == "relative_fraction"
            else f"±${row['tolerance_value']:.8f}"
        )
        lines.append(
            f"| {label} | {_fmt(row['codebase_value'], metric)} | {_fmt(row['workbook_value'], metric)} "
            f"| {row['absolute_difference']:,.6f} | {diff_str} | {tol_str} | {_pass_fail(row['passed'])} "
            f"| `{row['workbook_sheet']}!{row['workbook_cell']}` |"
        )
    return "\n".join(lines)


def _sensitivity_table_summary(ticker, sens):
    lines = ["| Table | Scenario Rows | Outputs/Row | Scalar Comparisons | All Pass | Workbook Direction | Recomputed Direction | Agree |",
             "|---|---|---|---|---|---|---|---|"]
    coverage = {label: (scen, outs, scal, ok) for label, scen, outs, scal, ok in sensitivity_coverage_rows(sens)}
    direction_specs = [
        ("Table 1 -- WACC", "table1_wacc", "workbook_monotonic_decreasing", "recomputed_monotonic_decreasing", "decreasing"),
        ("Table 2 -- Terminal growth", "table2_terminal_growth", "workbook_monotonic_increasing", "recomputed_monotonic_increasing", "increasing"),
        ("Table 3 -- Revenue growth", "table3_revenue_growth", "workbook_monotonic_increasing", "recomputed_monotonic_increasing", "increasing"),
        ("Table 4 -- Operating margin", "table4_operating_margin", "workbook_monotonic_increasing", "recomputed_monotonic_increasing", "increasing"),
    ]
    total_scalar = 0
    for label, key, wb_dir_key, rc_dir_key, expected in direction_specs:
        t = sens[key]
        scen, outs, scal, ok = coverage[label]
        total_scalar += scal
        wb_dir = t[wb_dir_key]
        rc_dir = t[rc_dir_key]
        agree = wb_dir == rc_dir
        lines.append(
            f"| {label} | {scen} | {outs} | {scal} | {_pass_fail(ok)} "
            f"| {'as expected' if wb_dir else 'INVERTED'} ({expected}) "
            f"| {'as expected' if rc_dir else 'INVERTED'} ({expected}) "
            f"| {'yes' if agree else '**NO**'} |"
        )
    t5_label = "Table 5 -- WACC x g two-way grid"
    scen, outs, scal, ok = coverage[t5_label]
    total_scalar += scal
    t5 = sens["table5_two_way"]
    lines.append(
        f"| {t5_label} | {scen}/{t5['cells_expected']} | {outs} | {scal} "
        f"| {_pass_fail(t5['all_passed'])} | n/a | n/a | n/a |"
    )
    lines.append(f"| **TOTAL (this company)** | | | **{total_scalar}** | | | | |")
    assert total_scalar == EXPECTED_SCALAR_COMPARISONS_PER_COMPANY, (
        f"{ticker}: expected {EXPECTED_SCALAR_COMPARISONS_PER_COMPANY} scalar comparisons, got {total_scalar}."
    )
    return "\n".join(lines)


def build_report_markdown(results: dict) -> str:
    verdict = results["verdict"]
    v1_hash = results.get("v1_workbook_sha256", results.get("original_workbook_sha256"))
    v2_hash = results.get("v2_workbook_sha256")
    lines = []
    lines.append("# DCF Codebase-to-Independent-Workbook Reconciliation Report")
    lines.append("")
    lines.append(
        "Track A Phase 2C. Compares `src/dcf_model/dcf.py`'s production output against the independent "
        "workbook **V2** (`validation/independent_dcf/independent_dcf_validation_v2.xlsx`), built from "
        "the `years_elapsed` specification clarified after Track A Phase 2B's original reconciliation "
        "attempt found a genuine, tolerance-exceeding discrepancy against workbook **V1**. V1 "
        "(`independent_dcf_validation.xlsx`) is preserved unchanged; its original failing evidence is "
        "archived at `validation/dcf_reconciliation/history/phase2b_initial_no_go/` and was never edited. "
        "Neither calculation path was modified to force agreement; this report records what was actually "
        "found."
    )
    lines.append("")
    lines.append(f"- **Branch**: `{results['branch']}`")
    lines.append(f"- **Commit**: `{results['commit']}`")
    lines.append(f"- **V1 workbook SHA-256** (preserved, historical): `{v1_hash}`")
    lines.append(f"- **V2 workbook SHA-256** (reconciliation target): `{v2_hash}`")
    lines.append("- **Frozen snapshot SHA-256**:")
    for ticker in TICKERS:
        lines.append(f"  - `{ticker}`: `{results['snapshot_sha256'][ticker]}`")
    lines.append("")
    lines.append(f"## Overall verdict: {'GO' if verdict['overall_pass'] else 'NO-GO'}")
    lines.append("")
    lines.append(f"- Base-case reconciliation (vs. V2): {'all pass' if verdict['base_case_all_pass'] else 'FAILURES: ' + ', '.join(verdict['companies_with_base_divergence'])}")
    lines.append(f"- Sensitivity reconciliation (vs. V2): {'all pass' if verdict['sensitivity_all_pass'] else 'FAILURES present'}")
    lines.append("")
    lines.append("**Second-reviewer sign-off is PENDING** -- this report alone does not close the "
                  "checklist in `docs/independent-validation-plan.md`; see the Sign-off section of "
                  "`dcf_reconciliation.xlsx`.")
    lines.append("**Profitability is NOT established by this reconciliation** -- it verifies that two "
                  "independent calculation paths agree (or documents where/why they don't), not that "
                  "any company is investable.")
    lines.append("")

    lines.append("## What changed since Phase 2B, and why production needed no change")
    lines.append("")
    lines.append(
        "Phase 2B's original NO-GO was caused by INTC's Revenue CAGR: V1's `DCF_INTC!B5` (\"Years "
        "elapsed\") computed `years_elapsed` as `COUNT(periods)-1` (a plain period count = 4), while "
        "`src/dcf_model/dcf.py`'s `calculate_historical_revenue_cagr` computed the ACTUAL elapsed "
        "calendar days between the earliest and latest frozen fiscal-period-end dates, divided by "
        "365.25 (= 4.005476 for INTC, whose fiscal year-end dates are not evenly spaced -- 1,463 actual "
        "days, not the 1,461 that 4 years at 365.25 days/year would be). `docs/model-specifications/"
        "dcf.md`'s prose named `years_elapsed` without specifying its exact day-count convention -- a "
        "genuine specification-precision gap, not a numerical bug in either calculation path.\n\n"
        "Track A Phase 2C resolved the ambiguity by clarifying the specification (see `A-028` in the "
        "assumptions register and `L-019` in the limitations register): `years_elapsed` is now defined "
        "as actual elapsed calendar days between the earliest- and latest-dated valid observations, "
        "divided by 365.25 -- never a plain period count. **Production code required no change**: "
        "`calculate_historical_revenue_cagr`'s existing implementation already computed `years_elapsed` "
        "this way; the ambiguity was in the written specification's prose, not in `dcf.py`. A second "
        "independent workbook (V2) was built from the corrected specification, blind to the codebase, "
        "with a new explicit diagnostic row (in every `DCF_<TICKER>` sheet) that computes and displays "
        "the naive `(N periods - 1)` count alongside the actual `years_elapsed`, flagging any company "
        "whose fiscal calendar is irregular enough for the two to diverge -- INTC is the only company "
        "flagged. **V1's `COUNT-1` convention is not described as having conformed to the now-clarified "
        "specification** -- it was a reasonable, independent resolution of a genuine ambiguity that the "
        "clarification has since closed, not a defect that was 'fixed' by editing V1 (V1 is preserved "
        "unchanged)."
    )
    lines.append("")

    lines.append("## Base-case reconciliation, by company (vs. V2)")
    lines.append("")
    for ticker in TICKERS:
        br = results["base_reconciliation"][ticker]
        lines.append(f"### {ticker} ({ENTITY_NAMES[ticker]})")
        lines.append("")
        fd = br["first_divergence"]
        if fd is None:
            lines.append("All 14 required metrics within documented tolerance against V2.")
        else:
            lines.append(f"**First divergence: `{METRIC_LABELS[fd]}`** (first metric, in comparison order, outside its documented tolerance).")
        lines.append("")
        lines.append(_base_table_for_company(ticker, br["comparisons"]))
        lines.append("")
        diag = br["diagnostic_wacc_raw"]
        lines.append(f"_Diagnostic only (no codebase field, not part of REQUIRED_METRICS): Raw (pre-clamp) "
                      f"WACC = `{diag['workbook_value']:.6%}` at `{diag['workbook_sheet']}!{diag['workbook_cell']}`._")
        lines.append("")

    lines.append("## Directional bias across companies")
    lines.append("")
    lines.append("For every required metric, the SIGNED (codebase - workbook V2) difference across all "
                  "four companies, checking whether within-tolerance noise is randomly distributed or "
                  "consistently biased in one direction:")
    lines.append("")
    lines.append("| Metric | MSFT | CAT | INTC | VZ | Consistent nonzero bias? |")
    lines.append("|---|---|---|---|---|---|")
    for metric in REQUIRED_METRICS:
        db = results["directional_bias"][metric]
        vals = db["signed_differences"]
        lines.append(
            f"| {METRIC_LABELS[metric]} | {vals['MSFT']:.6g} | {vals['CAT']:.6g} | {vals['INTC']:.6g} | "
            f"{vals['VZ']:.6g} | {'YES' if db['consistent_directional_bias'] else 'no'} |"
        )
    lines.append("")
    any_bias = any(results["directional_bias"][m]["consistent_directional_bias"] for m in REQUIRED_METRICS)
    lines.append(
        "No consistent nonzero directional bias found across the four companies for any required metric."
        if not any_bias else
        "At least one metric shows a consistent nonzero directional bias across companies -- see the "
        "flagged row(s) above; this warrants investigation even though every individual comparison is "
        "within tolerance."
    )
    lines.append("")

    lines.append("## Sensitivity reconciliation, by company (vs. V2)")
    lines.append("")
    lines.append(
        f"Every numeric grid cell recomputed via the production functions (`project_free_cash_flows`, "
        f"`calculate_terminal_value`, `discount_to_present_value`, `calculate_intrinsic_value_per_share`) "
        f"and compared against workbook V2's own cached grid values (Table 1 WACC, Table 2 terminal "
        f"growth, Table 3 revenue growth, Table 4 operating margin: ±0.5% IVPS / ±0.1% FCF / ±0.2% "
        f"TV-EV-EqV; Table 5 two-way WACC x g grid: ±0.5% IVPS, exact `n/a` matching for invalid WACC<=g "
        f"combinations). **{EXPECTED_SCALAR_COMPARISONS_PER_COMPANY} scalar comparisons per company "
        f"({EXPECTED_SCALAR_COMPARISONS_TOTAL} total across four companies)** -- \"Scenario Rows\" "
        f"(how many distinct input values were swept) and \"Scalar Comparisons\" (scenario rows x "
        f"output metrics per row) are reported as two distinct numbers below, never conflated under one "
        f"ambiguous \"cells\" label. Every one of these {EXPECTED_SCALAR_COMPARISONS_TOTAL} comparisons "
        f"also appears as its own formula-driven row on `dcf_reconciliation.xlsx`'s 'All Sensitivity "
        f"Comparisons' sheet, not only here or in `reconciliation_results.json`."
    )
    lines.append("")
    for ticker in TICKERS:
        lines.append(f"### {ticker}")
        lines.append("")
        lines.append(_sensitivity_table_summary(ticker, results["sensitivity_reconciliation"][ticker]))
        lines.append("")

    lines.append("### INTC's documented directional exception")
    lines.append("")
    lines.append(
        "INTC's base-case FCF is negative in every projected year (structurally negative unit "
        "economics: `margin*(1-tax) + D&A% - CapEx%` per dollar of revenue is negative). Workbook V2's "
        "Sensitivity_INTC sheet documents (and this reconciliation's production recompute independently "
        "confirms) three inverted directional relationships versus the normal-case expectation: "
        "Intrinsic Value per Share *rises* (not falls) as WACC rises (discounting a negative stream "
        "less), *falls* (not rises) as terminal growth rises (a more negative terminal value), and "
        "*falls* (not rises) as revenue growth rises (compounding the loss). This is mathematically "
        "correct DCF behavior given negative cash flows, not a defect in either calculation path, and "
        "both V2 and the production recompute agree on every one of these inverted directions -- see "
        "the 'Workbook Direction' / 'Recomputed Direction' / 'Agree' columns for INTC above."
    )
    lines.append("")

    lines.append("## Stop/go decision")
    lines.append("")
    if verdict["overall_pass"]:
        lines.append(
            "**GO** to separate second-reviewer sign-off. All four companies' base-case reconciliations "
            f"pass against V2, all {EXPECTED_SCALAR_COMPARISONS_TOTAL} sensitivity scalar comparisons "
            "pass (or are correctly matched `n/a` markers), and negative controls "
            "(`tests/validation/test_dcf_reconciliation.py`) prove the reconciliation process is "
            "sensitive enough to catch a real defect. This does NOT itself constitute the required "
            "second-reviewer sign-off -- see the Sign-off section of `dcf_reconciliation.xlsx`."
        )
    else:
        lines.append(
            "**NO-GO.** " + (
                f"Base-case divergence(s) outside documented tolerance for: "
                f"{', '.join(verdict['companies_with_base_divergence'])}. "
                if verdict["companies_with_base_divergence"] else ""
            ) + (
                "Sensitivity reconciliation has failing cells. " if not verdict["sensitivity_all_pass"] else ""
            ) + "Per the task's NO-GO protocol: no source code or independent-workbook edits were made in "
                "response to this finding; the discrepancy is reported here for explicit human approval "
                "before any remediation is attempted."
        )
    lines.append("")
    lines.append("Second-reviewer sign-off: **PENDING** (not performed in this session).")
    lines.append("Profitability: **NOT established** by this or any prior validation phase.")
    lines.append("")
    return "\n".join(lines)
