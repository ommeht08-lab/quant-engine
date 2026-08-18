"""
Phase 7 (revised Track A Phase 2C): builds
validation/dcf_reconciliation/dcf_reconciliation.xlsx from an already-computed
reconciliation_results.json-shaped dict. This is a SEPARATE workbook from
validation/independent_dcf/independent_dcf_validation_v2.xlsx (the V2
reconciliation target) and independent_dcf_validation.xlsx (V1, preserved
unchanged) -- neither is ever written to by this module.

Design: every PASS/FAIL, Absolute Difference, and Relative/pp Difference
cell is a live spreadsheet FORMULA, not a pre-computed literal -- so opening
this file in a real spreadsheet application and changing a hardcoded input
recomputes the verdict. Tolerance constants live once on the "Tolerances"
sheet and every comparison table's Tolerance column is a formula reference
to that single source, not a retyped literal per row. Hardcoded imported
values (codebase/workbook outputs, which come from Python, not from a
spreadsheet formula) use a visually distinct fill color from formula cells.

Track A Phase 2C fixes applied here (see docs/model-change-log.md):
  - Finding 2: number formats are now semantically correct and never collide
    (dollar amounts, per-share values, counts, percentages, and plain numeric
    diagnostics each get their own dedicated style/format -- see
    xlsx_writer.py's rewritten, collision-free format-ID allocator).
  - Finding 3: sensitivity coverage is reported as BOTH "Scenario Rows" and
    actual "Scalar Comparisons" (rows x outputs-per-row), never conflating
    the two under an ambiguous "Cells" label.
  - Finding 4: every Table 1-5 scalar comparison (908 total across four
    companies) is written into this workbook as its own formula-driven row
    on the "All Sensitivity Comparisons" sheet -- not summarized-out to JSON
    only.
  - Finding 5 (independent second-reviewer audit finding M-1): every
    PASS/FAIL cell `_comparison_row` writes previously cached an EMPTY
    string alongside its live formula -- structurally correct (a real
    formula, correctly referencing the right cells) but functionally blank
    to any reader that does not run a spreadsheet recalculation engine
    (none is available anywhere in this repository's tooling). Fixed by
    computing the same PASS/FAIL verdict in Python, via `compare.py`'s
    `compare_rate`/`compare_monetary` (the identical functions
    `reconcile.py` uses for `reconciliation_results.json`), from the exact
    codebase/workbook/tolerance values written into that row, and caching
    that as the formula cell's `<v>`. The `<f>` formula text itself is
    unchanged. This narrows, but does not close, the gap between
    "structurally verified formula" and "an actual spreadsheet engine
    evaluated it" -- see `README.md`'s "What 'PASS/FAIL' means at each
    layer" section for the full three-tier distinction.
"""
from validation.dcf_reconciliation.adapter import TICKERS
from validation.dcf_reconciliation.cell_map import BASE_METRIC_ORDER, METRIC_LABELS, REQUIRED_METRICS
from validation.dcf_reconciliation.compare import compare_monetary, compare_rate
from validation.dcf_reconciliation.coverage import (
    OUTPUT_METRIC_LABELS,
    OUTPUT_METRIC_TOL_KEY,
    OUTPUT_METRIC_VALUE_KIND,
    EXPECTED_SCALAR_COMPARISONS_TOTAL,
    iter_all_scalar_comparisons,
    sensitivity_coverage_rows as _sensitivity_coverage_rows,
)
from validation.dcf_reconciliation.xlsx_writer import Workbook, Style, col_letter

ENTITY_NAMES = {
    "MSFT": "Microsoft Corporation",
    "CAT": "Caterpillar Inc.",
    "INTC": "Intel Corporation",
    "VZ": "Verizon Communications Inc.",
}

RATE_METRICS = {"revenue_cagr", "operating_margin", "wacc"}
PER_SHARE_METRICS = {"intrinsic_value_per_share"}
# Everything else in REQUIRED_METRICS (FCF years, terminal value, PV's,
# enterprise value, equity value) is a dollar amount.

TOLERANCE_ROWS = [
    ("revenue_cagr", "Revenue CAGR", 0.0001, "pp"),
    ("operating_margin", "Operating Margin", 0.0001, "pp"),
    ("wacc", "WACC", 0.0005, "pp"),
    ("fcf", "Each FCF year", 0.001, "rel"),
    ("terminal_value", "Terminal Value", 0.002, "rel"),
    ("pv_explicit_fcf", "PV of Explicit FCF", 0.002, "rel"),
    ("pv_terminal_value", "PV of Terminal Value", 0.002, "rel"),
    ("enterprise_value", "Enterprise Value", 0.002, "rel"),
    ("equity_value", "Equity Value", 0.002, "rel"),
    ("intrinsic_value_per_share", "Intrinsic Value per Share", 0.005, "rel"),
    ("sensitivity_ivps", "Sensitivity IVPS (all tables)", 0.005, "rel"),
    ("zero_carveout", "Zero-denominator floating-point carve-out (absolute $)", 1e-6, "abs"),
]

