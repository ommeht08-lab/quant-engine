#!/usr/bin/env python3
"""
formula_audit.py -- Structural formula-text audit for independent_dcf_validation.xlsx.

Mechanically independent of the code that generates the workbook: this script
does NOT import build_workbook.py, shadow_calc.py, or xlsx_lite.py. It inspects
the FINISHED .xlsx file from the outside, using only the Python standard library
(zipfile, xml.etree.ElementTree), and locates every cell it checks by reading the
workbook's own labels, headers, and formula text -- the same way a human
reviewer opening the file would.

Cell parsing uses xml.etree.ElementTree (structured XML parsing), not regular
expressions, and explicitly handles every cell kind OOXML can produce:
numeric cells, inline strings, shared strings (xl/sharedStrings.xml), formula
cells (with their cached <v> result), and boolean/error cells. Relationship
resolution (sheet name -> worksheet part) reads xl/workbook.xml and
xl/_rels/workbook.xml.rels as XML trees, so it does not depend on attribute
order within a <sheet>/<Relationship> element.

Known-fixed defect (see workbook_build_log.md for full history): an earlier
regex-based version of this script silently discovered ZERO Table 5 (two-way
WACC x terminal-growth grid) cells for every company, because Table 5's row
and column headers are plain NUMERIC cells (<v>0.05</v>, no <f>, no <is>), and
that version's cell parser only recorded a "text" field populated from inline
strings -- a numeric-only cell parsed as entirely empty. The audit's header
list therefore came back empty, its per-cell loops never executed, and it
printed "PASS" having examined nothing. This version's Table 5 check FAILS
loudly, with an explicit cell/header count, if it examines anything other
than exactly 36 formula cells per company (144 total across 4 companies) --
see check_table5() and its REQUIRED_G_HEADERS / REQUIRED_WACC_HEADERS
constants below.

Usage:
    python3 formula_audit.py                      # audit independent_dcf_validation.xlsx (default)
    python3 formula_audit.py /path/to/other.xlsx   # audit a specific workbook (e.g. a temp negative-control copy)
    python3 formula_audit.py --self-test           # negative-control self-tests (see run_self_test())

Exit code 0 = audit passed. Exit code 1 = at least one defect found (default-path
audit) or a self-test itself did not behave as expected.
"""
import os
import re
import shutil
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_XLSX = os.path.join(HERE, "independent_dcf_validation.xlsx")
TICKERS = ["MSFT", "CAT", "INTC", "VZ"]

REQUIRED_G_HEADERS = 6
REQUIRED_WACC_HEADERS = 6
REQUIRED_GRID_CELLS = REQUIRED_G_HEADERS * REQUIRED_WACC_HEADERS  # 36
REQUIRED_GRID_CELLS_TOTAL = REQUIRED_GRID_CELLS * len(TICKERS)     # 144

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def q(ns, tag):
    return f"{{{ns}}}{tag}"


# ---------------------------------------------------------------------------
# Column-letter <-> index (local reimplementation -- no import from xlsx_lite)
# ---------------------------------------------------------------------------

def col_letters_to_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def col_index_to_letters(idx):
    letters = ""
    n = idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


def parse_ref(ref):
    m = CELL_REF_RE.match(ref)
    return int(m.group(2)) - 1, col_letters_to_index(m.group(1))


# ---------------------------------------------------------------------------
# Workbook loading (ElementTree-based; sheet-name resolution via rels, not
# positional rId assumptions; full support for numeric/inline/shared-string
# /formula/boolean cell kinds)
# ---------------------------------------------------------------------------

class Cell:
    __slots__ = ("ref", "row0", "col0", "formula", "text", "numeric", "kind")

    def __init__(self, ref, row0, col0, formula, text, numeric, kind):
        self.ref = ref
        self.row0 = row0
        self.col0 = col0
        self.formula = formula      # str|None -- <f> text, if this is a formula cell
        self.text = text            # str|None -- resolved string content (inline or shared string)
        self.numeric = numeric      # float|None -- resolved numeric value (plain numeric cell, or a formula's cached numeric result)
        self.kind = kind            # "formula" | "numeric" | "string" | "boolean" | "empty"

    def __repr__(self):
        return f"Cell({self.ref}, kind={self.kind}, formula={self.formula!r}, text={self.text!r}, numeric={self.numeric!r})"


