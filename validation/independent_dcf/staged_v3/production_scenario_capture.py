"""Validation-only wrapper around EXISTING production functions.

`ScenarioResult`/`ScenarioAssumptions` (the public return shape of
`compute_dcf_scenarios`, in `src/dcf_model/scenarios.py`) report only the
final `intrinsic_value_per_share` and a single representative
growth/margin/WACC/terminal-growth scalar per scenario -- they do not
expose the per-year shifted growth/margin path, per-year revenue/FCF, the
discounted FCF series, the terminal value, its present value, the
enterprise value, or the debt/cash equity bridge, even though
`_compute_scenario` computes all of them internally before discarding
everything but the final per-share figure.

This module captures those intermediates for audit by calling the ACTUAL
production functions in the same sequence `_compute_scenario`'s staged
branch does -- `_shift_staged_rate`/`_clamp` (private but genuine production
functions, imported directly rather than reimplemented) for the shift/clamp
step, then the public `project_free_cash_flows_from_path`,
`calculate_terminal_value`, `discount_to_present_value`, and
`calculate_intrinsic_value_per_share`. It changes nothing in `src/` and adds
no new business logic of its own beyond assembling call results into a dict
and the single documented, trivial `equity_value = enterprise_value - debt +
cash` bridge arithmetic (the same formula `calculate_intrinsic_value_per_share`
already applies internally; computed again here only because that function
returns the per-share figure, not the equity value itself).

`capture_scenario` is therefore the PRODUCTION side of a comparison, not an
independent implementation -- it exists only because the public API's
return shape is narrower than what an audit needs, not because the public
API is wrong. Compare its output against `independent_scenarios_v2.py`'s
frozen output; separately, `capture_and_compare_with_public_api` cross-checks
this wrapper's own orchestration against `compute_dcf_scenarios` directly, to
confirm the wrapper has not silently diverged from the real code path it is
meant to mirror.
"""

from __future__ import annotations

from src.dcf_model.dcf import (
    MAX_DISCOUNT_RATE,
    MAX_EXPLICIT_OPERATING_MARGIN,
    MAX_EXPLICIT_REVENUE_GROWTH_RATE,
    MAX_EXPLICIT_TERMINAL_GROWTH_RATE,
    MIN_DISCOUNT_RATE,
    MIN_EXPLICIT_OPERATING_MARGIN,
    MIN_EXPLICIT_REVENUE_GROWTH_RATE,
    MIN_EXPLICIT_TERMINAL_GROWTH_RATE,
    ForecastYearAssumptions,
    calculate_intrinsic_value_per_share,
    calculate_terminal_value,
    discount_to_present_value,
    project_free_cash_flows_from_path,
)
from src.dcf_model.scenarios import (  # validation-only: exposing intermediates the public dataclasses omit
    ScenarioInputs,
    _clamp,
    _shift_staged_rate,
    compute_dcf_scenarios,
)