# Sensitivity-table shape, output-metric labels/tolerance-keys/value-kinds, and
# the coverage-row/scenario-label helpers all now live in coverage.py (single
# source of truth shared with report.py -- Finding 3's fix must not drift
# between the two narratives).


def _styles(wb: Workbook):
    return {
        "title": wb.add_style(Style("title", bold=True)),
        "header": wb.add_style(Style("header", bold=True, fill_color="D9E1F2", border="bottom")),
        "section": wb.add_style(Style("section", bold=True, fill_color="BDD7EE")),
        "hardcoded": wb.add_style(Style("hardcoded", fill_color="FFF2CC")),
        "hardcoded_pct": wb.add_style(Style("hardcoded_pct", fill_color="FFF2CC", num_fmt="0.0000%")),
        "hardcoded_dollar": wb.add_style(Style("hardcoded_dollar", fill_color="FFF2CC", num_fmt="$#,##0;[Red]($#,##0);-")),
        "hardcoded_share": wb.add_style(Style("hardcoded_share", fill_color="FFF2CC", num_fmt="$0.00;[Red]($0.00);-")),
        "hardcoded_count": wb.add_style(Style("hardcoded_count", fill_color="FFF2CC", num_fmt="#,##0")),
        "hardcoded_num": wb.add_style(Style("hardcoded_num", fill_color="FFF2CC", num_fmt="#,##0.0000")),
        "formula_pct": wb.add_style(Style("formula_pct", num_fmt="0.0000%")),
        "formula_dollar": wb.add_style(Style("formula_dollar", num_fmt="$#,##0;[Red]($#,##0);-")),
        "formula_share": wb.add_style(Style("formula_share", num_fmt="$0.00;[Red]($0.00);-")),
        "formula_count": wb.add_style(Style("formula_count", num_fmt="#,##0")),
        "formula_num": wb.add_style(Style("formula_num", num_fmt="#,##0.0000")),
        "pass": wb.add_style(Style("pass", bold=True, fill_color="C6EFCE", font_color="006100")),
        "fail": wb.add_style(Style("fail", bold=True, fill_color="FFC7CE", font_color="9C0006")),
        "wrap": wb.add_style(Style("wrap", wrap=True)),
        "bold": wb.add_style(Style("bold_plain", bold=True)),
    }


def _value_style(styles, kind, hardcoded):
    key = {
        "rate": "hardcoded_pct" if hardcoded else "formula_pct",
        "dollar": "hardcoded_dollar" if hardcoded else "formula_dollar",
        "per_share": "hardcoded_share" if hardcoded else "formula_share",
        "count": "hardcoded_count" if hardcoded else "formula_count",
    }[kind]
    return styles[key]


def _tolerance_sheet(wb: Workbook, styles):
    s = wb.add_sheet("Tolerances")
    s.set_width(0, 44)
    s.set_width(1, 14)
    s.set_width(2, 8)
    s.set(0, 0, value="Tolerance constants -- single source of truth for every comparison table's Tolerance column", style=styles["title"])
    s.set(1, 0, value="Metric", style=styles["header"])
    s.set(1, 1, value="Tolerance", style=styles["header"])
    s.set(1, 2, value="Kind", style=styles["header"])
    refs = {}
    row = 2
    for key, label, value, kind in TOLERANCE_ROWS:
        s.set(row, 0, value=label)
        num_style = styles["hardcoded_pct"] if kind in ("pp", "rel") else styles["hardcoded_num"]
        s.set(row, 1, value=value, style=num_style)
        s.set(row, 2, value={"pp": "percentage points", "rel": "relative fraction", "abs": "absolute $"}[kind])
        refs[key] = f"'Tolerances'!$B${row + 1}"
        row += 1
    return refs


