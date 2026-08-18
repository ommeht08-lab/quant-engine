#!/usr/bin/env python3
"""
build_workbook_v2.py -- Independent DCF validation workbook generator, V2.

Track A Phase 2C, second generation. Builds
validation/independent_dcf/independent_dcf_validation_v2.xlsx from the SAME
frozen snapshots in validation/independent_dcf/snapshots/*.json V1 used, using
ONLY formulas derived directly from docs/model-specifications/dcf.md and
docs/model-specifications/wacc-capm.md -- never by reading or importing this
repository's src/dcf_model/dcf.py. This script and shadow_calc_v2.py were
written and this workbook was built, audited, and hashed BEFORE production
code or the Track A Phase 2B/2C reconciliation implementation was
re-inspected in this phase.

V2 exists because docs/model-specifications/dcf.md's prose did not, at the
time V1 was built, define `years_elapsed`'s exact day-count convention. V1
(preserved unchanged at independent_dcf_validation.xlsx) resolved that
ambiguity as `COUNT(periods) - 1`, a plain period count. Track A Phase 2C
clarified the specification (`years_elapsed` = actual elapsed calendar days
between the earliest- and latest-dated valid observations, divided by
365.25 -- see `A-028`/`L-019`) and this V2 workbook is built from that
clarified specification. Every OTHER formula in this workbook (WACC, FCF
projection, terminal value, discounting, sensitivity tables) is unchanged
from V1's spec-derived formulas, because the specification did not change
anywhere else.

No third-party libraries (openpyxl/xlsxwriter) and no LibreOffice were available
in this environment, and installing dependencies is out of scope for this
validation task, so the .xlsx file is hand-built as raw OOXML via xlsx_lite.py
(stdlib zipfile + string XML only; reused unmodified from V1 -- it is pure OOXML
serialization plumbing with no DCF calculation content). shadow_calc_v2.py
independently re-derives every formula in Python to supply the cached values
written alongside each Excel <f> formula (so the workbook shows correct numbers
immediately, and so the numbers can be sanity-checked without a spreadsheet
engine).

Run: python3 build_workbook_v2.py   (from this directory, or any cwd -- paths
are absolute-relative to this file's location).
"""
import json
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xlsx_lite import Workbook, Style, col_letter, cell_ref
import shadow_calc_v2 as sc

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, "snapshots")

TICKERS = ["MSFT", "CAT", "INTC", "VZ"]

PROFILE_LABEL = {
    "MSFT": "Large-cap, capital-light, high-margin",
    "CAT": "Capital-intensive, moderate leverage",
    "INTC": "Mature, negative/near-zero recent revenue growth",
    "VZ": "Meaningfully leveraged (recommended 4th profile)",
}

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


def build_styles(wb):
    s = {}
    s["title"] = wb.add_style(Style("title", bold=True, font_size=16))
    s["subtitle"] = wb.add_style(Style("subtitle", italic=True, font_size=11, font_color="555555"))
    s["section"] = wb.add_style(Style("section", bold=True, font_size=12, fill_color="1F4E78", font_color="FFFFFF"))
    s["header"] = wb.add_style(Style("header", bold=True, fill_color="D9E1F2", border="all_thin"))
    s["header_center"] = wb.add_style(Style("header_center", bold=True, fill_color="D9E1F2", border="all_thin", align="center"))
    s["header_wrap"] = wb.add_style(Style("header_wrap", bold=True, fill_color="D9E1F2", border="all_thin", wrap=True))
    s["label"] = wb.add_style(Style("label", border="all_thin"))
    s["label_bold"] = wb.add_style(Style("label_bold", bold=True, border="all_thin"))
    s["spec_ref"] = wb.add_style(Style("spec_ref", italic=True, font_size=9, font_color="666666", wrap=True))
    # INPUT cells: blue font -- editable/frozen raw facts (financial-modeling convention)
    s["input_num"] = wb.add_style(Style("input_num", font_color="0000CC", num_fmt="#,##0", border="all_thin"))
    s["input_usd"] = wb.add_style(Style("input_usd", font_color="0000CC", num_fmt="$#,##0", border="all_thin"))
    s["input_pct"] = wb.add_style(Style("input_pct", font_color="0000CC", num_fmt="0.00%", border="all_thin"))
    s["input_txt"] = wb.add_style(Style("input_txt", font_color="0000CC", border="all_thin"))
    # V2: a REAL Excel date serial number with a date display format -- not a text
    # label like V1's -- so DCF_<TK>'s "Years elapsed" formula can subtract two
    # date cells directly (date arithmetic requires numeric date values).
    s["input_date"] = wb.add_style(Style("input_date", font_color="0000CC", num_fmt="yyyy-mm-dd", border="all_thin"))
    s["formula_date_diag"] = wb.add_style(Style("formula_date_diag", num_fmt="#,##0.000000", border="all_thin", fill_color="F2F2F2"))
    # FORMULA cells: black font, light-gray fill -- computed, never hardcoded
    s["formula_usd"] = wb.add_style(Style("formula_usd", num_fmt="$#,##0", border="all_thin", fill_color="F2F2F2"))
    s["formula_pct"] = wb.add_style(Style("formula_pct", num_fmt="0.00%", border="all_thin", fill_color="F2F2F2"))
    s["formula_num"] = wb.add_style(Style("formula_num", num_fmt="#,##0.00", border="all_thin", fill_color="F2F2F2"))
    s["formula_price"] = wb.add_style(Style("formula_price", num_fmt="$#,##0.00", border="all_thin", fill_color="F2F2F2"))
    s["formula_ratio"] = wb.add_style(Style("formula_ratio", num_fmt="0.00\"x\"", border="all_thin", fill_color="F2F2F2"))
    s["formula_txt"] = wb.add_style(Style("formula_txt", border="all_thin", fill_color="F2F2F2"))
    # PASS/FAIL/PENDING
    s["pass"] = wb.add_style(Style("pass", bold=True, fill_color="C6EFCE", font_color="006100", border="all_thin", align="center"))
    s["fail"] = wb.add_style(Style("fail", bold=True, fill_color="FFC7CE", font_color="9C0006", border="all_thin", align="center"))
    s["pending"] = wb.add_style(Style("pending", bold=True, fill_color="FFEB9C", font_color="9C6500", border="all_thin", align="center"))
    s["note"] = wb.add_style(Style("note", italic=True, font_size=9, wrap=True, font_color="444444"))
    s["wrap"] = wb.add_style(Style("wrap", wrap=True, border="all_thin"))
    return s


# ---------------------------------------------------------------------------
# Small row-cursor helper to avoid hand-tracked row-number bugs
# ---------------------------------------------------------------------------

class RC:
    """Row cursor over a Sheet: sequential row writer with named-row bookmarks."""

    def __init__(self, sheet):
        self.sh = sheet
        self.r = 0
        self.marks = {}

    def mark(self, name):
        self.marks[name] = self.r
        return self.r

    def skip(self, n=1):
        self.r += n

    def title(self, text, style, span=6):
        self.sh.set(self.r, 0, value=text, style=style)
        self.sh.merge(self.r, 0, self.r, span)
        self.r += 1
        return self.r - 1

    def section(self, text, style, span=7):
        row = self.r
        self.sh.set(row, 0, value=text, style=style)
        for c in range(1, span + 1):
            self.sh.set(row, c, value=None, style=style)
        self.sh.merge(row, 0, row, span)
        self.r += 1
        return row

    def row(self):
        row = self.r
        self.r += 1
        return row


def load_snapshots(snap_dir=None):
    """snap_dir override lets a staged/transactional caller (build_snapshots.py's
    rebuild_from_cache) build a workbook from a STAGED snapshots directory, so the
    workbook can be validated before anything in the live output set is touched."""
    snap_dir = snap_dir or SNAP_DIR
    snaps = {}
    for tk in TICKERS:
        snaps[tk] = json.load(open(os.path.join(snap_dir, f"{tk}_snapshot.json")))
    return snaps


def load_results(snaps):
    return {tk: sc.run_full_dcf(snaps[tk]) for tk in TICKERS}


# ---------------------------------------------------------------------------
# Inputs_<TICKER> sheet -- frozen facts + assumptions, ALL hardcoded (input style)
# ---------------------------------------------------------------------------

def build_inputs_sheet(wb, s, tk, snap):
    sh = wb.add_sheet(f"Inputs_{tk}")
    rc = RC(sh)
    # V2: sort by actual fiscal-period-end date, never trust input/column order --
    # per the corrected specification. A no-op for these four companies' frozen
    # snapshots (already chronological), but the workbook's own behavior must not
    # silently depend on that coincidence.
    hist = sorted(snap["historical_annual_data"], key=lambda h: h["fiscal_period_end"])
    lyf = snap["latest_year_facts"]
    mkt = snap["market_data"]

    rc.title(f"Inputs — {tk} ({snap['entity_name']})  |  Frozen {snap['retrieval_timestamp_utc']}", s["title"])
    sh.set(rc.row(), 0, value=f"Profile: {PROFILE_LABEL[tk]}  |  CIK {snap['cik']}  |  All USD values are actual dollars (not thousands/millions) unless noted.", style=s["subtitle"])
    rc.skip(1)

    rc.section("SECTION 1 — Historical Annual Data (SEC EDGAR, frozen)", s["section"])
    hdr = rc.row()
    for c, txt in enumerate(["Fiscal Year End", "Revenue (USD)", "EBIT / Operating Income (USD)", "Provenance"]):
        sh.set(hdr, c, value=txt, style=s["header"])
    hist_start = rc.mark("hist_start")
    for h in hist:
        r = rc.row()
        # V2: a real Excel date serial number (with a "yyyy-mm-dd" display format),
        # not a text label -- required so DCF_<TK>'s "Years elapsed" formula can
        # subtract two date cells directly. Displays identically to V1's text dates.
        sh.set(r, 0, value=sc.excel_serial_date(h["fiscal_period_end"]), style=s["input_date"])
        sh.set(r, 1, value=h["revenue_raw_usd"], style=s["input_usd"])
        sh.set(r, 2, value=h["ebit_raw_usd"], style=s["input_usd"])
        prov = h["ebit_derivation_note"] if h["ebit_xbrl_tag"].startswith("DERIVED") else f"XBRL tag: {h['revenue_xbrl_tag']}"
        sh.set(r, 3, value=prov, style=s["note"])
    hist_end = rc.r - 1
    rc.marks["hist_end"] = hist_end
    rc.skip(1)

    rc.section("SECTION 2 — Latest-Year Facts (SEC EDGAR, frozen)", s["section"])
    r = rc.row(); sh.set(r, 0, value="Latest Complete Fiscal Year End", style=s["label"]); sh.set(r, 1, value=sc.excel_serial_date(snap["latest_complete_fiscal_year_end"]), style=s["input_date"])
    rc.marks["latest_fy_end"] = r
    r = rc.row(); sh.set(r, 0, value="Pretax Income (USD)", style=s["label"]); sh.set(r, 1, value=lyf["pretax_income_usd"], style=s["input_usd"])
    rc.marks["pretax"] = r
    r = rc.row(); sh.set(r, 0, value="Tax Provision (USD)", style=s["label"]); sh.set(r, 1, value=lyf["tax_provision_usd"], style=s["input_usd"])
    rc.marks["tax_prov"] = r
    r = rc.row(); sh.set(r, 0, value="Interest Expense (USD)", style=s["label"]); sh.set(r, 1, value=lyf["interest_expense_usd"], style=s["input_usd"])
    sh.set(r, 3, value=f"Method: {lyf['interest_expense_method']}; tag: {lyf['interest_expense_xbrl_tag']}", style=s["note"])
    rc.marks["interest"] = r
    r = rc.row(); sh.set(r, 0, value="Total Debt (USD)", style=s["label"]); sh.set(r, 1, value=lyf["total_debt_usd"], style=s["input_usd"])
    sh.set(r, 3, value=f"{lyf.get('total_debt_normalization_equation') or lyf['total_debt_normalization_method']}  |  Full per-component provenance (XBRL tag, accession, filed date, source URL): see Sources sheet.", style=s["note"])
    rc.marks["debt"] = r
    r = rc.row(); sh.set(r, 0, value="Cash & Equivalents (USD)", style=s["label"]); sh.set(r, 1, value=lyf["cash_and_equivalents_usd"], style=s["input_usd"])
    rc.marks["cash"] = r
    rc.skip(1)

    rc.section("SECTION 3 — Market Data (Yahoo Finance via yfinance, frozen)", s["section"])
    r = rc.row(); sh.set(r, 0, value="Current Price (USD/share)", style=s["label"]); sh.set(r, 1, value=mkt["current_price_usd_per_share"], style=s["input_usd"])
    rc.marks["price"] = r
    r = rc.row(); sh.set(r, 0, value="Shares Outstanding", style=s["label"]); sh.set(r, 1, value=mkt["shares_outstanding"], style=s["input_num"])
    rc.marks["shares"] = r
    r = rc.row(); sh.set(r, 0, value="Beta (levered equity)", style=s["label"]); sh.set(r, 1, value=mkt["beta_levered_equity"], style=s["input_num"])
    rc.marks["beta"] = r
    r = rc.row(); sh.set(r, 0, value="Sector (GICS)", style=s["label"]); sh.set(r, 1, value=mkt["sector_gics"], style=s["input_txt"])
    rc.marks["sector"] = r
    rc.skip(1)

    rc.section("SECTION 4 — Assumptions (shared across companies unless noted; assumptions-register.md)", s["section"])
    assumptions = [
        ("rf", "Risk-free rate (A-001)", 0.04),
        ("mrp", "Market risk premium (A-009)", 0.055),
        ("term_g", "Terminal growth rate, base case (A-003)", 0.025),
        ("proj_years", "Projection years", 5),
        ("da_pct", "D&A % of revenue (A-004)", 0.03),
        ("capex_pct", "CapEx % of revenue (A-004)", 0.04),
        ("nwc_pct", "ΔNWC % of revenue change (A-004)", 0.01),
        ("wacc_min", "WACC clamp — minimum (A-006)", 0.05),
        ("wacc_max", "WACC clamp — maximum (A-006)", 0.20),
        ("term_g_min", "Terminal growth clamp — minimum", 0.0),
        ("term_g_max", "Terminal growth clamp — maximum", 0.05),
        ("max_hist_growth", "Historical revenue growth cap (A-002)", 0.25),
    ]
    for key, label, val in assumptions:
        r = rc.row()
        sh.set(r, 0, value=label, style=s["label"])
        style = s["input_num"] if key == "proj_years" else s["input_pct"]
        sh.set(r, 1, value=val, style=style)
        rc.marks[key] = r

    sh.set_widths(0, [42, 16, 16, 60])
    sh.freeze_panes(4, 1)
    return rc.marks, hist_start, hist_end


