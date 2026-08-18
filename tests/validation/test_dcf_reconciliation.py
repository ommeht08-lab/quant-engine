"""
Track A Phase 2C -- tests for validation/dcf_reconciliation/ and the V1->V2
independent-workbook transition.

Kept under tests/ so tests/conftest.py's global isolation guards (env-var
sentinels, dotenv no-op, blocked raw sockets) load before this module is
collected -- the same "External access blocked during tests" backstop
every other test in this repository relies on.

No network access. Every negative control operates on an in-memory
structure or a temporary file copy, discarded at the end of the test that
created it -- never on validation/independent_dcf/independent_dcf_validation.xlsx,
independent_dcf_validation_v2.xlsx, the frozen snapshots, or
validation/dcf_reconciliation/history/ directly.
"""
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from src.dcf_model.dcf import (
    DCFAssumptions,
    calculate_historical_revenue_cagr,
    extract_valuation_inputs,
    run_dcf_valuation,
)

from validation.dcf_reconciliation.adapter import (
    SNAPSHOT_DIR,
    TICKERS,
    assert_adapter_matches_snapshot,
    build_financial_data,
    load_all,
    load_snapshot,
)
from validation.dcf_reconciliation.capture import (
    EXPECTED_V1_WORKBOOK_SHA256,
    EXPECTED_V2_WORKBOOK_SHA256,
    INDEPENDENT_WORKBOOK_PATH,
    V1_WORKBOOK_PATH,
    V2_WORKBOOK_PATH,
)
from validation.dcf_reconciliation.cell_map import (
    BASE_METRIC_ORDER,
    METRIC_LABELS,
    REQUIRED_METRICS,
    locate_dcf_metrics,
)
from validation.dcf_reconciliation.compare import compare_metric, compare_monetary, compare_rate
from validation.dcf_reconciliation.coverage import (
    EXPECTED_SCALAR_COMPARISONS_PER_COMPANY,
    EXPECTED_SCALAR_COMPARISONS_TOTAL,
    OUTPUT_METRIC_LABELS,
    iter_all_scalar_comparisons,
    sensitivity_coverage_rows,
)
from validation.dcf_reconciliation.sensitivity_map import locate_sensitivity_tables
from validation.dcf_reconciliation.xlsx_reader import Workbook, col_index_to_letters

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEPENDENT_DIR = REPO_ROOT / "validation" / "independent_dcf"
RECONCILIATION_DIR = REPO_ROOT / "validation" / "dcf_reconciliation"
ARCHIVE_DIR = RECONCILIATION_DIR / "history" / "phase2b_initial_no_go"

# Expected hashes of the frozen snapshot state this phase was told to expect
# (recorded read-only at session start; a guard against silent drift of the
# frozen inputs the whole reconciliation depends on). V1/V2 workbook hashes
# come directly from capture.py (single source of truth), not redeclared here.
EXPECTED_SNAPSHOT_SHA256 = {
    "MSFT": "7c50a3e9fb2d27095a890ad8d11732d4ccc72ef9350998604adb3af7a3c845a8",
    "CAT": "651d366c01415c2da33733058dabb9ac6e4f40cdf5b20e743c0b83ebc3a52c93",
    "INTC": "d73e4b40bce2442a75affde82aaa563ffdea9c68c7257e70cc325663cff24930",
    "VZ": "77b8ea27b8decaff5fe3b4aad857c632a852445eda0b7f2342f594cd3c4b5f3d",
}

FORMULA_ERROR_MARKERS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NULL!", "#NUM!")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_dir(path: Path) -> str:
    """Deterministic hash of every file's relative path + content under `path`."""
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(path)).encode("utf-8"))
            h.update(_sha256_file(p).encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 1. Exact company set
# ---------------------------------------------------------------------------

def test_exact_company_set():
    assert TICKERS == ("MSFT", "CAT", "INTC", "VZ")
    snapshot_files = sorted(p.stem.replace("_snapshot", "") for p in SNAPSHOT_DIR.glob("*_snapshot.json"))
    assert snapshot_files == sorted(TICKERS)


# ---------------------------------------------------------------------------
# 2. V1 workbook hash guard -- V1 must remain byte-for-byte unchanged forever
# ---------------------------------------------------------------------------

def test_v1_workbook_hash_unchanged():
    actual = _sha256_file(V1_WORKBOOK_PATH)
    assert actual == EXPECTED_V1_WORKBOOK_SHA256, (
        "V1's SHA-256 has changed -- V1 (independent_dcf_validation.xlsx) is historical "
        "validation evidence and must NEVER be modified or overwritten by any Phase 2C step."
    )


# ---------------------------------------------------------------------------
# 3. V2 workbook hash guard -- frozen at the end of Phase 3
# ---------------------------------------------------------------------------

def test_v2_workbook_hash_matches_frozen_value():
    actual = _sha256_file(V2_WORKBOOK_PATH)
    assert actual == EXPECTED_V2_WORKBOOK_SHA256, (
        "V2's SHA-256 no longer matches the value frozen at the end of Track A Phase 3. "
        "If V2 was legitimately rebuilt, EXPECTED_V2_WORKBOOK_SHA256 (capture.py) must be "
        "updated deliberately -- never silently reconcile against a workbook that no longer "
        "matches what was audited."
    )
    # The reconciliation target alias must actually point at V2, not V1.
    assert INDEPENDENT_WORKBOOK_PATH == V2_WORKBOOK_PATH


# ---------------------------------------------------------------------------
# 4. Snapshot hash guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TICKERS)
def test_snapshot_hash_guard(ticker):
    path = SNAPSHOT_DIR / f"{ticker}_snapshot.json"
    assert _sha256_file(path) == EXPECTED_SNAPSHOT_SHA256[ticker]


# ---------------------------------------------------------------------------
# 5. Snapshot-to-financial_data mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TICKERS)
def test_snapshot_to_financial_data_mapping(ticker):
    snapshot = load_snapshot(ticker)
    financial_data = build_financial_data(snapshot)
    assert_adapter_matches_snapshot(snapshot, financial_data)  # raises AssertionError on any mismatch


# ---------------------------------------------------------------------------
# 6. No missing required production inputs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TICKERS)
def test_no_missing_required_production_inputs(ticker):
    _snapshot, financial_data = load_all()[ticker]
    inputs = extract_valuation_inputs(financial_data)
    for field in ("revenue", "current_price", "shares_outstanding", "beta", "total_debt", "cash_and_equivalents"):
        assert inputs.get(field) is not None or financial_data.get(field) is not None, (
            f"{ticker}: required production input {field!r} is missing from the adapter output."
        )
    assert financial_data["current_price"] is not None
    assert financial_data["shares_outstanding"] is not None
    assert financial_data["beta"] is not None


# ---------------------------------------------------------------------------
# 7. Workbook metric discovery by labels/cells (against V2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TICKERS)
def test_workbook_metric_discovery_by_labels(ticker):
    wb = Workbook(str(V2_WORKBOOK_PATH))
    metrics = locate_dcf_metrics(wb, ticker)
    assert set(metrics.keys()) == set(BASE_METRIC_ORDER)
    for m in metrics.values():
        assert m.cached_value is not None


# ---------------------------------------------------------------------------
# 8. Every required base metric compared exactly once
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TICKERS)
def test_every_required_base_metric_compared_exactly_once(ticker):
    wb = Workbook(str(V2_WORKBOOK_PATH))
    wb_metrics = locate_dcf_metrics(wb, ticker)

    compared = []
    for metric in REQUIRED_METRICS:
        assert metric in wb_metrics
        compared.append(metric)
    assert compared == REQUIRED_METRICS  # exact order, no duplicates, nothing skipped
    assert len(set(compared)) == len(compared)
    assert "wacc_raw" not in REQUIRED_METRICS  # diagnostic-only metric excluded, by design


