"""Read-only production comparison for Bear/Bull. Run only after
frozen-results-scenarios.json is sealed (regenerate it via
`independent_scenarios.py`, which must not import production scenario code).

This is the Bear/Bull counterpart to `compare_after_freeze.py` (which
reconciles Base only). It imports `src.dcf_model.scenarios.compute_dcf_scenarios`
-- that import is legitimate here because this script is the *production*
side of the comparison; the frozen JSON it compares against was built by
`independent_scenarios.py`, which does not import it.
"""
import json
from pathlib import Path

from src.dcf_model.dcf import MultiStageForecastPolicy
from src.dcf_model.scenarios import ScenarioInputs, compute_dcf_scenarios

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "validation/independent_dcf"
HERE = BASE / "staged_v3"

frozen_base = json.loads((HERE / "frozen-results.json").read_text())
frozen_scenarios = json.loads((HERE / "frozen-results-scenarios.json").read_text())


def close(a, b, kind):
    tol = 1e-10 if kind == "rate" else 0.01 if kind == "share" else max(0.01, 1e-10 * abs(a))
    return abs(a - b) <= tol, a - b, tol


SYNTH_INPUTS = {
    "debt": 100e6, "cash": 50e6, "shares": 100e6,
    "base_revenue": (1000 + 50 * 4) * 1e6,
}


def fixed_inputs(ticker: str) -> dict:
    if ticker == "NEGATIVE":
        return SYNTH_INPUTS
    snap = json.loads((BASE / f"snapshots/{ticker}_snapshot.json").read_text())
    fact, market = snap["latest_year_facts"], snap["market_data"]
    return {
        "debt": fact["total_debt_usd"],
        "cash": fact["cash_and_equivalents_usd"],
        "shares": market["shares_outstanding"],
        "base_revenue": snap["historical_annual_data"][-1]["revenue_raw_usd"],
    }


all_rows = []
for ticker, base_entry in frozen_base["companies"].items():
    fi = fixed_inputs(ticker)
    tax_rate = base_entry["inputs"]["tax"]
    baseline_wacc = base_entry["inputs"]["wacc"]
    baseline_growth_path = base_entry["base"]["growth"]
    baseline_margin_path = base_entry["base"]["margin"]

    path = MultiStageForecastPolicy().build_path(baseline_growth_path[0], baseline_margin_path[0], 5)
    # Sanity: the reconstructed near-term rate must equal the frozen year-1 growth
    # (both should be the unfaded resolved growth rate, since year 1 is never faded).
    assert abs(path[0].revenue_growth_rate - baseline_growth_path[0]) < 1e-9

    scenarios = compute_dcf_scenarios(ScenarioInputs(
        base_revenue=fi["base_revenue"],
        baseline_revenue_growth_rate=baseline_growth_path[0],
        baseline_operating_margin=baseline_margin_path[0],
        baseline_wacc=baseline_wacc,
        baseline_terminal_growth_rate=0.025,
        tax_rate=tax_rate, da_pct_revenue=0.03, capex_pct_revenue=0.04,
        nwc_pct_revenue_change=0.01, projection_years=5,
        total_debt=fi["debt"], cash_and_equivalents=fi["cash"], shares_outstanding=fi["shares"],
        baseline_forecast_path=path,
    ))

    frozen_entry = frozen_scenarios["companies"][ticker]
    for name in ("bear", "bull"):
        prod = getattr(scenarios, name)
        frozen_case = frozen_entry[name]

        if prod.intrinsic_value_per_share is None or frozen_case["per_share"] is None:
            ok = (prod.intrinsic_value_per_share is None) == (frozen_case["per_share"] is None)
            all_rows.append(dict(
                ticker=ticker, metric=f"{name}.perShare", workbook=frozen_case["per_share"],
                production=prod.intrinsic_value_per_share, delta=None, tolerance=None, pass_=ok,
                note=f"validity mismatch: independent={frozen_case['invalid_reason']!r} production={prod.invalid_reason!r}" if not ok else "",
            ))
        else:
            ok, diff, tol = close(frozen_case["per_share"], prod.intrinsic_value_per_share, "share")
            all_rows.append(dict(
                ticker=ticker, metric=f"{name}.perShare", workbook=frozen_case["per_share"],
                production=prod.intrinsic_value_per_share, delta=diff, tolerance=tol, pass_=ok, note="",
            ))

        prod_assumptions = prod.assumptions
        for axis_name, frozen_value, prod_value in (
            ("wacc", frozen_case["wacc"], prod_assumptions.wacc),
            ("terminal_growth", frozen_case["terminal_growth"], prod_assumptions.terminal_growth_rate),
        ):
            ok2, diff2, tol2 = close(frozen_value, prod_value, "rate")
            all_rows.append(dict(
                ticker=ticker, metric=f"{name}.{axis_name}", workbook=frozen_value,
                production=prod_value, delta=diff2, tolerance=tol2, pass_=ok2, note="",
            ))

        for axis_name, frozen_path_value, prod_scalar in (
            ("growth.1", frozen_case["growth"][0], prod_assumptions.revenue_growth_rate),
            ("margin.1", frozen_case["margin"][0], prod_assumptions.operating_margin),
        ):
            ok3, diff3, tol3 = close(frozen_path_value, prod_scalar, "rate")
            all_rows.append(dict(
                ticker=ticker, metric=f"{name}.{axis_name}", workbook=frozen_path_value,
                production=prod_scalar, delta=diff3, tolerance=tol3, pass_=ok3,
                note="production ScenarioAssumptions reports a single scalar per axis (pre-fade-rebuild); "
                     "compared against the frozen year-1 (unfaded) value, which is the same number by construction.",
            ))

fails = [r for r in all_rows if not r["pass_"]]
report = {
    "frozen_base_source": str(HERE / "frozen-results.json"),
    "frozen_scenarios_source": str(HERE / "frozen-results-scenarios.json"),
    "comparisons": len(all_rows),
    "failures": len(fails),
    "max_abs_delta": max((abs(r["delta"]) for r in all_rows if r["delta"] is not None), default=0),
    "failed_rows": fails,
    "tolerances": "rate 1e-10 absolute; per-share $0.01",
}
print(json.dumps(report, indent=2))
if fails:
    raise SystemExit(1)
