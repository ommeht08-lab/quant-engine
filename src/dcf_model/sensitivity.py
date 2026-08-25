"""
DCF sensitivity analysis: a 5x5 WACC x terminal-growth-rate grid of
intrinsic-value-per-share outcomes around a completed valuation's own
baseline assumptions.

This module is a pure, read-only consumer of the DCF model seam in
`src.dcf_model.dcf` — it takes the ALREADY-COMPUTED baseline FCF
projection and equity-bridge inputs (`run_dcf_valuation`'s own return
values; see that function's docstring) and re-derives intrinsic value
under alternate WACC/terminal-growth pairs by calling the SAME
`calculate_terminal_value` / `discount_to_present_value` /
`calculate_intrinsic_value_per_share` functions `run_dcf_valuation`
itself calls. No financial formula is reimplemented here, and nothing in
this module fetches data or re-runs the projection step — the projected
FCF and the equity bridge (total debt, cash & equivalents, shares
outstanding) are held fixed across every cell; only WACC and terminal
growth vary.
"""

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from src.dcf_model.dcf import (
    MAX_DISCOUNT_RATE,
    MAX_EXPLICIT_TERMINAL_GROWTH_RATE,
    MIN_DISCOUNT_RATE,
    MIN_EXPLICIT_TERMINAL_GROWTH_RATE,
    calculate_intrinsic_value_per_share,
    calculate_terminal_value,
    discount_to_present_value,
)

# WACC varies baseline +/- 2 percentage points in 1-point increments;
# terminal growth varies baseline +/- 1 percentage point in 0.5-point
# increments. Expressed as (step size, offset multiples) rather than
# literal rate lists so the grid is centered on whatever baseline the
# completed valuation actually used, not a fixed absolute range.
WACC_STEP = 0.01
WACC_OFFSET_STEPS: tuple = (-2, -1, 0, 1, 2)

TERMINAL_GROWTH_STEP = 0.005
TERMINAL_GROWTH_OFFSET_STEPS: tuple = (-2, -1, 0, 1, 2)


@dataclass
class SensitivityAxis:
    """One axis (rows or columns) of the sensitivity grid."""

    label: str
    # The 5 actual decimal values used for this axis, in the same order
    # as the grid's rows/columns (ascending).
    values: List[float]
    # Index into `values` of the baseline (offset-0) value.
    baseline_index: int


@dataclass
class SensitivityMatrix:
    """
    A complete WACC x terminal-growth sensitivity grid.

    `cells[i][j]` is the intrinsic value per share for
    `wacc_axis.values[i]` combined with `terminal_growth_axis.values[j]`,
    or `None` for a combination this model refuses to value (WACC not
    strictly greater than terminal growth, or either value outside the
    DCF model's own declared economic bounds) — never NaN/infinity.
    """

    wacc_axis: SensitivityAxis
    terminal_growth_axis: SensitivityAxis
    cells: List[List[Optional[float]]]
    baseline_row: int
    baseline_col: int
    baseline_wacc: float
    baseline_terminal_growth_rate: float
    baseline_intrinsic_value_per_share: Optional[float]


def _compute_cell(
    fcf_projection: pd.DataFrame,
    final_year_fcf: float,
    wacc: float,
    terminal_growth_rate: float,
    total_debt: Optional[float],
    cash_and_equivalents: Optional[float],
    shares_outstanding: Optional[float],
) -> Optional[float]:
    """
    Intrinsic value per share for one (wacc, terminal_growth_rate) cell,
    or `None` if this combination is economically invalid for the model —
    reusing exactly the same terminal-value/discounting/equity-bridge
    functions the baseline valuation itself used, with the SAME
    `fcf_projection` and equity-bridge inputs held constant.
    """
    # Respect the DCF model's own declared economic bounds (the same
    # constants `DCFAssumptions`/`calculate_wacc` already enforce) rather
    # than re-deriving a separate range here — a cell whose WACC or
    # terminal growth would fall outside what the model itself considers
    # valid is refused the same way an out-of-range slider value is.
    if not (MIN_DISCOUNT_RATE <= wacc <= MAX_DISCOUNT_RATE):
        return None
    if not (MIN_EXPLICIT_TERMINAL_GROWTH_RATE <= terminal_growth_rate <= MAX_EXPLICIT_TERMINAL_GROWTH_RATE):
        return None
    if wacc <= terminal_growth_rate:
        return None

    try:
        terminal_value = calculate_terminal_value(
            final_year_fcf=final_year_fcf,
            wacc=wacc,
            terminal_growth_rate=terminal_growth_rate,
        )
        discounting = discount_to_present_value(fcf_projection, terminal_value, wacc)
        return calculate_intrinsic_value_per_share(
            enterprise_value=discounting["enterprise_value"],
            total_debt=total_debt,
            cash_and_equivalents=cash_and_equivalents,
            shares_outstanding=shares_outstanding,
        )
    except ValueError:
        # Every function above raises (never returns NaN/infinity) for an
        # economically invalid or numerically-overflowing combination —
        # e.g. WACC and terminal growth separated by only a minuscule
        # margin. That maps to this module's own "invalid cell" contract:
        # `None`, not a fabricated or non-finite number.
        return None


