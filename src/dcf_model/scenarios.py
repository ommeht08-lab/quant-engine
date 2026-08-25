"""
Bear / Base / Bull DCF valuation scenarios: three transparent policy
cases anchored to a completed valuation's own baseline assumptions —
not probabilities, forecasts, or price targets.

This module is a pure, read-only consumer of the DCF model seam in
`src.dcf_model.dcf`. Every scenario is produced by calling the SAME
`project_free_cash_flows` / `calculate_terminal_value` /
`discount_to_present_value` / `calculate_intrinsic_value_per_share`
functions the baseline valuation itself calls, with the baseline's own
already-fetched source data (base revenue, tax rate, D&A/CapEx/NWC
policy, projection horizon) and equity bridge (total debt, cash &
equivalents, shares outstanding) held fixed. No financial formula is
reimplemented here, and nothing in this module fetches data or makes a
provider call — every input is already-computed data the caller already
has in memory from running the baseline valuation.

Bear and Bull each apply a fixed policy delta to the baseline's growth,
margin, WACC, and terminal growth, clamped to the DCF model's own
declared economic bounds; Base applies a zero delta, which — because it
runs through the identical code path with the identical inputs —
reproduces the baseline valuation exactly. A scenario whose (clamped)
assumptions are still not economically valid for the model (e.g. WACC no
longer exceeds terminal growth) reports a `None` valuation and a concise
reason instead of raising — it never fails the baseline response.
"""

from dataclasses import dataclass
from typing import Optional

from src.dcf_model.dcf import (
    MAX_DISCOUNT_RATE,
    MAX_EXPLICIT_OPERATING_MARGIN,
    MAX_EXPLICIT_REVENUE_GROWTH_RATE,
    MAX_EXPLICIT_TERMINAL_GROWTH_RATE,
    MIN_DISCOUNT_RATE,
    MIN_EXPLICIT_OPERATING_MARGIN,
    MIN_EXPLICIT_REVENUE_GROWTH_RATE,
    MIN_EXPLICIT_TERMINAL_GROWTH_RATE,
    calculate_intrinsic_value_per_share,
    calculate_terminal_value,
    discount_to_present_value,
    project_free_cash_flows,
)

# Fixed policy deltas applied to the baseline's own growth/margin/WACC/
# terminal-growth. Base applies a zero delta on every axis.
BEAR_REVENUE_GROWTH_DELTA = -0.03
BEAR_OPERATING_MARGIN_DELTA = -0.02
BEAR_WACC_DELTA = 0.01
BEAR_TERMINAL_GROWTH_DELTA = -0.005

BULL_REVENUE_GROWTH_DELTA = 0.03
BULL_OPERATING_MARGIN_DELTA = 0.02
BULL_WACC_DELTA = -0.01
BULL_TERMINAL_GROWTH_DELTA = 0.005


@dataclass
class ScenarioInputs:
    """
    Everything a scenario needs, already computed by the baseline
    valuation — nothing here triggers a data-provider call or re-derives
    anything from raw financial statements.
    """

    base_revenue: float
    baseline_revenue_growth_rate: float
    baseline_operating_margin: float
    baseline_wacc: float
    baseline_terminal_growth_rate: float
    # Held fixed across every scenario (the DCF model's "source financial
    # data" beyond growth/margin/WACC/terminal-growth themselves).
    tax_rate: float
    da_pct_revenue: float
    capex_pct_revenue: float
    nwc_pct_revenue_change: float
    projection_years: int
    # The equity bridge — held fixed across every scenario.
    total_debt: Optional[float]
    cash_and_equivalents: Optional[float]
    shares_outstanding: Optional[float]


@dataclass
class ScenarioAssumptions:
    """The (clamped) assumptions actually used for one scenario, reported
    even when the scenario itself turned out not to be computable."""

    revenue_growth_rate: float
    operating_margin: float
    wacc: float
    terminal_growth_rate: float


@dataclass
class ScenarioResult:
    name: str  # "bear" | "base" | "bull"
    assumptions: ScenarioAssumptions
    intrinsic_value_per_share: Optional[float]
    is_valid: bool
    # A concise reason this scenario is not computable, or `None` when `is_valid` is True.
    invalid_reason: Optional[str]