# ---------------------------------------------------------------------------
# 9. Irregular-fiscal-date CAGR test (the Finding-1 root cause, directly on
#    production code) using INTC's actual frozen dates and revenues
# ---------------------------------------------------------------------------

def test_irregular_fiscal_date_cagr_uses_actual_elapsed_days_not_period_count():
    """Builds a synthetic income statement with INTC's REAL frozen fiscal-period-end
    dates (2021-12-25, 2022-12-31, 2023-12-30, 2024-12-28, 2025-12-27 -- irregular,
    1,463 actual days apart) and confirms `calculate_historical_revenue_cagr`
    (src/dcf_model/dcf.py) computes years_elapsed via actual date subtraction,
    producing a DIFFERENT (and correct) result than a naive (periods-1)=4 count would."""
    dates = ["2021-12-25", "2022-12-31", "2023-12-30", "2024-12-28", "2025-12-27"]
    revenues = [79024000000, 63054000000, 54228000000, 53101000000, 52853000000]
    income_stmt = pd.DataFrame(
        {pd.Timestamp(d): {"Total Revenue": r} for d, r in zip(dates, revenues)}
    )

    actual_cagr = calculate_historical_revenue_cagr(income_stmt)

    earliest, latest = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])
    actual_days = (latest - earliest).days
    assert actual_days == 1463  # NOT 1461 (= 4 x 365.25, what an evenly-spaced calendar would give)

    correct_years_elapsed = actual_days / 365.25
    assert correct_years_elapsed == pytest.approx(4.005475701574264, abs=1e-12)

    naive_years_elapsed = len(dates) - 1  # the exact bug: a plain period count
    assert naive_years_elapsed == 4
    assert correct_years_elapsed != naive_years_elapsed

    correct_cagr = (revenues[-1] / revenues[0]) ** (1 / correct_years_elapsed) - 1
    naive_cagr = (revenues[-1] / revenues[0]) ** (1 / naive_years_elapsed) - 1

    assert actual_cagr == pytest.approx(correct_cagr, abs=1e-12)
    assert actual_cagr != pytest.approx(naive_cagr, abs=1e-9)  # production does NOT reproduce the naive-count bug
    assert actual_cagr == pytest.approx(-0.09554417293826767, abs=1e-9)


def test_regular_fiscal_calendar_cagr_coincides_with_naive_count():
    """Sanity companion: for an EVENLY-spaced fiscal calendar (like MSFT/CAT/VZ's),
    actual-elapsed-days and naive (periods-1) agree exactly -- confirming the prior
    test's divergence is specifically due to INTC's irregular spacing, not a general
    property of calculate_historical_revenue_cagr."""
    dates = ["2022-06-30", "2023-06-30", "2024-06-30", "2025-06-30", "2026-06-30"]
    revenues = [198270000000, 211915000000, 245122000000, 281724000000, 331839000000]
    income_stmt = pd.DataFrame(
        {pd.Timestamp(d): {"Total Revenue": r} for d, r in zip(dates, revenues)}
    )
    actual_cagr = calculate_historical_revenue_cagr(income_stmt)
    naive_years_elapsed = len(dates) - 1
    naive_cagr = (revenues[-1] / revenues[0]) ** (1 / naive_years_elapsed) - 1
    assert actual_cagr == pytest.approx(naive_cagr, abs=1e-12)


# ---------------------------------------------------------------------------
# 10. V2 uses actual date subtraction, not COUNT(...)-1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TICKERS)
def test_v2_years_elapsed_formula_uses_date_subtraction_not_count(ticker):
    wb = Workbook(str(V2_WORKBOOK_PATH))
    grid = wb.grid(f"DCF_{ticker}")
    hit = None
    for (r, c), cell in grid.items():
        if c == 0 and cell.text and cell.text.startswith("Years elapsed"):
            hit = (r, c)
            break
    assert hit is not None, f"{ticker}: could not locate the 'Years elapsed' row label in V2."
    row0, _ = hit
    formula_cell = grid.get((row0, 1))
    assert formula_cell is not None and formula_cell.formula is not None
    formula = formula_cell.formula
    assert "COUNT" not in formula.upper(), f"{ticker}: V2's years_elapsed formula still uses COUNT(): {formula!r}"
    assert "365.25" in formula
    assert "-" in formula  # date subtraction
    # Must reference the Inputs sheet's date column (column A), not the revenue column (B).
    assert f"'Inputs_{ticker}'!$A$" in formula, f"{ticker}: formula does not reference Inputs!$A (dates): {formula!r}"


def test_v1_years_elapsed_formula_uses_count_unchanged():
    """Confirms V1 (preserved, historical) still uses its original COUNT(...)-1
    convention -- proving V1 was never edited to match V2's fix."""
    wb = Workbook(str(V1_WORKBOOK_PATH))
    grid = wb.grid("DCF_INTC")
    hit = None
    for (r, c), cell in grid.items():
        if c == 0 and cell.text and cell.text.startswith("Years elapsed"):
            hit = (r, c)
            break
    assert hit is not None
    row0, _ = hit
    formula_cell = grid.get((row0, 1))
    assert "COUNT" in formula_cell.formula.upper()


# ---------------------------------------------------------------------------
# 11. Tolerance math: positive/negative/zero values, percentage-point math for rates
# ---------------------------------------------------------------------------

class TestToleranceMath:
    def test_monetary_positive_values_within_tolerance(self):
        r = compare_monetary("m", 100.05, 100.0, tolerance_relative=0.001)
        assert r.passed is True
        assert r.absolute_difference == pytest.approx(0.05)

    def test_monetary_positive_values_outside_tolerance(self):
        r = compare_monetary("m", 105.0, 100.0, tolerance_relative=0.001)
        assert r.passed is False

    def test_monetary_negative_values(self):
        r = compare_monetary("m", -105.0, -100.0, tolerance_relative=0.01)
        assert r.absolute_difference == pytest.approx(5.0)
        assert r.passed is False
        r2 = compare_monetary("m", -100.5, -100.0, tolerance_relative=0.01)
        assert r2.passed is True

    def test_monetary_mixed_sign_values(self):
        r = compare_monetary("m", 10.0, -10.0, tolerance_relative=0.5)
        assert r.absolute_difference == pytest.approx(20.0)
        assert r.passed is False

    def test_monetary_zero_workbook_value_exact_match_passes(self):
        r = compare_monetary("m", 0.0, 0.0, tolerance_relative=0.001)
        assert r.passed is True
        assert r.difference_kind == "absolute_zero_carveout"

    def test_monetary_zero_workbook_value_negative_denominator_does_not_false_pass(self):
        r = compare_monetary("m", 5.0, 0.0, tolerance_relative=0.001)
        assert r.passed is False
        assert r.difference_kind == "absolute_zero_carveout"

    def test_monetary_tiny_nonzero_workbook_value_uses_relative_not_zero_carveout(self):
        r = compare_monetary("m", 0.002, 0.001, tolerance_relative=0.5)
        assert r.difference_kind == "relative_fraction"
        assert r.passed is False

    def test_rate_percentage_point_math_positive(self):
        r = compare_rate("wacc", 0.1004, 0.1000, tolerance_pp_decimal=0.0005)
        assert r.passed is True

    def test_rate_percentage_point_math_exceeds(self):
        r = compare_rate("wacc", 0.1006, 0.1000, tolerance_pp_decimal=0.0005)
        assert r.passed is False

    def test_rate_percentage_point_math_negative_values(self):
        r = compare_rate("revenue_cagr", -0.0955, -0.0957, tolerance_pp_decimal=0.0001)
        assert r.absolute_difference == pytest.approx(0.0002, abs=1e-12)
        assert r.passed is False

    def test_rate_percentage_point_is_not_relative_percentage(self):
        r = compare_rate("wacc", 0.02, 0.01, tolerance_pp_decimal=0.05)
        assert r.passed is True
        assert r.difference_value == pytest.approx(1.0)

    def test_dispatch_via_compare_metric_matches_documented_tolerance(self):
        r = compare_metric("intrinsic_value_per_share", 100.4, 100.0)
        assert r.passed is True
        r2 = compare_metric("intrinsic_value_per_share", 100.6, 100.0)
        assert r2.passed is False
        r3 = compare_metric("revenue_cagr", 0.1002, 0.1000)
        assert r3.passed is False


