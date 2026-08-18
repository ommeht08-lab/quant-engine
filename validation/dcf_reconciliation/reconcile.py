"""
Track A Phase 2B -- main reconciliation orchestrator.

Runs entirely offline against:
  - the committed, frozen validation/independent_dcf/snapshots/*.json (via adapter.py)
  - the committed, frozen validation/independent_dcf/independent_dcf_validation.xlsx (read-only, via xlsx_reader.py)

and produces every artifact required by Track A Phase 2B, all under
validation/dcf_reconciliation/ (validation/independent_dcf/ is never
written to). Deterministic: re-running this script against an unchanged
repository produces byte-identical JSON/Markdown/XLSX output (no
wall-clock content is embedded in any hashed artifact).

Usage:
    python3 -m validation.dcf_reconciliation.reconcile
"""
import hashlib
import json
import subprocess
from pathlib import Path

from src.dcf_model.dcf import DCFAssumptions, run_dcf_valuation

from validation.dcf_reconciliation.adapter import TICKERS, load_all
from validation.dcf_reconciliation.capture import (
    INDEPENDENT_WORKBOOK_PATH,
    build_codebase_outputs,
)
from validation.dcf_reconciliation.cell_map import (
    BASE_METRIC_ORDER,
    METRIC_LABELS,
    REQUIRED_METRICS,
)
from validation.dcf_reconciliation.compare import compare_metric
from validation.dcf_reconciliation.recompute import (
    BaseCase,
    recompute_table1_wacc,
    recompute_table2_terminal_growth,
    recompute_table3_revenue_growth,
    recompute_table4_operating_margin,
    recompute_table5_two_way,
)
from validation.dcf_reconciliation.sensitivity_map import locate_sensitivity_tables
from validation.dcf_reconciliation.cell_map import locate_dcf_metrics
from validation.dcf_reconciliation.xlsx_reader import Workbook

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

SENSITIVITY_TOLERANCE_IVPS = 0.005      # +/- 0.5%
SENSITIVITY_TOLERANCE_FCF = 0.001       # +/- 0.1%
SENSITIVITY_TOLERANCE_TV_EV_EQV = 0.002  # +/- 0.2%


def _json_default(o):
    """Handles numpy scalar types (np.bool_, np.float64, np.int64, ...) that
    can appear in comparison results because production DCF math runs
    through pandas/numpy -- never a silent data change, just a type
    normalization for JSON serialization."""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args) -> str:
    return subprocess.check_output(["git", *args], cwd=str(REPO_ROOT), text=True).strip()


# ---------------------------------------------------------------------------
# Base-case reconciliation (Phase 5)
# ---------------------------------------------------------------------------

def run_base_reconciliation(raw_results: dict, wb: Workbook) -> dict:
    """`raw_results` is {ticker: run_dcf_valuation(...) return dict} -- the
    live pandas-based result, NOT the JSON-serialized codebase_outputs
    shape. Returns {ticker: {"comparisons": [...], "first_divergence": str|None,
    "diagnostic_wacc_raw": {...}}}"""
    out = {}
    for ticker in TICKERS:
        r = raw_results[ticker]
        fcf = r["fcf_projection"]["fcf"]
        codebase_values = {
            "revenue_cagr": r["revenue_growth_rate"],
            "operating_margin": r["operating_margin"],
            "wacc": r["wacc"],
            "fcf_year_1": float(fcf.loc[1]),
            "fcf_year_2": float(fcf.loc[2]),
            "fcf_year_3": float(fcf.loc[3]),
            "fcf_year_4": float(fcf.loc[4]),
            "fcf_year_5": float(fcf.loc[5]),
            "terminal_value": r["terminal_value"],
            "pv_explicit_fcf": float(r["pv_fcf"].sum()),
            "pv_terminal_value": r["pv_terminal_value"],
            "enterprise_value": r["enterprise_value"],
            "equity_value": r["equity_value"],
            "intrinsic_value_per_share": r["intrinsic_value_per_share"],
        }
        workbook_metrics = locate_dcf_metrics(wb, ticker)

        comparisons = []
        first_divergence = None
        for metric in REQUIRED_METRICS:
            cb_val = codebase_values[metric]
            wb_metric = workbook_metrics[metric]
            result = compare_metric(metric, cb_val, wb_metric.cached_value)
            row = result.to_dict()
            row["label"] = METRIC_LABELS[metric]
            row["workbook_sheet"] = wb_metric.sheet
            row["workbook_cell"] = wb_metric.cell_ref
            row["workbook_formula"] = wb_metric.formula
            comparisons.append(row)
            if not result.passed and first_divergence is None:
                first_divergence = metric

        wacc_raw_wb = workbook_metrics["wacc_raw"]
        out[ticker] = {
            "comparisons": comparisons,
            "first_divergence": first_divergence,
            "diagnostic_wacc_raw": {
                "workbook_value": wacc_raw_wb.cached_value,
                "workbook_sheet": wacc_raw_wb.sheet,
                "workbook_cell": wacc_raw_wb.cell_ref,
                "note": "Pre-clamp WACC; calculate_wacc (src/dcf_model/dcf.py) only returns "
                        "the post-clamp value, so there is no codebase field to compare this "
                        "against. Informational only -- excluded from PASS/FAIL and from "
                        "REQUIRED_METRICS.",
            },
        }
    return out