class Workbook:
    def __init__(self, path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            wb_root = ET.fromstring(z.read("xl/workbook.xml"))
            rels_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))

            rid_to_target = {}
            for rel in rels_root.findall(q(PKG_REL_NS, "Relationship")):
                rid_to_target[rel.get("Id")] = rel.get("Target")

            name_to_rid = {}
            sheets_el = wb_root.find(q(MAIN_NS, "sheets"))
            for sheet_el in sheets_el.findall(q(MAIN_NS, "sheet")):
                name = sheet_el.get("name")
                rid = sheet_el.get(q(DOC_REL_NS, "id"))
                name_to_rid[name] = rid

            self.shared_strings = []
            if "xl/sharedStrings.xml" in names:
                sst_root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in sst_root.findall(q(MAIN_NS, "si")):
                    self.shared_strings.append(self._si_text(si))

            self.sheet_names = list(name_to_rid.keys())
            self._sheet_roots = {}
            for name, rid in name_to_rid.items():
                target = rid_to_target.get(rid)
                if target is None:
                    continue
                part_path = target if target.startswith("xl/") else "xl/" + target
                self._sheet_roots[name] = ET.fromstring(z.read(part_path))

    def _si_text(self, si_el):
        t_el = si_el.find(q(MAIN_NS, "t"))
        if t_el is not None:
            return t_el.text or ""
        # rich text: concatenate every <r><t>...</t></r> run
        parts = []
        for r_el in si_el.findall(q(MAIN_NS, "r")):
            t_el = r_el.find(q(MAIN_NS, "t"))
            if t_el is not None and t_el.text:
                parts.append(t_el.text)
        return "".join(parts)

    def has_sheet(self, name):
        return name in self._sheet_roots

    def grid(self, sheet_name):
        """Parse one sheet into {(row0,col0): Cell}, handling every OOXML cell kind."""
        root = self._sheet_roots[sheet_name]
        sheet_data = root.find(q(MAIN_NS, "sheetData"))
        cells = {}
        for row_el in sheet_data.findall(q(MAIN_NS, "row")):
            for c_el in row_el.findall(q(MAIN_NS, "c")):
                ref = c_el.get("r")
                if ref is None:
                    continue
                row0, col0 = parse_ref(ref)
                t = c_el.get("t")  # None/omitted -> number; "s" shared string; "str" formula-string result;
                                     # "inlineStr"; "b" boolean; "e" error
                f_el = c_el.find(q(MAIN_NS, "f"))
                v_el = c_el.find(q(MAIN_NS, "v"))
                is_el = c_el.find(q(MAIN_NS, "is"))

                formula = f_el.text if f_el is not None and f_el.text else None
                text = None
                numeric = None

                if is_el is not None:
                    text = self._si_text(is_el)
                elif t == "s" and v_el is not None and v_el.text is not None:
                    idx = int(v_el.text)
                    text = self.shared_strings[idx] if 0 <= idx < len(self.shared_strings) else None
                elif t == "str" and v_el is not None:
                    text = v_el.text  # formula whose cached result is a string
                elif t == "b" and v_el is not None:
                    text = "TRUE" if v_el.text == "1" else "FALSE"
                elif v_el is not None and v_el.text is not None:
                    try:
                        numeric = float(v_el.text)
                    except ValueError:
                        text = v_el.text

                if formula is not None:
                    kind = "formula"
                elif text is not None:
                    kind = "boolean" if t == "b" else "string"
                elif numeric is not None:
                    kind = "numeric"
                else:
                    kind = "empty"

                cells[(row0, col0)] = Cell(ref, row0, col0, formula, text, numeric, kind)
        return cells


# ---------------------------------------------------------------------------
# Label-driven discovery helpers
# ---------------------------------------------------------------------------

def find_cell_starting_with(grid, prefix, col0=None):
    matches = sorted((r, c) for (r, c), cell in grid.items()
                      if cell.text is not None and cell.text.startswith(prefix)
                      and (col0 is None or c == col0))
    return matches[0] if matches else None


def find_all_cells_starting_with(grid, prefix, col0=None):
    return sorted((r, c) for (r, c), cell in grid.items()
                  if cell.text is not None and cell.text.startswith(prefix)
                  and (col0 is None or c == col0))