def capture_scenario(
    growth_delta: float,
    margin_delta: float,
    wacc_delta: float,
    tg_delta: float,
    base_revenue: float,
    baseline_path: tuple[ForecastYearAssumptions, ...],
    baseline_wacc: float,
    baseline_terminal_growth: float,
    tax_rate: float,
    da_pct_revenue: float,
    capex_pct_revenue: float,
    nwc_pct_revenue_change: float,
    total_debt: float,
    cash_and_equivalents: float,
    shares_outstanding: float,
) -> dict:
    """Re-run `_compute_scenario`'s staged branch step by step, using the
    actual production functions at every step, capturing every intermediate."""
    scenario_path = tuple(
        ForecastYearAssumptions(
            year=step.year,
            stage=step.stage,
            revenue_growth_rate=_shift_staged_rate(
                step.revenue_growth_rate, growth_delta,
                MIN_EXPLICIT_REVENUE_GROWTH_RATE, MAX_EXPLICIT_REVENUE_GROWTH_RATE,
            ),
            operating_margin=_shift_staged_rate(
                step.operating_margin, margin_delta,
                MIN_EXPLICIT_OPERATING_MARGIN, MAX_EXPLICIT_OPERATING_MARGIN,
            ),
        )
        for step in baseline_path
    )
    wacc = _clamp(baseline_wacc + wacc_delta, MIN_DISCOUNT_RATE, MAX_DISCOUNT_RATE)
    tg = _clamp(baseline_terminal_growth + tg_delta, MIN_EXPLICIT_TERMINAL_GROWTH_RATE, MAX_EXPLICIT_TERMINAL_GROWTH_RATE)

    result = {
        "growth": [s.revenue_growth_rate for s in scenario_path],
        "margin": [s.operating_margin for s in scenario_path],
        "wacc": wacc,
        "terminal_growth": tg,
    }
    try:
        fcf_projection = project_free_cash_flows_from_path(
            base_revenue, scenario_path, tax_rate, da_pct_revenue, capex_pct_revenue, nwc_pct_revenue_change,
        )
        tv = calculate_terminal_value(float(fcf_projection["fcf"].iloc[-1]), wacc, tg)
        discounting = discount_to_present_value(fcf_projection, tv, wacc)
        per_share = calculate_intrinsic_value_per_share(
            discounting["enterprise_value"], total_debt, cash_and_equivalents, shares_outstanding,
        )
        result.update({
            "revenue": fcf_projection["revenue"].tolist(),
            "fcf": fcf_projection["fcf"].tolist(),
            "pv_fcf": discounting["pv_fcf"].tolist(),
            "terminal_value": tv,
            "pv_terminal_value": discounting["pv_terminal_value"],
            "enterprise_value": discounting["enterprise_value"],
            "equity_value": discounting["enterprise_value"] - total_debt + cash_and_equivalents,
            "per_share": per_share,
            "invalid_reason": None,
        })
    except ValueError as exc:
        result.update({
            "revenue": None, "fcf": None, "pv_fcf": None, "terminal_value": None,
            "pv_terminal_value": None, "enterprise_value": None, "equity_value": None,
            "per_share": None, "invalid_reason": str(exc),
        })
    return result


def capture_via_public_api(
    base_revenue: float,
    baseline_path: tuple[ForecastYearAssumptions, ...],
    baseline_wacc: float,
    baseline_terminal_growth: float,
    tax_rate: float,
    da_pct_revenue: float,
    capex_pct_revenue: float,
    nwc_pct_revenue_change: float,
    total_debt: float,
    cash_and_equivalents: float,
    shares_outstanding: float,
) -> dict:
    """Call `compute_dcf_scenarios` (the real public entrypoint) directly and
    return bear/bull's per_share, wacc, terminal_growth_rate,
    revenue_growth_rate, operating_margin, invalid_reason -- used only to
    cross-check that `capture_scenario`'s hand-orchestrated call sequence
    above has not silently diverged from what `_compute_scenario` actually
    does end to end."""
    scenarios = compute_dcf_scenarios(ScenarioInputs(
        base_revenue=base_revenue,
        baseline_revenue_growth_rate=baseline_path[0].revenue_growth_rate,
        baseline_operating_margin=baseline_path[0].operating_margin,
        baseline_wacc=baseline_wacc,
        baseline_terminal_growth_rate=baseline_terminal_growth,
        tax_rate=tax_rate, da_pct_revenue=da_pct_revenue, capex_pct_revenue=capex_pct_revenue,
        nwc_pct_revenue_change=nwc_pct_revenue_change, projection_years=len(baseline_path),
        total_debt=total_debt, cash_and_equivalents=cash_and_equivalents, shares_outstanding=shares_outstanding,
        baseline_forecast_path=baseline_path,
    ))
    out = {}
    for name in ("bear", "bull"):
        case = getattr(scenarios, name)
        out[name] = {
            "per_share": case.intrinsic_value_per_share,
            "wacc": case.assumptions.wacc,
            "terminal_growth": case.assumptions.terminal_growth_rate,
            "invalid_reason": case.invalid_reason,
        }
    return out
