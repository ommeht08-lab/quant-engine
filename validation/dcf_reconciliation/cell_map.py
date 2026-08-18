"""
Phase 1 comparison contract: locates every reconciled metric's workbook
cell by the workbook's own row/column LABELS (never by trusting a fixed
row number copied from one company's sheet), and pairs each with the
exact codebase return-dict field that supplies the same quantity.

This is the single source of truth for "codebase field <-> workbook cell"
used by both codebase_outputs.json generation and reconcile.py, so the
mapping is defined exactly once.
"""
from dataclasses import dataclass
from typing import Optional

from validation.dcf_reconciliation.xlsx_reader import (
    Workbook,
    find_cell_starting_with,
    find_all_cells_starting_with,
    col_index_to_letters,
)

# Base metrics, in the exact order the task's comparison contract lists
# them. `codebase_field` documents exactly how to derive the value from
# `run_dcf_valuation`'s return dict (a literal key, or a short expression
# over it). `workbook_label` is the DCF_<TICKER> sheet's column-A label
# prefix used to locate the row; `workbook_value_col` is the fixed column
# offset from column A where that row's single scalar value lives (1 = B),
# EXCEPT for the FCF-year metrics, which are located via the Year-N column
# header instead (see `locate_dcf_metrics`).
BASE_METRIC_ORDER = [
    "revenue_cagr",
    "operating_margin",
    "wacc_raw",
    "wacc",
    "fcf_year_1",
    "fcf_year_2",
    "fcf_year_3",
    "fcf_year_4",
    "fcf_year_5",
    "terminal_value",
    "pv_explicit_fcf",
    "pv_terminal_value",
    "enterprise_value",
    "equity_value",
    "intrinsic_value_per_share",
]

# Metrics required by docs/independent-validation-plan.md's tolerance
# table / the task's Phase 5 instructions (wacc_raw is diagnostic-only:
# `calculate_wacc` never returns the pre-clamp value, so it has no
# codebase counterpart and is excluded from REQUIRED_METRICS).
REQUIRED_METRICS = [m for m in BASE_METRIC_ORDER if m != "wacc_raw"]

TOLERANCES = {
    # ("absolute_pp", x) -> |codebase - workbook| <= x, measured in
    # percentage points (0.0001 == 0.01pp), independent of sign.
    # ("relative_pct", x) -> |codebase - workbook| / |workbook| <= x, with
    # a documented near-zero absolute-tolerance carve-out (see reconcile.py).
    "revenue_cagr": ("absolute_pp", 0.0001),       # +/- 0.01pp
    "operating_margin": ("absolute_pp", 0.0001),   # +/- 0.01pp
    "wacc": ("absolute_pp", 0.0005),                # +/- 0.05pp
    "fcf_year_1": ("relative_pct", 0.001),          # +/- 0.1%
    "fcf_year_2": ("relative_pct", 0.001),
    "fcf_year_3": ("relative_pct", 0.001),
    "fcf_year_4": ("relative_pct", 0.001),
    "fcf_year_5": ("relative_pct", 0.001),
    "terminal_value": ("relative_pct", 0.002),      # +/- 0.2%
    "pv_explicit_fcf": ("relative_pct", 0.002),
    "pv_terminal_value": ("relative_pct", 0.002),
    "enterprise_value": ("relative_pct", 0.002),
    "equity_value": ("relative_pct", 0.002),
    "intrinsic_value_per_share": ("relative_pct", 0.005),  # +/- 0.5%
}

METRIC_LABELS = {
    "revenue_cagr": "Historical Revenue CAGR",
    "operating_margin": "Historical average Operating Margin",
    "wacc_raw": "Raw WACC (pre-clamp; diagnostic only, no codebase field)",
    "wacc": "WACC (final, clamped [5%,20%])",
    "fcf_year_1": "FCF Year 1",
    "fcf_year_2": "FCF Year 2",
    "fcf_year_3": "FCF Year 3",
    "fcf_year_4": "FCF Year 4",
    "fcf_year_5": "FCF Year 5",
    "terminal_value": "Terminal Value",
    "pv_explicit_fcf": "PV of Explicit FCF (sum of pv_fcf series)",
    "pv_terminal_value": "PV of Terminal Value",
    "enterprise_value": "Enterprise Value",
    "equity_value": "Equity Value",
    "intrinsic_value_per_share": "Intrinsic Value per Share",
}

