"""Read-only production comparison for the v2 Bear/Bull independent
reconciliation. Run only after `frozen-results-scenarios-v2.json` and
`frozen-manifest-v2.json` are sealed (regenerate via
`independent_scenarios_v2.py`, which must not import production scenario
code).

Verifies, before running any comparison, that `frozen-results.json` (the
untouched Base freeze this module depends on) and
`frozen-results-scenarios-v2.json` (this module's own frozen output) still
hash to what `frozen-manifest-v2.json` recorded at freeze time -- if either
has drifted since freezing, this aborts rather than silently comparing
against a file that is no longer the frozen evidence it claims to be.

Every comparison row -- pass and fail alike -- is written to
`scenario_v2_reconciliation_report.json`, not just failures, so the report
is auditable: a reader can see exactly what was checked, not only what
(if anything) failed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from production_scenario_capture import capture_scenario, capture_via_public_api

from src.dcf_model.dcf import ForecastYearAssumptions

HERE = Path(__file__).resolve().parent
BASE = HERE.parent  # validation/independent_dcf

DA_PCT_REVENUE, CAPEX_PCT_REVENUE, NWC_PCT_REVENUE_CHANGE = 0.03, 0.04, 0.01

BEAR_GROWTH_DELTA, BEAR_MARGIN_DELTA, BEAR_WACC_DELTA, BEAR_TG_DELTA = -0.03, -0.02, 0.01, -0.005
BULL_GROWTH_DELTA, BULL_MARGIN_DELTA, BULL_WACC_DELTA, BULL_TG_DELTA = 0.03, 0.02, -0.01, 0.005
DELTAS = {
    "bear": (BEAR_GROWTH_DELTA, BEAR_MARGIN_DELTA, BEAR_WACC_DELTA, BEAR_TG_DELTA),
    "bull": (BULL_GROWTH_DELTA, BULL_MARGIN_DELTA, BULL_WACC_DELTA, BULL_TG_DELTA),
}


def verify_hashes() -> None:
    manifest = json.loads((HERE / "frozen-manifest-v2.json").read_text())
    checks = [
        ("frozen-results.json", HERE / "frozen-results.json"),
        ("frozen-results-scenarios-v2.json", HERE / "frozen-results-scenarios-v2.json"),
    ]
    problems = []
    for key, path in checks:
        expected = manifest["files"][key]["sha256"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            problems.append(f"{key}: manifest says {expected}, actual file hash is {actual}")
    if problems:
        raise SystemExit("Frozen-file hash verification FAILED -- aborting before any comparison:\n" + "\n".join(problems))


def close(independent_value, production_value, kind: str):
    # Cast to plain Python float first: production values traced through
    # pandas/numpy arithmetic are numpy.float64, and numpy>=2.0's
    # numpy.float64.__le__ returns numpy.bool (its __class__.__name__ is
    # literally "bool", not "bool_", despite not being JSON-serializable) --
    # this keeps every field in the report a plain, serializable Python type.
    independent_value = float(independent_value)
    production_value = float(production_value)
    if kind == "rate":
        tol = 1e-10
    elif kind == "share":
        tol = 0.01
    else:
        tol = max(0.01, 1e-10 * abs(independent_value))
    ok = bool(abs(independent_value - production_value) <= tol)
    return ok, production_value - independent_value, tol


def build_baseline_path(growth_path: list[float], margin_path: list[float]) -> tuple[ForecastYearAssumptions, ...]:
    return tuple(
        ForecastYearAssumptions(
            year=i + 1,
            stage="near_term" if i + 1 <= 2 else "maturation",
            revenue_growth_rate=g,
            operating_margin=m,
        )
        for i, (g, m) in enumerate(zip(growth_path, margin_path))
    )


def compare_case(rows: list[dict], case_label: str, scenario_name: str, independent: dict, baseline: dict) -> None:
    growth_delta, margin_delta, wacc_delta, tg_delta = DELTAS[scenario_name]
    baseline_path = build_baseline_path(baseline["growth"], baseline["margin"])

    production = capture_scenario(
        growth_delta, margin_delta, wacc_delta, tg_delta,
        baseline["base_revenue"], baseline_path, baseline["wacc"], baseline["terminal_growth"],
        baseline["tax"], DA_PCT_REVENUE, CAPEX_PCT_REVENUE, NWC_PCT_REVENUE_CHANGE,
        baseline["debt"], baseline["cash"], baseline["shares"],
    )
    public_api = capture_via_public_api(
        baseline["base_revenue"], baseline_path, baseline["wacc"], baseline["terminal_growth"],
        baseline["tax"], DA_PCT_REVENUE, CAPEX_PCT_REVENUE, NWC_PCT_REVENUE_CHANGE,
        baseline["debt"], baseline["cash"], baseline["shares"],
    )[scenario_name]

    def row(metric: str, kind: str, indep_v, prod_v, note: str = "") -> None:
        if indep_v is None or prod_v is None:
            ok = (indep_v is None) == (prod_v is None)
            rows.append(dict(case=case_label, scenario=scenario_name, metric=metric,
                              independent=indep_v, production=prod_v, delta=None, tolerance=None,
                              pass_=ok, note=note or ("both None" if ok else "one side None, other side has a value")))
            return
        ok, delta, tol = close(indep_v, prod_v, kind)
        rows.append(dict(case=case_label, scenario=scenario_name, metric=metric,
                          independent=float(indep_v), production=float(prod_v),
                          delta=delta, tolerance=tol, pass_=ok, note=note))

    # Per-year growth / margin / revenue / FCF / discounted FCF (5 years each).
    for i in range(5):
        row(f"growth.{i + 1}", "rate", independent["growth"][i], production["growth"][i])
        row(f"margin.{i + 1}", "rate", independent["margin"][i], production["margin"][i])
        indep_rev = independent["revenue"][i] if independent["revenue"] is not None else None
        prod_rev = production["revenue"][i] if production["revenue"] is not None else None
        row(f"revenue.{i + 1}", "money", indep_rev, prod_rev)
        indep_fcf = independent["fcf"][i] if independent["fcf"] is not None else None
        prod_fcf = production["fcf"][i] if production["fcf"] is not None else None
        row(f"fcf.{i + 1}", "money", indep_fcf, prod_fcf)
        indep_pv = independent["pv_fcf"][i] if independent["pv_fcf"] is not None else None
        prod_pv = production["pv_fcf"][i] if production["pv_fcf"] is not None else None
        row(f"pv_fcf.{i + 1}", "money", indep_pv, prod_pv)

    row("wacc", "rate", independent["wacc"], production["wacc"])
    row("terminal_growth", "rate", independent["terminal_growth"], production["terminal_growth"])
    row("terminal_value", "money", independent["terminal_value"], production["terminal_value"])
    row("pv_terminal_value", "money", independent["pv_terminal_value"], production["pv_terminal_value"])
    row("enterprise_value", "money", independent["enterprise_value"], production["enterprise_value"])
    row("equity_value", "money", independent["equity_value"], production["equity_value"])
    row("per_share", "share", independent["per_share"], production["per_share"])

    invalid_match = (independent["invalid_reason"] is None) == (production["invalid_reason"] is None)
    rows.append(dict(case=case_label, scenario=scenario_name, metric="invalid_reason_presence",
                      independent=independent["invalid_reason"], production=production["invalid_reason"],
                      delta=None, tolerance=None, pass_=invalid_match, note=""))

    # Wrapper-fidelity cross-check: does capture_scenario's hand-orchestrated
    # call sequence match compute_dcf_scenarios (the real public entrypoint)?
    for metric, prod_wrapper_v, public_v, kind in (
        ("wrapper_vs_public_api.per_share", production["per_share"], public_api["per_share"], "share"),
        ("wrapper_vs_public_api.wacc", production["wacc"], public_api["wacc"], "rate"),
        ("wrapper_vs_public_api.terminal_growth", production["terminal_growth"], public_api["terminal_growth"], "rate"),
    ):
        row(metric, kind, prod_wrapper_v, public_v, note="internal check: wrapper orchestration vs compute_dcf_scenarios directly")
    invalid_fidelity = (production["invalid_reason"] is None) == (public_api["invalid_reason"] is None)
    rows.append(dict(case=case_label, scenario=scenario_name, metric="wrapper_vs_public_api.invalid_reason_presence",
                      independent=production["invalid_reason"], production=public_api["invalid_reason"],
                      delta=None, tolerance=None, pass_=invalid_fidelity,
                      note="internal check: wrapper orchestration vs compute_dcf_scenarios directly"))


def main() -> None:
    verify_hashes()

    independent = json.loads((HERE / "frozen-results-scenarios-v2.json").read_text())

    rows: list[dict] = []
    for ticker, entry in independent["companies"].items():
        for scenario_name in ("bear", "bull"):
            compare_case(rows, f"company:{ticker}", scenario_name, entry[scenario_name], entry["baseline"])
    for name, entry in independent["boundaryFixtures"].items():
        for scenario_name in ("bear", "bull"):
            compare_case(rows, f"fixture:{name}", scenario_name, entry[scenario_name], entry["baseline"])

    fails = [r for r in rows if not r["pass_"]]
    numeric_deltas = [abs(r["delta"]) for r in rows if r["delta"] is not None]
    report = {
        "frozen_base_source": str(HERE / "frozen-results.json"),
        "frozen_scenarios_v2_source": str(HERE / "frozen-results-scenarios-v2.json"),
        "hash_verification": "passed (see verify_hashes(); would have raised SystemExit before this report otherwise)",
        "total_comparisons": len(rows),
        "failures": len(fails),
        "max_abs_delta": max(numeric_deltas, default=0),
        "failed_rows": fails,
        "all_rows": rows,
        "tolerances": "rate 1e-10 absolute; money max($0.01, 1e-10 x |independent value|); per-share $0.01 absolute",
    }
    out_path = HERE / "scenario_v2_reconciliation_report.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    print(json.dumps({k: v for k, v in report.items() if k != "all_rows"}, indent=2))
    print(f"\nFull row-by-row report (including passes) written to {out_path}")
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