# ---------------------------------------------------------------------------
# DCF_<TICKER> sheet -- all formulas, referencing Inputs_<TICKER>
# ---------------------------------------------------------------------------

def xrow(n):
    """0-indexed row -> 1-indexed Excel row."""
    return n + 1


def build_dcf_sheet(wb, s, tk, snap, result, imarks, hist_start, hist_end):
    sh = wb.add_sheet(f"DCF_{tk}")
    rc = RC(sh)
    IN = f"'Inputs_{tk}'!"

    n_hist = hist_end - hist_start + 1  # number of historical rows (5)
    hist_first_rev = f"{IN}$B${xrow(hist_start)}"
    hist_last_rev = f"{IN}$B${xrow(hist_end)}"
    hist_first_date = f"{IN}$A${xrow(hist_start)}"
    hist_last_date = f"{IN}$A${xrow(hist_end)}"
    hist_rev_range = f"{IN}$B${xrow(hist_start)}:$B${xrow(hist_end)}"
    hist_ebit_range = f"{IN}$C${xrow(hist_start)}:$C${xrow(hist_end)}"

    def IREF(key):
        return f"{IN}$B${xrow(imarks[key])}"

    rc.title(f"DCF Valuation — {tk} ({snap['entity_name']})  [V2 — Track A Phase 2C]", s["title"])
    sh.set(rc.row(), 0, value="Every formula below is entered independently from docs/model-specifications/dcf.md and wacc-capm.md prose/equations — never copied from src/dcf_model/dcf.py. V2: years_elapsed now uses actual fiscal-period-end date subtraction, per the Track A Phase 2C specification clarification (A-028/L-019) — see row below.", style=s["subtitle"])
    rc.skip(1)

    # ---- Section: historical derivation ----
    rc.section("SECTION 1 — Historical Revenue CAGR & Operating Margin  [dcf.md §Historical revenue-growth and operating-margin derivation]", s["section"])
    r = rc.row()
    sh.set(r, 0, value="Years elapsed = (latest fiscal date − earliest fiscal date) / 365.25  [V2; A-028]", style=s["label"])
    f_years = f"({hist_last_date}-{hist_first_date})/365.25"
    sh.set(r, 1, formula=f_years, cached=result["years_elapsed"], style=s["formula_date_diag"])
    sh.set(r, 3, value="Spec: dcf.md — years_elapsed is ACTUAL elapsed calendar days between the earliest- and latest-dated valid observations / 365.25, never (periods−1) (A-028; Track A Phase 2C, resolving the ambiguity Phase 2B found).", style=s["spec_ref"])
    years_row = r

    r = rc.row()
    naive_n_minus_1 = n_hist - 1
    sh.set(r, 0, value="Diagnostic — naive (N periods − 1) years [V1's convention; NOT used below]", style=s["label"])
    sh.set(r, 1, value=naive_n_minus_1, style=s["input_num"])
    irregular = abs(result["years_elapsed"] - naive_n_minus_1) > 1e-9
    diag_style = s["fail"] if irregular else s["pass"]
    diag_text = (
        f"IRREGULAR FISCAL CALENDAR DETECTED — actual years_elapsed ({result['years_elapsed']:.6f}) "
        f"≠ naive count ({naive_n_minus_1}). This is the exact condition that made V1 (COUNT−1) diverge "
        f"from the codebase for this company in Track A Phase 2B — see docs/model-change-log.md."
        if irregular else
        f"Fiscal calendar is evenly spaced for this company — actual years_elapsed equals the naive "
        f"(N periods − 1) count to within floating-point noise, so V1 and V2 agree here by coincidence, "
        f"not because (periods − 1) is a generally correct convention."
    )
    sh.set(r, 2, value=("IRREGULAR" if irregular else "REGULAR"), style=diag_style)
    sh.set(r, 3, value=diag_text, style=s["note"])
    irregular_check_row = r
    rc.skip(1)

    r = rc.row()
    sh.set(r, 0, value="Raw Revenue CAGR = (Rev_latest/Rev_earliest)^(1/years) − 1", style=s["label"])
    f = f"({hist_last_rev}/{hist_first_rev})^(1/B{xrow(years_row)})-1"
    sh.set(r, 1, formula=f, cached=result["raw_cagr"], style=s["formula_pct"])
    raw_cagr_row = r

    r = rc.row()
    sh.set(r, 0, value="Capped Revenue CAGR (cap A-002; NOT floored below)", style=s["label"])
    sh.set(r, 1, formula=f"MIN(B{xrow(raw_cagr_row)},{IREF('max_hist_growth')})", cached=result["capped_cagr"], style=s["formula_pct"])
    capped_cagr_row = r
    sh.set(r, 3, value="Spec: dcf.md — capped at MAX_REVENUE_GROWTH_RATE=25%, unbounded below (A-002)", style=s["spec_ref"])

    r = rc.row()
    ratios = "+".join(f"{IN}$C${xrow(hist_start+i)}/{IN}$B${xrow(hist_start+i)}" for i in range(n_hist))
    sh.set(r, 0, value="Average Historical Operating Margin = simple average of EBIT_t/Revenue_t", style=s["label"])
    sh.set(r, 1, formula=f"({ratios})/{n_hist}", cached=result["avg_operating_margin"], style=s["formula_pct"])
    margin_row = r
    rc.skip(1)

    # ---- Section: WACC ----
    rc.section("SECTION 2 — WACC via CAPM  [wacc-capm.md §Formula]", s["section"])
    r = rc.row(); sh.set(r, 0, value="Market Capitalization = Price × Shares Outstanding", style=s["label"])
    sh.set(r, 1, formula=f"{IREF('price')}*{IREF('shares')}", cached=result["wacc"]["market_cap"], style=s["formula_usd"])
    mktcap_row = r

    r = rc.row(); sh.set(r, 0, value="Effective Tax Rate = Tax Provision / Pretax Income (latest FY)", style=s["label"])
    sh.set(r, 1, formula=f"{IREF('tax_prov')}/{IREF('pretax')}", cached=result["tax_rate"], style=s["formula_pct"])
    tax_row = r
    sh.set(r, 3, value=f"Method: {result['tax_rate_method']}. Spec: wacc-capm.md — derived as tax_provision/pretax_income, valid range [0,1); DEFAULT_TAX_RATE=21% only on missing data.", style=s["spec_ref"])

    r = rc.row(); sh.set(r, 0, value="Cost of Debt = |Interest Expense| / Total Debt (latest FY)", style=s["label"])
    sh.set(r, 1, formula=f"ABS({IREF('interest')})/{IREF('debt')}", cached=result["cost_of_debt"], style=s["formula_pct"])
    cod_row = r

    r = rc.row(); sh.set(r, 0, value="Cost of Equity (CAPM) = Rf + Beta × MRP", style=s["label"])
    sh.set(r, 1, formula=f"{IREF('rf')}+{IREF('beta')}*{IREF('mrp')}", cached=result["wacc"]["cost_of_equity"], style=s["formula_pct"])
    coe_row = r

    r = rc.row(); sh.set(r, 0, value="After-Tax Cost of Debt = Cost of Debt × (1 − Tax Rate)", style=s["label"])
    sh.set(r, 1, formula=f"B{xrow(cod_row)}*(1-B{xrow(tax_row)})", cached=result["wacc"]["after_tax_cost_of_debt"], style=s["formula_pct"])
    atcod_row = r

    r = rc.row(); sh.set(r, 0, value="Weight of Equity = MktCap / (MktCap + Total Debt)", style=s["label"])
    sh.set(r, 1, formula=f"B{xrow(mktcap_row)}/(B{xrow(mktcap_row)}+{IREF('debt')})", cached=result["wacc"]["we"], style=s["formula_pct"])
    we_row = r

    r = rc.row(); sh.set(r, 0, value="Weight of Debt = Total Debt / (MktCap + Total Debt)", style=s["label"])
    sh.set(r, 1, formula=f"{IREF('debt')}/(B{xrow(mktcap_row)}+{IREF('debt')})", cached=result["wacc"]["wd"], style=s["formula_pct"])
    wd_row = r

    r = rc.row(); sh.set(r, 0, value="Raw WACC = We×Re + Wd×Rd(after-tax)", style=s["label"])
    sh.set(r, 1, formula=f"B{xrow(we_row)}*B{xrow(coe_row)}+B{xrow(wd_row)}*B{xrow(atcod_row)}", cached=result["wacc"]["raw_wacc"], style=s["formula_pct"])
    raw_wacc_row = r

    r = rc.row(); sh.set(r, 0, value="Final WACC — clamped to [5%,20%] (A-006), via MEDIAN(raw,min,max)", style=s["label"])
    sh.set(r, 1, formula=f"MEDIAN(B{xrow(raw_wacc_row)},{IREF('wacc_min')},{IREF('wacc_max')})", cached=result["wacc"]["final_wacc"], style=s["formula_pct"])
    wacc_row = r
    sh.set(r, 3, value="MEDIAN(x,min,max) is a standard single-formula clamp: returns min if x<min, max if x>max, else x.", style=s["spec_ref"])
    rc.skip(1)

    # ---- Section: FCF projection ----
    rc.section("SECTION 3 — 5-Year Free Cash Flow Projection  [dcf.md §Free Cash Flow projection]", s["section"])
    hdr = rc.row()
    sh.set(hdr, 0, value="", style=s["header"])
    sh.set(hdr, 1, value="Year 0 (base, actual)", style=s["header_center"])
    for i in range(5):
        sh.set(hdr, 2 + i, value=f"Year {i+1}", style=s["header_center"])

    rev_row = rc.row()
    sh.set(rev_row, 0, value="Revenue = Revenue_(t-1) × (1 + growth)", style=s["label"])
    sh.set(rev_row, 1, formula=f"{hist_last_rev}", cached=result["base_revenue"], style=s["formula_usd"])
    prev_col = 1
    for i in range(5):
        col = 2 + i
        f = f"{col_letter(prev_col)}{xrow(rev_row)}*(1+$B${xrow(capped_cagr_row)})"
        sh.set(rev_row, col, formula=f, cached=result["fcf_rows"][i]["revenue"], style=s["formula_usd"])
        prev_col = col

    ebit_row = rc.row()
    sh.set(ebit_row, 0, value="EBIT = Revenue × Operating Margin", style=s["label"])
    for i in range(5):
        col = 2 + i
        f = f"{col_letter(col)}{xrow(rev_row)}*$B${xrow(margin_row)}"
        sh.set(ebit_row, col, formula=f, cached=result["fcf_rows"][i]["ebit"], style=s["formula_usd"])

    nopat_row = rc.row()
    sh.set(nopat_row, 0, value="NOPAT = EBIT × (1 − Tax Rate)", style=s["label"])
    for i in range(5):
        col = 2 + i
        f = f"{col_letter(col)}{xrow(ebit_row)}*(1-$B${xrow(tax_row)})"
        sh.set(nopat_row, col, formula=f, cached=result["fcf_rows"][i]["nopat"], style=s["formula_usd"])

    da_row = rc.row()
    sh.set(da_row, 0, value="D&A = Revenue × D&A% (A-004)", style=s["label"])
    for i in range(5):
        col = 2 + i
        f = f"{col_letter(col)}{xrow(rev_row)}*{IREF('da_pct')}"
        sh.set(da_row, col, formula=f, cached=result["fcf_rows"][i]["da"], style=s["formula_usd"])

    capex_row = rc.row()
    sh.set(capex_row, 0, value="CapEx = Revenue × CapEx% (A-004)", style=s["label"])
    for i in range(5):
        col = 2 + i
        f = f"{col_letter(col)}{xrow(rev_row)}*{IREF('capex_pct')}"
        sh.set(capex_row, col, formula=f, cached=result["fcf_rows"][i]["capex"], style=s["formula_usd"])

    nwc_row = rc.row()
    sh.set(nwc_row, 0, value="ΔNWC = NWC% × (Revenue_t − Revenue_(t-1)) (A-004)", style=s["label"])
    prev_col = 1
    for i in range(5):
        col = 2 + i
        f = f"{IREF('nwc_pct')}*({col_letter(col)}{xrow(rev_row)}-{col_letter(prev_col)}{xrow(rev_row)})"
        sh.set(nwc_row, col, formula=f, cached=result["fcf_rows"][i]["d_nwc"], style=s["formula_usd"])
        prev_col = col

    fcf_row = rc.row()
    sh.set(fcf_row, 0, value="Unlevered FCF = NOPAT + D&A − CapEx − ΔNWC", style=s["label"])
    for i in range(5):
        col = 2 + i
        cl = col_letter(col)
        f = f"{cl}{xrow(nopat_row)}+{cl}{xrow(da_row)}-{cl}{xrow(capex_row)}-{cl}{xrow(nwc_row)}"
        sh.set(fcf_row, col, formula=f, cached=result["fcf_rows"][i]["fcf"], style=s["formula_usd"])
    rc.skip(1)

    # ---- Section: terminal value ----
    rc.section("SECTION 4 — Terminal Value (Gordon Growth)  [dcf.md §Terminal value]", s["section"])
    r = rc.row(); sh.set(r, 0, value="Terminal Value = FCF_Y5 × (1+g) / (WACC − g)", style=s["label"])
    g5 = col_letter(6)  # Year 5 column = index 6 (0=A label,1=B,...,6=G)
    f = f"{g5}{xrow(fcf_row)}*(1+{IREF('term_g')})/(B{xrow(wacc_row)}-{IREF('term_g')})"
    sh.set(r, 1, formula=f, cached=result["terminal_value"], style=s["formula_usd"])
    tv_row = r

    r = rc.row(); sh.set(r, 0, value="Check: Final WACC > Terminal Growth Rate?", style=s["label"])
    check_f = f'IF(B{xrow(wacc_row)}>{IREF("term_g")},"PASS","FAIL")'
    sh.set(r, 1, formula=check_f, cached="PASS" if result["wacc"]["final_wacc"] > 0.025 else "FAIL", style=s["formula_txt"])
    wacc_gt_g_row = r
    rc.skip(1)

    # ---- Section: discounting & bridge ----
    rc.section("SECTION 5 — Discounting & Enterprise-to-Equity Bridge  [dcf.md §Discounting and the Enterprise-to-Equity bridge]", s["section"])
    hdr2 = rc.row()
    sh.set(hdr2, 0, value="PV(FCF_t) = FCF_t / (1+WACC)^t", style=s["label"])
    for i in range(5):
        col = 2 + i
        cl = col_letter(col)
        f = f"{cl}{xrow(fcf_row)}/(1+$B${xrow(wacc_row)})^{i+1}"
        sh.set(hdr2, col, formula=f, cached=result["bridge"]["pv_fcf"][i], style=s["formula_usd"])
    pv_fcf_row = hdr2

    r = rc.row(); sh.set(r, 0, value="Sum of PV(FCF Years 1-5)", style=s["label"])
    sh.set(r, 1, formula=f"SUM(C{xrow(pv_fcf_row)}:G{xrow(pv_fcf_row)})", cached=sum(result["bridge"]["pv_fcf"]), style=s["formula_usd"])
    sum_pv_fcf_row = r

    r = rc.row(); sh.set(r, 0, value="PV(Terminal Value) = TV / (1+WACC)^5", style=s["label"])
    sh.set(r, 1, formula=f"B{xrow(tv_row)}/(1+B{xrow(wacc_row)})^5", cached=result["bridge"]["pv_tv"], style=s["formula_usd"])
    pv_tv_row = r

    r = rc.row(); sh.set(r, 0, value="Enterprise Value = Sum PV(FCF) + PV(Terminal Value)", style=s["label"])
    sh.set(r, 1, formula=f"B{xrow(sum_pv_fcf_row)}+B{xrow(pv_tv_row)}", cached=result["bridge"]["enterprise_value"], style=s["formula_usd"])
    ev_row = r

    r = rc.row(); sh.set(r, 0, value="Equity Value = Enterprise Value − Total Debt + Cash", style=s["label"])
    sh.set(r, 1, formula=f"B{xrow(ev_row)}-{IREF('debt')}+{IREF('cash')}", cached=result["bridge"]["equity_value"], style=s["formula_usd"])
    eqv_row = r

    r = rc.row(); sh.set(r, 0, value="Intrinsic Value per Share = Equity Value / Shares Outstanding", style=s["label"])
    sh.set(r, 1, formula=f"B{xrow(eqv_row)}/{IREF('shares')}", cached=result["bridge"]["intrinsic_value_per_share"], style=s["formula_price"])
    ivps_row = r

    r = rc.row(); sh.set(r, 0, value="Current Price / Intrinsic Value (descriptive ratio, NOT a recommendation)", style=s["label"])
    sh.set(r, 1, formula=f"{IREF('price')}/B{xrow(ivps_row)}", cached=result["bridge"]["price_to_intrinsic_value"], style=s["formula_ratio"])
    piv_row = r
    rc.skip(1)

    # ---- Section: validation checks ----
    rc.section("SECTION 6 — Validation Checks (local to this company)", s["section"])
    def check_row(label, formula, cached_pass, note=""):
        r = rc.row()
        sh.set(r, 0, value=label, style=s["label"])
        style = s["pass"] if cached_pass else s["fail"]
        sh.set(r, 1, formula=formula, cached="PASS" if cached_pass else "FAIL", style=style)
        if note:
            sh.set(r, 3, value=note, style=s["note"])
        return r

    check_row("WACC within documented clamp [5%,20%]?",
               f"IF(AND(B{xrow(wacc_row)}>={IREF('wacc_min')},B{xrow(wacc_row)}<={IREF('wacc_max')}),\"PASS\",\"FAIL\")",
               True)
    check_row("Enterprise Value is a finite number?",
               f"IF(ISNUMBER(B{xrow(ev_row)}),\"PASS\",\"FAIL\")", True)
    check_row("Intrinsic Value per Share is a finite number?",
               f"IF(ISNUMBER(B{xrow(ivps_row)}),\"PASS\",\"FAIL\")", True)
    ivps_positive = result["bridge"]["intrinsic_value_per_share"] > 0
    check_row("Intrinsic Value per Share is positive?",
               f"IF(B{xrow(ivps_row)}>0,\"PASS\",\"FAIL\")", ivps_positive,
               "" if ivps_positive else "Negative base-case IV is a legitimate model output here (structurally negative unlevered FCF under fixed A-004 percentages + near-100% latest-year effective tax rate) — see Research Outlook. Not a workbook defect.")
    tax_in_range = 0 <= result["tax_rate"] < 1
    check_row("Effective tax rate within valid [0,1) range?",
               f"IF(AND(B{xrow(tax_row)}>=0,B{xrow(tax_row)}<1),\"PASS\",\"FAIL\")", tax_in_range)
    check_row("Final WACC exceeds terminal growth rate?",
               f"IF(B{xrow(wacc_row)}>{IREF('term_g')},\"PASS\",\"FAIL\")",
               result["wacc"]["final_wacc"] > 0.025)

    sh.set_widths(0, [46, 17, 17, 17, 17, 17, 17, 55])
    sh.freeze_panes(4, 1)

    marks = dict(years_row=years_row, raw_cagr_row=raw_cagr_row, capped_cagr_row=capped_cagr_row,
                 margin_row=margin_row, mktcap_row=mktcap_row, tax_row=tax_row, cod_row=cod_row,
                 coe_row=coe_row, atcod_row=atcod_row, we_row=we_row, wd_row=wd_row,
                 raw_wacc_row=raw_wacc_row, wacc_row=wacc_row, rev_row=rev_row, ebit_row=ebit_row,
                 nopat_row=nopat_row, da_row=da_row, capex_row=capex_row, nwc_row=nwc_row,
                 fcf_row=fcf_row, tv_row=tv_row, pv_fcf_row=pv_fcf_row, sum_pv_fcf_row=sum_pv_fcf_row,
                 pv_tv_row=pv_tv_row, ev_row=ev_row, eqv_row=eqv_row, ivps_row=ivps_row, piv_row=piv_row)
    return marks