def compute_dcf_sensitivity(
    fcf_projection: pd.DataFrame,
    baseline_wacc: float,
    baseline_terminal_growth_rate: float,
    total_debt: Optional[float],
    cash_and_equivalents: Optional[float],
    shares_outstanding: Optional[float],
) -> SensitivityMatrix:
    """
    Build the 5x5 WACC x terminal-growth sensitivity grid around a
    completed valuation's own baseline assumptions.

    Args:
        fcf_projection: The baseline `project_free_cash_flows` output
            (from `run_dcf_valuation`'s own return value) — reused
            unchanged for every cell; this function never re-projects FCF.
        baseline_wacc: The baseline valuation's own computed WACC
            (`run_dcf_valuation`'s `"wacc"`).
        baseline_terminal_growth_rate: The baseline valuation's own
            terminal growth assumption.
        total_debt: The equity bridge's total debt, held constant across
            every cell (`run_dcf_valuation`'s `"total_debt"`).
        cash_and_equivalents: The equity bridge's cash & equivalents, held
            constant across every cell (`run_dcf_valuation`'s
            `"cash_and_equivalents"`).
        shares_outstanding: Shares outstanding, held constant across
            every cell (`run_dcf_valuation`'s `"shares_outstanding"`).

    Returns:
        A `SensitivityMatrix` whose baseline cell
        (`cells[baseline_row][baseline_col]`) reproduces the same
        intrinsic value per share the baseline valuation itself computed,
        since it is derived from the identical inputs via the identical
        functions.

    Makes no data-provider call and re-runs no FCF projection — every
    input is already-computed data the caller already has in memory from
    running the baseline valuation.
    """
    final_year_fcf = float(fcf_projection["fcf"].iloc[-1])

    wacc_values = [baseline_wacc + step * WACC_STEP for step in WACC_OFFSET_STEPS]
    terminal_growth_values = [
        baseline_terminal_growth_rate + step * TERMINAL_GROWTH_STEP for step in TERMINAL_GROWTH_OFFSET_STEPS
    ]

    cells: List[List[Optional[float]]] = [
        [
            _compute_cell(
                fcf_projection,
                final_year_fcf,
                wacc,
                terminal_growth_rate,
                total_debt,
                cash_and_equivalents,
                shares_outstanding,
            )
            for terminal_growth_rate in terminal_growth_values
        ]
        for wacc in wacc_values
    ]

    baseline_row = WACC_OFFSET_STEPS.index(0)
    baseline_col = TERMINAL_GROWTH_OFFSET_STEPS.index(0)

    return SensitivityMatrix(
        wacc_axis=SensitivityAxis(label="WACC", values=wacc_values, baseline_index=baseline_row),
        terminal_growth_axis=SensitivityAxis(
            label="Terminal Growth Rate", values=terminal_growth_values, baseline_index=baseline_col
        ),
        cells=cells,
        baseline_row=baseline_row,
        baseline_col=baseline_col,
        baseline_wacc=baseline_wacc,
        baseline_terminal_growth_rate=baseline_terminal_growth_rate,
        baseline_intrinsic_value_per_share=cells[baseline_row][baseline_col],
    )
