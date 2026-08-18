"""
Phase 6: recompute each sensitivity scenario using the PRODUCTION DCF
functions themselves (project_free_cash_flows, calculate_terminal_value,
discount_to_present_value, calculate_intrinsic_value_per_share) -- never a
reimplementation of the formulas. Only the single varied input changes per
scenario; every other input is held at the company's actual base-case
value, exactly matching how each Sensitivity_<TICKER> table is documented
to behave ("one variable at a time, other inputs held at base case").
"""
from typing import Optional, Union

from src.dcf_model.dcf import (
    calculate_intrinsic_value_per_share,
    calculate_terminal_value,
    discount_to_present_value,
    extract_valuation_inputs,
    project_free_cash_flows,
)

NA = "n/a"
CellValue = Union[float, str]


class BaseCase:
    """Every fixed input needed to hold "all else equal" while sweeping one
    sensitivity variable, extracted directly from the actual production run
    (never re-derived independently) so a recompute is always internally
    consistent with the base-case result it is a perturbation of."""

    def __init__(self, financial_data: dict, base_result: dict):
        inputs = extract_valuation_inputs(financial_data)
        fcf = base_result["fcf_projection"]

        self.base_revenue = inputs["revenue"]
        self.total_debt = inputs["total_debt"]
        self.cash_and_equivalents = inputs["cash_and_equivalents"]
        self.shares_outstanding = financial_data["shares_outstanding"]

        self.wacc = base_result["wacc"]
        self.revenue_growth_rate = base_result["revenue_growth_rate"]
        self.operating_margin = base_result["operating_margin"]
        self.terminal_growth_rate = 0.025  # DCFAssumptions() default -- also Inputs_<T>!B29

        # Effective tax rate actually used by the production run, recovered
        # from its own FCF projection (NOPAT = EBIT * (1 - tax_rate)) so
        # this is exactly the rate production used, not a re-derivation
        # that could silently diverge from it.
        ebit_y1 = float(fcf.loc[1, "ebit"])
        nopat_y1 = float(fcf.loc[1, "nopat"])
        self.tax_rate = 1.0 - (nopat_y1 / ebit_y1)

        self.da_pct_revenue = 0.03
        self.capex_pct_revenue = 0.04
        self.nwc_pct_revenue_change = 0.01
        self.projection_years = 5

        self.base_fcf_projection = fcf
        self.pv_fcf_sum = float(base_result["pv_fcf"].sum())


def _equity_and_ivps(base: BaseCase, enterprise_value: float):
    equity_value = enterprise_value - base.total_debt + base.cash_and_equivalents
    ivps = calculate_intrinsic_value_per_share(
        enterprise_value=enterprise_value,
        total_debt=base.total_debt,
        cash_and_equivalents=base.cash_and_equivalents,
        shares_outstanding=base.shares_outstanding,
    )
    return equity_value, ivps


def recompute_table1_wacc(base: BaseCase, wacc_values):
    """WACC sensitivity: base FCF path held fixed, WACC varies."""
    out = []
    final_fcf = float(base.base_fcf_projection["fcf"].iloc[-1])
    for wacc in wacc_values:
        if wacc <= base.terminal_growth_rate:
            out.append({"wacc": wacc, "terminal_value": NA, "enterprise_value": NA,
                        "equity_value": NA, "intrinsic_value_per_share": NA})
            continue
        tv = calculate_terminal_value(final_fcf, wacc, base.terminal_growth_rate)
        disc = discount_to_present_value(base.base_fcf_projection, tv, wacc)
        ev = disc["enterprise_value"]
        eqv, ivps = _equity_and_ivps(base, ev)
        out.append({"wacc": wacc, "terminal_value": tv, "enterprise_value": ev,
                    "equity_value": eqv, "intrinsic_value_per_share": ivps})
    return out