# ---------------------------------------------------------------------------
# Sensitivity_<TICKER> sheet
# ---------------------------------------------------------------------------

WACC_GRID = [0.05, 0.075, 0.10, 0.125, 0.15, 0.175, 0.20]
TERM_G_GRID = [0.00, 0.01, 0.02, 0.025, 0.03, 0.04, 0.05]
REV_G_GRID = [-0.10, -0.05, 0.00, 0.05, 0.10, 0.20, 0.30, 0.40]
MARGIN_GRID = [0.00, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60]
TWO_WAY_WACC = [0.05, 0.08, 0.11, 0.14, 0.17, 0.20]
TWO_WAY_G = [0.00, 0.01, 0.02, 0.03, 0.04, 0.05]


def build_sensitivity_sheet(wb, s, tk, snap, result, imarks, dmarks):
    sh = wb.add_sheet(f"Sensitivity_{tk}")
    rc = RC(sh)
    IN = f"'Inputs_{tk}'!"
    DC = f"'DCF_{tk}'!"

    def IREF(key):
        return f"{IN}$B${xrow(imarks[key])}"

    def DREF(key, col=1):
        return f"{DC}${col_letter(col)}${xrow(dmarks[key])}"

    base_fcf_range = f"{DC}$C${xrow(dmarks['fcf_row'])}:$G${xrow(dmarks['fcf_row'])}"
    base_fcf5 = f"{DC}$G${xrow(dmarks['fcf_row'])}"  # Year-5 FCF -- Gordon Growth numerator (dcf.md: TV = FCF_Y5*(1+g)/(WACC-g))
    wacc_ref = DREF("wacc_row")
    sum_pv_fcf_ref = DREF("sum_pv_fcf_row")
    debt_ref = IREF("debt")
    cash_ref = IREF("cash")
    shares_ref = IREF("shares")

    rc.title(f"Sensitivity Analysis — {tk}", s["title"])
    sh.set(rc.row(), 0, value="One variable at a time, other inputs held at base case. Per docs/independent-validation-plan.md §Sensitivity tables.", style=s["subtitle"])
    rc.skip(1)

    # -------- Table 1: WACC sensitivity --------
    rc.section("TABLE 1 — WACC Sensitivity (range [5%,20%], base FCF path held fixed)  [dcf.md Terminal Value + discounting]", s["section"], span=4)
    hdr = rc.row()
    for c, t in enumerate(["WACC", "Terminal Value", "Enterprise Value", "Equity Value", "Intrinsic Value/Share"]):
        sh.set(hdr, c, value=t, style=s["header"])
    t1_start = rc.r
    s_wacc = sc.sensitivity_wacc(result["fcf_rows"], WACC_GRID, snap["latest_year_facts"]["total_debt_usd"],
                                  snap["latest_year_facts"]["cash_and_equivalents_usd"], snap["market_data"]["shares_outstanding"])
    for i, w in enumerate(WACC_GRID):
        r = rc.row()
        sh.set(r, 0, value=w, style=s["input_pct"])
        a = f"$A${xrow(r)}"
        tv_f = f'IF({a}<={IREF("term_g")},"n/a (WACC<=g)",{base_fcf5}*(1+{IREF("term_g")})/({a}-{IREF("term_g")}))'
        row_result = s_wacc[i]
        tv_cached = row_result.get("note", None) or None
        sh.set(r, 1, formula=tv_f, cached=(row_result["ivps"] is not None and _tv_wacc(result, w)) or "n/a", style=s["formula_usd"])
        ev_f = (f'IF(B{xrow(r)}="n/a (WACC<=g)","n/a",'
                f'SUMPRODUCT({base_fcf_range},1/(1+{a})^{{1,2,3,4,5}})+B{xrow(r)}/(1+{a})^5)')
        ev_cached = _ev_from_ivps(row_result["ivps"], debtv=snap["latest_year_facts"]["total_debt_usd"], cashv=snap["latest_year_facts"]["cash_and_equivalents_usd"], sharesv=snap["market_data"]["shares_outstanding"]) if row_result["ivps"] is not None else "n/a"
        sh.set(r, 2, formula=ev_f, cached=ev_cached, style=s["formula_usd"])
        eqv_f = f'IF(C{xrow(r)}="n/a","n/a",C{xrow(r)}-{debt_ref}+{cash_ref})'
        eqv_cached = (ev_cached - snap["latest_year_facts"]["total_debt_usd"] + snap["latest_year_facts"]["cash_and_equivalents_usd"]) if ev_cached != "n/a" else "n/a"
        sh.set(r, 3, formula=eqv_f, cached=eqv_cached, style=s["formula_usd"])
        ivps_f = f'IF(D{xrow(r)}="n/a","n/a",D{xrow(r)}/{shares_ref})'
        sh.set(r, 4, formula=ivps_f, cached=row_result["ivps"] if row_result["ivps"] is not None else "n/a", style=s["formula_price"])
    t1_end = rc.r - 1
    r = rc.row()
    sh.set(r, 0, value="Direction check: IV decreases as WACC rises?", style=s["wrap"])
    sh.row_heights[r] = 45
    dc_formula = f"IF(E{xrow(t1_start)}>E{xrow(t1_end)},\"PASS (as expected)\",\"FAIL — investigate (see note)\")"
    ivs = [x["ivps"] for x in s_wacc if x["ivps"] is not None]
    passed = ivs[0] > ivs[-1] if len(ivs) >= 2 else None
    sh.set(r, 1, formula=dc_formula, cached=("PASS (as expected)" if passed else "FAIL — investigate (see note)"), style=(s["pass"] if passed else s["fail"]))
    if not passed:
        sh.set(r, 5, value="INVESTIGATED: base-case FCF is negative every year for this company, so higher WACC discounts a negative stream LESS, making IV rise (not fall) with WACC. This is mathematically correct DCF behavior for negative cash flows, not a workbook defect — see Research Outlook.", style=s["note"])
    rc.skip(2)

    # -------- Table 2: Terminal growth sensitivity --------
    rc.section("TABLE 2 — Terminal Growth Rate Sensitivity (range [0%,5%]); invalid where g>=WACC", s["section"], span=4)
    hdr = rc.row()
    for c, t in enumerate(["Terminal Growth g", "Terminal Value", "Enterprise Value", "Equity Value", "Intrinsic Value/Share"]):
        sh.set(hdr, c, value=t, style=s["header"])
    t2_start = rc.r
    s_g = sc.sensitivity_terminal_growth(result["fcf_rows"], TERM_G_GRID, result["wacc"]["final_wacc"],
                                          snap["latest_year_facts"]["total_debt_usd"], snap["latest_year_facts"]["cash_and_equivalents_usd"],
                                          snap["market_data"]["shares_outstanding"])
    for i, g in enumerate(TERM_G_GRID):
        r = rc.row()
        sh.set(r, 0, value=g, style=s["input_pct"])
        a = f"$A${xrow(r)}"
        tv_f = f'IF({a}>={wacc_ref},"n/a (g>=WACC)",{base_fcf5}*(1+{a})/({wacc_ref}-{a}))'
        row_result = s_g[i]
        tv_cached = "n/a" if row_result["ivps"] is None else _tv_termg(result, g)
        sh.set(r, 1, formula=tv_f, cached=tv_cached, style=s["formula_usd"])
        ev_f = f'IF(B{xrow(r)}="n/a (g>=WACC)","n/a",{sum_pv_fcf_ref}+B{xrow(r)}/(1+{wacc_ref})^5)'
        ev_cached = "n/a" if row_result["ivps"] is None else (sum(result["bridge"]["pv_fcf"]) + tv_cached / (1 + result["wacc"]["final_wacc"]) ** 5)
        sh.set(r, 2, formula=ev_f, cached=ev_cached, style=s["formula_usd"])
        eqv_f = f'IF(C{xrow(r)}="n/a","n/a",C{xrow(r)}-{debt_ref}+{cash_ref})'
        eqv_cached = "n/a" if ev_cached == "n/a" else (ev_cached - snap["latest_year_facts"]["total_debt_usd"] + snap["latest_year_facts"]["cash_and_equivalents_usd"])
        sh.set(r, 3, formula=eqv_f, cached=eqv_cached, style=s["formula_usd"])
        ivps_f = f'IF(D{xrow(r)}="n/a","n/a",D{xrow(r)}/{shares_ref})'
        sh.set(r, 4, formula=ivps_f, cached=row_result["ivps"] if row_result["ivps"] is not None else "n/a", style=s["formula_price"])
    t2_end = rc.r - 1
    r = rc.row()
    sh.set(r, 0, value="Direction check: IV increases as terminal growth rises (where valid)?", style=s["wrap"])
    sh.row_heights[r] = 45
    ivs2 = [x["ivps"] for x in s_g if x["ivps"] is not None]
    passed2 = ivs2[0] < ivs2[-1] if len(ivs2) >= 2 else None
    sh.set(r, 1, formula=f"IF(E{xrow(t2_start)}<E{xrow(t2_end-1) if s_g[-1]['ivps'] is None else xrow(t2_end)},\"PASS (as expected)\",\"FAIL — investigate (see note)\")",
           cached=("PASS (as expected)" if passed2 else "FAIL — investigate (see note)"), style=(s["pass"] if passed2 else s["fail"]))
    if not passed2:
        sh.set(r, 5, value="INVESTIGATED: base-case FCF is negative, so a higher terminal growth rate makes the (negative) terminal value MORE negative, not less — IV falls as g rises. Mathematically correct given negative FCF, not a defect. See Research Outlook.", style=s["note"])
    rc.skip(2)

    # -------- Table 3: Revenue growth sensitivity --------
    rc.section("TABLE 3 — Revenue Growth Sensitivity (range [-10%,40%]); base WACC & margin held fixed", s["section"], span=13)
    hdr = rc.row()
    heads = ["Growth g", "Rev Y1", "Rev Y2", "Rev Y3", "Rev Y4", "Rev Y5",
             "FCF Y1", "FCF Y2", "FCF Y3", "FCF Y4", "FCF Y5", "Terminal Value",
             "Enterprise Value", "Equity Value", "Intrinsic Value/Share"]
    for c, t in enumerate(heads):
        sh.set(hdr, c, value=t, style=s["header"])
    t3_start = rc.r
    base_rev0 = f"{DC}$B${xrow(dmarks['rev_row'])}"
    margin_ref = DREF("margin_row")
    tax_ref = DREF("tax_row")
    da_pct_ref = IREF("da_pct")
    capex_pct_ref = IREF("capex_pct")
    nwc_pct_ref = IREF("nwc_pct")
    term_g_ref = IREF("term_g")
    s_rg = sc.sensitivity_revenue_growth(result["base_revenue"], REV_G_GRID, result["avg_operating_margin"],
                                          result["tax_rate"], result["wacc"]["final_wacc"],
                                          snap["latest_year_facts"]["total_debt_usd"], snap["latest_year_facts"]["cash_and_equivalents_usd"],
                                          snap["market_data"]["shares_outstanding"])
    for i, g in enumerate(REV_G_GRID):
        r = rc.row()
        sh.set(r, 0, value=g, style=s["input_pct"])
        a = f"$A${xrow(r)}"
        # revenue columns B..F (cols 1..5)
        prev_ref = base_rev0
        rows = sc.project_fcf(result["base_revenue"], g, result["avg_operating_margin"], result["tax_rate"])
        for yi in range(5):
            col = 1 + yi
            f = f"{prev_ref}*(1+{a})" if yi == 0 else f"{col_letter(col-1)}{xrow(r)}*(1+{a})"
            sh.set(r, col, formula=f, cached=rows[yi]["revenue"], style=s["formula_usd"])
            prev_ref = f"{col_letter(col)}{xrow(r)}"
        # FCF columns G..K (cols 6..10): FCF_t = Rev_t*margin*(1-tax) + Rev_t*da% - Rev_t*capex% - nwc%*(Rev_t-Rev_(t-1))
        for yi in range(5):
            rev_col = col_letter(1 + yi)
            prev_rev_col = col_letter(yi) if yi > 0 else None
            prev_rev_expr = f"{prev_rev_col}{xrow(r)}" if yi > 0 else base_rev0
            fcf_col = 6 + yi
            f = (f"{rev_col}{xrow(r)}*{margin_ref}*(1-{tax_ref})"
                 f"+{rev_col}{xrow(r)}*{da_pct_ref}-{rev_col}{xrow(r)}*{capex_pct_ref}"
                 f"-{nwc_pct_ref}*({rev_col}{xrow(r)}-{prev_rev_expr})")
            sh.set(r, fcf_col, formula=f, cached=rows[yi]["fcf"], style=s["formula_usd"])
        # Terminal value (col L=11)
        fcf5_col = col_letter(10)
        tv_f = f"{fcf5_col}{xrow(r)}*(1+{term_g_ref})/({wacc_ref}-{term_g_ref})"
        tv_cached = rows[-1]["fcf"] * (1 + 0.025) / (result["wacc"]["final_wacc"] - 0.025)
        sh.set(r, 11, formula=tv_f, cached=tv_cached, style=s["formula_usd"])
        # Enterprise value (col M=12): SUMPRODUCT(FCF1:FCF5, discount array) + TV/(1+WACC)^5
        fcf_range = f"G{xrow(r)}:K{xrow(r)}"
        ev_f = f"SUMPRODUCT({fcf_range},1/(1+{wacc_ref})^{{1,2,3,4,5}})+L{xrow(r)}/(1+{wacc_ref})^5"
        ev_cached = sum(rw["fcf"] / (1 + result["wacc"]["final_wacc"]) ** (j + 1) for j, rw in enumerate(rows)) + tv_cached / (1 + result["wacc"]["final_wacc"]) ** 5
        sh.set(r, 12, formula=ev_f, cached=ev_cached, style=s["formula_usd"])
        # Equity value (col N=13)
        eqv_f = f"M{xrow(r)}-{debt_ref}+{cash_ref}"
        eqv_cached = ev_cached - snap["latest_year_facts"]["total_debt_usd"] + snap["latest_year_facts"]["cash_and_equivalents_usd"]
        sh.set(r, 13, formula=eqv_f, cached=eqv_cached, style=s["formula_usd"])
        # IVPS (col O=14)
        ivps_f = f"N{xrow(r)}/{shares_ref}"
        sh.set(r, 14, formula=ivps_f, cached=s_rg[i]["ivps"], style=s["formula_price"])
    t3_end = rc.r - 1
    r = rc.row()
    sh.set(r, 0, value="Direction check: IV increases as revenue growth rises?", style=s["wrap"])
    sh.row_heights[r] = 45
    ivs3 = [x["ivps"] for x in s_rg]
    passed3 = ivs3[0] < ivs3[-1]
    sh.set(r, 1, formula=f"IF(O{xrow(t3_start)}<O{xrow(t3_end)},\"PASS (as expected)\",\"FAIL — investigate (see note)\")",
           cached=("PASS (as expected)" if passed3 else "FAIL — investigate (see note)"), style=(s["pass"] if passed3 else s["fail"]))
    if not passed3:
        sh.set(r, 15, value="INVESTIGATED: this company's unit economics (margin*(1-tax)+D&A%-CapEx%) are structurally negative per dollar of revenue, so growing revenue faster only compounds losses — IV falls as growth rises. Mathematically correct, not a defect. See Research Outlook.", style=s["note"])
    rc.skip(2)

    # -------- Table 4: Operating margin sensitivity --------
    rc.section("TABLE 4 — Operating Margin Sensitivity (range [0%,60%]); base revenue path & WACC held fixed", s["section"], span=9)
    hdr = rc.row()
    heads4 = ["Margin", "FCF Y1", "FCF Y2", "FCF Y3", "FCF Y4", "FCF Y5", "Terminal Value", "Enterprise Value", "Equity Value", "Intrinsic Value/Share"]
    for c, t in enumerate(heads4):
        sh.set(hdr, c, value=t, style=s["header"])
    t4_start = rc.r
    s_m = sc.sensitivity_operating_margin(result["base_revenue"], MARGIN_GRID, result["capped_cagr"], result["tax_rate"],
                                           result["wacc"]["final_wacc"], snap["latest_year_facts"]["total_debt_usd"],
                                           snap["latest_year_facts"]["cash_and_equivalents_usd"], snap["market_data"]["shares_outstanding"])
    for i, m in enumerate(MARGIN_GRID):
        r = rc.row()
        sh.set(r, 0, value=m, style=s["input_pct"])
        a = f"$A${xrow(r)}"
        rows = sc.project_fcf(result["base_revenue"], result["capped_cagr"], m, result["tax_rate"])
        for yi in range(5):
            fcf_col = 1 + yi
            dcf_rev_col = col_letter(2 + yi)  # DCF sheet Year(yi+1) column
            dcf_da_col = col_letter(2 + yi)
            f = (f"{DC}${dcf_rev_col}${xrow(dmarks['rev_row'])}*{a}*(1-{tax_ref})"
                 f"+{DC}${dcf_da_col}${xrow(dmarks['da_row'])}"
                 f"-{DC}${dcf_da_col}${xrow(dmarks['capex_row'])}"
                 f"-{DC}${dcf_da_col}${xrow(dmarks['nwc_row'])}")
            sh.set(r, fcf_col, formula=f, cached=rows[yi]["fcf"], style=s["formula_usd"])
        fcf5_col = col_letter(5)
        tv_f = f"{fcf5_col}{xrow(r)}*(1+{term_g_ref})/({wacc_ref}-{term_g_ref})"
        tv_cached = rows[-1]["fcf"] * 1.025 / (result["wacc"]["final_wacc"] - 0.025)
        sh.set(r, 6, formula=tv_f, cached=tv_cached, style=s["formula_usd"])
        fcf_range = f"B{xrow(r)}:F{xrow(r)}"
        ev_f = f"SUMPRODUCT({fcf_range},1/(1+{wacc_ref})^{{1,2,3,4,5}})+G{xrow(r)}/(1+{wacc_ref})^5"
        ev_cached = sum(rw["fcf"] / (1 + result["wacc"]["final_wacc"]) ** (j + 1) for j, rw in enumerate(rows)) + tv_cached / (1 + result["wacc"]["final_wacc"]) ** 5
        sh.set(r, 7, formula=ev_f, cached=ev_cached, style=s["formula_usd"])
        eqv_f = f"G{xrow(r)}".replace("G", "H")  # placeholder not used
        eqv_f = f"H{xrow(r)}-{debt_ref}+{cash_ref}"
        eqv_cached = ev_cached - snap["latest_year_facts"]["total_debt_usd"] + snap["latest_year_facts"]["cash_and_equivalents_usd"]
        sh.set(r, 8, formula=eqv_f, cached=eqv_cached, style=s["formula_usd"])
        ivps_f = f"I{xrow(r)}/{shares_ref}"
        sh.set(r, 9, formula=ivps_f, cached=s_m[i]["ivps"], style=s["formula_price"])
    t4_end = rc.r - 1
    r = rc.row()
    sh.set(r, 0, value="Direction check: IV increases as operating margin rises?", style=s["wrap"])
    sh.row_heights[r] = 45
    ivs4 = [x["ivps"] for x in s_m]
    passed4 = ivs4[0] < ivs4[-1]
    sh.set(r, 1, formula=f"IF(J{xrow(t4_start)}<J{xrow(t4_end)},\"PASS (as expected)\",\"FAIL — investigate\")",
           cached=("PASS (as expected)" if passed4 else "FAIL — investigate"), style=(s["pass"] if passed4 else s["fail"]))
    rc.skip(2)

    # -------- Table 5: two-way WACC x Terminal growth --------
    rc.section("TABLE 5 — Two-Way Sensitivity: WACC (rows) × Terminal Growth (columns) — Intrinsic Value/Share", s["section"], span=len(TWO_WAY_G))
    hdr = rc.row()
    sh.set(hdr, 0, value="WACC \\ g", style=s["header"])
    for c, g in enumerate(TWO_WAY_G):
        sh.set(hdr, 1 + c, value=g, style=s["header_center"])
    for ri, w in enumerate(TWO_WAY_WACC):
        r = rc.row()
        sh.set(r, 0, value=w, style=s["input_pct"])
        wcell = f"$A{xrow(r)}"
        for ci, g in enumerate(TWO_WAY_G):
            gcell = f"{col_letter(1+ci)}${xrow(hdr)}"
            f = (f'IF({gcell}>={wcell},"n/a",'
                 f'(SUMPRODUCT({base_fcf_range},1/(1+{wcell})^{{1,2,3,4,5}})'
                 f'+({base_fcf5}*(1+{gcell})/({wcell}-{gcell}))/(1+{wcell})^5'
                 f'-{debt_ref}+{cash_ref})/{shares_ref})')
            if g >= w:
                cached = "n/a"
            else:
                tv = result["fcf_rows"][-1]["fcf"] * (1 + g) / (w - g)
                pvfcf = sum(rw["fcf"] / (1 + w) ** (j + 1) for j, rw in enumerate(result["fcf_rows"]))
                ev = pvfcf + tv / (1 + w) ** 5
                eqv = ev - snap["latest_year_facts"]["total_debt_usd"] + snap["latest_year_facts"]["cash_and_equivalents_usd"]
                cached = eqv / snap["market_data"]["shares_outstanding"]
            sh.set(r, 1 + ci, formula=f, cached=cached, style=s["formula_price"])
    rc.skip(1)

    sh.set_widths(0, [14] + [14] * 14)
    sh.freeze_panes(2, 1)

    return dict(t1_start=t1_start, t1_end=t1_end, t2_start=t2_start, t2_end=t2_end,
                t3_start=t3_start, t3_end=t3_end, t4_start=t4_start, t4_end=t4_end)


