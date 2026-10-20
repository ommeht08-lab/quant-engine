"""
Vectorized NumPy DCF valuation for the Monte Carlo experiments.

This is NOT a second DCF model. It evaluates the same arithmetic as
`build_path` -> `project_free_cash_flows_from_path` (or the constant-growth
`project_free_cash_flows`) -> `calculate_terminal_value` ->
`discount_to_present_value` -> `calculate_intrinsic_value_per_share` in
`src.dcf_model.dcf`, for many (growth, margin, WACC) draws at once, in the same
operation order so float64 results match the scalar path. Tests pin it to the
scalar functions (zero-uncertainty equality and a randomized property test);
if `dcf.py` arithmetic ever changes, those tests fail.

One draw carries exactly one WACC: the same `wacc[i]` is the terminal-value
denominator, every year's discount factor, and the terminal-value discount.
There is no pandas in the hot path; failures are per-draw codes, never
exceptions, mirroring the conditions under which the scalar functions raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np

# Failure codes, in the order the scalar pipeline would raise them.
OK = 0
GROWTH_LE_NEG_100 = 1
NONFINITE_PROJECTION = 2
WACC_NOT_ABOVE_TERMINAL_GROWTH = 3
NONFINITE_TERMINAL_VALUE = 4
NONFINITE_DISCOUNTING = 5
NONFINITE_EQUITY = 6

FAILURE_CODE_NAMES: Dict[int, str] = {
    GROWTH_LE_NEG_100: "growth_le_neg_100",
    NONFINITE_PROJECTION: "nonfinite_projection",
    WACC_NOT_ABOVE_TERMINAL_GROWTH: "wacc_not_above_terminal_growth",
    NONFINITE_TERMINAL_VALUE: "nonfinite_terminal_value",
    NONFINITE_DISCOUNTING: "nonfinite_discounting",
    NONFINITE_EQUITY: "nonfinite_equity",
}


@dataclass(frozen=True)
class KernelInputs:
    """Everything held fixed across draws, as resolved by `run_dcf_valuation`."""

    base_revenue: float
    tax_rate: float
    da_pct_revenue: float
    capex_pct_revenue: float
    nwc_pct_revenue_change: float
    terminal_growth_rate: float
    projection_years: int
    total_debt: float
    cash_and_equivalents: float
    shares_outstanding: float
    forecast_mode: str  # "maturation" | "constant"
    near_term_years: int
    mature_growth_ceiling: float


def _growth_for_year(
    inputs: KernelInputs, growth: np.ndarray, mature_rate: np.ndarray, year: int
) -> np.ndarray:
    """Year-`year` growth, mirroring `MultiStageForecastPolicy.build_path`."""
    if inputs.forecast_mode == "constant" or year <= inputs.near_term_years:
        return growth
    fade_years = inputs.projection_years - inputs.near_term_years
    # scalar: growth + (mature - growth) * (year - near) / fade_years
    return growth + (mature_rate - growth) * float(year - inputs.near_term_years) / float(fade_years)


def evaluate_valuations(
    inputs: KernelInputs,
    growth: np.ndarray,
    margin: np.ndarray,
    wacc: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Value every draw. Returns (value_per_share, failure_code); NaN where failed."""
    growth = np.asarray(growth, dtype=np.float64)
    margin = np.asarray(margin, dtype=np.float64)
    wacc = np.asarray(wacc, dtype=np.float64)
    n = growth.shape[0]
    failure = np.zeros(n, dtype=np.int8)

    def mark(mask: np.ndarray, code: int) -> None:
        failure[mask & (failure == OK)] = code

    with np.errstate(all="ignore"):
        mark(growth <= -1.0, GROWTH_LE_NEG_100)

        mature_rate = np.minimum(growth, inputs.mature_growth_ceiling)
        one_minus_tax = 1 - inputs.tax_rate

        fcf_by_year = []
        prior_revenue = np.full(n, inputs.base_revenue, dtype=np.float64)
        projection_ok = np.ones(n, dtype=bool)
        for year in range(1, inputs.projection_years + 1):
            year_growth = _growth_for_year(inputs, growth, mature_rate, year)
            revenue = prior_revenue * (1 + year_growth)
            ebit = revenue * margin
            nopat = ebit * one_minus_tax
            da = revenue * inputs.da_pct_revenue
            capex = revenue * inputs.capex_pct_revenue
            change_in_nwc = inputs.nwc_pct_revenue_change * (revenue - prior_revenue)
            fcf = nopat + da - capex - change_in_nwc
            for quantity in (revenue, ebit, nopat, da, capex, change_in_nwc, fcf):
                projection_ok &= np.isfinite(quantity)
            fcf_by_year.append(fcf)
            prior_revenue = revenue
        mark(~projection_ok, NONFINITE_PROJECTION)

        gt = inputs.terminal_growth_rate
        mark(~(np.isfinite(wacc) & (wacc > gt)), WACC_NOT_ABOVE_TERMINAL_GROWTH)

        terminal_value = fcf_by_year[-1] * (1 + gt) / (wacc - gt)
        mark(~np.isfinite(terminal_value), NONFINITE_TERMINAL_VALUE)

        # np.power on a float array with a Python-int exponent takes the same
        # libm pow path as the scalar pipeline's `(1 + wacc) ** years`.
        base = 1 + wacc
        discounting_ok = np.ones(n, dtype=bool)
        pv_columns = []
        for year, fcf in enumerate(fcf_by_year, start=1):
            discount_factor = 1 / np.power(base, year)
            pv = fcf * discount_factor
            discounting_ok &= np.isfinite(pv)
            pv_columns.append(pv)
        # Summed as a C-contiguous (draws x years) matrix along its last axis:
        # the same NumPy reduction (including its pairwise blocking beyond 8
        # terms) that `pd.Series.sum()` applies in the scalar pipeline.
        pv_total = np.stack(pv_columns, axis=1).sum(axis=1)
        pv_terminal = terminal_value / np.power(base, inputs.projection_years)
        discounting_ok &= np.isfinite(pv_terminal)
        enterprise_value = pv_total + pv_terminal
        discounting_ok &= np.isfinite(enterprise_value)
        mark(~discounting_ok, NONFINITE_DISCOUNTING)

        equity_value = enterprise_value - inputs.total_debt + inputs.cash_and_equivalents
        value_per_share = equity_value / inputs.shares_outstanding
        mark(~(np.isfinite(equity_value) & np.isfinite(value_per_share)), NONFINITE_EQUITY)

    value_per_share = np.where(failure == OK, value_per_share, np.nan)
    return value_per_share, failure