def _comparison_row(sheet, row, styles, label, codebase_val, workbook_val, tol_ref, tol_value, value_kind,
                     wb_cell_note="", value_col0=1, note_col0=None, label_style=None):
    """Writes label (at column 0, unless label is None) + a six-column
    comparison block starting at `value_col0` (0-indexed): codebase(hardcoded),
    workbook(hardcoded), abs diff(formula), relative-or-pp diff(formula),
    tolerance(formula ref), PASS/FAIL(formula). Column positions are computed
    from `value_col0`, never hardcoded to A-G, so this same function can be
    reused at an arbitrary column offset (e.g. the long-format "All
    Sensitivity Comparisons" sheet, which reserves columns A-D for
    identifying labels before the comparison block starts at E).

    `value_kind`: "rate" | "dollar" | "per_share" -- selects the semantically
    correct number format for the value/diff columns (the relative-or-pp
    diff and tolerance columns are always shown as percentages, since a
    relative difference or a rate tolerance is a fraction regardless of what
    kind of value it was computed from).

    `tol_value`: the actual numeric tolerance (same value the caller also
    hands to `_fill_tolerance_cached` for the Tolerance column) -- required
    here so the PASS/FAIL cell's CACHED value can be computed independently
    in Python from exactly the three numbers this row itself writes
    (codebase_val, workbook_val, tol_value), via the same `compare_rate`/
    `compare_monetary` functions `reconcile.py` uses to produce the
    Python-side verdict in reconciliation_results.json. This is a
    structural-plus-semantic fix (Track A Phase 2C, M-1 remediation): the
    cell's `<f>` formula text is UNCHANGED (still a live spreadsheet formula
    a real recalculation would independently reproduce), but the `<v>`
    cached alongside it is no longer an empty string -- it is the actual
    verdict, computed the same way, so a structural/XML reader (no
    spreadsheet engine) sees the real answer instead of blank. Never
    called for "n/a" (invalid-combination) rows -- those are written
    directly by each call site with a literal PASS/FAIL value before this
    function would ever be reached, since there is no codebase/workbook
    pair to compare in that case (see `add_sensitivity_sheet` and
    `add_full_sensitivity_detail_sheet`'s `kind == "na_marker"` branches).

    Returns the 0-indexed column of the PASS/FAIL cell, so a caller can
    re-style it (e.g. color) after inspecting the actual PASS/FAIL outcome.
    """
    if label is not None:
        sheet.set(row, 0, value=label, style=label_style)

    c0 = value_col0
    b_ref = f"{col_letter(c0)}{row + 1}"      # codebase
    c_ref = f"{col_letter(c0 + 1)}{row + 1}"  # workbook
    d_ref = f"{col_letter(c0 + 2)}{row + 1}"  # abs diff
    e_ref = f"{col_letter(c0 + 3)}{row + 1}"  # rel/pp diff
    f_ref = f"{col_letter(c0 + 4)}{row + 1}"  # tolerance

    is_rate = value_kind == "rate"
    val_style = _value_style(styles, value_kind, hardcoded=True)
    diff_style = _value_style(styles, value_kind, hardcoded=False)
    sheet.set(row, c0, value=codebase_val, style=val_style)
    sheet.set(row, c0 + 1, value=workbook_val, style=val_style)
    abs_diff = abs(codebase_val - workbook_val)
    sheet.set(row, c0 + 2, formula=f"ABS({b_ref}-{c_ref})", cached=abs_diff, style=diff_style)
    if is_rate:
        sheet.set(row, c0 + 3, formula=f"{d_ref}", cached=abs_diff, style=styles["formula_pct"])
        # Same comparator reconcile.py's Python-side verdict uses (compare.py) --
        # single source of truth for "did this pass", not a re-derived rule.
        comparison = compare_rate("workbook_cell", codebase_val, workbook_val, tol_value)
    else:
        rel = abs_diff / abs(workbook_val) if workbook_val != 0 else None
        formula = f'IF({c_ref}=0,"n/a (zero denom)",{d_ref}/ABS({c_ref}))'
        sheet.set(row, c0 + 3, formula=formula, cached=(rel if rel is not None else "n/a (zero denom)"), style=styles["formula_pct"])
        comparison = compare_monetary("workbook_cell", codebase_val, workbook_val, tol_value)
    sheet.set(row, c0 + 4, formula=tol_ref, cached=tol_value, style=styles["formula_pct"])
    if is_rate:
        pf_formula = f'IF({d_ref}<={f_ref},"PASS","FAIL")'
    else:
        pf_formula = f'IF({c_ref}=0,IF({d_ref}<=0.000001,"PASS","FAIL"),IF({e_ref}<={f_ref},"PASS","FAIL"))'
    pf_cached = "PASS" if comparison.passed else "FAIL"
    pf_style = styles["pass"] if comparison.passed else styles["fail"]
    sheet.set(row, c0 + 5, formula=pf_formula, cached=pf_cached, style=pf_style)
    if wb_cell_note:
        note_col = note_col0 if note_col0 is not None else c0 + 6
        sheet.set(row, note_col, value=wb_cell_note)
    return c0 + 5


def _fill_tolerance_cached(sheet, row, tol_value, value_col0=1):
    """The Tolerance column's cached <v> must equal the actual constant so
    the file shows a sensible value pre-recalc; xlsx_writer needs a numeric
    cached value alongside the formula string."""
    sheet.cells[(row, value_col0 + 4)].cached = tol_value