# ---------------------------------------------------------------------------
# 12. Missing / perturbed workbook value detection -- temp copy only (against V2)
# ---------------------------------------------------------------------------

def _sheet_part_path(zip_names_content, sheet_display_name):
    import xml.etree.ElementTree as ET
    MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
    DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    wb_root = ET.fromstring(zip_names_content["xl/workbook.xml"])
    rels_root = ET.fromstring(zip_names_content["xl/_rels/workbook.xml.rels"])
    rid_to_target = {
        rel.get("Id"): rel.get("Target")
        for rel in rels_root.findall(f"{{{PKG_REL_NS}}}Relationship")
    }
    sheets_el = wb_root.find(f"{{{MAIN_NS}}}sheets")
    rid = None
    for sheet_el in sheets_el.findall(f"{{{MAIN_NS}}}sheet"):
        if sheet_el.get("name") == sheet_display_name:
            rid = sheet_el.get(f"{{{DOC_REL_NS}}}id")
            break
    target = rid_to_target[rid]
    return target if target.startswith("xl/") else "xl/" + target


def _make_temp_copy_with_cell_edit(source_path, sheet_display_name, cell_ref, mode, new_value=None):
    """Copies `source_path` to a temp file, edits exactly one cell's XML in that
    temp copy, and returns the temp path. The original file is never opened
    for writing. Caller is responsible for deleting the temp file."""
    tmp_dir = tempfile.mkdtemp(prefix="dcf_reconciliation_negctrl_")
    tmp_path = Path(tmp_dir) / "corrupted_copy.xlsx"
    shutil.copy2(source_path, tmp_path)

    with zipfile.ZipFile(tmp_path) as z:
        names = z.namelist()
        content = {n: z.read(n) for n in names}

    part = _sheet_part_path(content, sheet_display_name)
    xml_text = content[part].decode("utf-8")

    pattern = re.compile(rf'(<c r="{re.escape(cell_ref)}"[^>]*>.*?</c>)', re.DOTALL)
    m = pattern.search(xml_text)
    assert m is not None, f"Test setup failed: cell {cell_ref} not found in {sheet_display_name}."
    cell_xml = m.group(1)

    if mode == "remove":
        new_cell_xml = ""
    elif mode == "set_value":
        new_cell_xml, n_subs = re.subn(r"<v>[^<]*</v>", f"<v>{new_value}</v>", cell_xml)
        assert n_subs == 1, f"Test setup failed: no <v> element found in cell {cell_ref}."
    else:
        raise ValueError(mode)

    new_xml_text = xml_text[: m.start(1)] + new_cell_xml + xml_text[m.end(1):]
    content[part] = new_xml_text.encode("utf-8")

    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, content[n])

    return tmp_path, tmp_dir


def _find_ivps_cell_ref(wb, ticker):
    grid = wb.grid(f"DCF_{ticker}")
    for (r, c), cell in grid.items():
        if c == 0 and cell.text and cell.text.startswith("Intrinsic Value per Share = Equity Value"):
            return f"B{r + 1}"
    raise AssertionError(f"{ticker}: could not locate Intrinsic Value per Share row.")


def test_missing_workbook_formula_or_value_detected():
    """Negative control: delete DCF_MSFT's Intrinsic Value per Share cell entirely
    from a TEMP COPY of V2. locate_dcf_metrics must refuse the comparison (raise),
    not silently skip the missing metric."""
    wb_orig = Workbook(str(V2_WORKBOOK_PATH))
    ivps_ref = _find_ivps_cell_ref(wb_orig, "MSFT")
    tmp_path, tmp_dir = _make_temp_copy_with_cell_edit(V2_WORKBOOK_PATH, "DCF_MSFT", ivps_ref, mode="remove")
    try:
        wb = Workbook(str(tmp_path))
        with pytest.raises(ValueError, match="Intrinsic Value per Share"):
            locate_dcf_metrics(wb, "MSFT")
    finally:
        shutil.rmtree(tmp_dir)
    assert _sha256_file(V2_WORKBOOK_PATH) == EXPECTED_V2_WORKBOOK_SHA256


def test_detect_perturbed_workbook_output():
    """Negative control: corrupt DCF_MSFT's cached Intrinsic Value per Share on a
    TEMP COPY of V2 to a value far outside tolerance of the true codebase output,
    and confirm compare_metric flags FAIL."""
    wb_orig = Workbook(str(V2_WORKBOOK_PATH))
    ivps_ref = _find_ivps_cell_ref(wb_orig, "MSFT")
    tmp_path, tmp_dir = _make_temp_copy_with_cell_edit(
        V2_WORKBOOK_PATH, "DCF_MSFT", ivps_ref, mode="set_value", new_value=99999.0
    )
    try:
        wb = Workbook(str(tmp_path))
        metrics = locate_dcf_metrics(wb, "MSFT")
        assert metrics["intrinsic_value_per_share"].cached_value == 99999.0

        _snapshot, financial_data = load_all()["MSFT"]
        result = run_dcf_valuation(financial_data, DCFAssumptions())
        cmp_result = compare_metric(
            "intrinsic_value_per_share",
            result["intrinsic_value_per_share"],
            metrics["intrinsic_value_per_share"].cached_value,
        )
        assert cmp_result.passed is False
    finally:
        shutil.rmtree(tmp_dir)
    assert _sha256_file(V2_WORKBOOK_PATH) == EXPECTED_V2_WORKBOOK_SHA256


# ---------------------------------------------------------------------------
# 13. Detect an intentionally perturbed codebase output -- in-memory only
# ---------------------------------------------------------------------------

def test_detect_perturbed_codebase_output():
    wb = Workbook(str(V2_WORKBOOK_PATH))
    metrics = locate_dcf_metrics(wb, "VZ")
    true_workbook_value = metrics["intrinsic_value_per_share"].cached_value

    perturbed_codebase_value = true_workbook_value * 1.10
    result = compare_metric("intrinsic_value_per_share", perturbed_codebase_value, true_workbook_value)
    assert result.passed is False

    _snapshot, financial_data = load_all()["VZ"]
    real_result = run_dcf_valuation(financial_data, DCFAssumptions())
    real_cmp = compare_metric(
        "intrinsic_value_per_share", real_result["intrinsic_value_per_share"], true_workbook_value
    )
    assert real_cmp.passed is True


# ---------------------------------------------------------------------------
# 14. Detection of the first divergent intermediate
# ---------------------------------------------------------------------------

def test_detect_first_divergent_intermediate():
    from validation.dcf_reconciliation.reconcile import run_base_reconciliation

    wb = Workbook(str(V2_WORKBOOK_PATH))
    raw_results = {}
    for ticker in TICKERS:
        _snapshot, financial_data = load_all()[ticker]
        raw_results[ticker] = run_dcf_valuation(financial_data, DCFAssumptions())

    # Against V2, ALL four companies (including INTC) must now pass -- the
    # Phase 2B root cause is resolved by V2's construction.
    base = run_base_reconciliation(raw_results, wb)
    for ticker in TICKERS:
        assert base[ticker]["first_divergence"] is None, (
            f"{ticker}: unexpected divergence against V2: {base[ticker]['first_divergence']!r}"
        )

    # Deliberately perturb a COPY of MSFT's raw result so operating_margin (2nd metric)
    # diverges while revenue_cagr (1st metric) does not -- confirm first_divergence
    # correctly reports "operating_margin", not "revenue_cagr" or the last metric.
    import copy
    perturbed = copy.deepcopy(raw_results)
    perturbed["MSFT"]["operating_margin"] = perturbed["MSFT"]["operating_margin"] + 0.01
    base_perturbed = run_base_reconciliation(perturbed, wb)
    assert base_perturbed["MSFT"]["first_divergence"] == "operating_margin"