def find_header_column(grid, header_row0, exact_text):
    for (r, c), cell in grid.items():
        if r == header_row0 and cell.text == exact_text:
            return c
    return None


def contiguous_numeric_run(grid, row0, start_col0, direction=(0, 1)):
    """Count/collect a contiguous run of NUMERIC cells starting at (row0,start_col0),
    stepping by `direction` each time, stopping at the first non-numeric or empty cell."""
    dr, dc = direction
    out = []
    r, c = row0, start_col0
    while True:
        cell = grid.get((r, c))
        if cell is None or cell.kind != "numeric":
            break
        out.append((r, c, cell.numeric))
        r, c = r + dr, c + dc
    return out


# ---------------------------------------------------------------------------
# Per-company audit
# ---------------------------------------------------------------------------

def audit_company(wb, ticker, failures, counters):
    dcf_name = f"DCF_{ticker}"
    sens_name = f"Sensitivity_{ticker}"
    if not wb.has_sheet(dcf_name):
        failures.append((ticker, dcf_name, "N/A", "N/A", "sheet not found in workbook"))
        return
    if not wb.has_sheet(sens_name):
        failures.append((ticker, sens_name, "N/A", "N/A", "sheet not found in workbook"))
        return

    dcf = wb.grid(dcf_name)
    sens = wb.grid(sens_name)

    year5_hits = find_all_cells_starting_with(dcf, "Year 5")
    if not year5_hits:
        failures.append((ticker, dcf_name, "?", "(missing)", "could not locate a 'Year 5' column header"))
        return
    year5_row, year5_col = year5_hits[0]

    rev_hit = find_cell_starting_with(dcf, "Revenue = Revenue_(t-1)", col0=0)
    fcf_hit = find_cell_starting_with(dcf, "Unlevered FCF = NOPAT", col0=0)
    if rev_hit is None or fcf_hit is None:
        failures.append((ticker, dcf_name, "A?", "(missing)", "could not locate Revenue or Unlevered FCF row labels"))
        return
    rev_row, fcf_row = rev_hit[0], fcf_hit[0]

    fcf_ref_str = f"{col_index_to_letters(year5_col)}{fcf_row + 1}"
    rev_ref_str = f"{col_index_to_letters(year5_col)}{rev_row + 1}"
    fcf_full_ref = f"'{dcf_name}'!$" + col_index_to_letters(year5_col) + f"${fcf_row + 1}"
    rev_full_ref = f"'{dcf_name}'!$" + col_index_to_letters(year5_col) + f"${rev_row + 1}"

    # ---- Check 1: base DCF Terminal Value formula ----
    tv_label_hit = find_cell_starting_with(dcf, "Terminal Value = FCF_Y5", col0=0)
    if tv_label_hit is None:
        failures.append((ticker, dcf_name, "A?", "(missing)", "could not locate the 'Terminal Value = FCF_Y5...' row label"))
    else:
        tv_row = tv_label_hit[0]
        tv_cell = dcf.get((tv_row, 1))
        cellname = f"B{tv_row + 1}"
        if tv_cell is None or tv_cell.formula is None:
            failures.append((ticker, dcf_name, cellname, "(missing)", "Terminal Value formula not found next to its label"))
        else:
            formula = tv_cell.formula
            if fcf_ref_str not in formula:
                failures.append((ticker, dcf_name, cellname, formula, f"does not reference Year-5 FCF cell {fcf_ref_str}"))
            if re.search(rf"(?<![A-Z0-9]){re.escape(rev_ref_str)}(?![0-9])", formula):
                failures.append((ticker, dcf_name, cellname, formula, f"unexpectedly ALSO references Year-5 Revenue cell {rev_ref_str}"))
        counters["dcf_tv_checked"] += 1

    # ---- Locate the five TABLE section titles ----
    t1_title = find_cell_starting_with(sens, "TABLE 1", col0=0)
    t2_title = find_cell_starting_with(sens, "TABLE 2", col0=0)
    t3_title = find_cell_starting_with(sens, "TABLE 3", col0=0)
    t4_title = find_cell_starting_with(sens, "TABLE 4", col0=0)
    t5_title = find_cell_starting_with(sens, "TABLE 5", col0=0)
    if not all([t1_title, t2_title, t3_title, t4_title, t5_title]):
        failures.append((ticker, sens_name, "A?", "(missing)", "could not locate all five TABLE section titles"))
        return

    def data_row_range(title_row, next_title_row):
        start = title_row + 2
        end = None
        for r in range(start, next_title_row if next_title_row else start + 50):
            cell = sens.get((r, 0))
            if cell is not None and cell.text is not None and cell.text.startswith("Direction check"):
                end = r - 1
                break
        if end is None:
            end = (next_title_row - 1) if next_title_row else start
        return start, end

    t1_start, t1_end = data_row_range(t1_title[0], t2_title[0])
    t2_start, t2_end = data_row_range(t2_title[0], t3_title[0])
    t3_start, t3_end = data_row_range(t3_title[0], t4_title[0])
    t4_start, t4_end = data_row_range(t4_title[0], t5_title[0])

    # ---- Check 2: Table 1 (WACC sensitivity) ----
    n_checked = 0
    for r in range(t1_start, t1_end + 1):
        cell = sens.get((r, 1))
        cellname = f"B{r + 1}"
        if cell is None or cell.formula is None:
            failures.append((ticker, sens_name, cellname, "(missing)", "Table 1 Terminal Value formula not found"))
            continue
        n_checked += 1
        if fcf_full_ref not in cell.formula:
            failures.append((ticker, sens_name, cellname, cell.formula, f"does not reference base DCF Year-5 FCF cell {fcf_full_ref}"))
        if rev_full_ref in cell.formula:
            failures.append((ticker, sens_name, cellname, cell.formula, f"references Year-5 REVENUE cell {rev_full_ref} instead of FCF"))
    counters["table1_checked"] += n_checked

    # ---- Check 3: Table 2 (terminal-growth sensitivity) ----
    n_checked = 0
    for r in range(t2_start, t2_end + 1):
        cell = sens.get((r, 1))
        cellname = f"B{r + 1}"
        if cell is None or cell.formula is None:
            failures.append((ticker, sens_name, cellname, "(missing)", "Table 2 Terminal Value formula not found"))
            continue
        n_checked += 1
        if fcf_full_ref not in cell.formula:
            failures.append((ticker, sens_name, cellname, cell.formula, f"does not reference base DCF Year-5 FCF cell {fcf_full_ref}"))
        if rev_full_ref in cell.formula:
            failures.append((ticker, sens_name, cellname, cell.formula, f"references Year-5 REVENUE cell {rev_full_ref} instead of FCF"))
    counters["table2_checked"] += n_checked

    # ---- Check 4: Table 3 (revenue growth) -- own-row local FCF ----
    t3_header_row = t3_title[0] + 1
    t3_tv_col = find_header_column(sens, t3_header_row, "Terminal Value")
    t3_fcf5_col = find_header_column(sens, t3_header_row, "FCF Y5")
    n_checked = 0
    if t3_tv_col is None or t3_fcf5_col is None:
        failures.append((ticker, sens_name, "?", "(missing)", "Table 3 header row missing 'Terminal Value' or 'FCF Y5' column"))
    else:
        for r in range(t3_start, t3_end + 1):
            cell = sens.get((r, t3_tv_col))
            cellname = f"{col_index_to_letters(t3_tv_col)}{r + 1}"
            local_fcf5_ref = f"{col_index_to_letters(t3_fcf5_col)}{r + 1}"
            if cell is None or cell.formula is None:
                failures.append((ticker, sens_name, cellname, "(missing)", "Table 3 Terminal Value formula not found"))
                continue
            n_checked += 1
            if local_fcf5_ref not in cell.formula:
                failures.append((ticker, sens_name, cellname, cell.formula, f"does not reference its own row's local Year-5 FCF cell {local_fcf5_ref}"))
            if fcf_full_ref in cell.formula or rev_full_ref in cell.formula:
                failures.append((ticker, sens_name, cellname, cell.formula, "references the base DCF sheet directly instead of this table's own recomputed FCF"))
    counters["table3_checked"] += n_checked

    # ---- Check 5: Table 4 (operating margin) -- own-row local FCF ----
    t4_header_row = t4_title[0] + 1
    t4_tv_col = find_header_column(sens, t4_header_row, "Terminal Value")
    t4_fcf5_col = find_header_column(sens, t4_header_row, "FCF Y5")
    n_checked = 0
    if t4_tv_col is None or t4_fcf5_col is None:
        failures.append((ticker, sens_name, "?", "(missing)", "Table 4 header row missing 'Terminal Value' or 'FCF Y5' column"))
    else:
        for r in range(t4_start, t4_end + 1):
            cell = sens.get((r, t4_tv_col))
            cellname = f"{col_index_to_letters(t4_tv_col)}{r + 1}"
            local_fcf5_ref = f"{col_index_to_letters(t4_fcf5_col)}{r + 1}"
            if cell is None or cell.formula is None:
                failures.append((ticker, sens_name, cellname, "(missing)", "Table 4 Terminal Value formula not found"))
                continue
            n_checked += 1
            if local_fcf5_ref not in cell.formula:
                failures.append((ticker, sens_name, cellname, cell.formula, f"does not reference its own row's local Year-5 FCF cell {local_fcf5_ref}"))
    counters["table4_checked"] += n_checked

    # ---- Check 6: Table 5 (two-way grid) -- the fixed defect ----
    n_checked = check_table5(sens, sens_name, ticker, fcf_full_ref, rev_full_ref, failures)
    counters["table5_checked"] += n_checked