def directional_bias_analysis(base_results: dict) -> dict:
    """For each required metric, collect the SIGNED (codebase - workbook)
    difference across all four companies and flag whether every company's
    difference for that metric shares the same nonzero sign -- a
    within-tolerance bias that is consistent in direction across unrelated
    companies is more concerning than random per-company noise."""
    out = {}
    for metric in REQUIRED_METRICS:
        signed_diffs = {}
        for ticker in TICKERS:
            row = next(c for c in base_results[ticker]["comparisons"] if c["metric"] == metric)
            signed_diffs[ticker] = row["codebase_value"] - row["workbook_value"]
        nonzero = [v for v in signed_diffs.values() if v != 0]
        signs = {1 if v > 0 else -1 for v in nonzero}
        consistent_bias = len(nonzero) >= 2 and len(signs) == 1
        out[metric] = {
            "signed_differences": signed_diffs,
            "consistent_directional_bias": consistent_bias,
        }
    return out


# ---------------------------------------------------------------------------
# Sensitivity reconciliation (Phase 6)
# ---------------------------------------------------------------------------

def _compare_scalar(codebase_value, workbook_value, tolerance):
    """n/a-aware scalar comparator for sensitivity cells: exact string match
    required if either side is the "n/a" marker, else relative tolerance."""
    if isinstance(codebase_value, str) or isinstance(workbook_value, str):
        passed = codebase_value == workbook_value
        return {"codebase_value": codebase_value, "workbook_value": workbook_value,
                "passed": passed, "kind": "na_marker"}
    if workbook_value == 0:
        passed = abs(codebase_value - workbook_value) <= 1e-6
        return {"codebase_value": codebase_value, "workbook_value": workbook_value,
                "passed": passed, "kind": "absolute_zero_carveout"}
    rel = abs(codebase_value - workbook_value) / abs(workbook_value)
    return {"codebase_value": codebase_value, "workbook_value": workbook_value,
            "passed": rel <= tolerance, "relative_difference": rel, "tolerance": tolerance,
            "kind": "relative_fraction"}


def _monotonic(pairs, direction):
    """pairs: list of (x, y) with y numeric only (n/a rows excluded), sorted by x ascending.
    direction: 'increasing' or 'decreasing'. Returns True if y is non-strictly monotonic
    in that direction across the whole sequence."""
    ys = [y for _x, y in pairs]
    if len(ys) < 2:
        return True
    if direction == "increasing":
        return all(b >= a - 1e-9 for a, b in zip(ys, ys[1:]))
    return all(b <= a + 1e-9 for a, b in zip(ys, ys[1:]))


