"""
Phase 6: locates the five sensitivity tables in Sensitivity_<TICKER> purely
from the sheet's own section-title/header labels (never a hardcoded row
range), and reads each grid cell's cached value (a float, or the literal
string "n/a" for an invalid WACC<=g / g>=WACC combination).

Independently written (not imported) from the same general technique
validation/independent_dcf/formula_audit.py uses for formula-text checks;
this module reads cached VALUES instead, because Phase 6 needs to compare
numbers against the production model's recomputed numbers, not formula text.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Union

from validation.dcf_reconciliation.xlsx_reader import (
    Workbook,
    find_cell_starting_with,
    find_header_column,
    contiguous_numeric_run,
)

CellValue = Union[float, str]  # float, or "n/a"


@dataclass
class Table1Row:
    wacc: float
    terminal_value: CellValue
    enterprise_value: CellValue
    equity_value: CellValue
    intrinsic_value_per_share: CellValue
    row_ref: int


@dataclass
class Table2Row:
    terminal_growth: float
    terminal_value: CellValue
    enterprise_value: CellValue
    equity_value: CellValue
    intrinsic_value_per_share: CellValue
    row_ref: int


@dataclass
class Table3Row:
    revenue_growth: float
    fcf: List[float]  # FCF Year 1..5
    terminal_value: CellValue
    enterprise_value: CellValue
    equity_value: CellValue
    intrinsic_value_per_share: CellValue
    row_ref: int


@dataclass
class Table4Row:
    operating_margin: float
    fcf: List[float]  # FCF Year 1..5
    terminal_value: CellValue
    enterprise_value: CellValue
    equity_value: CellValue
    intrinsic_value_per_share: CellValue
    row_ref: int


@dataclass
class SensitivityTables:
    table1: List[Table1Row] = field(default_factory=list)
    table2: List[Table2Row] = field(default_factory=list)
    table3: List[Table3Row] = field(default_factory=list)
    table4: List[Table4Row] = field(default_factory=list)
    table5_wacc_headers: List[float] = field(default_factory=list)
    table5_g_headers: List[float] = field(default_factory=list)
    table5_grid: Dict[tuple, CellValue] = field(default_factory=dict)  # (wacc, g) -> value


def _cell_val(cell) -> CellValue:
    if cell is None:
        return None
    if cell.numeric is not None:
        return cell.numeric
    if cell.text is not None:
        return cell.text
    return None


def locate_sensitivity_tables(wb: Workbook, ticker: str) -> SensitivityTables:
    sheet_name = f"Sensitivity_{ticker}"
    if not wb.has_sheet(sheet_name):
        raise ValueError(f"Sheet {sheet_name!r} not found in workbook.")
    grid = wb.grid(sheet_name)
    out = SensitivityTables()

    t1 = find_cell_starting_with(grid, "TABLE 1", col0=0)
    t2 = find_cell_starting_with(grid, "TABLE 2", col0=0)
    t3 = find_cell_starting_with(grid, "TABLE 3", col0=0)
    t4 = find_cell_starting_with(grid, "TABLE 4", col0=0)
    t5 = find_cell_starting_with(grid, "TABLE 5", col0=0)
    if not all([t1, t2, t3, t4, t5]):
        raise ValueError(f"{sheet_name}: could not locate all five TABLE section titles.")

    def data_rows(title_row0, next_title_row0):
        start = title_row0 + 2
        rows = []
        for r in range(start, next_title_row0):
            cell = grid.get((r, 0))
            if cell is None or cell.numeric is None:
                continue
            rows.append(r)
        return rows

    # ---- Table 1: WACC sensitivity ----
    for r in data_rows(t1[0], t2[0]):
        out.table1.append(Table1Row(
            wacc=grid[(r, 0)].numeric,
            terminal_value=_cell_val(grid.get((r, 1))),
            enterprise_value=_cell_val(grid.get((r, 2))),
            equity_value=_cell_val(grid.get((r, 3))),
            intrinsic_value_per_share=_cell_val(grid.get((r, 4))),
            row_ref=r + 1,
        ))

    # ---- Table 2: Terminal growth sensitivity ----
    for r in data_rows(t2[0], t3[0]):
        out.table2.append(Table2Row(
            terminal_growth=grid[(r, 0)].numeric,
            terminal_value=_cell_val(grid.get((r, 1))),
            enterprise_value=_cell_val(grid.get((r, 2))),
            equity_value=_cell_val(grid.get((r, 3))),
            intrinsic_value_per_share=_cell_val(grid.get((r, 4))),
            row_ref=r + 1,
        ))

    # ---- Table 3: Revenue growth sensitivity ----
    t3_header_row = t3[0] + 1
    t3_fcf_cols = [find_header_column(grid, t3_header_row, f"FCF Y{y}") for y in range(1, 6)]
    t3_tv_col = find_header_column(grid, t3_header_row, "Terminal Value")
    t3_ev_col = find_header_column(grid, t3_header_row, "Enterprise Value")
    t3_eqv_col = find_header_column(grid, t3_header_row, "Equity Value")
    t3_ivps_col = find_header_column(grid, t3_header_row, "Intrinsic Value/Share")
    if None in t3_fcf_cols or None in (t3_tv_col, t3_ev_col, t3_eqv_col, t3_ivps_col):
        raise ValueError(f"{sheet_name}: Table 3 header row missing an expected column.")
    for r in data_rows(t3[0], t4[0]):
        out.table3.append(Table3Row(
            revenue_growth=grid[(r, 0)].numeric,
            fcf=[grid[(r, c)].numeric for c in t3_fcf_cols],
            terminal_value=_cell_val(grid.get((r, t3_tv_col))),
            enterprise_value=_cell_val(grid.get((r, t3_ev_col))),
            equity_value=_cell_val(grid.get((r, t3_eqv_col))),
            intrinsic_value_per_share=_cell_val(grid.get((r, t3_ivps_col))),
            row_ref=r + 1,
        ))

    # ---- Table 4: Operating margin sensitivity ----
    t4_header_row = t4[0] + 1
    t4_fcf_cols = [find_header_column(grid, t4_header_row, f"FCF Y{y}") for y in range(1, 6)]
    t4_tv_col = find_header_column(grid, t4_header_row, "Terminal Value")
    t4_ev_col = find_header_column(grid, t4_header_row, "Enterprise Value")
    t4_eqv_col = find_header_column(grid, t4_header_row, "Equity Value")
    t4_ivps_col = find_header_column(grid, t4_header_row, "Intrinsic Value/Share")
    if None in t4_fcf_cols or None in (t4_tv_col, t4_ev_col, t4_eqv_col, t4_ivps_col):
        raise ValueError(f"{sheet_name}: Table 4 header row missing an expected column.")
    for r in data_rows(t4[0], t5[0]):
        out.table4.append(Table4Row(
            operating_margin=grid[(r, 0)].numeric,
            fcf=[grid[(r, c)].numeric for c in t4_fcf_cols],
            terminal_value=_cell_val(grid.get((r, t4_tv_col))),
            enterprise_value=_cell_val(grid.get((r, t4_ev_col))),
            equity_value=_cell_val(grid.get((r, t4_eqv_col))),
            intrinsic_value_per_share=_cell_val(grid.get((r, t4_ivps_col))),
            row_ref=r + 1,
        ))

    # ---- Table 5: two-way WACC x g grid ----
    hdr_hit = find_cell_starting_with(grid, "WACC \\ g", col0=0)
    if hdr_hit is None:
        raise ValueError(f"{sheet_name}: could not locate Table 5's 'WACC \\ g' header cell.")
    hdr_row, hdr_col = hdr_hit
    g_run = contiguous_numeric_run(grid, hdr_row, hdr_col + 1, direction=(0, 1))
    wacc_run = contiguous_numeric_run(grid, hdr_row + 1, hdr_col, direction=(1, 0))
    if len(g_run) != 6 or len(wacc_run) != 6:
        raise ValueError(
            f"{sheet_name}: Table 5 expected 6 g-headers/6 WACC-headers, "
            f"found {len(g_run)}/{len(wacc_run)}."
        )
    out.table5_g_headers = [v for (_r, _c, v) in g_run]
    out.table5_wacc_headers = [v for (_r, _c, v) in wacc_run]
    g_cols = [c for (_r, c, _v) in g_run]
    wacc_rows = [r for (r, _c, _v) in wacc_run]
    for wacc_val, r in zip(out.table5_wacc_headers, wacc_rows):
        for g_val, c in zip(out.table5_g_headers, g_cols):
            out.table5_grid[(wacc_val, g_val)] = _cell_val(grid.get((r, c)))

    return out