def check_table5(sens, sens_name, ticker, fcf_full_ref, rev_full_ref, failures):
    """Requires EXACTLY 6 numeric g-headers, EXACTLY 6 numeric WACC headers, and
    EXACTLY 36 formula cells at their intersection, each referencing the correct
    row's WACC header cell, the correct column's g header cell, and the base DCF
    sheet's Year-5 FCF cell (never the Revenue cell). Returns the number of grid
    cells actually found to carry a formula (0..36) -- the caller uses this to
    prove the audit examined something, not merely that it printed PASS."""
    hdr_hit = find_cell_starting_with(sens, "WACC \\ g", col0=0)
    if hdr_hit is None:
        failures.append((ticker, sens_name, "A?", "(missing)", "could not locate Table 5's 'WACC \\ g' header cell"))
        return 0
    hdr_row, hdr_col = hdr_hit  # hdr_col is 0 (column A)

    g_run = contiguous_numeric_run(sens, hdr_row, hdr_col + 1, direction=(0, 1))
    if len(g_run) != REQUIRED_G_HEADERS:
        failures.append((ticker, sens_name, f"row {hdr_row + 1}", f"{len(g_run)} numeric headers found",
                          f"Table 5 requires exactly {REQUIRED_G_HEADERS} numeric terminal-growth column headers; found {len(g_run)}"))
        return 0

    wacc_run = contiguous_numeric_run(sens, hdr_row + 1, hdr_col, direction=(1, 0))
    if len(wacc_run) != REQUIRED_WACC_HEADERS:
        failures.append((ticker, sens_name, f"col A", f"{len(wacc_run)} numeric headers found",
                          f"Table 5 requires exactly {REQUIRED_WACC_HEADERS} numeric WACC row headers; found {len(wacc_run)}"))
        return 0

    g_cols = [c for (_r, c, _v) in g_run]
    wacc_rows = [r for (r, _c, _v) in wacc_run]

    n_checked = 0
    for r in wacc_rows:
        wacc_header_ref = f"$A{r + 1}"  # this row's own WACC header cell, absolute-column/relative-row style used by the generator
        for c in g_cols:
            g_header_ref = f"{col_index_to_letters(c)}${hdr_row + 1}"  # this column's own g header cell
            cellname = f"{col_index_to_letters(c)}{r + 1}"
            cell = sens.get((r, c))
            if cell is None or cell.formula is None:
                failures.append((ticker, sens_name, cellname, "(missing)",
                                  "Table 5 grid cell has no formula -- a missing formula is a failure, not a skip"))
                continue
            n_checked += 1
            formula = cell.formula
            if fcf_full_ref not in formula:
                failures.append((ticker, sens_name, cellname, formula, f"does not reference base DCF Year-5 FCF cell {fcf_full_ref}"))
            if rev_full_ref in formula:
                failures.append((ticker, sens_name, cellname, formula, f"references Year-5 REVENUE cell {rev_full_ref} instead of FCF"))
            if wacc_header_ref not in formula:
                failures.append((ticker, sens_name, cellname, formula, f"does not reference its own row's WACC header cell {wacc_header_ref} -- WACC/growth references must correspond to the correct row/column headers"))
            if g_header_ref not in formula:
                failures.append((ticker, sens_name, cellname, formula, f"does not reference its own column's terminal-growth header cell {g_header_ref} -- WACC/growth references must correspond to the correct row/column headers"))

    if n_checked != REQUIRED_GRID_CELLS:
        failures.append((ticker, sens_name, "grid", f"{n_checked}/{REQUIRED_GRID_CELLS} cells with formulas",
                          f"Table 5 requires exactly {REQUIRED_GRID_CELLS} formula cells (6x6); found {n_checked} with a formula present"))

    return n_checked