def run_sensitivity_reconciliation(financial_data_by_ticker: dict, raw_results: dict, wb: Workbook) -> dict:
    """`raw_results` is {ticker: run_dcf_valuation(...) return dict} -- the
    live pandas-based result, NOT the JSON-serialized codebase_outputs shape."""
    out = {}
    for ticker in TICKERS:
        financial_data = financial_data_by_ticker[ticker]
        result = raw_results[ticker]
        base = BaseCase(financial_data, result)
        wb_tables = locate_sensitivity_tables(wb, ticker)

        ticker_out = {}

        # ---- Table 1: WACC ----
        wacc_values = [row.wacc for row in wb_tables.table1]
        recomputed1 = recompute_table1_wacc(base, wacc_values)
        rows = []
        for wb_row, my in zip(wb_tables.table1, recomputed1):
            rows.append({
                "wacc": wb_row.wacc,
                "terminal_value": _compare_scalar(my["terminal_value"], wb_row.terminal_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "enterprise_value": _compare_scalar(my["enterprise_value"], wb_row.enterprise_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "equity_value": _compare_scalar(my["equity_value"], wb_row.equity_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "intrinsic_value_per_share": _compare_scalar(my["intrinsic_value_per_share"], wb_row.intrinsic_value_per_share, SENSITIVITY_TOLERANCE_IVPS),
            })
        valid_pairs = [(r["wacc"], r["intrinsic_value_per_share"]["workbook_value"])
                       for r in rows if not isinstance(r["intrinsic_value_per_share"]["workbook_value"], str)]
        ticker_out["table1_wacc"] = {
            "rows": rows,
            "all_passed": all(r[k]["passed"] for r in rows for k in
                               ("terminal_value", "enterprise_value", "equity_value", "intrinsic_value_per_share")),
            "workbook_monotonic_decreasing": _monotonic(valid_pairs, "decreasing"),
            "recomputed_monotonic_decreasing": _monotonic(
                [(w, my["intrinsic_value_per_share"]) for w, my in zip(wacc_values, recomputed1)
                 if not isinstance(my["intrinsic_value_per_share"], str)], "decreasing"),
        }

        # ---- Table 2: terminal growth ----
        g_values = [row.terminal_growth for row in wb_tables.table2]
        recomputed2 = recompute_table2_terminal_growth(base, g_values)
        rows = []
        for wb_row, my in zip(wb_tables.table2, recomputed2):
            rows.append({
                "terminal_growth": wb_row.terminal_growth,
                "terminal_value": _compare_scalar(my["terminal_value"], wb_row.terminal_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "enterprise_value": _compare_scalar(my["enterprise_value"], wb_row.enterprise_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "equity_value": _compare_scalar(my["equity_value"], wb_row.equity_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "intrinsic_value_per_share": _compare_scalar(my["intrinsic_value_per_share"], wb_row.intrinsic_value_per_share, SENSITIVITY_TOLERANCE_IVPS),
            })
        valid_pairs = [(r["terminal_growth"], r["intrinsic_value_per_share"]["workbook_value"])
                       for r in rows if not isinstance(r["intrinsic_value_per_share"]["workbook_value"], str)]
        ticker_out["table2_terminal_growth"] = {
            "rows": rows,
            "all_passed": all(r[k]["passed"] for r in rows for k in
                               ("terminal_value", "enterprise_value", "equity_value", "intrinsic_value_per_share")),
            "workbook_monotonic_increasing": _monotonic(valid_pairs, "increasing"),
            "recomputed_monotonic_increasing": _monotonic(
                [(g, my["intrinsic_value_per_share"]) for g, my in zip(g_values, recomputed2)
                 if not isinstance(my["intrinsic_value_per_share"], str)], "increasing"),
        }

        # ---- Table 3: revenue growth ----
        growth_values = [row.revenue_growth for row in wb_tables.table3]
        recomputed3 = recompute_table3_revenue_growth(base, growth_values)
        rows = []
        for wb_row, my in zip(wb_tables.table3, recomputed3):
            fcf_cmp = [_compare_scalar(my["fcf"][i], wb_row.fcf[i], SENSITIVITY_TOLERANCE_FCF) for i in range(5)]
            rows.append({
                "revenue_growth": wb_row.revenue_growth,
                "fcf": fcf_cmp,
                "terminal_value": _compare_scalar(my["terminal_value"], wb_row.terminal_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "enterprise_value": _compare_scalar(my["enterprise_value"], wb_row.enterprise_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "equity_value": _compare_scalar(my["equity_value"], wb_row.equity_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "intrinsic_value_per_share": _compare_scalar(my["intrinsic_value_per_share"], wb_row.intrinsic_value_per_share, SENSITIVITY_TOLERANCE_IVPS),
            })
        valid_pairs = [(r["revenue_growth"], r["intrinsic_value_per_share"]["workbook_value"])
                       for r in rows if not isinstance(r["intrinsic_value_per_share"]["workbook_value"], str)]
        ticker_out["table3_revenue_growth"] = {
            "rows": rows,
            "all_passed": all(r[k]["passed"] for r in rows for k in
                               ("terminal_value", "enterprise_value", "equity_value", "intrinsic_value_per_share")) and
                          all(fc["passed"] for r in rows for fc in r["fcf"]),
            "workbook_monotonic_increasing": _monotonic(valid_pairs, "increasing"),
            "recomputed_monotonic_increasing": _monotonic(
                [(g, my["intrinsic_value_per_share"]) for g, my in zip(growth_values, recomputed3)
                 if not isinstance(my["intrinsic_value_per_share"], str)], "increasing"),
        }

        # ---- Table 4: operating margin ----
        margin_values = [row.operating_margin for row in wb_tables.table4]
        recomputed4 = recompute_table4_operating_margin(base, margin_values)
        rows = []
        for wb_row, my in zip(wb_tables.table4, recomputed4):
            fcf_cmp = [_compare_scalar(my["fcf"][i], wb_row.fcf[i], SENSITIVITY_TOLERANCE_FCF) for i in range(5)]
            rows.append({
                "operating_margin": wb_row.operating_margin,
                "fcf": fcf_cmp,
                "terminal_value": _compare_scalar(my["terminal_value"], wb_row.terminal_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "enterprise_value": _compare_scalar(my["enterprise_value"], wb_row.enterprise_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "equity_value": _compare_scalar(my["equity_value"], wb_row.equity_value, SENSITIVITY_TOLERANCE_TV_EV_EQV),
                "intrinsic_value_per_share": _compare_scalar(my["intrinsic_value_per_share"], wb_row.intrinsic_value_per_share, SENSITIVITY_TOLERANCE_IVPS),
            })
        valid_pairs = [(r["operating_margin"], r["intrinsic_value_per_share"]["workbook_value"])
                       for r in rows if not isinstance(r["intrinsic_value_per_share"]["workbook_value"], str)]
        ticker_out["table4_operating_margin"] = {
            "rows": rows,
            "all_passed": all(r[k]["passed"] for r in rows for k in
                               ("terminal_value", "enterprise_value", "equity_value", "intrinsic_value_per_share")) and
                          all(fc["passed"] for r in rows for fc in r["fcf"]),
            "workbook_monotonic_increasing": _monotonic(valid_pairs, "increasing"),
            "recomputed_monotonic_increasing": _monotonic(
                [(m, my["intrinsic_value_per_share"]) for m, my in zip(margin_values, recomputed4)
                 if not isinstance(my["intrinsic_value_per_share"], str)], "increasing"),
        }

        # ---- Table 5: two-way WACC x g grid ----
        recomputed5 = recompute_table5_two_way(base, wb_tables.table5_wacc_headers, wb_tables.table5_g_headers)
        grid_rows = []
        n_examined = 0
        for wacc_v in wb_tables.table5_wacc_headers:
            for g_v in wb_tables.table5_g_headers:
                wb_v = wb_tables.table5_grid[(wacc_v, g_v)]
                my_v = recomputed5[(wacc_v, g_v)]
                cmp = _compare_scalar(my_v, wb_v, SENSITIVITY_TOLERANCE_IVPS)
                grid_rows.append({"wacc": wacc_v, "terminal_growth": g_v, **cmp})
                n_examined += 1
        ticker_out["table5_two_way"] = {
            "grid": grid_rows,
            "cells_examined": n_examined,
            "cells_expected": 36,
            "all_passed": all(r["passed"] for r in grid_rows),
        }

        out[ticker] = ticker_out
    return out


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def run_reconciliation():
    """Returns a dict of {codebase_outputs, base_results, directional_bias, sensitivity_results}."""
    loaded = load_all()
    snapshots_by_ticker = {t: s for t, (s, _fd) in loaded.items()}
    financial_data_by_ticker = {t: fd for t, (_s, fd) in loaded.items()}

    # Live pandas-based results (used for reconciliation math/recompute) --
    # kept separate from codebase_outputs.json's JSON-serialized shape.
    raw_results = {
        ticker: run_dcf_valuation(financial_data_by_ticker[ticker], DCFAssumptions())
        for ticker in TICKERS
    }

    codebase_outputs = build_codebase_outputs()

    wb = Workbook(str(INDEPENDENT_WORKBOOK_PATH))

    base_results = run_base_reconciliation(raw_results, wb)
    bias = directional_bias_analysis(base_results)
    sensitivity_results = run_sensitivity_reconciliation(financial_data_by_ticker, raw_results, wb)

    return {
        "codebase_outputs": codebase_outputs,
        "base_results": base_results,
        "directional_bias": bias,
        "sensitivity_results": sensitivity_results,
    }


def overall_verdict(assembled: dict) -> dict:
    base_pass = all(
        assembled["base_results"][t]["first_divergence"] is None for t in TICKERS
    )
    sens_pass = all(
        assembled["sensitivity_results"][t][table]["all_passed"]
        for t in TICKERS
        for table in ("table1_wacc", "table2_terminal_growth", "table3_revenue_growth",
                       "table4_operating_margin", "table5_two_way")
    )
    failing_companies = [t for t in TICKERS if assembled["base_results"][t]["first_divergence"] is not None]
    return {
        "base_case_all_pass": base_pass,
        "sensitivity_all_pass": sens_pass,
        "overall_pass": base_pass and sens_pass,
        "companies_with_base_divergence": failing_companies,
    }


def main():
    assembled = run_reconciliation()
    verdict = overall_verdict(assembled)

    results_payload = {
        "branch": assembled["codebase_outputs"]["branch"],
        "commit": assembled["codebase_outputs"]["commit"],
        # Track A Phase 2C: reconciliation runs against V2; V1 is preserved and
        # its hash still recorded for provenance/history. "original_workbook_sha256"
        # is kept as an alias of v1_workbook_sha256 for backward-compatible field
        # naming in older report/workbook code -- it is NOT the reconciliation target.
        "original_workbook_sha256": assembled["codebase_outputs"]["v1_workbook_sha256"],
        "v1_workbook_sha256": assembled["codebase_outputs"]["v1_workbook_sha256"],
        "v2_workbook_sha256": assembled["codebase_outputs"]["v2_workbook_sha256"],
        "reconciliation_target": assembled["codebase_outputs"]["reconciliation_target"],
        "snapshot_sha256": assembled["codebase_outputs"]["snapshot_sha256"],
        "companies": list(TICKERS),
        "base_reconciliation": assembled["base_results"],
        "directional_bias": assembled["directional_bias"],
        "sensitivity_reconciliation": assembled["sensitivity_results"],
        "verdict": verdict,
    }

    codebase_outputs_path = HERE / "codebase_outputs.json"
    with open(codebase_outputs_path, "w", encoding="utf-8") as f:
        json.dump(assembled["codebase_outputs"], f, indent=2, sort_keys=True, default=_json_default)
        f.write("\n")

    results_path = HERE / "reconciliation_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=2, sort_keys=True, default=_json_default)
        f.write("\n")

    from validation.dcf_reconciliation.report import build_report_markdown
    report_path = HERE / "reconciliation_report.md"
    report_text = build_report_markdown(results_payload)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    from validation.dcf_reconciliation.workbook_builder import build_reconciliation_workbook
    xlsx_path = HERE / "dcf_reconciliation.xlsx"
    build_reconciliation_workbook(results_payload, str(xlsx_path))

    manifest = {
        "description": "SHA-256 of every reconciliation artifact this session produced, "
                        "except this manifest file itself (a manifest cannot meaningfully "
                        "hash its own final bytes before they are written).",
        "original_workbook_sha256": assembled["codebase_outputs"]["v1_workbook_sha256"],
        "v1_workbook_sha256": assembled["codebase_outputs"]["v1_workbook_sha256"],
        "v2_workbook_sha256": assembled["codebase_outputs"]["v2_workbook_sha256"],
        "reconciliation_target": assembled["codebase_outputs"]["reconciliation_target"],
        "snapshot_sha256": assembled["codebase_outputs"]["snapshot_sha256"],
        "artifacts": {
            "codebase_outputs.json": _sha256_file(codebase_outputs_path),
            "reconciliation_results.json": _sha256_file(results_path),
            "reconciliation_report.md": _sha256_file(report_path),
            "dcf_reconciliation.xlsx": _sha256_file(xlsx_path),
        },
    }
    manifest_path = HERE / "reconciliation_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True, default=_json_default)
        f.write("\n")

    print(f"Wrote {codebase_outputs_path}")
    print(f"Wrote {results_path}")
    print(f"Wrote {report_path}")
    print(f"Wrote {xlsx_path}")
    print(f"Wrote {manifest_path}")
    print(f"\nVerdict: {verdict}")


if __name__ == "__main__":
    main()