# ---------------------------------------------------------------------------
# 15. Exact sensitivity-grid coverage (against V2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TICKERS)
def test_exact_sensitivity_grid_coverage(ticker):
    wb = Workbook(str(V2_WORKBOOK_PATH))
    tables = locate_sensitivity_tables(wb, ticker)
    assert len(tables.table1) == 7
    assert len(tables.table2) == 7
    assert len(tables.table3) == 8
    assert len(tables.table4) == 7
    assert len(tables.table5_wacc_headers) == 6
    assert len(tables.table5_g_headers) == 6
    assert len(tables.table5_grid) == 36


# ---------------------------------------------------------------------------
# 16. Invalid g >= WACC / WACC <= g cells handled as n/a (against V2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", TICKERS)
def test_invalid_g_ge_wacc_cells_handled_as_na(ticker):
    wb = Workbook(str(V2_WORKBOOK_PATH))
    tables = locate_sensitivity_tables(wb, ticker)
    na_count = 0
    valid_count = 0
    for (wacc, g), value in tables.table5_grid.items():
        if g >= wacc:
            assert value == "n/a", f"{ticker}: expected n/a at wacc={wacc}, g={g}, got {value!r}"
            na_count += 1
        else:
            assert value != "n/a"
            valid_count += 1
    assert na_count + valid_count == 36
    assert na_count >= 1

    from validation.dcf_reconciliation.recompute import BaseCase, recompute_table5_two_way
    _snapshot, financial_data = load_all()[ticker]
    result = run_dcf_valuation(financial_data, DCFAssumptions())
    base = BaseCase(financial_data, result)
    recomputed = recompute_table5_two_way(base, tables.table5_wacc_headers, tables.table5_g_headers)
    for (wacc, g), wb_value in tables.table5_grid.items():
        rc_value = recomputed[(wacc, g)]
        assert (wb_value == "n/a") == (rc_value == "n/a"), (
            f"{ticker}: n/a pattern mismatch at wacc={wacc}, g={g}: workbook={wb_value!r}, recomputed={rc_value!r}"
        )


# ---------------------------------------------------------------------------
# 17. Exact scalar-comparison counts (Finding 3): 28/28/72/63/36 per table,
#     227 per company, 908 total
# ---------------------------------------------------------------------------

def _run_full_sensitivity_reconciliation():
    from validation.dcf_reconciliation.reconcile import run_sensitivity_reconciliation
    wb = Workbook(str(V2_WORKBOOK_PATH))
    financial_data_by_ticker = {}
    raw_results = {}
    for ticker in TICKERS:
        _snapshot, fd = load_all()[ticker]
        financial_data_by_ticker[ticker] = fd
        raw_results[ticker] = run_dcf_valuation(fd, DCFAssumptions())
    return run_sensitivity_reconciliation(financial_data_by_ticker, raw_results, wb)


@pytest.fixture(scope="module")
def sensitivity_results_v2():
    return _run_full_sensitivity_reconciliation()


def test_scalar_comparison_counts_per_table(sensitivity_results_v2):
    expected = {
        "Table 1 -- WACC": 28,
        "Table 2 -- Terminal growth": 28,
        "Table 3 -- Revenue growth": 72,
        "Table 4 -- Operating margin": 63,
        "Table 5 -- WACC x g two-way grid": 36,
    }
    for ticker in TICKERS:
        rows = sensitivity_coverage_rows(sensitivity_results_v2[ticker])
        actual = {label: scalar for label, _scen, _outs, scalar, _ok in rows}
        assert actual == expected, f"{ticker}: scalar comparison counts mismatch: {actual}"


def test_scalar_comparison_counts_per_company_and_total(sensitivity_results_v2):
    grand_total = 0
    for ticker in TICKERS:
        rows = sensitivity_coverage_rows(sensitivity_results_v2[ticker])
        company_total = sum(scalar for _l, _s, _o, scalar, _ok in rows)
        assert company_total == EXPECTED_SCALAR_COMPARISONS_PER_COMPANY == 227
        grand_total += company_total
    assert grand_total == EXPECTED_SCALAR_COMPARISONS_TOTAL == 908


# ---------------------------------------------------------------------------
# 18. Every Table 1-5 scalar comparison appears in the reconciliation
#     WORKBOOK (not only JSON) -- Finding 4
# ---------------------------------------------------------------------------

def test_all_sensitivity_comparisons_sheet_has_full_coverage():
    xlsx_path = RECONCILIATION_DIR / "dcf_reconciliation.xlsx"
    assert xlsx_path.exists(), "Run `python3 -m validation.dcf_reconciliation.reconcile` first."
    wb = Workbook(str(xlsx_path))
    assert wb.has_sheet("All Sensitivity Comparisons")
    grid = wb.grid("All Sensitivity Comparisons")

    header_row = None
    for (r, c), cell in grid.items():
        if c == 0 and cell.text == "Ticker":
            header_row = r
            break
    assert header_row is not None

    data_rows = [r for (r, c) in grid.keys() if c == 0 and r > header_row]
    data_rows = sorted(set(data_rows))
    assert len(data_rows) == EXPECTED_SCALAR_COMPARISONS_TOTAL == 908

    per_ticker_count = {t: 0 for t in TICKERS}
    per_table_count = {}
    for r in data_rows:
        ticker = grid[(r, 0)].text
        table = grid[(r, 1)].text
        assert ticker in TICKERS
        per_ticker_count[ticker] += 1
        per_table_count[table] = per_table_count.get(table, 0) + 1

    for ticker in TICKERS:
        assert per_ticker_count[ticker] == EXPECTED_SCALAR_COMPARISONS_PER_COMPANY == 227

    expected_per_table_total = {
        "Table 1 -- WACC": 28 * 4, "Table 2 -- Terminal growth": 28 * 4,
        "Table 3 -- Revenue growth": 72 * 4, "Table 4 -- Operating margin": 63 * 4,
        "Table 5 -- WACC x g two-way grid": 36 * 4,
    }
    assert per_table_count == expected_per_table_total


# ---------------------------------------------------------------------------
# 19. PASS/FAIL cells are formulas tied to visible tolerances
# ---------------------------------------------------------------------------

def test_pass_fail_cells_are_live_formulas_referencing_tolerances_sheet():
    xlsx_path = RECONCILIATION_DIR / "dcf_reconciliation.xlsx"
    wb = Workbook(str(xlsx_path))
    grid = wb.grid("Base Reconciliation")

    checked = 0
    for (r, c), cell in grid.items():
        if c == 6 and cell.formula is not None and ("PASS" in cell.formula or "FAIL" in cell.formula):
            assert '"PASS"' in cell.formula and '"FAIL"' in cell.formula
            # The tolerance column (F, same row) must itself be a formula referencing 'Tolerances'!.
            tol_cell = grid.get((r, 5))
            assert tol_cell is not None and tol_cell.formula is not None
            assert "Tolerances" in tol_cell.formula
            checked += 1
    assert checked >= 14 * 4  # at least the 56 base-reconciliation rows


def test_sensitivity_pass_fail_cells_are_formulas():
    xlsx_path = RECONCILIATION_DIR / "dcf_reconciliation.xlsx"
    wb = Workbook(str(xlsx_path))
    grid = wb.grid("All Sensitivity Comparisons")
    checked = 0
    for (r, c), cell in grid.items():
        if c == 9 and cell.formula is not None:
            assert '"PASS"' in cell.formula and '"FAIL"' in cell.formula
            checked += 1
    assert checked > 800  # the large majority of 908 rows have a live formula (na_marker rows are hardcoded text, by design)