def run_audit(xlsx_path):
    if not os.path.exists(xlsx_path):
        print(f"FATAL: {xlsx_path} does not exist.")
        return 1, [], {}
    wb = Workbook(xlsx_path)
    failures = []
    counters = {"dcf_tv_checked": 0, "table1_checked": 0, "table2_checked": 0,
                "table3_checked": 0, "table4_checked": 0, "table5_checked": 0}
    for tk in TICKERS:
        audit_company(wb, tk, failures, counters)
    return (1 if failures else 0), failures, counters


def print_report(xlsx_path, failures, counters):
    print(f"Audited: {xlsx_path}")
    print(f"Companies: {', '.join(TICKERS)}")
    print("Checks: base DCF Terminal Value; Table 1 (WACC) sensitivity; Table 2 (terminal-growth) "
          "sensitivity; Table 3 (revenue-growth) own-row FCF; Table 4 (margin) own-row FCF; "
          "Table 5 (two-way grid) Terminal Value -- all cell locations discovered from the "
          "workbook's own labels/headers via structured XML parsing (xml.etree.ElementTree), "
          "not supplied by the generating code.")
    print(f"\nCells actually examined (proof of coverage, not just a PASS/FAIL verdict):")
    print(f"  Base DCF Terminal Value formulas checked: {counters.get('dcf_tv_checked', 0)} (expected {len(TICKERS)})")
    print(f"  Table 1 (WACC) formulas checked:           {counters.get('table1_checked', 0)}")
    print(f"  Table 2 (terminal growth) formulas checked: {counters.get('table2_checked', 0)}")
    print(f"  Table 3 (revenue growth) formulas checked: {counters.get('table3_checked', 0)}")
    print(f"  Table 4 (margin) formulas checked:         {counters.get('table4_checked', 0)}")
    print(f"  Table 5 (two-way grid) formulas checked:   {counters.get('table5_checked', 0)} "
          f"(expected {REQUIRED_GRID_CELLS_TOTAL} = {REQUIRED_GRID_CELLS} per company x {len(TICKERS)} companies)")

    if failures:
        print(f"\nFAIL -- {len(failures)} formula-text defect(s) found:\n")
        for tk, sheet, cell, formula, reason in failures:
            print(f"  [{tk}] {sheet}!{cell}")
            print(f"    formula: {formula}")
            print(f"    reason:  {reason}\n")
    else:
        table5_ok = counters.get("table5_checked", 0) == REQUIRED_GRID_CELLS_TOTAL
        if not table5_ok:
            print(f"\nFAIL -- 0 formula-text defects, but Table 5 coverage is {counters.get('table5_checked', 0)}"
                  f"/{REQUIRED_GRID_CELLS_TOTAL} instead of the required {REQUIRED_GRID_CELLS_TOTAL}. "
                  f"A PASS is not printed unless every required cell was actually examined.")
        else:
            print("\nPASS -- every audited formula references the correct cell, "
                  f"including all {REQUIRED_GRID_CELLS_TOTAL} Table 5 grid cells across {len(TICKERS)} companies.")


