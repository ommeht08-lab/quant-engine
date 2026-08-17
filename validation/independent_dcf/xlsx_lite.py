#!/usr/bin/env python3
"""
xlsx_lite.py -- Minimal, dependency-free .xlsx (OOXML) writer.

Written for this independent DCF validation because openpyxl/xlsxwriter and
LibreOffice are not available in this environment, and the validation task
instructions prohibit installing dependencies. Uses only the Python standard
library (zipfile, xml string templates, hashlib).

Supports exactly what this validation workbook needs: multiple sheets, string/
number/formula cells (formulas carry a real <f> element plus a Python-computed
cached <v> so the file shows sensible values even before a spreadsheet engine
recalculates), a small set of named styles (number formats + fills + fonts +
borders), column widths, freeze panes, and row heights. Does not support
charts, images, pivot tables, or full formula parsing -- not needed here.
"""
import zipfile
import re


def col_letter(col_idx):
    """0-indexed column number -> Excel column letters (0 -> A, 25 -> Z, 26 -> AA)."""
    letters = ""
    n = col_idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def cell_ref(row_idx, col_idx):
    """0-indexed row/col -> 'A1'-style reference (row_idx 0 -> row 1)."""
    return f"{col_letter(col_idx)}{row_idx + 1}"


def xml_escape(s):
    s = str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


class Style:
    """A named cell style: number format + font + fill + border + alignment."""
    _next_id = 0

    def __init__(self, name, num_fmt="General", bold=False, italic=False, font_color=None,
                 fill_color=None, border=None, align=None, font_size=11, wrap=False):
        self.name = name
        self.num_fmt = num_fmt
        self.bold = bold
        self.italic = italic
        self.font_color = font_color  # RGB hex like "FF0000" (no #)
        self.fill_color = fill_color
        self.border = border  # None, "thin_bottom", "all_thin", "top_bottom"
        self.align = align  # None or "center"
        self.font_size = font_size
        self.wrap = wrap


class Cell:
    __slots__ = ("value", "formula", "style", "cached")

    def __init__(self, value=None, formula=None, style=None, cached=None):
        self.value = value
        self.formula = formula
        self.style = style
        self.cached = cached


class Sheet:
    def __init__(self, name):
        self.name = name
        self.cells = {}  # (row, col) -> Cell
        self.col_widths = {}  # col_idx -> width
        self.freeze = None  # (row_idx, col_idx) rows/cols above/left are frozen
        self.row_heights = {}
        self.merges = []  # list of "A1:B2"
        self.tab_color = None
        self.hidden = False

    def set(self, row, col, value=None, formula=None, style=None, cached=None):
        self.cells[(row, col)] = Cell(value, formula, style, cached)

    def set_width(self, col, width):
        self.col_widths[col] = width

    def set_widths(self, start_col, widths):
        for i, w in enumerate(widths):
            self.col_widths[start_col + i] = w

    def merge(self, r1, c1, r2, c2):
        self.merges.append(f"{cell_ref(r1, c1)}:{cell_ref(r2, c2)}")

    def freeze_panes(self, row, col):
        self.freeze = (row, col)

    def max_row(self):
        return max((r for r, c in self.cells.keys()), default=-1)

    def max_col(self):
        return max((c for r, c in self.cells.keys()), default=-1)