@dataclass
class ScenarioSet:
    bear: ScenarioResult
    base: ScenarioResult
    bull: ScenarioResult


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _compute_scenario(
    name: str,
    revenue_growth_delta: float,
    operating_margin_delta: float,
    wacc_delta: float,
    terminal_growth_delta: float,
    inputs: ScenarioInputs,
) -> ScenarioResult:
    revenue_growth_rate = _clamp(
        inputs.baseline_revenue_growth_rate + revenue_growth_delta,
        MIN_EXPLICIT_REVENUE_GROWTH_RATE,
        MAX_EXPLICIT_REVENUE_GROWTH_RATE,
    )
    operating_margin = _clamp(
        inputs.baseline_operating_margin + operating_margin_delta,
        MIN_EXPLICIT_OPERATING_MARGIN,
        MAX_EXPLICIT_OPERATING_MARGIN,
    )
    wacc = _clamp(inputs.baseline_wacc + wacc_delta, MIN_DISCOUNT_RATE, MAX_DISCOUNT_RATE)
    terminal_growth_rate = _clamp(
        inputs.baseline_terminal_growth_rate + terminal_growth_delta,
        MIN_EXPLICIT_TERMINAL_GROWTH_RATE,
        MAX_EXPLICIT_TERMINAL_GROWTH_RATE,
    )
    assumptions = ScenarioAssumptions(
        revenue_growth_rate=revenue_growth_rate,
        operating_margin=operating_margin,
        wacc=wacc,
        terminal_growth_rate=terminal_growth_rate,
    )

    try:
        fcf_projection = project_free_cash_flows(
            base_revenue=inputs.base_revenue,
            revenue_growth_rate=revenue_growth_rate,
            operating_margin=operating_margin,
            tax_rate=inputs.tax_rate,
            da_pct_revenue=inputs.da_pct_revenue,
            capex_pct_revenue=inputs.capex_pct_revenue,
            nwc_pct_revenue_change=inputs.nwc_pct_revenue_change,
            years=inputs.projection_years,
        )
        terminal_value = calculate_terminal_value(
            final_year_fcf=float(fcf_projection["fcf"].iloc[-1]),
            wacc=wacc,
            terminal_growth_rate=terminal_growth_rate,
        )
        discounting = discount_to_present_value(fcf_projection, terminal_value, wacc)
        intrinsic_value_per_share = calculate_intrinsic_value_per_share(
            enterprise_value=discounting["enterprise_value"],
            total_debt=inputs.total_debt,
            cash_and_equivalents=inputs.cash_and_equivalents,
            shares_outstanding=inputs.shares_outstanding,
        )
    except ValueError as exc:
        # Every function above raises (never returns NaN/infinity) for an
        # economically invalid or numerically-overflowing combination —
        # its own message is already a concise, accurate description of
        # why, so it's reused as-is rather than re-explained here.
        return ScenarioResult(
            name=name,
            assumptions=assumptions,
            intrinsic_value_per_share=None,
            is_valid=False,
            invalid_reason=str(exc),
        )

    return ScenarioResult(
        name=name,
        assumptions=assumptions,
        intrinsic_value_per_share=intrinsic_value_per_share,
        is_valid=True,
        invalid_reason=None,
    )


def compute_dcf_scenarios(inputs: ScenarioInputs) -> ScenarioSet:
    """
    Build the Bear / Base / Bull scenario set anchored to a completed
    valuation's own baseline assumptions.

    Base uses a zero delta on every axis and runs through the identical
    code path as Bear/Bull, so it reproduces the baseline valuation
    exactly (same base revenue, growth, margin, tax rate, D&A/CapEx/NWC
    policy, and projection horizon -> the identical `fcf_projection`;
    same WACC and terminal growth -> the identical terminal value and
    discounting; same equity bridge -> the identical intrinsic value per
    share).

    Never raises: a scenario that isn't economically valid after
    clamping (e.g. WACC no longer exceeds terminal growth) is reported
    with `intrinsic_value_per_share=None` and a concise `invalid_reason`,
    not an exception — so one bad scenario can never fail the others or
    the baseline response.
    """
    return ScenarioSet(
        bear=_compute_scenario(
            "bear",
            BEAR_REVENUE_GROWTH_DELTA,
            BEAR_OPERATING_MARGIN_DELTA,
            BEAR_WACC_DELTA,
            BEAR_TERMINAL_GROWTH_DELTA,
            inputs,
        ),
        base=_compute_scenario("base", 0.0, 0.0, 0.0, 0.0, inputs),
        bull=_compute_scenario(
            "bull",
            BULL_REVENUE_GROWTH_DELTA,
            BULL_OPERATING_MARGIN_DELTA,
            BULL_WACC_DELTA,
            BULL_TERMINAL_GROWTH_DELTA,
            inputs,
        ),
    )