def audit_exit_code(rc, counters):
    if rc != 0:
        return 1
    if counters.get("table5_checked", 0) != REQUIRED_GRID_CELLS_TOTAL:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Self-test: negative controls against temporary corrupted copies
# ---------------------------------------------------------------------------

def _get_sheet_part_path(content, sheet_display_name):
    wb_xml = content["xl/workbook.xml"].decode("utf-8")
    rels_xml = content["xl/_rels/workbook.xml.rels"].decode("utf-8")
    wb_root = ET.fromstring(wb_xml)
    rels_root = ET.fromstring(rels_xml)
    rid_to_target = {rel.get("Id"): rel.get("Target") for rel in rels_root.findall(q(PKG_REL_NS, "Relationship"))}
    sheets_el = wb_root.find(q(MAIN_NS, "sheets"))
    rid = None
    for sheet_el in sheets_el.findall(q(MAIN_NS, "sheet")):
        if sheet_el.get("name") == sheet_display_name:
            rid = sheet_el.get(q(DOC_REL_NS, "id"))
            break
    target = rid_to_target[rid]
    return target if target.startswith("xl/") else "xl/" + target


def _load_zip_content(path):
    with zipfile.ZipFile(path) as z:
        return z.namelist(), {n: z.read(n) for n in z.namelist()}