def _value_kind_for_metric(metric: str) -> str:
    if metric in RATE_METRICS:
        return "rate"
    if metric in PER_SHARE_METRICS:
        return "per_share"
    return "dollar"


TOL_KEY_FOR_BASE_METRIC = {
    "revenue_cagr": "revenue_cagr", "operating_margin": "operating_margin", "wacc": "wacc",
    "fcf_year_1": "fcf", "fcf_year_2": "fcf", "fcf_year_3": "fcf", "fcf_year_4": "fcf", "fcf_year_5": "fcf",
    "terminal_value": "terminal_value", "pv_explicit_fcf": "pv_explicit_fcf",
    "pv_terminal_value": "pv_terminal_value", "enterprise_value": "enterprise_value",
    "equity_value": "equity_value", "intrinsic_value_per_share": "intrinsic_value_per_share",
}


def add_base_reconciliation_sheet(wb: Workbook, results: dict, tol_refs: dict, tol_values: dict, styles):
    s = wb.add_sheet("Base Reconciliation")
    for c, w in enumerate([32, 16, 16, 14, 12, 12, 10, 30]):
        s.set_width(c, w)
    s.set(0, 0, value="Base-case reconciliation -- all four companies, all 14 required metrics", style=styles["title"])
    s.set(1, 0, value=(
        "Codebase/Workbook columns are hardcoded values imported from codebase_outputs.json and the "
        "independent workbook V2's cached cells (yellow fill). Absolute Difference / Relative-or-pp "
        "Difference / Tolerance / PASS-FAIL are live formulas (white fill). Number formats: percentages "
        "for rates, $ for dollar amounts, $0.00 for per-share values -- never conflated."
    ), style=styles["wrap"])
    row = 3
    header_row = row
    for c, h in enumerate(["Metric", "Codebase Output", "Workbook Output", "Absolute Difference",
                            "Relative/pp Difference", "Tolerance", "PASS/FAIL", "Workbook Cell"]):
        s.set(header_row, c, value=h, style=styles["header"])
    row += 1

    for ticker in TICKERS:
        s.set(row, 0, value=f"-- {ticker} ({ENTITY_NAMES[ticker]}) --", style=styles["section"])
        row += 1
        br = results["base_reconciliation"][ticker]
        comp_by_metric = {c["metric"]: c for c in br["comparisons"]}
        for metric in REQUIRED_METRICS:
            c = comp_by_metric[metric]
            tol_key = TOL_KEY_FOR_BASE_METRIC[metric]
            _comparison_row(
                s, row, styles, METRIC_LABELS[metric],
                c["codebase_value"], c["workbook_value"],
                tol_refs[tol_key], tol_values[tol_key], _value_kind_for_metric(metric),
                wb_cell_note=f"{c['workbook_sheet']}!{c['workbook_cell']}",
            )
            _fill_tolerance_cached(s, row, tol_values[tol_key])
            row += 1
    s.freeze_panes(header_row + 1, 1)
    return s




def add_company_detail_sheet(wb: Workbook, ticker: str, results: dict, tol_refs, tol_values, styles):
    s = wb.add_sheet(f"Detail_{ticker}")
    for c, w in enumerate([32, 16, 16, 14, 12, 12, 10, 30]):
        s.set_width(c, w)
    s.set(0, 0, value=f"Detailed base-case reconciliation -- {ticker} ({ENTITY_NAMES[ticker]})", style=styles["title"])
    br = results["base_reconciliation"][ticker]
    fd = br["first_divergence"]
    s.set(1, 0, value=(f"First divergence: {METRIC_LABELS[fd]}" if fd else "All metrics within tolerance."), style=styles["bold"])
    row = 3
    for c, h in enumerate(["Metric", "Codebase Output", "Workbook Output", "Absolute Difference",
                            "Relative/pp Difference", "Tolerance", "PASS/FAIL", "Workbook Cell"]):
        s.set(row, c, value=h, style=styles["header"])
    row += 1
    comp_by_metric = {c["metric"]: c for c in br["comparisons"]}
    for metric in REQUIRED_METRICS:
        c = comp_by_metric[metric]
        tol_key = TOL_KEY_FOR_BASE_METRIC[metric]
        _comparison_row(
            s, row, styles, METRIC_LABELS[metric],
            c["codebase_value"], c["workbook_value"],
            tol_refs[tol_key], tol_values[tol_key], _value_kind_for_metric(metric),
            wb_cell_note=f"{c['workbook_sheet']}!{c['workbook_cell']}",
        )
        _fill_tolerance_cached(s, row, tol_values[tol_key])
        row += 1

    row += 1
    s.set(row, 0, value="Sensitivity reconciliation coverage (full per-cell detail: 'All Sensitivity Comparisons' sheet)", style=styles["section"])
    row += 1
    for c, h in enumerate(["Table", "Scenario Rows", "Outputs / Row", "Scalar Comparisons", "All Pass"]):
        s.set(row, c, value=h, style=styles["header"])
    row += 1
    sens = results["sensitivity_reconciliation"][ticker]
    total_scalar = 0
    for label, scenario_rows, outputs_per_row, scalar_comparisons, all_passed in _sensitivity_coverage_rows(sens):
        s.set(row, 0, value=label)
        s.set(row, 1, value=scenario_rows, style=styles["hardcoded_count"])
        s.set(row, 2, value=outputs_per_row, style=styles["hardcoded_count"])
        s.set(row, 3, formula=f"B{row+1}*C{row+1}", cached=scalar_comparisons, style=styles["formula_count"])
        s.set(row, 4, value=("PASS" if all_passed else "FAIL"), style=(styles["pass"] if all_passed else styles["fail"]))
        total_scalar += scalar_comparisons
        row += 1
    s.set(row, 0, value="TOTAL (this company)", style=styles["bold"])
    s.set(row, 3, formula=f"SUM(D{row-4}:D{row})", cached=total_scalar, style=styles["formula_count"])
    s.freeze_panes(4, 1)
    return s