def _tv_wacc(result, w):
    return result["fcf_rows"][-1]["fcf"] * 1.025 / (w - 0.025)


def _tv_termg(result, g):
    return result["fcf_rows"][-1]["fcf"] * (1 + g) / (result["wacc"]["final_wacc"] - g)


def _ev_from_ivps(ivps, debtv, cashv, sharesv):
    eqv = ivps * sharesv
    return eqv - cashv + debtv


# ---------------------------------------------------------------------------
# README sheet
# ---------------------------------------------------------------------------

def build_readme_sheet(wb, s, snaps):
    sh = wb.add_sheet("README")
    rc = RC(sh)
    rc.title("Independent DCF Validation Workbook", s["title"], span=6)
    sh.set(rc.row(), 0, value="Track A Phase 2A — Independent spreadsheet validator, built in a fresh session with no exposure to this repository's DCF/WACC implementation.", style=s["subtitle"])
    rc.skip(1)

    def para(text, span=6):
        r = rc.row()
        sh.set(r, 0, value=text, style=s["wrap"])
        sh.merge(r, 0, r, span)
        sh.row_heights[r] = max(15, 15 * (len(text) // 110 + 1))

    rc.section("Purpose", s["section"])
    para("This workbook independently reconstructs the Discounted Cash Flow (DCF) and WACC/CAPM calculations "
         "described in docs/model-specifications/dcf.md and docs/model-specifications/wacc-capm.md, entered as "
         "real, cell-by-cell Excel formulas from the written specification's prose and equations — never by "
         "reading, importing, or copying formulas from src/dcf_model/dcf.py or any other implementation file in "
         "this repository. Its purpose is to provide a second, mechanically independent calculation path that "
         "could catch a systematic formula bug the codebase's own test suite could never catch (a test written to "
         "match an incorrect implementation would still pass). See docs/independent-validation-plan.md and L-012 "
         "in docs/limitations-register.md.")
    rc.skip(1)

    rc.section("What this workbook does NOT do", s["section"])
    para("It does not import, execute, or call any code from this repository. It does not compare its own output "
         "against a live run of the codebase — the 'codebase output' columns on the Summary & Reconciliation sheet "
         "are deliberately left blank, to be filled in by a separate future session per the validation plan's "
         "explicit two-session design. It makes no investment recommendation of any kind.")
    rc.skip(1)

    rc.section("Sheet inventory", s["section"])
    hdr = rc.row()
    for c, t in enumerate(["Sheet", "Contents"]):
        sh.set(hdr, c, value=t, style=s["header"])
    inventory = [
        ("README", "This sheet."),
        ("Sources", "Every data source, hostname, and retrieval detail used to build this workbook."),
        ("Assumptions", "Every shared modeling assumption, with its source and rationale."),
        ("Summary & Reconciliation", "Cross-company summary of every key output, plus the (currently blank) reconciliation-vs-codebase table."),
        ("Inputs_<TICKER> (x4)", "Frozen raw facts and assumptions for one company — all hardcoded input cells (blue font)."),
        ("DCF_<TICKER> (x4)", "Every DCF/WACC formula for one company (black font, gray fill = computed, never hardcoded), with spec-section labels and local Validation Checks."),
        ("Sensitivity_<TICKER> (x4)", "One-variable and two-way sensitivity tables for one company, with direction checks."),
        ("Research Outlook", "Narrative interpretation of the independent findings, fragility discussion, and the profitability-is-not-established disclaimer."),
        ("Validation Checks", "Cross-company rollup of every PASS/FAIL/PENDING check, plus the sign-off checklist from independent-validation-plan.md."),
    ]
    for name, desc in inventory:
        r = rc.row()
        sh.set(r, 0, value=name, style=s["label_bold"])
        sh.set(r, 1, value=desc, style=s["wrap"])
    rc.skip(1)

    rc.section("Legend", s["section"])
    r = rc.row(); sh.set(r, 0, value="Blue text", style=s["input_txt"]); sh.set(r, 1, value="Hardcoded input — a frozen fact or a stated assumption. Never a formula.", style=s["wrap"])
    r = rc.row(); sh.set(r, 0, value="Black text, gray fill", style=s["formula_txt"]); sh.set(r, 1, value="A live formula, computed from other cells. Never a pasted/hardcoded result.", style=s["wrap"])
    r = rc.row(); sh.set(r, 0, value="PASS", style=s["pass"]); sh.set(r, 1, value="Check succeeded.", style=s["wrap"])
    r = rc.row(); sh.set(r, 0, value="FAIL", style=s["fail"]); sh.set(r, 1, value="Check failed — investigated and documented (never silently forced to PASS).", style=s["wrap"])
    r = rc.row(); sh.set(r, 0, value="PENDING", style=s["pending"]); sh.set(r, 1, value="Cannot be evaluated yet (e.g. awaiting the codebase-output comparison from a separate future session).", style=s["wrap"])
    rc.skip(1)

    rc.section("Companies validated", s["section"])
    hdr = rc.row()
    for c, t in enumerate(["Ticker", "Entity", "Profile (confirmed from frozen data)", "Revenue CAGR", "Avg Op Margin"]):
        sh.set(hdr, c, value=t, style=s["header_wrap"] if c == 2 else s["header"])
    sh.row_heights[hdr] = 30
    for tk in TICKERS:
        snap = snaps[tk]
        hist = snap["historical_annual_data"]
        hist_sorted = sorted(hist, key=lambda h: h["fiscal_period_end"])
        rev0, revN = hist_sorted[0]["revenue_raw_usd"], hist_sorted[-1]["revenue_raw_usd"]
        yrs = sc.years_elapsed_actual(hist)  # V2: actual elapsed calendar days / 365.25, never (periods-1)
        cagr = (revN / rev0) ** (1 / yrs) - 1
        margins = [h["ebit_raw_usd"] / h["revenue_raw_usd"] for h in hist]
        avgm = sum(margins) / len(margins)
        r = rc.row()
        sh.set(r, 0, value=tk, style=s["label_bold"])
        sh.set(r, 1, value=snap["entity_name"], style=s["label"])
        sh.set(r, 2, value=PROFILE_LABEL[tk], style=s["wrap"])
        sh.set(r, 3, value=f"{cagr:.2%}", style=s["label"])
        sh.set(r, 4, value=f"{avgm:.2%}", style=s["label"])
        sh.row_heights[r] = 30
    rc.skip(1)

    rc.section("Reproducibility", s["section"])
    para("Generated by build_workbook_v2.py (this directory), which loads snapshots/*.json and writes every formula "
         "programmatically via xlsx_lite.py (a small stdlib-only OOXML writer — openpyxl, xlsxwriter, and "
         "LibreOffice were not available in this build environment, and installing dependencies is out of scope "
         "for this task). Re-run: python3 build_workbook_v2.py")

    sh.set_widths(0, [26, 70, 32, 16, 16])
    return sh


# ---------------------------------------------------------------------------
# Sources sheet
# ---------------------------------------------------------------------------

def build_sources_sheet(wb, s, snaps):
    sh = wb.add_sheet("Sources")
    rc = RC(sh)
    rc.title("Data Sources", s["title"], span=6)
    sh.set(rc.row(), 0, value="Full per-field provenance is in source_manifest.csv alongside this workbook. This sheet summarizes.", style=s["subtitle"])
    rc.skip(1)

    rc.section("Hostnames contacted (read-only, unauthenticated)", s["section"])
    hdr = rc.row()
    for c, t in enumerate(["Hostname", "Purpose", "Endpoint pattern used"]):
        sh.set(hdr, c, value=t, style=s["header"])
    rows = [
        ("www.sec.gov", "CIK ticker lookup", "https://www.sec.gov/files/company_tickers.json"),
        ("data.sec.gov", "Primary financial-statement facts (XBRL)", "https://data.sec.gov/api/xbrl/companyfacts/CIK{10-digit}.json"),
        ("query1/query2.finance.yahoo.com (via yfinance)", "Current price, shares outstanding, beta, sector", "yfinance Ticker.info / Ticker.fast_info"),
    ]
    for h, p, e in rows:
        r = rc.row()
        sh.set(r, 0, value=h, style=s["label"])
        sh.set(r, 1, value=p, style=s["label"])
        sh.set(r, 2, value=e, style=s["note"])
    rc.skip(1)

    for tk in TICKERS:
        snap = snaps[tk]
        rc.section(f"{tk} — {snap['entity_name']} (CIK {snap['cik']})", s["section"])
        hdr = rc.row()
        for c, t in enumerate(["Field", "Fiscal Period / Timestamp", "XBRL Tag / Source", "Filing Accession",
                                "Filed / Retrieved Date", "Provenance", "Source URL"]):
            sh.set(hdr, c, value=t, style=s["header"])
        for h in snap["historical_annual_data"]:
            r = rc.row()
            sh.set(r, 0, value="Revenue", style=s["label"])
            sh.set(r, 1, value=h["fiscal_period_end"], style=s["label"])
            sh.set(r, 2, value=h["revenue_xbrl_tag"], style=s["note"])
            sh.set(r, 3, value=h["revenue_accession"], style=s["note"])
            sh.set(r, 4, value=h.get("revenue_filed_date") or "", style=s["label"])
            sh.set(r, 5, value="reported", style=s["label"])
            sh.set(r, 6, value=h.get("revenue_source_url") or "", style=s["note"])
            r = rc.row()
            sh.set(r, 0, value="EBIT/Operating Income", style=s["label"])
            sh.set(r, 1, value=h["fiscal_period_end"], style=s["label"])
            sh.set(r, 2, value=h["ebit_xbrl_tag"], style=s["note"])
            sh.set(r, 3, value=h.get("ebit_accession") or "", style=s["note"])
            sh.set(r, 4, value=h.get("ebit_filed_date") or "", style=s["label"])
            sh.set(r, 5, value="derived" if h["ebit_xbrl_tag"].startswith("DERIVED") else "reported", style=s["label"])
            sh.set(r, 6, value=h.get("ebit_source_url") or "", style=s["note"])
        lyf = snap["latest_year_facts"]
        for label, key_tag, key_accn, key_filed, key_url in [
            ("Pretax Income (latest)", "pretax_income_xbrl_tag", "pretax_income_accession", "pretax_income_filed_date", "pretax_income_source_url"),
            ("Tax Provision (latest)", "tax_provision_xbrl_tag", "tax_provision_accession", "tax_provision_filed_date", "tax_provision_source_url"),
            ("Interest Expense (latest)", "interest_expense_xbrl_tag", "interest_expense_accession", "interest_expense_filed_date", "interest_expense_source_url"),
            ("Cash & Equivalents (latest)", "cash_xbrl_tag", "cash_accession", "cash_filed_date", "cash_source_url"),
        ]:
            r = rc.row()
            sh.set(r, 0, value=label, style=s["label"])
            sh.set(r, 1, value=snap["latest_complete_fiscal_year_end"], style=s["label"])
            sh.set(r, 2, value=lyf.get(key_tag) or "", style=s["note"])
            sh.set(r, 3, value=lyf.get(key_accn) or "", style=s["note"])
            sh.set(r, 4, value=lyf.get(key_filed) or "", style=s["label"])
            sh.set(r, 5, value="reported", style=s["label"])
            sh.set(r, 6, value=lyf.get(key_url) or "", style=s["note"])

        # Total Debt: reported directly (VZ) vs. derived by summing components (MSFT/CAT/INTC) --
        # full provenance shown for every underlying component, per Phase 2A defect-fix pass.
        if lyf.get("total_debt_components"):
            for comp_tag, comp in lyf["total_debt_components"].items():
                r = rc.row()
                sh.set(r, 0, value=f"Total Debt component: {comp_tag}", style=s["label"])
                sh.set(r, 1, value=comp["fiscal_period_end"], style=s["label"])
                sh.set(r, 2, value=comp["xbrl_tag"], style=s["note"])
                sh.set(r, 3, value=comp["accession"], style=s["note"])
                sh.set(r, 4, value=comp["filed_date"], style=s["label"])
                sh.set(r, 5, value="reported (component)", style=s["label"])
                sh.set(r, 6, value=comp["source_url"], style=s["note"])
            r = rc.row()
            sh.set(r, 0, value="Total Debt (latest, derived)", style=s["label_bold"])
            sh.set(r, 1, value=snap["latest_complete_fiscal_year_end"], style=s["label"])
            sh.set(r, 2, value=lyf.get("total_debt_normalization_equation") or "", style=s["note"])
            sh.set(r, 3, value=lyf.get("total_debt_rollup_accession_joined") or "", style=s["note"])
            sh.set(r, 4, value=lyf.get("total_debt_rollup_filed_date_joined") or "", style=s["label"])
            sh.set(r, 5, value="derived (sum of components above)", style=s["label"])
            sh.set(r, 6, value=lyf.get("total_debt_rollup_source_url_joined") or "", style=s["note"])
        else:
            r = rc.row()
            sh.set(r, 0, value="Total Debt (latest, reported)", style=s["label_bold"])
            sh.set(r, 1, value=snap["latest_complete_fiscal_year_end"], style=s["label"])
            sh.set(r, 2, value=lyf.get("total_debt_normalization_method") or "", style=s["note"])
            sh.set(r, 3, value=lyf.get("total_debt_reported_accession") or "", style=s["note"])
            sh.set(r, 4, value=lyf.get("total_debt_reported_filed_date") or "", style=s["label"])
            sh.set(r, 5, value="reported", style=s["label"])
            sh.set(r, 6, value=lyf.get("total_debt_reported_source_url") or "", style=s["note"])

        mkt = snap["market_data"]
        r = rc.row()
        sh.set(r, 0, value="Price / Shares / Beta / Sector", style=s["label"])
        sh.set(r, 1, value=mkt["retrieved_at_utc"], style=s["label"])
        sh.set(r, 2, value="yfinance Ticker.info", style=s["note"])
        sh.set(r, 4, value=mkt["retrieved_at_utc"], style=s["label"])
        sh.set(r, 5, value="reported (unchanged from original freeze)", style=s["label"])
        sh.set(r, 6, value="https://finance.yahoo.com/quote/" + tk, style=s["note"])
        rc.skip(1)

    sh.set_widths(0, [30, 24, 40, 20, 24, 38, 60])
    return sh


# ---------------------------------------------------------------------------
# Assumptions sheet
# ---------------------------------------------------------------------------

def build_assumptions_sheet(wb, s, results):
    sh = wb.add_sheet("Assumptions")
    rc = RC(sh)
    rc.title("Assumptions", s["title"], span=6)
    sh.set(rc.row(), 0, value="Per docs/assumptions-register.md and the task's shared-assumption directive.", style=s["subtitle"])
    rc.skip(1)

    rc.section("Shared assumptions (identical for every company unless noted)", s["section"])
    hdr = rc.row()
    for c, t in enumerate(["Assumption", "Value", "Register ID", "Rationale"]):
        sh.set(hdr, c, value=t, style=s["header"])
    shared = [
        ("Risk-free rate", "4.0%", "A-001", "10Y Treasury proxy; fixed per task directive rather than a live fetch (SEC/yfinance macro endpoints not required here)."),
        ("Market risk premium", "5.5%", "A-009", "Standard long-run U.S. equity risk premium estimate."),
        ("Terminal growth rate", "2.5%", "A-003", "Roughly tracks long-run nominal GDP growth; bounded well under typical WACC."),
        ("Projection period", "5 years", "—", "Standard explicit DCF forecast window."),
        ("D&A % of revenue", "3%", "A-004", "Fixed simplifying assumption, not derived per-company."),
        ("CapEx % of revenue", "4%", "A-004", "Fixed simplifying assumption, not derived per-company."),
        ("ΔNWC % of revenue change", "1%", "A-004", "Applied to the CHANGE in revenue, not revenue itself (avoids double-counting)."),
        ("WACC clamp", "[5%, 20%]", "A-006", "Prevents a degenerate beta/rate combination from producing a nonsensical discount rate."),
        ("Terminal growth clamp", "[0%, 5%]", "spec (dcf.md)", "Keeps WACC−g well-behaved."),
        ("Historical revenue growth cap", "25% (uncapped below)", "A-002", "Caps a hyper-growth anomaly; deliberately NOT floored — a shrinking company's negative CAGR is used as-is."),
    ]
    for a, v, rid, rat in shared:
        r = rc.row()
        sh.set(r, 0, value=a, style=s["label"])
        sh.set(r, 1, value=v, style=s["label_bold"])
        sh.set(r, 2, value=rid, style=s["label"])
        sh.set(r, 3, value=rat, style=s["wrap"])
    rc.skip(1)

    rc.section("Company-specific derived values & disclosed fallbacks", s["section"])
    hdr = rc.row()
    for c, t in enumerate(["Ticker", "Tax Rate (derived)", "Method", "Cost of Debt (derived)", "Method", "Note"]):
        sh.set(hdr, c, value=t, style=s["header"])
    for tk in TICKERS:
        r = results[tk]
        rr = rc.row()
        sh.set(rr, 0, value=tk, style=s["label_bold"])
        sh.set(rr, 1, value=f"{r['tax_rate']:.2%}", style=s["label"])
        sh.set(rr, 2, value=r["tax_rate_method"], style=s["label"])
        sh.set(rr, 3, value=f"{r['cost_of_debt']:.2%}", style=s["label"])
        sh.set(rr, 4, value=r["cost_of_debt_method"], style=s["label"])
        note = ""
        if tk == "INTC":
            note = "Tax rate 98.3% is technically in [0,1) but economically extreme — latest-year pretax income ($1.56B) is nearly fully offset by tax provision ($1.53B), likely a valuation-allowance/one-time effect on a slim profit base. Used as-is per spec (in-range values are not overridden); flagged prominently here and in Research Outlook, not silently smoothed."
        sh.set(rr, 5, value=note, style=s["note"])
    rc.skip(1)

    rc.section("Missing-data fallbacks actually triggered in this validation", s["section"])
    para_row = rc.row()
    sh.set(para_row, 0, value="None. All four companies had complete SEC EDGAR data for every required field (revenue, EBIT/operating income — one derived via Pretax+Interest for periods without a reported OperatingIncomeLoss tag, pretax income, tax provision, interest expense, total debt, cash) and complete yfinance market data (price, shares, beta, sector). No DEFAULT_TAX_RATE=21%, DEFAULT_COST_OF_DEBT=5%, DEFAULT_BETA=1.0, or DEFAULT_REVENUE_GROWTH/MARGIN fallback was triggered for any of the four companies — every derived value below is a genuine derivation from frozen facts, not a fallback default.", style=s["wrap"])
    sh.merge(para_row, 0, para_row, 5)
    sh.row_heights[para_row] = 60

    sh.set_widths(0, [10, 20, 30, 22, 30, 70])
    return sh


# ---------------------------------------------------------------------------
# Summary & Reconciliation sheet
# ---------------------------------------------------------------------------

TOLERANCES = {
    "Revenue CAGR": (0.0001, "±0.01pp"),
    "Operating Margin": (0.0001, "±0.01pp"),
    "Raw WACC": (0.0005, "±0.05pp"),
    "Final WACC": (0.0005, "±0.05pp"),
    "FCF Year 1": (0.001, "±0.1%"),
    "FCF Year 2": (0.001, "±0.1%"),
    "FCF Year 3": (0.001, "±0.1%"),
    "FCF Year 4": (0.001, "±0.1%"),
    "FCF Year 5": (0.001, "±0.1%"),
    "Terminal Value": (0.002, "±0.2%"),
    "PV of Explicit FCF": (0.002, "±0.2%"),
    "PV of Terminal Value": (0.002, "±0.2%"),
    "Enterprise Value": (0.002, "±0.2%"),
    "Equity Value": (0.002, "±0.2%"),
    "Intrinsic Value per Share": (0.005, "±0.5%"),
}


def build_summary_sheet(wb, s, snaps, results, all_dmarks):
    sh = wb.add_sheet("Summary & Reconciliation")
    rc = RC(sh)
    rc.title("Summary & Reconciliation", s["title"], span=7)
    sh.set(rc.row(), 0, value="Per docs/independent-validation-plan.md §Reconciliation format. 'Codebase Output' is deliberately left blank — populating it is the explicit scope of a SEPARATE future session (never this one). PASS/FAIL/PENDING formulas therefore read PENDING until that column is filled.", style=s["subtitle"])
    rc.skip(1)

    hdr = rc.row()
    heads = ["Metric", "Independent Workbook Output", "Codebase Output (fill in future session)",
              "Absolute Difference", "Percentage Difference", "Documented Tolerance", "PASS/FAIL/PENDING", "Notes"]
    for c, t in enumerate(heads):
        sh.set(hdr, c, value=t, style=s["header"])

    metric_rows = {}
    for tk in TICKERS:
        dm = all_dmarks[tk]
        DC = f"'DCF_{tk}'!"
        r0 = rc.row()
        sh.set(r0, 0, value=f"— {tk} ({snaps[tk]['entity_name']}) —", style=s["section"])
        for c in range(1, 8):
            sh.set(r0, c, value=None, style=s["section"])

        metric_refs = [
            ("Revenue CAGR", f"{DC}$B${xrow(dm['capped_cagr_row'])}", result_val(results[tk], "capped_cagr")),
            ("Operating Margin", f"{DC}$B${xrow(dm['margin_row'])}", result_val(results[tk], "avg_operating_margin")),
            ("Raw WACC", f"{DC}$B${xrow(dm['raw_wacc_row'])}", results[tk]["wacc"]["raw_wacc"]),
            ("Final WACC", f"{DC}$B${xrow(dm['wacc_row'])}", results[tk]["wacc"]["final_wacc"]),
        ]
        for i in range(5):
            col = col_letter(2 + i)
            metric_refs.append((f"FCF Year {i+1}", f"{DC}${col}${xrow(dm['fcf_row'])}", results[tk]["fcf_rows"][i]["fcf"]))
        metric_refs += [
            ("Terminal Value", f"{DC}$B${xrow(dm['tv_row'])}", results[tk]["terminal_value"]),
            ("PV of Explicit FCF", f"{DC}$B${xrow(dm['sum_pv_fcf_row'])}", sum(results[tk]["bridge"]["pv_fcf"])),
            ("PV of Terminal Value", f"{DC}$B${xrow(dm['pv_tv_row'])}", results[tk]["bridge"]["pv_tv"]),
            ("Enterprise Value", f"{DC}$B${xrow(dm['ev_row'])}", results[tk]["bridge"]["enterprise_value"]),
            ("Equity Value", f"{DC}$B${xrow(dm['eqv_row'])}", results[tk]["bridge"]["equity_value"]),
            ("Intrinsic Value per Share", f"{DC}$B${xrow(dm['ivps_row'])}", results[tk]["bridge"]["intrinsic_value_per_share"]),
        ]

        for metric, ref, cached in metric_refs:
            r = rc.row()
            metric_rows[(tk, metric)] = r
            sh.set(r, 0, value=metric, style=s["label"])
            is_pct = metric in ("Revenue CAGR", "Operating Margin", "Raw WACC", "Final WACC")
            fstyle = s["formula_pct"] if is_pct else s["formula_usd"]
            if metric == "Intrinsic Value per Share":
                fstyle = s["formula_price"]
            sh.set(r, 1, formula=ref.lstrip("="), cached=cached, style=fstyle)
            sh.set(r, 2, value=None, style=s["input_usd"] if not is_pct else s["input_pct"])  # blank, editable by future session
            tol, tol_label = TOLERANCES[metric]
            b_ref, c_ref = f"B{xrow(r)}", f"C{xrow(r)}"
            sh.set(r, 3, formula=f'IF({c_ref}="","PENDING",ABS({b_ref}-{c_ref}))', cached="PENDING", style=s["formula_txt"])
            sh.set(r, 4, formula=f'IF(OR({c_ref}="",{c_ref}=0),"PENDING",ABS({b_ref}-{c_ref})/ABS({c_ref}))', cached="PENDING", style=s["formula_txt"])
            sh.set(r, 5, value=tol_label, style=s["label"])
            sh.set(r, 6, formula=f'IF({c_ref}="","PENDING",IF(E{xrow(r)}<={tol},"PASS","FAIL"))', cached="PENDING", style=s["pending"])
            sh.set(r, 7, value="", style=s["note"])
        rc.skip(1)

    sh.set_widths(0, [28, 24, 26, 18, 18, 16, 16, 40])
    sh.freeze_panes(1, 1)
    return metric_rows


def result_val(result, key):
    return result[key]


# ---------------------------------------------------------------------------
# Research Outlook sheet
# ---------------------------------------------------------------------------

def build_research_outlook_sheet(wb, s, snaps, results):
    sh = wb.add_sheet("Research Outlook")
    rc = RC(sh)
    rc.title("Research Outlook — Independent DCF Findings", s["title"], span=7)
    sh.set(rc.row(), 0, value="Interpretation of THIS workbook's own independent calculations under the frozen assumptions above. Not a comparison to the codebase (that is a separate future session) and not investment advice.", style=s["subtitle"])
    rc.skip(1)

    def para(text, span=7, height=None):
        r = rc.row()
        sh.set(r, 0, value=text, style=s["wrap"])
        sh.merge(r, 0, r, span)
        sh.row_heights[r] = height or max(15, 15 * (len(text) // 130 + 1))
        return r

    rc.section("PROMINENT DISCLAIMER — read first", s["section"])
    para("Profitability remains UNPROVEN. Nothing in this workbook demonstrates that any of these four companies "
         "would produce a profitable trade, nor that a DCF-driven strategy earns a durable, statistically credible "
         "return. This workbook tests ONE narrow question: does a mechanically independent implementation of the "
         "written DCF/WACC specification produce internally consistent, sensible numbers? An 'apparently "
         "undervalued under these assumptions' finding is a statement about a model's output under one fixed set "
         "of policy assumptions (4% risk-free rate, 5.5% market risk premium, 2.5% terminal growth, fixed D&A/CapEx/"
         "NWC percentages) — it is not a statement about expected forward returns, and it is not, and must never be "
         "read as, a recommendation to buy, sell, or hold any security.", height=60)
    rc.skip(1)

    rc.section("Base-case findings, per company", s["section"])
    hdr = rc.row()
    for c, t in enumerate(["Ticker", "Intrinsic Value/Share", "Current Price", "Price / Intrinsic Value", "Reading under these assumptions"]):
        sh.set(hdr, c, value=t, style=s["header"])
    findings = {
        "MSFT": "Model output is below the current price (P/IV ≈ 1.4x) — under fixed 2.5% terminal growth, MSFT's own historical 13.7% revenue CAGR and 44% operating margin are NOT extrapolated into the terminal period (dcf.md's explicit no-glide-path design), so the model's terminal value is comparatively conservative relative to a market plausibly pricing in continued elevated growth. This is the cleanest-fitting of the four profiles — capital-light, high-margin, low leverage, all assumptions land in unremarkable ranges.",
        "CAT": "Model output is far below the current price (P/IV ≈ 5.4x). This workbook did NOT source or freeze CAT's actual historical CapEx or D&A dollar figures — only the fixed 4% CapEx / 3% D&A policy assumption (A-004) was applied, identically to every other company in this set. It is plausible that a fixed generic CapEx/D&A policy fits a heavy-machinery manufacturer with a captive finance arm poorly, and that possibility should be tested separately (by sourcing CAT's own historical CapEx/D&A-to-revenue ratios and comparing them to the fixed 4%/3% policy) rather than assumed here. The magnitude of this gap is large enough that it also warrants independently re-checking the frozen price and SEC EDGAR inputs themselves before attributing it to any single cause.",
        "INTC": "Model output is NEGATIVE. With revenue declining at -9.6%/year, near-zero (0.46%) average historical operating margin, and a latest-year effective tax rate of 98.3% (Pretax Income and Tax Provision both landed at similar small magnitudes — see Assumptions sheet), NOPAT is structurally close to zero every projected year while fixed CapEx (4%) exceeds fixed D&A (3%), producing a persistently negative unlevered FCF path. This is the single most fragile case in this validation set: three of four sensitivity direction checks mathematically INVERT (see Sensitivity_INTC) because the underlying FCF is negative. This is disclosed, not hidden — see 'Fragility' below.",
        "VZ": "Model output is far ABOVE the current price (P/IV ≈ 0.30x) — VZ's raw WACC (4.4%) computed below the model's own [5%,20%] floor and was clamped up to 5% (A-006), and its measured beta (0.231) is unusually low. A low discount rate very close to the 2.5% terminal growth rate makes the Gordon Growth denominator (WACC−g) small, which mechanically inflates terminal value and therefore intrinsic value. This is a textbook illustration of L-005 (terminal-value sensitivity) and the WACC clamp binding in practice (A-006).",
    }
    for tk in TICKERS:
        b = results[tk]["bridge"]
        r = rc.row()
        sh.set(r, 0, value=tk, style=s["label_bold"])
        sh.set(r, 1, value=f"${b['intrinsic_value_per_share']:,.2f}", style=s["label"])
        sh.set(r, 2, value=f"${snaps[tk]['market_data']['current_price_usd_per_share']:,.2f}", style=s["label"])
        sh.set(r, 3, value=f"{b['price_to_intrinsic_value']:.2f}x" if b['intrinsic_value_per_share'] > 0 else "n/m (negative IV)", style=s["label"])
        sh.set(r, 4, value=findings[tk], style=s["wrap"])
        sh.row_heights[r] = 90
    rc.skip(1)

    rc.section("Which assumptions drive the widest valuation range?", s["section"])
    para("Terminal growth rate and WACC dominate, exactly as L-005 warns: the Gordon Growth denominator (WACC−g) "
         "appears in every company's Table 2 (Sensitivity_<TICKER>) with the steepest curvature near where WACC "
         "approaches the terminal growth rate — VZ's base WACC (5.0%, at the clamp floor) sits closest to the 2.5% "
         "terminal growth rate of any of the four companies, and correspondingly shows the widest terminal-growth "
         "sensitivity range of the four (see Sensitivity_VZ Table 2). The fixed D&A/CapEx/NWC percentages (A-004) "
         "are the second-largest driver for the capital-intensive profile (CAT) specifically, since those "
         "percentages were not derived from CAT's own historical ratios.")
    rc.skip(1)

    rc.section("Where is the DCF most fragile?", s["section"])
    para("INTC. A company with structurally negative average operating margin risk (historical average is only "
         "0.46%, and the two most recent years were outright negative) combined with fixed CapEx exceeding fixed "
         "D&A produces a persistently negative unlevered FCF path under this model's assumptions. Once base FCF is "
         "negative, the model's own sensitivity direction checks flip sign (documented, not hidden, in "
         "Sensitivity_INTC) — higher WACC and higher terminal growth both make the (negative) intrinsic value LESS "
         "negative rather than more negative, and higher assumed revenue growth makes it MORE negative, since "
         "growing a structurally lossmaking revenue base faster only accelerates the losses. This is mathematically "
         "correct DCF behavior, not a bug in this workbook or evidence of a codebase defect — but it is a vivid "
         "demonstration of why a single point-estimate DCF intrinsic value should never be read without its "
         "sensitivity context (L-005), and why the model is a poor fit for a company whose near-term unit "
         "economics are this compressed.")
    rc.skip(1)

    rc.section("Which profiles fit the model cleanly, and which expose limitations?", s["section"])
    para("MSFT fits cleanly: strong positive growth, high stable margin, minimal leverage, WACC comfortably inside "
         "[5%,20%] without clamping, no fallback triggered anywhere. CAT computes cleanly (no errors, no clamps) "
         "but exposes A-004's simplification (fixed CapEx/D&A percentages) — the model's fixed 4%/3% split does "
         "not adapt to a genuinely capital-intensive business the way historical revenue growth and operating "
         "margin do. INTC exposes both A-002 (unbounded-below historical growth — a genuine real-world negative-"
         "growth company should be valuable to be able to model, and it is, but the result is a stress test of "
         "the whole projection chain) and the general fragility of a single-stage Gordon Growth terminal value "
         "applied to a company with near-zero or negative near-term profitability. VZ exposes A-006 (the WACC "
         "clamp) actually binding for a real company, not just as a theoretical safeguard, and the general "
         "sensitivity of Gordon Growth to a WACC-minus-g spread that is unusually narrow.")
    rc.skip(1)

    rc.section("Why \"apparently undervalued under these assumptions\" is not \"expected profitable trade\"", s["section"])
    para("A DCF intrinsic value is a conditional statement: IF a company's cash flows in fact grow, are margined, "
         "and are capitalized the way these fixed assumptions say, THEN this is what its equity would be worth "
         "at this discount rate. It says nothing about (a) whether the market will ever re-price the security "
         "toward that estimate within any particular holding period, (b) whether these specific fixed assumptions "
         "(2.5% terminal growth, 4%/3% CapEx/D&A, a single risk-free rate and market risk premium applied "
         "identically to a capital-light software company and a heavily levered telecom) are the RIGHT assumptions "
         "for each specific company, or (c) what it would cost in spread, slippage, and execution delay to actually "
         "act on the signal. See docs/research-overview.md's identical distinction between 'valuation accuracy' and "
         "'tradeable profitability' — this workbook addresses only the former, and only as a spec-conformance check, "
         "not even as a claim that the spec's fixed assumptions are themselves well-calibrated.", height=60)
    rc.skip(1)

    rc.section("Evidence still required before any profitability claim", s["section"])
    reqs = [
        "Codebase-to-workbook reconciliation (Summary & Reconciliation sheet, PENDING — separate future session).",
        "Point-in-time walk-forward testing on a frozen, untouched holdout period (Track B item 5).",
        "Survivorship-bias controls on the universe of companies considered, not just these four hand-picked profiles (Track B item 2 / L-002).",
        "Transaction costs, slippage, and execution delay modeled explicitly (Track B item 6 / L-009).",
        "Benchmark comparison against SPY and simpler baselines (Track B item 7 / L-010).",
        "Confidence intervals / statistical significance on any headline performance number, not a single point estimate (Track B item 9).",
    ]
    for req in reqs:
        r = rc.row()
        sh.set(r, 0, value="☐", style=s["pending"])
        sh.set(r, 1, value=req, style=s["wrap"])
        sh.merge(r, 1, r, 7)

    sh.set_widths(0, [16, 18, 16, 18, 60, 12, 12, 12])
    return sh


# ---------------------------------------------------------------------------
# Validation Checks (master rollup) sheet
# ---------------------------------------------------------------------------

def build_validation_checks_sheet(wb, s, results, all_dmarks):
    sh = wb.add_sheet("Validation Checks")
    rc = RC(sh)
    rc.title("Validation Checks — Master Rollup", s["title"], span=5)
    rc.skip(1)

    rc.section("Per-company local checks (live formulas — see DCF_<TICKER> Section 6 for the underlying formula)", s["section"])
    hdr = rc.row()
    for c, t in enumerate(["Ticker", "WACC in [5%,20%]?", "EV finite?", "IVPS finite?", "IVPS positive?", "Tax rate in [0,1)?", "WACC > terminal g?"]):
        sh.set(hdr, c, value=t, style=s["header"])
    check_keys = ["wacc_row", "ev_row", "ivps_row", "ivps_row", "tax_row", "wacc_row"]
    for tk in TICKERS:
        dm = all_dmarks[tk]
        DC = f"'DCF_{tk}'!"
        r = rc.row()
        sh.set(r, 0, value=tk, style=s["label_bold"])
        # Section 6 check rows are 6 consecutive rows right after eqv/ivps/piv rows; recompute by reading directly the DCF sheet's own check cells (col B) via formula reference
        # The six checks are located at fixed offsets after piv_row in build_dcf_sheet (rc.section + 6 check_row calls, 1 header + 6 rows)
        first_check_row = dm['piv_row'] + 3  # piv_row, then skip(1) -> +1 blank, then section header -> +1, then first check row
        ivps_positive = results[tk]["bridge"]["intrinsic_value_per_share"] > 0
        tax_in_range = 0 <= results[tk]["tax_rate"] < 1
        wacc_gt_g = results[tk]["wacc"]["final_wacc"] > 0.025
        cached_checks = ["PASS", "PASS", "PASS", "PASS" if ivps_positive else "FAIL",
                          "PASS" if tax_in_range else "FAIL", "PASS" if wacc_gt_g else "FAIL"]
        for i in range(6):
            col_letter_idx = 1 + i
            ref_row = first_check_row + i
            f = f"{DC}$B${xrow(ref_row)}"
            cell_style = s["pass"] if cached_checks[i] == "PASS" else s["fail"]
            sh.set(r, col_letter_idx, formula=f, cached=cached_checks[i], style=cell_style)
    rc.skip(2)

    rc.section("Cross-company monotonicity finding", s["section"])
    r = rc.row()
    sh.set(r, 0, value="INTC's WACC / terminal-growth / revenue-growth direction checks are documented FAILs (see Sensitivity_INTC) due to negative base-case FCF — mathematically correct, investigated, and disclosed, not forced to PASS.", style=s["wrap"])
    sh.merge(r, 0, r, 6)
    rc.skip(1)

    rc.section("Sign-off checklist (per docs/independent-validation-plan.md §Review / sign-off checklist)", s["section"])
    checklist = [
        ("Input snapshot frozen, dated, stored alongside workbook", True, "snapshots/*.json + snapshot_manifest.json with SHA-256 checksums"),
        ("Every formula cell traceable to a dcf.md / wacc-capm.md section, not to dcf.py", True, "Adjacent labels on every formula row across all 4 DCF_<TICKER> sheets"),
        ("Workbook built without importing or calling any code from this repository", True, "build_workbook_v2.py imports only stdlib + shadow_calc_v2.py (independent reimplementation)"),
        ("Full intermediate-value reconciliation table complete for each company", "partial", "Summary & Reconciliation sheet structure complete; Codebase Output column is intentionally blank pending a separate future session"),
        ("Sensitivity tables (WACC, terminal growth, revenue growth, margin) complete for each company", True, "Sensitivity_<TICKER> Tables 1-4 + two-way table, all 4 companies"),
        ("Deliberate error-injection check performed at least once, result documented", True, "See error_injection_evidence.md"),
        ("A second reviewer (not the workbook's builder) has independently checked the reconciliation table and a sensitivity table", False, "PENDING — requires a human or a separate agent session; not performed here"),
        ("Any discrepancy beyond tolerance resolved or documented as accepted divergence", "n/a", "No discrepancy exists yet to resolve — reconciliation against the codebase has not been run in this session"),
    ]
    hdr2 = rc.row()
    for c, t in enumerate(["Item", "Status", "Evidence / Note"]):
        sh.set(hdr2, c, value=t, style=s["header"])
    for item, status, note in checklist:
        r = rc.row()
        sh.set(r, 0, value=item, style=s["wrap"])
        if status is True:
            sh.set(r, 1, value="DONE", style=s["pass"])
        elif status is False:
            sh.set(r, 1, value="PENDING", style=s["pending"])
        elif status == "partial":
            sh.set(r, 1, value="PARTIAL", style=s["pending"])
        else:
            sh.set(r, 1, value="N/A", style=s["label"])
        sh.set(r, 2, value=note, style=s["wrap"])
        sh.row_heights[r] = 30

    sh.set_widths(0, [55, 12, 60, 14, 14, 14, 14])
    return sh


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def build_workbook(out_path, snap_dir=None):
    wb = Workbook()
    s = build_styles(wb)
    snaps = load_snapshots(snap_dir)
    results = load_results(snaps)

    build_readme_sheet(wb, s, snaps)
    build_sources_sheet(wb, s, snaps)
    build_assumptions_sheet(wb, s, results)

    all_imarks, all_dmarks, all_hist = {}, {}, {}
    for tk in TICKERS:
        imarks, hstart, hend = build_inputs_sheet(wb, s, tk, snaps[tk])
        all_imarks[tk] = imarks
        all_hist[tk] = (hstart, hend)
        dmarks = build_dcf_sheet(wb, s, tk, snaps[tk], results[tk], imarks, hstart, hend)
        all_dmarks[tk] = dmarks
        build_sensitivity_sheet(wb, s, tk, snaps[tk], results[tk], imarks, dmarks)

    build_summary_sheet(wb, s, snaps, results, all_dmarks)
    build_research_outlook_sheet(wb, s, snaps, results)
    build_validation_checks_sheet(wb, s, results, all_dmarks)

    wb.save(out_path)
    return wb, snaps, results, all_imarks, all_dmarks


if __name__ == "__main__":
    if os.environ.get("BW_SMOKE_TEST"):
        wb = Workbook()
        s = build_styles(wb)
        snaps = load_snapshots()
        results = load_results(snaps)
        tk = "MSFT"
        imarks, hstart, hend = build_inputs_sheet(wb, s, tk, snaps[tk])
        dmarks = build_dcf_sheet(wb, s, tk, snaps[tk], results[tk], imarks, hstart, hend)
        smarks = build_sensitivity_sheet(wb, s, tk, snaps[tk], results[tk], imarks, dmarks)
        wb.save("/tmp/smoke_test.xlsx")
        print("Smoke test saved OK")
    else:
        out_path = os.path.join(HERE, "independent_dcf_validation_v2.xlsx")
        build_workbook(out_path)
        print(f"Workbook saved: {out_path}")