def _write_zip_content(path, names, content):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, content[n])


def _escape_xml(text):
    return (text.replace("&", "&amp;").replace("'", "&apos;").replace('"', "&quot;")
                .replace("<", "&lt;").replace(">", "&gt;"))


def _make_corrupted_copy(source_xlsx, tmp_path, mode):
    """mode: 'corrupt' replaces the FCF ref in Sensitivity_MSFT!B55 with the Revenue
    ref; 'remove_formula' deletes B55's <f> element entirely, leaving only its cached
    <v>, to test the missing-formula-is-a-failure requirement. Returns the target
    cell address that was altered."""
    shutil.copy2(source_xlsx, tmp_path)
    names, content = _load_zip_content(tmp_path)

    wb = Workbook(tmp_path)
    dcf_grid = wb.grid("DCF_MSFT")
    sens_grid = wb.grid("Sensitivity_MSFT")

    year5_hits = find_all_cells_starting_with(dcf_grid, "Year 5")
    year5_row, year5_col = year5_hits[0]
    rev_hit = find_cell_starting_with(dcf_grid, "Revenue = Revenue_(t-1)", col0=0)
    fcf_hit = find_cell_starting_with(dcf_grid, "Unlevered FCF = NOPAT", col0=0)
    fcf_ref = "'DCF_MSFT'!$" + col_index_to_letters(year5_col) + f"${fcf_hit[0] + 1}"
    rev_ref = "'DCF_MSFT'!$" + col_index_to_letters(year5_col) + f"${rev_hit[0] + 1}"

    hdr_hit = find_cell_starting_with(sens_grid, "WACC \\ g", col0=0)
    hdr_row = hdr_hit[0]
    first_grid_row = hdr_row + 1
    target_cell = sens_grid.get((first_grid_row, 1))  # B<first_grid_row+1> -- expected to be "B55"
    target_addr = f"B{first_grid_row + 1}"
    if target_cell is None or target_cell.formula is None or fcf_ref not in target_cell.formula:
        raise RuntimeError(f"Self-test setup failed: expected a formula containing {fcf_ref} at {target_addr}, "
                            f"found: {target_cell.formula if target_cell else None}")

    sheet_part = _get_sheet_part_path(content, "Sensitivity_MSFT")
    xml_text = content[sheet_part].decode("utf-8")
    old_escaped_formula = _escape_xml(target_cell.formula)

    if mode == "corrupt":
        new_formula = target_cell.formula.replace(fcf_ref, rev_ref)
        new_escaped_formula = _escape_xml(new_formula)
        old_block = f"<f>{old_escaped_formula}</f>"
        new_block = f"<f>{new_escaped_formula}</f>"
    elif mode == "remove_formula":
        old_block = f"<f>{old_escaped_formula}</f>"
        new_block = ""  # delete the <f>...</f> element entirely, leaving any cached <v> untouched
    else:
        raise ValueError(mode)

    if old_block not in xml_text:
        raise RuntimeError(f"Self-test setup failed: could not find the exact formula text to alter at {target_addr}.")
    corrupted_xml = xml_text.replace(old_block, new_block, 1)
    content[sheet_part] = corrupted_xml.encode("utf-8")
    _write_zip_content(tmp_path, names, content)
    return target_addr