class Workbook:
    def __init__(self):
        self.sheets = []
        self.styles = {}  # name -> Style
        self._style_order = []
        self.add_style(Style("default"))

    def add_sheet(self, name):
        s = Sheet(name)
        self.sheets.append(s)
        return s

    def add_style(self, style):
        if style.name not in self.styles:
            self.styles[style.name] = style
            self._style_order.append(style.name)
        return style.name

    # ---------- XML building ----------

    def _build_styles_xml(self):
        # collect unique number formats (builtin ones skip custom id allocation)
        builtin_fmts = {"General": 0, "0.00%": 10, "0.0%": 9, "#,##0": 3, "#,##0.00": 4,
                        "$#,##0.00": 44, "0.00": 2, "0": 1}
        custom_fmts = {}
        next_fmt_id = 164
        fmt_ids = {}
        for name in self._style_order:
            st = self.styles[name]
            fmt = st.num_fmt
            if fmt in builtin_fmts:
                fmt_ids[fmt] = builtin_fmts[fmt]
            elif fmt not in fmt_ids:
                fmt_ids[fmt] = next_fmt_id
                custom_fmts[next_fmt_id] = fmt
                next_fmt_id += 1

        numfmts_xml = "".join(
            f'<numFmt numFmtId="{fid}" formatCode="{xml_escape(code)}"/>'
            for fid, code in custom_fmts.items()
        )

        fonts = []
        fills = ['<fill><patternFill patternType="none"/></fill>',
                 '<fill><patternFill patternType="gray125"/></fill>']
        fill_ids = {None: 0}
        borders = ['<border><left/><right/><top/><bottom/><diagonal/></border>']
        border_ids = {None: 0}
        xfs = []
        xf_ids = {}

        for idx, name in enumerate(self._style_order):
            st = self.styles[name]
            font_attrs = f'<sz val="{st.font_size}"/><name val="Calibri"/>'
            if st.bold:
                font_attrs = "<b/>" + font_attrs
            if st.italic:
                font_attrs = "<i/>" + font_attrs
            if st.font_color:
                font_attrs += f'<color rgb="FF{st.font_color}"/>'
            else:
                font_attrs += '<color rgb="FF000000"/>'
            fonts.append(f"<font>{font_attrs}</font>")
            font_id = idx

            if st.fill_color not in fill_ids:
                fill_ids[st.fill_color] = len(fills)
                fills.append(
                    f'<fill><patternFill patternType="solid"><fgColor rgb="FF{st.fill_color}"/>'
                    f'<bgColor indexed="64"/></patternFill></fill>'
                )
            fill_id = fill_ids[st.fill_color]

            if st.border not in border_ids:
                bstyle = "thin"
                if st.border == "all_thin":
                    bxml = (f'<left style="{bstyle}"><color indexed="64"/></left>'
                            f'<right style="{bstyle}"><color indexed="64"/></right>'
                            f'<top style="{bstyle}"><color indexed="64"/></top>'
                            f'<bottom style="{bstyle}"><color indexed="64"/></bottom><diagonal/>')
                elif st.border == "bottom":
                    bxml = f'<left/><right/><top/><bottom style="{bstyle}"><color indexed="64"/></bottom><diagonal/>'
                elif st.border == "top":
                    bxml = f'<left/><right/><top style="{bstyle}"><color indexed="64"/></top><bottom/><diagonal/>'
                elif st.border == "top_bottom":
                    bxml = (f'<left/><right/><top style="{bstyle}"><color indexed="64"/></top>'
                            f'<bottom style="{bstyle}"><color indexed="64"/></bottom><diagonal/>')
                else:
                    bxml = "<left/><right/><top/><bottom/><diagonal/>"
                border_ids[st.border] = len(borders)
                borders.append(f"<border>{bxml}</border>")
            border_id = border_ids[st.border]

            fmt_id = fmt_ids[st.num_fmt]
            align_xml = ""
            if st.align == "center":
                align_xml = '<alignment horizontal="center" vertical="center"/>'
            elif st.wrap:
                align_xml = '<alignment wrapText="1" vertical="top"/>'
            xfs.append(
                f'<xf numFmtId="{fmt_id}" fontId="{font_id}" fillId="{fill_id}" borderId="{border_id}" '
                f'xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyNumberFormat="1" '
                f'applyAlignment="{"1" if align_xml else "0"}">{align_xml}</xf>'
            )
            xf_ids[name] = idx

        self._xf_ids = xf_ids

        xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="{len(custom_fmts)}">{numfmts_xml}</numFmts>