# ---------------------------------------------------------------------------
# 19b. M-1 remediation: PASS/FAIL formula cells must carry a correct,
#      non-empty CACHED value -- not just a well-formed formula (independent
#      second-reviewer audit finding M-1). Every check here reads the
#      regenerated dcf_reconciliation.xlsx from disk; none of them touch
#      V1, V2, or the archived Phase 2B evidence.
# ---------------------------------------------------------------------------

_METRIC_LABEL_TO_KEY = {v: k for k, v in METRIC_LABELS.items() if k in REQUIRED_METRICS}
_SECTION_HEADER_RE = re.compile(r"^-- (\w+) \(")


def _pass_fail_formula_cells(wb, sheet_name, col):
    """(row0, Cell) for every PASS/FAIL FORMULA cell in `col` -- excludes
    na_marker rows, which are written as a literal value, never a formula."""
    grid = wb.grid(sheet_name)
    out = [
        (r, cell) for (r, c), cell in grid.items()
        if c == col and cell.formula is not None and ('"PASS"' in cell.formula and '"FAIL"' in cell.formula)
    ]
    return sorted(out, key=lambda rc: rc[0])


def _iter_base_reconciliation_rows(grid):
    """Yields (ticker, metric_key, row0) for every base-metric row in the
    'Base Reconciliation' sheet's grid, discovered from the sheet's own
    section-header ('-- TICKER (Name) --') and metric-label text -- never a
    hardcoded row offset, so this does not silently stop validating anything
    if workbook_builder.py's layout shifts."""
    current_ticker = None
    for (r, c), cell in sorted(grid.items()):
        if c != 0 or cell.text is None:
            continue
        m = _SECTION_HEADER_RE.match(cell.text)
        if m:
            current_ticker = m.group(1)
            continue
        if cell.text in _METRIC_LABEL_TO_KEY:
            yield current_ticker, _METRIC_LABEL_TO_KEY[cell.text], r


def test_base_reconciliation_all_56_pass_fail_cells_have_nonempty_cached_value():
    wb = Workbook(str(RECONCILIATION_DIR / "dcf_reconciliation.xlsx"))
    cells = _pass_fail_formula_cells(wb, "Base Reconciliation", col=6)
    assert len(cells) == len(TICKERS) * len(REQUIRED_METRICS)  # 4 x 14 = 56
    for row0, cell in cells:
        assert cell.text in ("PASS", "FAIL"), (
            f"Base Reconciliation!{cell.ref}: cached value is {cell.text!r}, expected 'PASS' or 'FAIL'."
        )


@pytest.mark.parametrize("ticker", TICKERS)
def test_detail_sheet_all_14_pass_fail_cells_have_nonempty_cached_value(ticker):
    wb = Workbook(str(RECONCILIATION_DIR / "dcf_reconciliation.xlsx"))
    cells = _pass_fail_formula_cells(wb, f"Detail_{ticker}", col=6)
    assert len(cells) == len(REQUIRED_METRICS)
    for row0, cell in cells:
        assert cell.text in ("PASS", "FAIL"), f"Detail_{ticker}!{cell.ref}: cached value is {cell.text!r}."


def test_all_sensitivity_comparisons_pass_fail_cells_have_nonempty_cached_value(sensitivity_results_v2):
    wb = Workbook(str(RECONCILIATION_DIR / "dcf_reconciliation.xlsx"))
    cells = _pass_fail_formula_cells(wb, "All Sensitivity Comparisons", col=9)

    # Expected formula-row count = total scalar comparisons minus na_marker
    # rows (those are literal text, never a formula) -- derived from the
    # actual reconciliation data on this run, not a hardcoded literal.
    total_count = 0
    na_marker_count = 0
    for ticker in TICKERS:
        for _tl, _tk, _s, _ok, cell_data in iter_all_scalar_comparisons(sensitivity_results_v2[ticker]):
            total_count += 1
            if cell_data.get("kind") == "na_marker":
                na_marker_count += 1
    assert total_count == EXPECTED_SCALAR_COMPARISONS_TOTAL
    assert len(cells) == total_count - na_marker_count
    for row0, cell in cells:
        assert cell.text in ("PASS", "FAIL"), (
            f"All Sensitivity Comparisons!{cell.ref}: cached value is {cell.text!r}."
        )


def test_sensitivity_reconciliation_table1_table2_pass_fail_cells_have_nonempty_cached_value(sensitivity_results_v2):
    wb = Workbook(str(RECONCILIATION_DIR / "dcf_reconciliation.xlsx"))
    cells = _pass_fail_formula_cells(wb, "Sensitivity Reconciliation", col=6)
    total = 0
    na = 0
    for ticker in TICKERS:
        sens = sensitivity_results_v2[ticker]
        for tkey in ("table1_wacc", "table2_terminal_growth"):
            for r in sens[tkey]["rows"]:
                total += 1
                if r["intrinsic_value_per_share"]["kind"] == "na_marker":
                    na += 1
    assert len(cells) == total - na
    for row0, cell in cells:
        assert cell.text in ("PASS", "FAIL")


def test_base_reconciliation_cached_verdicts_match_independent_recomputation():
    """For each of the 56 base-reconciliation rows: reads that row's OWN
    Codebase/Workbook values directly from the sheet (not from Python
    memory or a fixture), independently recomputes PASS/FAIL via
    `compare_metric` -- the same tolerance table as the 'Tolerances' sheet,
    already cross-verified elsewhere -- and confirms it matches the cell's
    CACHED PASS/FAIL text exactly. This is the specific "recompute each
    verdict from workbook values and tolerance, compare to cached result"
    check for M-1."""
    wb = Workbook(str(RECONCILIATION_DIR / "dcf_reconciliation.xlsx"))
    grid = wb.grid("Base Reconciliation")
    rows = list(_iter_base_reconciliation_rows(grid))
    assert len(rows) == len(TICKERS) * len(REQUIRED_METRICS)
    for ticker, metric, r in rows:
        codebase_val = grid[(r, 1)].numeric
        workbook_val = grid[(r, 2)].numeric
        pf_cell = grid[(r, 6)]
        assert codebase_val is not None and workbook_val is not None
        independent = compare_metric(metric, codebase_val, workbook_val)
        expected_text = "PASS" if independent.passed else "FAIL"
        assert pf_cell.text == expected_text, (
            f"{ticker} {metric} (Base Reconciliation row {r + 1}): cached {pf_cell.text!r}, "
            f"independently recomputed {expected_text!r} from codebase={codebase_val!r} workbook={workbook_val!r}."
        )


@pytest.mark.parametrize("ticker", TICKERS)
def test_detail_sheet_cached_verdicts_match_independent_recomputation(ticker):
    wb = Workbook(str(RECONCILIATION_DIR / "dcf_reconciliation.xlsx"))
    grid = wb.grid(f"Detail_{ticker}")
    checked = 0
    for (r, c), cell in grid.items():
        if c == 0 and cell.text in _METRIC_LABEL_TO_KEY:
            metric = _METRIC_LABEL_TO_KEY[cell.text]
            codebase_val = grid[(r, 1)].numeric
            workbook_val = grid[(r, 2)].numeric
            pf_cell = grid[(r, 6)]
            independent = compare_metric(metric, codebase_val, workbook_val)
            expected_text = "PASS" if independent.passed else "FAIL"
            assert pf_cell.text == expected_text, f"Detail_{ticker}!{pf_cell.ref}: cached {pf_cell.text!r} != recomputed {expected_text!r}"
            checked += 1
    assert checked == len(REQUIRED_METRICS)