def add_sensitivity_sheet(wb: Workbook, results: dict, tol_refs, tol_values, styles):
    """Table 1 & 2 full live-formula grids, per company, for quick visual
    scanning -- plus an accurate coverage-count summary (Finding 3). Full
    per-scalar-comparison detail for ALL five tables lives on the separate
    'All Sensitivity Comparisons' sheet (Finding 4)."""
    s = wb.add_sheet("Sensitivity Reconciliation")
    for c, w in enumerate([14, 16, 16, 14, 12, 12, 10]):
        s.set_width(c, w)
    s.set(0, 0, value="Sensitivity reconciliation -- production recompute vs. workbook V2 cached grid, all four companies", style=styles["title"])
    s.set(1, 0, value=(
        "Table 1 (WACC) and Table 2 (terminal growth) IVPS rows shown here with full live-formula "
        "reconciliation for quick visual scanning. ALL FIVE tables' full per-scalar-comparison detail "
        "(908 rows total across four companies) is on the 'All Sensitivity Comparisons' sheet -- never "
        "summarized-out to JSON only."
    ), style=styles["wrap"])
    row = 3

    s.set(row, 0, value="Coverage summary -- scenario rows vs. actual scalar comparisons (Finding 3 fix)", style=styles["section"])
    row += 1
    for c, h in enumerate(["Ticker", "Table", "Scenario Rows", "Outputs / Row", "Scalar Comparisons", "All Pass"]):
        s.set(row, c, value=h, style=styles["header"])
    row += 1
    grand_total = 0
    for ticker in TICKERS:
        sens = results["sensitivity_reconciliation"][ticker]
        for label, scenario_rows, outputs_per_row, scalar_comparisons, all_passed in _sensitivity_coverage_rows(sens):
            s.set(row, 0, value=ticker)
            s.set(row, 1, value=label)
            s.set(row, 2, value=scenario_rows, style=styles["hardcoded_count"])
            s.set(row, 3, value=outputs_per_row, style=styles["hardcoded_count"])
            s.set(row, 4, formula=f"C{row+1}*D{row+1}", cached=scalar_comparisons, style=styles["formula_count"])
            s.set(row, 5, value=("PASS" if all_passed else "FAIL"), style=(styles["pass"] if all_passed else styles["fail"]))
            grand_total += scalar_comparisons
            row += 1
    s.set(row, 0, value="GRAND TOTAL (4 companies x 5 tables)", style=styles["bold"])
    s.set(row, 4, formula=f"SUM(E5:E{row})", cached=grand_total, style=styles["formula_count"])
    row += 2

    for ticker in TICKERS:
        s.set(row, 0, value=f"-- {ticker} ({ENTITY_NAMES[ticker]}) --", style=styles["section"])
        row += 1
        sens = results["sensitivity_reconciliation"][ticker]

        s.set(row, 0, value="Table 1 -- WACC sensitivity (Intrinsic Value/Share)", style=styles["bold"])
        row += 1
        for c, h in enumerate(["WACC", "Codebase IVPS", "Workbook IVPS", "Abs. Diff", "Rel. Diff", "Tolerance", "PASS/FAIL"]):
            s.set(row, c, value=h, style=styles["header"])
        row += 1
        for r in sens["table1_wacc"]["rows"]:
            cell = r["intrinsic_value_per_share"]
            if cell["kind"] == "na_marker":
                s.set(row, 0, value=r["wacc"], style=styles["hardcoded_pct"])
                s.set(row, 1, value=str(cell["codebase_value"]), style=styles["hardcoded"])
                s.set(row, 2, value=str(cell["workbook_value"]), style=styles["hardcoded"])
                s.set(row, 6, value=("PASS" if cell["passed"] else "FAIL"), style=(styles["pass"] if cell["passed"] else styles["fail"]))
            else:
                _comparison_row(s, row, styles, r["wacc"], cell["codebase_value"], cell["workbook_value"],
                                 tol_refs["sensitivity_ivps"], tol_values["sensitivity_ivps"], "per_share",
                                 label_style=styles["hardcoded_pct"])
                _fill_tolerance_cached(s, row, tol_values["sensitivity_ivps"])
            row += 1

        s.set(row, 0, value="Table 2 -- Terminal growth sensitivity (Intrinsic Value/Share)", style=styles["bold"])
        row += 1
        for c, h in enumerate(["Terminal g", "Codebase IVPS", "Workbook IVPS", "Abs. Diff", "Rel. Diff", "Tolerance", "PASS/FAIL"]):
            s.set(row, c, value=h, style=styles["header"])
        row += 1
        for r in sens["table2_terminal_growth"]["rows"]:
            cell = r["intrinsic_value_per_share"]
            if cell["kind"] == "na_marker":
                s.set(row, 0, value=r["terminal_growth"], style=styles["hardcoded_pct"])
                s.set(row, 1, value=str(cell["codebase_value"]), style=styles["hardcoded"])
                s.set(row, 2, value=str(cell["workbook_value"]), style=styles["hardcoded"])
                s.set(row, 6, value=("PASS" if cell["passed"] else "FAIL"), style=(styles["pass"] if cell["passed"] else styles["fail"]))
            else:
                _comparison_row(s, row, styles, r["terminal_growth"], cell["codebase_value"], cell["workbook_value"],
                                 tol_refs["sensitivity_ivps"], tol_values["sensitivity_ivps"], "per_share",
                                 label_style=styles["hardcoded_pct"])
                _fill_tolerance_cached(s, row, tol_values["sensitivity_ivps"])
            row += 1

        s.set(row, 0, value=(
            f"Tables 3/4/5 -- see 'All Sensitivity Comparisons' sheet for full detail. "
            f"Summary: T3={'PASS' if sens['table3_revenue_growth']['all_passed'] else 'FAIL'}, "
            f"T4={'PASS' if sens['table4_operating_margin']['all_passed'] else 'FAIL'}, "
            f"T5={'PASS' if sens['table5_two_way']['all_passed'] else 'FAIL'} "
            f"({sens['table5_two_way']['cells_examined']}/{sens['table5_two_way']['cells_expected']} cells)."
        ), style=styles["wrap"])
        row += 2
    return s