# Codebase side: exact field / expression on run_dcf_valuation(financial_data, DCFAssumptions())'s
# return dict, recorded as documentation (also mirrored in reconcile.py's actual extraction code).
CODEBASE_FIELD = {
    "revenue_cagr": "result['revenue_growth_rate']",
    "operating_margin": "result['operating_margin']",
    "wacc_raw": None,  # not exposed -- calculate_wacc only returns the post-clamp value
    "wacc": "result['wacc']",
    "fcf_year_1": "result['fcf_projection']['fcf'].loc[1]",
    "fcf_year_2": "result['fcf_projection']['fcf'].loc[2]",
    "fcf_year_3": "result['fcf_projection']['fcf'].loc[3]",
    "fcf_year_4": "result['fcf_projection']['fcf'].loc[4]",
    "fcf_year_5": "result['fcf_projection']['fcf'].loc[5]",
    "terminal_value": "result['terminal_value']",
    "pv_explicit_fcf": "result['pv_fcf'].sum()",
    "pv_terminal_value": "result['pv_terminal_value']",
    "enterprise_value": "result['enterprise_value']",
    "equity_value": "result['equity_value']",
    "intrinsic_value_per_share": "result['intrinsic_value_per_share']",
}


@dataclass
class WorkbookMetric:
    metric: str
    sheet: str
    cell_ref: str
    formula: Optional[str]
    cached_value: float


def locate_dcf_metrics(wb: Workbook, ticker: str) -> dict:
    """Return {metric_name: WorkbookMetric} for the 15 BASE_METRIC_ORDER entries,
    located in DCF_<ticker> purely from the sheet's own row/column labels.
    Raises ValueError (refuses the comparison) if any required label or
    cell is missing -- per the task's "Refuse a comparison if a required
    workbook value or formula is missing" instruction.
    """
    sheet_name = f"DCF_{ticker}"
    if not wb.has_sheet(sheet_name):
        raise ValueError(f"Sheet {sheet_name!r} not found in workbook.")
    grid = wb.grid(sheet_name)

    def scalar(label_prefix: str, metric: str, value_col0: int = 1) -> WorkbookMetric:
        hit = find_cell_starting_with(grid, label_prefix, col0=0)
        if hit is None:
            raise ValueError(f"{sheet_name}: could not locate row label starting with {label_prefix!r} for metric {metric!r}.")
        row0, _ = hit
        cell = grid.get((row0, value_col0))
        if cell is None or cell.numeric is None:
            raise ValueError(
                f"{sheet_name}!{col_index_to_letters(value_col0)}{row0 + 1}: "
                f"missing cached numeric value for metric {metric!r} (label {label_prefix!r})."
            )
        return WorkbookMetric(metric, sheet_name, cell.ref, cell.formula, cell.numeric)

    metrics = {
        "revenue_cagr": scalar("Capped Revenue CAGR", "revenue_cagr"),
        "operating_margin": scalar("Average Historical Operating Margin", "operating_margin"),
        "wacc_raw": scalar("Raw WACC", "wacc_raw"),
        "wacc": scalar("Final WACC", "wacc"),
        "terminal_value": scalar("Terminal Value = FCF_Y5", "terminal_value"),
        "pv_explicit_fcf": scalar("Sum of PV(FCF Years 1-5)", "pv_explicit_fcf"),
        "pv_terminal_value": scalar("PV(Terminal Value)", "pv_terminal_value"),
        "enterprise_value": scalar("Enterprise Value = Sum PV(FCF)", "enterprise_value"),
        "equity_value": scalar("Equity Value = Enterprise Value", "equity_value"),
        "intrinsic_value_per_share": scalar("Intrinsic Value per Share = Equity Value", "intrinsic_value_per_share"),
    }

    # FCF Year 1..5: located by the "Year N" column header (row of A22-style
    # header cells) crossed with the "Unlevered FCF = NOPAT..." row label --
    # never a hardcoded column letter.
    fcf_row_hit = find_cell_starting_with(grid, "Unlevered FCF = NOPAT", col0=0)
    if fcf_row_hit is None:
        raise ValueError(f"{sheet_name}: could not locate the 'Unlevered FCF = NOPAT...' row label.")
    fcf_row0, _ = fcf_row_hit

    for year in range(1, 6):
        year_hits = find_all_cells_starting_with(grid, f"Year {year}")
        # "Year 1".."Year 5" headers only (exclude "Year 0 (base, actual)").
        year_hits = [(r, c) for (r, c) in year_hits if grid[(r, c)].text == f"Year {year}"]
        if not year_hits:
            raise ValueError(f"{sheet_name}: could not locate the 'Year {year}' column header.")
        _, col0 = year_hits[0]
        cell = grid.get((fcf_row0, col0))
        if cell is None or cell.numeric is None:
            raise ValueError(f"{sheet_name}!{col_index_to_letters(col0)}{fcf_row0 + 1}: missing cached FCF Year {year} value.")
        metrics[f"fcf_year_{year}"] = WorkbookMetric(f"fcf_year_{year}", sheet_name, cell.ref, cell.formula, cell.numeric)

    missing = [m for m in BASE_METRIC_ORDER if m not in metrics]
    if missing:
        raise ValueError(f"{sheet_name}: failed to locate required metrics: {missing}")
    return metrics