def test_all_sensitivity_comparisons_cached_verdicts_match_independent_recomputation():
    """Reads Codebase/Workbook/Tolerance directly from each non-na_marker
    row of 'All Sensitivity Comparisons' and independently recomputes
    PASS/FAIL via `compare_monetary` (every sensitivity output metric is
    dollar- or per-share-valued -- never a percentage-point rate; see
    `coverage.OUTPUT_METRIC_VALUE_KIND`), confirming it matches the row's
    own cached verdict."""
    wb = Workbook(str(RECONCILIATION_DIR / "dcf_reconciliation.xlsx"))
    grid = wb.grid("All Sensitivity Comparisons")
    max_row = max(r for r, _c in grid.keys())
    checked = 0
    na_seen = 0
    for r in range(0, max_row + 1):
        out_metric_cell = grid.get((r, 3))
        pf_cell = grid.get((r, 9))
        if out_metric_cell is None or out_metric_cell.text not in OUTPUT_METRIC_LABELS.values() or pf_cell is None:
            continue
        if pf_cell.formula is None:
            na_seen += 1
            continue
        codebase_val = grid[(r, 4)].numeric
        workbook_val = grid[(r, 5)].numeric
        tol_val = grid[(r, 8)].numeric
        assert codebase_val is not None and workbook_val is not None and tol_val is not None, (
            f"All Sensitivity Comparisons row {r + 1}: missing numeric codebase/workbook/tolerance value."
        )
        independent = compare_monetary("cell", codebase_val, workbook_val, tol_val)
        expected_text = "PASS" if independent.passed else "FAIL"
        assert pf_cell.text == expected_text, (
            f"All Sensitivity Comparisons row {r + 1}: cached {pf_cell.text!r}, recomputed {expected_text!r} "
            f"from codebase={codebase_val!r} workbook={workbook_val!r} tolerance={tol_val!r}."
        )
        checked += 1
    assert na_seen >= 1
    assert checked >= 800


def test_perturbed_value_produces_cached_fail():
    """Negative control: calls the REAL `_comparison_row` (not a copy or a
    reimplementation) with a codebase value deliberately far outside
    tolerance of the workbook value, and confirms the resulting cached
    PASS/FAIL cell is 'FAIL' -- proving the cache genuinely reflects the
    comparison rather than defaulting to a fixed answer."""
    from validation.dcf_reconciliation.workbook_builder import _comparison_row, _styles
    from validation.dcf_reconciliation.xlsx_writer import Workbook as XWorkbook

    xwb = XWorkbook()
    styles = _styles(xwb)
    s = xwb.add_sheet("Probe")
    _comparison_row(
        s, 0, styles, "Probe Metric", codebase_val=100.0, workbook_val=50.0,
        tol_ref="'Tolerances'!$B$3", tol_value=0.0001, value_kind="dollar",
    )
    pf_cell = s.cells[(0, 6)]
    assert pf_cell.cached == "FAIL"
    assert pf_cell.formula is not None and '"PASS"' in pf_cell.formula and '"FAIL"' in pf_cell.formula

    # Companion: a genuinely matching pair caches PASS, not a fixed default.
    s2 = xwb.add_sheet("Probe2")
    _comparison_row(
        s2, 0, styles, "Probe Metric", codebase_val=100.0, workbook_val=100.0001,
        tol_ref="'Tolerances'!$B$3", tol_value=0.01, value_kind="dollar",
    )
    assert s2.cells[(0, 6)].cached == "PASS"


def test_deliberately_incorrect_cached_verdict_is_detected():
    """Negative control: on a TEMP COPY of the regenerated
    dcf_reconciliation.xlsx, corrupts exactly one PASS/FAIL cell's CACHED
    value to the wrong verdict (formula text and the codebase/workbook/
    tolerance cells are left untouched), and confirms the independent-
    recomputation check catches the mismatch -- proving M-1's fix is
    actually checked, not merely asserted to be correct once at write time."""
    xlsx_path = RECONCILIATION_DIR / "dcf_reconciliation.xlsx"
    wb_orig = Workbook(str(xlsx_path))
    grid_orig = wb_orig.grid("Base Reconciliation")

    row0, col0 = next(
        (r, c) for (r, c), cell in sorted(grid_orig.items())
        if c == 6 and cell.formula is not None
    )
    cell_ref = f"{col_index_to_letters(col0)}{row0 + 1}"
    true_text = grid_orig[(row0, col0)].text
    assert true_text in ("PASS", "FAIL")
    wrong_text = "FAIL" if true_text == "PASS" else "PASS"

    tmp_path, tmp_dir = _make_temp_copy_with_cell_edit(
        xlsx_path, "Base Reconciliation", cell_ref, mode="set_value", new_value=wrong_text
    )
    try:
        wb = Workbook(str(tmp_path))
        grid = wb.grid("Base Reconciliation")
        corrupted_text = grid[(row0, col0)].text
        assert corrupted_text == wrong_text, "Test setup failed: corruption was not applied."

        metric = _METRIC_LABEL_TO_KEY[grid[(row0, 0)].text]
        codebase_val = grid[(row0, 1)].numeric
        workbook_val = grid[(row0, 2)].numeric
        independent = compare_metric(metric, codebase_val, workbook_val)
        expected_text = "PASS" if independent.passed else "FAIL"

        assert corrupted_text != expected_text, (
            "The independent-recomputation check did not detect the deliberately "
            "corrupted cached PASS/FAIL value -- it should have."
        )
    finally:
        shutil.rmtree(tmp_dir)
    assert _sha256_file(xlsx_path) == _sha256_file(xlsx_path)  # sanity: reading never mutates the original
    assert xlsx_path.exists()


def test_na_marker_rows_remain_literal_and_correctly_flagged():
    """Verifies M-1's fix left the pre-existing 'n/a' (invalid WACC<=g /
    g>=WACC combination) rows exactly as they were: literal text (never a
    formula) in every comparison column, and a literal PASS/FAIL value
    that is still present and non-empty."""
    wb = Workbook(str(RECONCILIATION_DIR / "dcf_reconciliation.xlsx"))
    grid = wb.grid("All Sensitivity Comparisons")
    na_rows = [r for (r, c), cell in grid.items() if c == 6 and cell.text == "n/a"]
    assert len(na_rows) >= 1
    for r in na_rows:
        codebase_cell = grid[(r, 4)]
        workbook_cell = grid[(r, 5)]
        abs_diff_cell = grid[(r, 6)]
        rel_diff_cell = grid[(r, 7)]
        pf_cell = grid[(r, 9)]
        assert codebase_cell.text == "n/a"
        assert workbook_cell.text == "n/a"
        assert abs_diff_cell.formula is None, f"row {r + 1}: na_marker Absolute Difference should be literal, not a formula."
        assert rel_diff_cell.text == "n/a"
        assert pf_cell.formula is None, f"row {r + 1}: na_marker PASS/FAIL should be a literal value, not a formula."
        assert pf_cell.text in ("PASS", "FAIL"), f"row {r + 1}: na_marker PASS/FAIL cached text is {pf_cell.text!r}."


def test_pass_fail_formula_references_own_row_cells():
    """Confirms each PASS/FAIL formula (Base Reconciliation sheet) only
    references cells in ITS OWN row -- not a copy-pasted reference to a
    different row's Absolute Difference / Tolerance cells, which caching a
    fixed cell value could otherwise mask."""
    wb = Workbook(str(RECONCILIATION_DIR / "dcf_reconciliation.xlsx"))
    cells = _pass_fail_formula_cells(wb, "Base Reconciliation", col=6)
    assert len(cells) == len(TICKERS) * len(REQUIRED_METRICS)
    for row0, cell in cells:
        own_row = str(row0 + 1)
        refs_in_formula = re.findall(r"[A-Z]+(\d+)", cell.formula)
        assert refs_in_formula, f"{cell.ref}: no cell references found in formula {cell.formula!r}"
        assert all(ref_row == own_row for ref_row in refs_in_formula), (
            f"{cell.ref}: formula {cell.formula!r} references a row other than its own ({own_row})."
        )