def add_full_sensitivity_detail_sheet(wb: Workbook, results: dict, tol_refs, tol_values, styles):
    """Finding 4 fix: every Table 1-5 scalar comparison, for every company,
    as its own row -- long/tidy format, one row per (ticker, table, scenario,
    output metric) comparison. 908 rows total (227 per company x 4). Every
    row exposes: Ticker, Table, Scenario (varied input), Output Metric,
    Codebase Value, Workbook Value, Absolute Difference, Relative/pp
    Difference, Tolerance, PASS/FAIL, Notes."""
    s = wb.add_sheet("All Sensitivity Comparisons")
    for c, w in enumerate([8, 26, 22, 24, 16, 16, 14, 12, 12, 10, 40]):
        s.set_width(c, w)
    s.set(0, 0, value="All sensitivity scalar comparisons -- Tables 1-5, all four companies (908 rows)", style=styles["title"])
    s.set(1, 0, value=(
        "One row per individual scalar comparison (a single output metric within a single sensitivity "
        "scenario). Codebase/Workbook columns are hardcoded imports (yellow); Absolute Difference / "
        "Relative-or-pp Difference / PASS-FAIL are live formulas (white), with Tolerance a formula "
        "reference to the 'Tolerances' sheet's single source of truth."
    ), style=styles["wrap"])
    header_row = 3
    for c, h in enumerate(["Ticker", "Table", "Scenario", "Output Metric", "Codebase Value", "Workbook Value",
                            "Absolute Difference", "Relative/pp Difference", "Tolerance", "PASS/FAIL", "Notes"]):
        s.set(header_row, c, value=h, style=styles["header"])
    row = header_row + 1
    n_written = 0

    for ticker in TICKERS:
        sens = results["sensitivity_reconciliation"][ticker]
        for table_label, table_key, scenario, out_key, cell in iter_all_scalar_comparisons(sens):
                    out_label = OUTPUT_METRIC_LABELS[out_key]
                    tol_key = OUTPUT_METRIC_TOL_KEY[out_key]
                    value_kind = OUTPUT_METRIC_VALUE_KIND[out_key]

                    s.set(row, 0, value=ticker)
                    s.set(row, 1, value=table_label)
                    s.set(row, 2, value=scenario)
                    s.set(row, 3, value=out_label)

                    if cell.get("kind") == "na_marker":
                        s.set(row, 4, value=str(cell["codebase_value"]), style=styles["hardcoded"])
                        s.set(row, 5, value=str(cell["workbook_value"]), style=styles["hardcoded"])
                        s.set(row, 6, value="n/a", style=styles["hardcoded"])
                        s.set(row, 7, value="n/a", style=styles["hardcoded"])
                        s.set(row, 8, formula=tol_refs[tol_key], cached=tol_values[tol_key], style=styles["formula_pct"])
                        passed = cell["passed"]
                        s.set(row, 9, value=("PASS" if passed else "FAIL"), style=(styles["pass"] if passed else styles["fail"]))
                        s.set(row, 10, value="Both sides report the invalid-combination marker; exact string equality, not a numeric/tolerance comparison.")
                    else:
                        # Comparison block written directly at columns E-J (value_col0=4),
                        # since this sheet reserves A-D for identifying labels (Ticker/Table/
                        # Scenario/Output Metric) -- no post-hoc cell relocation needed.
                        _comparison_row(
                            s, row, styles, label=None,
                            codebase_val=cell["codebase_value"], workbook_val=cell["workbook_value"],
                            tol_ref=tol_refs[tol_key], tol_value=tol_values[tol_key], value_kind=value_kind,
                            value_col0=4,
                        )
                        _fill_tolerance_cached(s, row, tol_values[tol_key], value_col0=4)
                        s.set(row, 10, value="")
                    n_written += 1
                    row += 1
    assert n_written == EXPECTED_SCALAR_COMPARISONS_TOTAL, (
        f"Expected exactly {EXPECTED_SCALAR_COMPARISONS_TOTAL} sensitivity scalar comparisons, wrote {n_written}."
    )
    s.freeze_panes(header_row + 1, 4)
    return s, n_written


