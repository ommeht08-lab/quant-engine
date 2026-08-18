"""
Minimal, read-only, stdlib-only OOXML (.xlsx) reader for Track A Phase 2B.

Mechanically independent of the code that generates
independent_dcf_validation.xlsx: does NOT import build_workbook.py,
shadow_calc.py, or xlsx_lite.py from validation/independent_dcf/. Parses
the finished .xlsx from the outside using only `zipfile` and
`xml.etree.ElementTree`, and locates cells by the workbook's own labels/
headers -- the same technique validation/independent_dcf/formula_audit.py
uses (independently reimplemented here, not imported, to keep this
directory's tooling self-contained).
"""
import re
import zipfile
import xml.etree.ElementTree as ET

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _q(ns, tag):
    return f"{{{ns}}}{tag}"


def col_letters_to_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def col_index_to_letters(idx: int) -> str:
    letters = ""
    n = idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


def parse_ref(ref: str):
    m = _CELL_REF_RE.match(ref)
    if not m:
        raise ValueError(f"Not a valid cell reference: {ref!r}")
    return int(m.group(2)) - 1, col_letters_to_index(m.group(1))


class Cell:
    __slots__ = ("ref", "row0", "col0", "formula", "text", "numeric", "kind")

    def __init__(self, ref, row0, col0, formula, text, numeric, kind):
        self.ref = ref
        self.row0 = row0
        self.col0 = col0
        self.formula = formula
        self.text = text
        self.numeric = numeric
        self.kind = kind

    @property
    def value(self):
        """Best-effort scalar value: numeric if present, else text."""
        if self.numeric is not None:
            return self.numeric
        return self.text

    def __repr__(self):
        return f"Cell({self.ref}, kind={self.kind}, formula={self.formula!r}, text={self.text!r}, numeric={self.numeric!r})"


class Workbook:
    """Read-only, whole-workbook loader. Every sheet is fully parsed at construction time."""

    def __init__(self, path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            wb_root = ET.fromstring(z.read("xl/workbook.xml"))
            rels_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))

            rid_to_target = {
                rel.get("Id"): rel.get("Target")
                for rel in rels_root.findall(_q(PKG_REL_NS, "Relationship"))
            }

            name_to_rid = {}
            sheets_el = wb_root.find(_q(MAIN_NS, "sheets"))
            for sheet_el in sheets_el.findall(_q(MAIN_NS, "sheet")):
                name_to_rid[sheet_el.get("name")] = sheet_el.get(_q(DOC_REL_NS, "id"))

            self.shared_strings = []
            if "xl/sharedStrings.xml" in names:
                sst_root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in sst_root.findall(_q(MAIN_NS, "si")):
                    self.shared_strings.append(self._si_text(si))

            self.sheet_names = list(name_to_rid.keys())
            self._grids = {}
            for name, rid in name_to_rid.items():
                target = rid_to_target.get(rid)
                if target is None:
                    continue
                part_path = target if target.startswith("xl/") else "xl/" + target
                root = ET.fromstring(z.read(part_path))
                self._grids[name] = self._parse_sheet(root)

    def _si_text(self, si_el):
        t_el = si_el.find(_q(MAIN_NS, "t"))
        if t_el is not None:
            return t_el.text or ""
        parts = []
        for r_el in si_el.findall(_q(MAIN_NS, "r")):
            t_el = r_el.find(_q(MAIN_NS, "t"))
            if t_el is not None and t_el.text:
                parts.append(t_el.text)
        return "".join(parts)

    def _parse_sheet(self, root):
        sheet_data = root.find(_q(MAIN_NS, "sheetData"))
        cells = {}
        if sheet_data is None:
            return cells
        for row_el in sheet_data.findall(_q(MAIN_NS, "row")):
            for c_el in row_el.findall(_q(MAIN_NS, "c")):
                ref = c_el.get("r")
                if ref is None:
                    continue
                row0, col0 = parse_ref(ref)
                t = c_el.get("t")
                f_el = c_el.find(_q(MAIN_NS, "f"))
                v_el = c_el.find(_q(MAIN_NS, "v"))
                is_el = c_el.find(_q(MAIN_NS, "is"))

                formula = f_el.text if f_el is not None and f_el.text else None
                text = None
                numeric = None

                if is_el is not None:
                    text = self._si_text(is_el)
                elif t == "s" and v_el is not None and v_el.text is not None:
                    idx = int(v_el.text)
                    text = self.shared_strings[idx] if 0 <= idx < len(self.shared_strings) else None
                elif t == "str" and v_el is not None:
                    text = v_el.text
                elif t == "b" and v_el is not None:
                    text = "TRUE" if v_el.text == "1" else "FALSE"
                elif v_el is not None and v_el.text is not None:
                    try:
                        numeric = float(v_el.text)
                    except ValueError:
                        text = v_el.text

                kind = "formula" if formula is not None else (
                    "string" if text is not None else ("numeric" if numeric is not None else "empty")
                )
                cells[(row0, col0)] = Cell(ref, row0, col0, formula, text, numeric, kind)
        return cells

    def has_sheet(self, name):
        return name in self._grids

    def grid(self, sheet_name):
        if sheet_name not in self._grids:
            raise KeyError(f"Sheet {sheet_name!r} not found. Available: {self.sheet_names}")
        return self._grids[sheet_name]

    def cell(self, sheet_name, ref):
        row0, col0 = parse_ref(ref)
        return self.grid(sheet_name).get((row0, col0))


def find_cell_starting_with(grid, prefix, col0=None):
    matches = sorted(
        (r, c) for (r, c), cell in grid.items()
        if cell.text is not None and cell.text.startswith(prefix) and (col0 is None or c == col0)
    )
    return matches[0] if matches else None


def find_all_cells_starting_with(grid, prefix, col0=None):
    return sorted(
        (r, c) for (r, c), cell in grid.items()
        if cell.text is not None and cell.text.startswith(prefix) and (col0 is None or c == col0)
    )


def find_header_column(grid, header_row0, exact_text):
    for (r, c), cell in grid.items():
        if r == header_row0 and cell.text == exact_text:
            return c
    return None


def contiguous_numeric_run(grid, row0, start_col0, direction=(0, 1)):
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