def test_no_new_custom_number_formats_introduced_by_pass_fail_fix():
    """M-1's fix touches PASS/FAIL cell caching and (for real, non-na_marker
    rows) applies the pre-existing pass/fail fill-color styles -- neither
    should add a new custom number format. Confirms the collision-free
    allocator (Track A Phase 2C Finding 2) still produces exactly the same
    4 distinct custom formats with no duplicate IDs."""
    xlsx_path = RECONCILIATION_DIR / "dcf_reconciliation.xlsx"
    with zipfile.ZipFile(xlsx_path) as z:
        styles_xml = z.read("xl/styles.xml").decode()
    fmts = re.findall(r'<numFmt numFmtId="(\d+)" formatCode="([^"]*)"/>', styles_xml)
    ids = [fid for fid, _code in fmts]
    assert len(ids) == len(set(ids)), f"Duplicate numFmtId allocated: {ids}"
    assert len(fmts) == 4, f"Expected exactly 4 distinct custom number formats (unchanged baseline), found {len(fmts)}: {fmts}"
    codes = {code for _fid, code in fmts}
    assert codes == {"0.0000%", "$#,##0;[Red]($#,##0);-", "$0.00;[Red]($0.00);-", "#,##0.0000"}


# ---------------------------------------------------------------------------
# 20. OOXML number-format tests (Finding 2)
# ---------------------------------------------------------------------------

class TestNumberFormatAllocator:
    def test_unique_custom_format_ids(self):
        from validation.dcf_reconciliation.xlsx_writer import Workbook as XWorkbook, Style
        wb = XWorkbook()
        wb.add_style(Style("pct", num_fmt="0.0000%"))
        wb.add_style(Style("dollar", num_fmt="$#,##0;[Red]($#,##0);-"))
        wb.add_style(Style("share", num_fmt="$0.00;[Red]($0.00);-"))
        wb.add_style(Style("diag", num_fmt="#,##0.0000"))
        wb.add_style(Style("pct2", num_fmt="0.0000%"))  # reuses the SAME format string -- must reuse the SAME id
        xml = wb._build_styles_xml()
        ids = re.findall(r'<numFmt numFmtId="(\d+)"', xml)
        assert len(ids) == len(set(ids)), f"Duplicate custom numFmtId allocated: {ids}"

    def test_no_two_distinct_formats_share_an_id(self):
        from validation.dcf_reconciliation.xlsx_writer import Workbook as XWorkbook, Style
        wb = XWorkbook()
        wb.add_style(Style("pct", num_fmt="0.0000%"))
        wb.add_style(Style("dollar", num_fmt="$#,##0;[Red]($#,##0);-"))
        xml = wb._build_styles_xml()
        fmts = re.findall(r'<numFmt numFmtId="(\d+)" formatCode="([^"]*)"/>', xml)
        by_id = {}
        for fid, code in fmts:
            assert fid not in by_id or by_id[fid] == code, f"numFmtId {fid} maps to two different formats: {by_id.get(fid)!r} and {code!r}"
            by_id[fid] = code

    def test_generated_reconciliation_workbook_monetary_cells_not_percentage_formatted(self):
        """Confirms the actual FCF Year 1 cell (a dollar amount, hundreds of
        millions/billions) in the generated dcf_reconciliation.xlsx does NOT use
        the same numFmtId as a known rate cell (Revenue CAGR)."""
        xlsx_path = RECONCILIATION_DIR / "dcf_reconciliation.xlsx"
        with zipfile.ZipFile(xlsx_path) as z:
            styles_xml = z.read("xl/styles.xml").decode()
            sheet_xml = z.read("xl/worksheets/sheet3.xml").decode()  # Base Reconciliation
        m = re.search(r'<cellXfs count="\d+">(.*?)</cellXfs>', styles_xml, re.DOTALL)
        xfs = re.findall(r'<xf numFmtId="(\d+)"', m.group(1))

        rate_style = re.search(r'<c r="B6"( s="(\d+)")?[^>]*>', sheet_xml)
        dollar_style = re.search(r'<c r="B9"( s="(\d+)")?[^>]*>', sheet_xml)
        assert rate_style and dollar_style
        rate_s = int(rate_style.group(2) or 0)
        dollar_s = int(dollar_style.group(2) or 0)
        rate_fmt_id = xfs[rate_s]
        dollar_fmt_id = xfs[dollar_s]
        assert rate_fmt_id != dollar_fmt_id, "Rate cell (B6) and dollar cell (B9) share the same number format ID."

        numfmts = dict(re.findall(r'<numFmt numFmtId="(\d+)" formatCode="([^"]*)"/>', styles_xml))
        assert "%" in numfmts.get(rate_fmt_id, "%"), f"B6 (rate) format code doesn't look like a percentage: {numfmts.get(rate_fmt_id)!r}"
        assert "$" in numfmts.get(dollar_fmt_id, "$"), f"B9 (dollar) format code doesn't look like a dollar amount: {numfmts.get(dollar_fmt_id)!r}"
        assert "%" not in numfmts.get(dollar_fmt_id, ""), f"B9 (dollar/FCF) format code contains a percentage symbol: {numfmts.get(dollar_fmt_id)!r}"

    def test_count_cells_use_integer_format(self):
        xlsx_path = RECONCILIATION_DIR / "dcf_reconciliation.xlsx"
        wb = Workbook(str(xlsx_path))
        grid = wb.grid("Detail_MSFT")
        # Locate the "Scenario Rows" header and the numeric count cell directly below it.
        header_hit = None
        for (r, c), cell in grid.items():
            if cell.text == "Scenario Rows":
                header_hit = (r, c)
                break
        assert header_hit is not None
        r, c = header_hit
        count_cell = grid.get((r + 1, c))
        assert count_cell is not None and count_cell.numeric is not None
        assert count_cell.numeric == int(count_cell.numeric)  # a genuine whole-number count

    def test_rate_driver_cells_in_sensitivity_tables_display_as_percentages(self):
        xlsx_path = RECONCILIATION_DIR / "dcf_reconciliation.xlsx"
        with zipfile.ZipFile(xlsx_path) as z:
            names = z.namelist()
            content = {n: z.read(n) for n in names}
        styles_xml = content["xl/styles.xml"].decode()
        sens_part = _sheet_part_path(content, "Sensitivity Reconciliation")
        sheet_xml = content[sens_part].decode()

        m = re.search(r'<cellXfs count="\d+">(.*?)</cellXfs>', styles_xml, re.DOTALL)
        xfs = re.findall(r'<xf numFmtId="(\d+)"', m.group(1))
        numfmts = dict(re.findall(r'<numFmt numFmtId="(\d+)" formatCode="([^"]*)"/>', styles_xml))

        # Find at least one WACC scenario value cell (column A, a decimal like 0.05..0.20) in the
        # Table 1 sub-sections and confirm its format code is a percentage, not raw General.
        found_pct = False
        for m2 in re.finditer(r'<c r="A(\d+)"( s="(\d+)")?[^>]*><v>(0\.0[5-9]|0\.1\d|0\.20)</v></c>', sheet_xml):
            s_idx = int(m2.group(3) or 0)
            fmt_id = xfs[s_idx]
            code = numfmts.get(fmt_id, "General")
            if "%" in code:
                found_pct = True
                break
        assert found_pct, "No WACC scenario cell in Sensitivity Reconciliation was found with a percentage format."


# ---------------------------------------------------------------------------
# 21. Formula-error scans
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("xlsx_path", [
    RECONCILIATION_DIR / "dcf_reconciliation.xlsx",
    V2_WORKBOOK_PATH,
    V1_WORKBOOK_PATH,
])
def test_no_formula_error_markers(xlsx_path):
    assert xlsx_path.exists()
    wb = Workbook(str(xlsx_path))
    offenders = []
    for name in wb.sheet_names:
        grid = wb.grid(name)
        for (r, c), cell in grid.items():
            text = cell.text
            if text and any(marker in text for marker in FORMULA_ERROR_MARKERS):
                offenders.append((name, cell.ref, text))
    assert not offenders, f"Formula-error markers found: {offenders[:10]}"


# ---------------------------------------------------------------------------
# 22. Reconciliation manifest hashes every output artifact
# ---------------------------------------------------------------------------