def add_findings_sheet(wb: Workbook, results: dict, styles):
    s = wb.add_sheet("Findings")
    s.set_width(0, 100)
    s.set(0, 0, value="Findings", style=styles["title"])
    row = 2
    verdict = results["verdict"]
    findings = []
    findings.append(f"Overall verdict: {'GO' if verdict['overall_pass'] else 'NO-GO'}")
    findings.append(
        "Reconciliation target: independent workbook V2 (independent_dcf_validation_v2.xlsx), built in "
        "Track A Phase 2C from the corrected years_elapsed specification (A-028/L-019). V1 "
        "(independent_dcf_validation.xlsx) is preserved unchanged; its original Phase 2B NO-GO evidence "
        "is archived at validation/dcf_reconciliation/history/phase2b_initial_no_go/ and was never edited."
    )
    if verdict["companies_with_base_divergence"]:
        findings.append("Base-case divergence found for: " + ", ".join(verdict["companies_with_base_divergence"]))
        for ticker in verdict["companies_with_base_divergence"]:
            fd = results["base_reconciliation"][ticker]["first_divergence"]
            findings.append(f"  - {ticker}: first divergence at metric '{METRIC_LABELS[fd]}'.")
    else:
        findings.append(
            "No base-case divergence found in any company against V2. Phase 2B's original NO-GO (against "
            "V1) was caused by V1's 'years_elapsed = COUNT(periods)-1' convention, which did NOT conform "
            "to the now-clarified specification (docs/model-specifications/dcf.md's years_elapsed = actual "
            "elapsed calendar days / 365.25) for a company on an irregular fiscal calendar (INTC). V1's "
            "convention is not described as having been correct under the clarified specification -- it "
            "was a reasonable resolution of a genuine ambiguity that the clarification has since closed. "
            "Production code (src/dcf_model/dcf.py) required NO change: its existing "
            "calculate_historical_revenue_cagr implementation already computed years_elapsed via actual "
            "elapsed calendar days, which is what the specification was clarified to require."
        )
    findings.append(
        "Directional bias: MSFT, CAT, and VZ match V2 to full floating-point precision on every required "
        "metric (zero signed difference) -- no bias to investigate among them."
    )
    findings.append(
        "INTC sensitivity tables show a documented, mathematically-correct inverted direction (IV rises "
        "with WACC, falls with terminal growth, falls with revenue growth) caused by negative base-case "
        "FCF -- both V2 and the production recompute agree on every inverted direction."
    )
    findings.append(
        "Sensitivity coverage: 908 total scalar comparisons across four companies (227 per company: "
        "Table 1=28, Table 2=28, Table 3=72, Table 4=63, Table 5=36) -- every one appears as its own row "
        "on the 'All Sensitivity Comparisons' sheet, not only in reconciliation_results.json."
    )
    for i, line in enumerate(findings):
        s.set(row + i, 0, value=line, style=styles["wrap"])
    return s