def run_self_test(source_xlsx=DEFAULT_XLSX):
    print("=== formula_audit.py self-test (negative controls) ===\n")

    print("[1/4] Auditing the REAL workbook -- expect PASS with full Table 5 coverage")
    rc_real, failures_real, counters_real = run_audit(source_xlsx)
    print_report(source_xlsx, failures_real, counters_real)
    if audit_exit_code(rc_real, counters_real) != 0:
        print("\nSELF-TEST FAILED: the real workbook did not pass with full coverage. "
              "Stopping before attempting corruption.")
        return 1
    if counters_real["table5_checked"] != REQUIRED_GRID_CELLS_TOTAL:
        print(f"\nSELF-TEST FAILED: real workbook Table 5 coverage is "
              f"{counters_real['table5_checked']}, expected {REQUIRED_GRID_CELLS_TOTAL}.")
        return 1

    overall_ok = True
    tmp_dir = tempfile.mkdtemp(prefix="formula_audit_selftest_")
    try:
        # --- Negative control A: corrupt a Table 5 formula's cell reference ---
        tmp_path_a = os.path.join(tmp_dir, "_TEMP_negative_control_corrupt.xlsx")
        try:
            print(f"\n[2/4] Negative control A -- corrupting a Table 5 formula's FCF reference: {tmp_path_a}")
            target_addr = _make_corrupted_copy(source_xlsx, tmp_path_a, mode="corrupt")
            print(f"  Corrupted {target_addr} (FCF reference -> Revenue reference).")
            rc_a, failures_a, counters_a = run_audit(tmp_path_a)
            print_report(tmp_path_a, failures_a, counters_a)
            identified = any(tk == "MSFT" and sheet == "Sensitivity_MSFT" and cell == target_addr
                              for tk, sheet, cell, formula, reason in failures_a)
            if audit_exit_code(rc_a, counters_a) == 0:
                print(f"\nSELF-TEST FAILED (control A): audit passed against a corrupted {target_addr}.")
                overall_ok = False
            elif not identified:
                print(f"\nSELF-TEST FAILED (control A): audit failed but did not specifically identify {target_addr}.")
                overall_ok = False
            else:
                print(f"\nControl A PASSED: corrupted {target_addr} was specifically identified as a defect.")
        finally:
            if os.path.exists(tmp_path_a):
                os.remove(tmp_path_a)
                print(f"Deleted temporary copy: {tmp_path_a}")

        # --- Negative control B: remove the formula entirely from a Table 5 cell ---
        tmp_path_b = os.path.join(tmp_dir, "_TEMP_negative_control_missing_formula.xlsx")
        try:
            print(f"\n[3/4] Negative control B -- deleting a Table 5 cell's formula entirely: {tmp_path_b}")
            target_addr_b = _make_corrupted_copy(source_xlsx, tmp_path_b, mode="remove_formula")
            print(f"  Removed the <f> element from {target_addr_b}, leaving only its cached value.")
            rc_b, failures_b, counters_b = run_audit(tmp_path_b)
            print_report(tmp_path_b, failures_b, counters_b)
            identified_missing = any(tk == "MSFT" and sheet == "Sensitivity_MSFT" and cell == target_addr_b
                                      and formula == "(missing)"
                                      for tk, sheet, cell, formula, reason in failures_b)
            if audit_exit_code(rc_b, counters_b) == 0:
                print(f"\nSELF-TEST FAILED (control B): audit passed despite a missing formula at {target_addr_b}.")
                overall_ok = False
            elif not identified_missing:
                print(f"\nSELF-TEST FAILED (control B): audit failed but did not identify {target_addr_b} as missing.")
                overall_ok = False
            else:
                print(f"\nControl B PASSED: missing formula at {target_addr_b} was correctly treated as a failure, not skipped.")
        finally:
            if os.path.exists(tmp_path_b):
                os.remove(tmp_path_b)
                print(f"Deleted temporary copy: {tmp_path_b}")

        print(f"\n[4/4] Self-test summary: {'ALL CONTROLS PASSED' if overall_ok else 'AT LEAST ONE CONTROL FAILED'}")
        return 0 if overall_ok else 1
    finally:
        if os.path.isdir(tmp_dir):
            remaining = os.listdir(tmp_dir)
            for fn in remaining:
                os.remove(os.path.join(tmp_dir, fn))
            os.rmdir(tmp_dir)


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return run_self_test()

    xlsx_path = args[0] if args else DEFAULT_XLSX
    rc, failures, counters = run_audit(xlsx_path)
    print_report(xlsx_path, failures, counters)
    return audit_exit_code(rc, counters)


if __name__ == "__main__":
    sys.exit(main())