def test_reconciliation_manifest_hashes_every_output_artifact():
    manifest_path = RECONCILIATION_DIR / "reconciliation_manifest.json"
    assert manifest_path.exists(), "Run `python3 -m validation.dcf_reconciliation.reconcile` first."
    manifest = json.loads(manifest_path.read_text())

    expected_artifacts = {
        "codebase_outputs.json",
        "reconciliation_results.json",
        "reconciliation_report.md",
        "dcf_reconciliation.xlsx",
    }
    assert set(manifest["artifacts"].keys()) == expected_artifacts
    assert "reconciliation_manifest.json" not in manifest["artifacts"]
    for name, expected_hash in manifest["artifacts"].items():
        actual_hash = _sha256_file(RECONCILIATION_DIR / name)
        assert actual_hash == expected_hash, f"{name}: manifest hash does not match the file on disk."
    assert manifest["v1_workbook_sha256"] == EXPECTED_V1_WORKBOOK_SHA256
    assert manifest["v2_workbook_sha256"] == EXPECTED_V2_WORKBOOK_SHA256


# ---------------------------------------------------------------------------
# 22b. H-1 remediation: required evidence workbooks must not be git-ignored
#
# Read-only checks only -- never runs `git add`/`git rm`/anything that
# mutates the index or working tree. Confirms the .gitignore exceptions
# added for Track A Phase 2C's H-1 finding actually take effect, and that
# they are narrowly scoped (no other workbook, and *.xlsx in general, is
# silently re-included).
# ---------------------------------------------------------------------------

REQUIRED_EVIDENCE_PATHS = [
    RECONCILIATION_DIR / "dcf_reconciliation.xlsx",
    ARCHIVE_DIR / "dcf_reconciliation.xlsx",
    INDEPENDENT_DIR / "independent_dcf_validation_v2.xlsx",
]


def _git_check_ignore_quiet(path: Path) -> int:
    """Returns the exit code of `git check-ignore -q <path>` (0 = ignored,
    1 = not ignored, >1 = error). Uses -q (no -v): -v's exit-code semantics
    differ (it can exit 0 merely because *some* pattern, including a
    negation, matched), so -q is the unambiguous form for this assertion."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        cwd=str(REPO_ROOT), capture_output=True,
    )
    return result.returncode


@pytest.mark.parametrize("path", REQUIRED_EVIDENCE_PATHS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_required_evidence_paths_are_not_git_ignored(path):
    assert path.exists(), f"{path}: required evidence file is missing entirely."
    rc = _git_check_ignore_quiet(path)
    assert rc == 1, (
        f"{path}: expected git check-ignore to report 'not ignored' (exit 1), got exit {rc}. "
        "If this is 0, the .gitignore exception for this exact path was removed or broken."
    )


@pytest.mark.parametrize("path", REQUIRED_EVIDENCE_PATHS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_required_evidence_paths_appear_as_trackable_in_git_status(path):
    """Cross-check independent of check-ignore's exit-code semantics: an
    ignored file never appears in `git ls-files --others --exclude-standard`
    (untracked-and-not-ignored) or `git ls-files` (already tracked)."""
    rel = str(path.relative_to(REPO_ROOT))
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", rel],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    ).stdout.strip()
    tracked = subprocess.run(
        ["git", "ls-files", "--", rel],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    ).stdout.strip()
    assert untracked == rel or tracked == rel, (
        f"{rel}: not visible to git as either untracked-and-not-ignored or tracked -- "
        "it is being silently excluded."
    )


def test_general_xlsx_gitignore_rule_still_applies_to_other_workbooks():
    """Confirms the H-1 fix is narrow: an arbitrary .xlsx file elsewhere in
    the repo (not one of the three named evidence paths) is still ignored
    by the blanket `*.xlsx` rule -- the exception list was not widened into
    a general re-allow."""
    tmp_dir = tempfile.mkdtemp(prefix="gitignore_scope_check_", dir=str(REPO_ROOT / "validation"))
    try:
        probe = Path(tmp_dir) / "some_other_workbook.xlsx"
        probe.write_bytes(b"not a real workbook, just a gitignore-scope probe")
        rc = _git_check_ignore_quiet(probe)
        assert rc == 0, (
            f"{probe}: expected the general *.xlsx rule to still ignore an unrelated workbook "
            f"(exit 0), got exit {rc} -- the H-1 .gitignore exception may have been widened "
            "beyond the three named evidence paths."
        )
    finally:
        shutil.rmtree(tmp_dir)


def test_gitignore_exceptions_name_exactly_the_three_required_paths():
    """Static check on .gitignore's own text: exactly three `!`-exception
    lines exist for *.xlsx evidence, and they name exactly the three
    required paths -- not a directory-wide or wildcard re-inclusion."""
    gitignore_text = (REPO_ROOT / ".gitignore").read_text()
    exception_lines = [
        line.strip() for line in gitignore_text.splitlines()
        if line.strip().startswith("!") and line.strip().endswith(".xlsx")
    ]
    expected = {
        "!/validation/dcf_reconciliation/dcf_reconciliation.xlsx",
        "!/validation/dcf_reconciliation/history/phase2b_initial_no_go/dcf_reconciliation.xlsx",
        "!/validation/independent_dcf/independent_dcf_validation_v2.xlsx",
    }
    assert set(exception_lines) == expected, (
        f".gitignore's *.xlsx exception lines are {set(exception_lines)!r}, expected exactly {expected!r}."
    )


# ---------------------------------------------------------------------------
# 23. Original validation directory (V1 + V2 + snapshots) remains unchanged;
#     archived Phase 2B evidence remains immutable during regeneration
# ---------------------------------------------------------------------------

def test_original_validation_directory_and_archive_unchanged_by_regeneration():
    before_dir = _sha256_dir(INDEPENDENT_DIR)
    before_archive = _sha256_dir(ARCHIVE_DIR)

    from validation.dcf_reconciliation import reconcile as reconcile_module
    reconcile_module.main()

    after_dir = _sha256_dir(INDEPENDENT_DIR)
    after_archive = _sha256_dir(ARCHIVE_DIR)
    assert before_dir == after_dir, "validation/independent_dcf/ changed as a side effect of running reconcile.py."
    assert before_archive == after_archive, (
        "validation/dcf_reconciliation/history/phase2b_initial_no_go/ (archived Phase 2B evidence) "
        "changed as a side effect of regenerating the live reconciliation artifacts -- the archive "
        "must be immutable."
    )
    assert _sha256_file(V1_WORKBOOK_PATH) == EXPECTED_V1_WORKBOOK_SHA256
    assert _sha256_file(V2_WORKBOOK_PATH) == EXPECTED_V2_WORKBOOK_SHA256


def test_rerunning_reconcile_produces_byte_identical_output():
    """Deterministic regeneration: two consecutive runs against an unchanged
    repository must produce byte-identical artifacts."""
    from validation.dcf_reconciliation import reconcile as reconcile_module
    tracked = ["codebase_outputs.json", "reconciliation_results.json",
               "reconciliation_report.md", "dcf_reconciliation.xlsx", "reconciliation_manifest.json"]
    reconcile_module.main()
    first = {name: _sha256_file(RECONCILIATION_DIR / name) for name in tracked}
    reconcile_module.main()
    second = {name: _sha256_file(RECONCILIATION_DIR / name) for name in tracked}
    assert first == second


# ---------------------------------------------------------------------------
# 24. No external-service guard fires (no network access occurs)
# ---------------------------------------------------------------------------

def test_no_external_service_guard_fires():
    """tests/conftest.py replaces socket.socket.connect with a version that
    always raises RuntimeError, for the whole pytest session. Simply
    succeeding here (adapter + a full DCF run, offline) proves nothing in
    this reconciliation path attempted a real network connection."""
    for ticker in TICKERS:
        _snapshot, financial_data = load_all()[ticker]
        result = run_dcf_valuation(financial_data, DCFAssumptions())
        assert result["intrinsic_value_per_share"] is not None