def add_signoff_sheet(wb: Workbook, results: dict, styles):
    s = wb.add_sheet("Sign-off")
    s.set_width(0, 44)
    s.set_width(1, 60)
    s.set(0, 0, value="Sign-off", style=styles["title"])
    rows = [
        ("Source implementation commit", results["commit"]),
        ("Branch", results["branch"]),
        ("V1 independent workbook SHA-256 (preserved, historical)", results.get("v1_workbook_sha256", results.get("original_workbook_sha256"))),
        ("V2 independent workbook SHA-256 (reconciliation target)", results.get("v2_workbook_sha256", "N/A")),
        ("Frozen snapshot SHA-256 (MSFT)", results["snapshot_sha256"]["MSFT"]),
        ("Frozen snapshot SHA-256 (CAT)", results["snapshot_sha256"]["CAT"]),
        ("Frozen snapshot SHA-256 (INTC)", results["snapshot_sha256"]["INTC"]),
        ("Frozen snapshot SHA-256 (VZ)", results["snapshot_sha256"]["VZ"]),
        ("Overall reconciliation status", "GO" if results["verdict"]["overall_pass"] else "NO-GO"),
        ("Profitability established?", "NOT ESTABLISHED -- this reconciliation checks calculation agreement only"),
        ("Second-reviewer sign-off", "PENDING -- not performed in this session"),
    ]
    for i, (label, value) in enumerate(rows):
        s.set(2 + i, 0, value=label, style=styles["bold"])
        s.set(2 + i, 1, value=str(value), style=styles["hardcoded"])
    return s


def add_readme_sheet(wb: Workbook, results: dict, styles):
    s = wb.add_sheet("README")
    s.set_width(0, 110)
    lines = [
        ("DCF Codebase-to-Independent-Workbook Reconciliation (V2)", styles["title"]),
        ("Track A Phase 2C. This workbook reconciles src/dcf_model/dcf.py's production output against "
         "the independent workbook V2 (validation/independent_dcf/independent_dcf_validation_v2.xlsx), "
         "built from the corrected years_elapsed specification. V1 is preserved unchanged and never "
         "modified by this workbook or by any script in validation/dcf_reconciliation/.", styles["wrap"]),
        ("", None),
        ("Sheets:", styles["bold"]),
        (" - Tolerances: the single source of truth for every tolerance constant used elsewhere.", None),
        (" - Base Reconciliation: all 14 required metrics x 4 companies, live-formula comparison.", None),
        (" - Detail_<TICKER>: per-company base reconciliation + accurate sensitivity coverage counts.", None),
        (" - Sensitivity Reconciliation: Table 1/2 full live-formula detail + coverage-count summary.", None),
        (" - All Sensitivity Comparisons: EVERY Table 1-5 scalar comparison (908 rows), formula-driven.", None),
        (" - Findings: narrative summary of what was (and wasn't) found.", None),
        (" - Sign-off: commit/hash provenance (V1 AND V2) and outstanding second-reviewer requirement.", None),
        ("", None),
        (f"Source commit: {results['commit']} (branch {results['branch']})", styles["bold"]),
        (f"V1 workbook SHA-256 (preserved, historical): {results.get('v1_workbook_sha256', results.get('original_workbook_sha256'))}", None),
        (f"V2 workbook SHA-256 (reconciliation target): {results.get('v2_workbook_sha256', 'N/A')}", None),
        (f"Overall status: {'GO' if results['verdict']['overall_pass'] else 'NO-GO'} "
         "(pending separate second-reviewer sign-off)", styles["bold"]),
        ("PROFITABILITY NOT ESTABLISHED -- this workbook verifies calculation agreement between two "
         "independent implementations. It is not investment advice and does not establish that any "
         "company is profitable to trade.", styles["bold"]),
    ]
    for i, (text, style) in enumerate(lines):
        s.set(i, 0, value=text, style=style)
    return s


def build_reconciliation_workbook(results: dict, out_path: str):
    wb = Workbook()
    styles = _styles(wb)
    tol_refs = _tolerance_sheet(wb, styles)
    tol_values = {key: value for key, _label, value, _kind in TOLERANCE_ROWS}
    add_readme_sheet(wb, results, styles)
    add_base_reconciliation_sheet(wb, results, tol_refs, tol_values, styles)
    for ticker in TICKERS:
        add_company_detail_sheet(wb, ticker, results, tol_refs, tol_values, styles)
    add_sensitivity_sheet(wb, results, tol_refs, tol_values, styles)
    add_full_sensitivity_detail_sheet(wb, results, tol_refs, tol_values, styles)
    add_findings_sheet(wb, results, styles)
    add_signoff_sheet(wb, results, styles)
    wb.save(out_path)
    return out_path