def recompute_table2_terminal_growth(base: BaseCase, g_values):
    """Terminal growth sensitivity: base WACC & FCF path held fixed, g varies."""
    out = []
    final_fcf = float(base.base_fcf_projection["fcf"].iloc[-1])
    for g in g_values:
        if g >= base.wacc:
            out.append({"terminal_growth": g, "terminal_value": NA, "enterprise_value": NA,
                        "equity_value": NA, "intrinsic_value_per_share": NA})
            continue
        tv = calculate_terminal_value(final_fcf, base.wacc, g)
        disc = discount_to_present_value(base.base_fcf_projection, tv, base.wacc)
        ev = disc["enterprise_value"]
        eqv, ivps = _equity_and_ivps(base, ev)
        out.append({"terminal_growth": g, "terminal_value": tv, "enterprise_value": ev,
                    "equity_value": eqv, "intrinsic_value_per_share": ivps})
    return out


def recompute_table3_revenue_growth(base: BaseCase, growth_values):
    """Revenue growth sensitivity: base WACC & operating margin held fixed, growth varies."""
    out = []
    for g in growth_values:
        proj = project_free_cash_flows(
            base_revenue=base.base_revenue,
            revenue_growth_rate=g,
            operating_margin=base.operating_margin,
            tax_rate=base.tax_rate,
            da_pct_revenue=base.da_pct_revenue,
            capex_pct_revenue=base.capex_pct_revenue,
            nwc_pct_revenue_change=base.nwc_pct_revenue_change,
            years=base.projection_years,
        )
        final_fcf = float(proj["fcf"].iloc[-1])
        # Table 3 does not apply a WACC<=g / g>=WACC guard (only Tables 1/2/5 do) --
        # it always computes TV at the fixed base WACC/terminal growth.
        tv = calculate_terminal_value(final_fcf, base.wacc, base.terminal_growth_rate)
        disc = discount_to_present_value(proj, tv, base.wacc)
        ev = disc["enterprise_value"]
        eqv, ivps = _equity_and_ivps(base, ev)
        out.append({
            "revenue_growth": g,
            "fcf": [float(v) for v in proj["fcf"]],
            "terminal_value": tv, "enterprise_value": ev,
            "equity_value": eqv, "intrinsic_value_per_share": ivps,
        })
    return out


def recompute_table4_operating_margin(base: BaseCase, margin_values):
    """Operating margin sensitivity: base revenue path & WACC held fixed, margin varies."""
    out = []
    for m in margin_values:
        proj = project_free_cash_flows(
            base_revenue=base.base_revenue,
            revenue_growth_rate=base.revenue_growth_rate,
            operating_margin=m,
            tax_rate=base.tax_rate,
            da_pct_revenue=base.da_pct_revenue,
            capex_pct_revenue=base.capex_pct_revenue,
            nwc_pct_revenue_change=base.nwc_pct_revenue_change,
            years=base.projection_years,
        )
        final_fcf = float(proj["fcf"].iloc[-1])
        tv = calculate_terminal_value(final_fcf, base.wacc, base.terminal_growth_rate)
        disc = discount_to_present_value(proj, tv, base.wacc)
        ev = disc["enterprise_value"]
        eqv, ivps = _equity_and_ivps(base, ev)
        out.append({
            "operating_margin": m,
            "fcf": [float(v) for v in proj["fcf"]],
            "terminal_value": tv, "enterprise_value": ev,
            "equity_value": eqv, "intrinsic_value_per_share": ivps,
        })
    return out


def recompute_table5_two_way(base: BaseCase, wacc_values, g_values):
    """Two-way WACC (rows) x terminal growth (cols) grid -> Intrinsic Value/Share only."""
    final_fcf = float(base.base_fcf_projection["fcf"].iloc[-1])
    grid = {}
    for wacc in wacc_values:
        for g in g_values:
            if g >= wacc:
                grid[(wacc, g)] = NA
                continue
            tv = calculate_terminal_value(final_fcf, wacc, g)
            disc = discount_to_present_value(base.base_fcf_projection, tv, wacc)
            _eqv, ivps = _equity_and_ivps(base, disc["enterprise_value"])
            grid[(wacc, g)] = ivps
    return grid