<fonts count="{len(fonts)}">{"".join(fonts)}</fonts>
<fills count="{len(fills)}">{"".join(fills)}</fills>
<borders count="{len(borders)}">{"".join(borders)}</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
        return xml

    def _style_id(self, name):
        if name is None:
            return 0
        return self._xf_ids[name]

    def _build_sheet_xml(self, sheet):
        max_r = sheet.max_row()
        max_c = sheet.max_col()
        rows_xml = []
        by_row = {}
        for (r, c), cell in sheet.cells.items():
            by_row.setdefault(r, []).append((c, cell))

        for r in sorted(by_row.keys()):
            cells_in_row = sorted(by_row[r], key=lambda x: x[0])
            cell_xml_parts = []
            for c, cell in cells_in_row:
                ref = cell_ref(r, c)
                sid = self._style_id(cell.style)
                s_attr = f' s="{sid}"' if sid else ""
                if cell.formula is not None:
                    fval = xml_escape(cell.formula)
                    cached = cell.cached if cell.cached is not None else 0
                    if isinstance(cached, (int, float)):
                        cell_xml_parts.append(f'<c r="{ref}"{s_attr}><f>{fval}</f><v>{cached}</v></c>')
                    else:
                        cell_xml_parts.append(
                            f'<c r="{ref}"{s_attr} t="str"><f>{fval}</f><v>{xml_escape(cached)}</v></c>')
                elif cell.value is not None:
                    if isinstance(cell.value, bool):
                        cell_xml_parts.append(f'<c r="{ref}"{s_attr} t="b"><v>{1 if cell.value else 0}</v></c>')
                    elif isinstance(cell.value, (int, float)):
                        cell_xml_parts.append(f'<c r="{ref}"{s_attr}><v>{cell.value}</v></c>')
                    else:
                        text = xml_escape(cell.value)
                        cell_xml_parts.append(f'<c r="{ref}"{s_attr} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>')
                else:
                    if sid:
                        cell_xml_parts.append(f'<c r="{ref}"{s_attr}/>')
            ht = sheet.row_heights.get(r)
            ht_attr = f' ht="{ht}" customHeight="1"' if ht else ""
            rows_xml.append(f'<row r="{r+1}"{ht_attr}>{"".join(cell_xml_parts)}</row>')

        cols_xml = ""
        if sheet.col_widths:
            parts = []
            for c, w in sorted(sheet.col_widths.items()):
                parts.append(f'<col min="{c+1}" max="{c+1}" width="{w}" customWidth="1"/>')
            cols_xml = f'<cols>{"".join(parts)}</cols>'

        pane_xml = ""
        if sheet.freeze:
            fr, fc = sheet.freeze
            if fr or fc:
                active_cell = cell_ref(fr, fc)
                top_left = cell_ref(fr, fc)
                state = "frozen"
                pane_xml = (f'<pane xSplit="{fc}" ySplit="{fr}" topLeftCell="{top_left}" '
                            f'activePane="bottomRight" state="{state}"/>'
                            f'<selection pane="bottomRight" activeCell="{active_cell}" sqref="{active_cell}"/>')

        sheet_views = f'<sheetViews><sheetView workbookViewId="0">{pane_xml}</sheetView></sheetViews>'

        merge_xml = ""
        if sheet.merges:
            merge_xml = f'<mergeCells count="{len(sheet.merges)}">' + \
                        "".join(f'<mergeCell ref="{m}"/>' for m in sheet.merges) + "</mergeCells>"

        dim_ref = f"A1:{cell_ref(max(max_r,0), max(max_c,0))}"

        xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<dimension ref="{dim_ref}"/>
{sheet_views}
<sheetFormatPr defaultRowHeight="15"/>
{cols_xml}
<sheetData>{"".join(rows_xml)}</sheetData>
{merge_xml}
</worksheet>'''
        return xml

    def save(self, path):
        self._build_styles_xml_str = self._build_styles_xml()

        n = len(self.sheets)
        sheets_xml = "".join(
            f'<sheet name="{xml_escape(s.name)}" sheetId="{i+1}" r:id="rId{i+1}"/>'
            for i, s in enumerate(self.sheets)
        )
        workbook_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<fileVersion appName="xlsx_lite"/>
<workbookPr/>
<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="25600" windowHeight="15000"/></bookViews>
<sheets>{sheets_xml}</sheets>
<calcPr calcId="999999" fullCalcOnLoad="1"/>
</workbook>'''

        wb_rels = "".join(
            f'<Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i+1}.xml"/>'
            for i in range(n)
        )
        wb_rels_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{wb_rels}
<Relationship Id="rId{n+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

        root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''

        content_types = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
{"".join(f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(n))}
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''

        # Deliberately NO dcterms:created/modified here: both fields are optional in OOXML,
        # and a wall-clock "when was this built" value embedded in the file's content would
        # make the generated .xlsx different on every run even from byte-identical inputs --
        # incompatible with this project's determinism requirement for --rebuild-from-cache
        # (see build_snapshots.py's module docstring, "Determinism design"). Any build-time
        # information belongs in console output, never in generated file content.
        core_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>Independent DCF Validation Workbook</dc:title>
<dc:creator>Independent Validation Session</dc:creator>
</cp:coreProperties>'''

        app_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
<Application>xlsx_lite (stdlib-only, no macros)</Application>
<TitlesOfParts><vt:vector size="{n}" baseType="lpstr" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
{"".join(f'<vt:lpstr>{xml_escape(s.name)}</vt:lpstr>' for s in self.sheets)}
</vt:vector></TitlesOfParts>
</Properties>'''

        def fixed_writestr(z, name, data):
            # zipfile.writestr(name_as_str, data) stamps each entry with the CURRENT
            # wall-clock time by default, which would make the .xlsx's raw bytes differ
            # between two runs even when every part's XML content is identical --
            # incompatible with this project's determinism requirement (see
            # build_snapshots.py's "Determinism design"). Using an explicit ZipInfo
            # with a fixed date_time (the minimum valid DOS timestamp) makes the
            # output bytes a pure function of content, not of when it was built.
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            z.writestr(info, data)

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            fixed_writestr(z, "[Content_Types].xml", content_types)
            fixed_writestr(z, "_rels/.rels", root_rels)
            fixed_writestr(z, "docProps/core.xml", core_xml)
            fixed_writestr(z, "docProps/app.xml", app_xml)
            fixed_writestr(z, "xl/workbook.xml", workbook_xml)
            fixed_writestr(z, "xl/_rels/workbook.xml.rels", wb_rels_xml)
            fixed_writestr(z, "xl/styles.xml", self._build_styles_xml_str)
            for i, s in enumerate(self.sheets):
                fixed_writestr(z, f"xl/worksheets/sheet{i+1}.xml", self._build_sheet_xml(s))
        return path
