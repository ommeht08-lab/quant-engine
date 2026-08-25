"""
Bear/Base/Bull DCF scenarios (`src.dcf_model.scenarios`): baseline
(custom or historical) anchoring, exact delta application, bounds
clamping, ordinary bear < base < bull ordering, invalid-after-clamping
handling (never NaN/infinity), and negative-value support.

Exercises only the public module interface (`compute_dcf_scenarios` /
`ScenarioInputs`) — never a private helper.
"""

import math

import pandas as pd
import pytest

from src.dcf_model.dcf import (
    DCFAssumptions,
    MAX_DISCOUNT_RATE,
    MAX_EXPLICIT_OPERATING_MARGIN,
    MAX_EXPLICIT_TERMINAL_GROWTH_RATE,
    MIN_DISCOUNT_RATE,
    run_dcf_valuation,
)
from src.dcf_model.scenarios import (
    BEAR_OPERATING_MARGIN_DELTA,
    BEAR_REVENUE_GROWTH_DELTA,
    BEAR_TERMINAL_GROWTH_DELTA,
    BEAR_WACC_DELTA,
    BULL_OPERATING_MARGIN_DELTA,
    BULL_REVENUE_GROWTH_DELTA,
    BULL_TERMINAL_GROWTH_DELTA,
    BULL_WACC_DELTA,
    ScenarioInputs,
    compute_dcf_scenarios,
)

DEFAULT_INPUTS_KWARGS = dict(
    base_revenue=1000.0,
    tax_rate=0.21,
    da_pct_revenue=0.03,
    capex_pct_revenue=0.04,
    nwc_pct_revenue_change=0.01,
    projection_years=5,
    total_debt=100.0,
    cash_and_equivalents=50.0,
    shares_outstanding=100.0,
)


def _scenario_inputs(
    baseline_wacc: float,
    baseline_terminal_growth_rate: float,
    baseline_revenue_growth_rate: float = 0.08,
    baseline_operating_margin: float = 0.25,
    **overrides,
) -> ScenarioInputs:
    kwargs = {**DEFAULT_INPUTS_KWARGS, **overrides}
    return ScenarioInputs(
        baseline_wacc=baseline_wacc,
        baseline_terminal_growth_rate=baseline_terminal_growth_rate,
        baseline_revenue_growth_rate=baseline_revenue_growth_rate,
        baseline_operating_margin=baseline_operating_margin,
        **kwargs,
    )


def _synthetic_financial_data(total_debt: float = 100.0, cash: float = 50.0) -> dict:
    income_stmt = pd.DataFrame(
        {
            pd.Timestamp("2022-12-31"): {"Total Revenue": 1000.0, "Pretax Income": 200.0, "Tax Provision": 50.0},
            pd.Timestamp("2023-12-31"): {"Total Revenue": 1100.0, "Pretax Income": 220.0, "Tax Provision": 55.0},
        }
    )
    balance_sheet = pd.DataFrame(
        {pd.Timestamp("2023-12-31"): {"Total Debt": total_debt, "Cash And Cash Equivalents": cash}}
    )
    return {
        "income_statement": income_stmt,
        "balance_sheet": balance_sheet,
        "cash_flow": None,
        "current_price": 50.0,
        "shares_outstanding": 100.0,
        "beta": 1.0,
    }


class TestBaselineConsistency:
    def test_base_scenario_matches_normal_intrinsic_value_exactly(self):
        """Base runs through the SAME code path as Bear/Bull with a zero
        delta, so it must reproduce run_dcf_valuation's own intrinsic
        value exactly -- not merely approximately."""
        financial_data = _synthetic_financial_data()
        assumptions = DCFAssumptions(revenue_growth_rate=0.08, operating_margin=0.25, terminal_growth_rate=0.025)
        result = run_dcf_valuation(financial_data, assumptions)

        inputs = ScenarioInputs(
            base_revenue=result["base_revenue"],
            baseline_revenue_growth_rate=result["revenue_growth_rate"],
            baseline_operating_margin=result["operating_margin"],
            baseline_wacc=result["wacc"],
            baseline_terminal_growth_rate=assumptions.terminal_growth_rate,
            tax_rate=result["tax_rate"],
            da_pct_revenue=assumptions.da_pct_revenue,
            capex_pct_revenue=assumptions.capex_pct_revenue,
            nwc_pct_revenue_change=assumptions.nwc_pct_revenue_change,
            projection_years=assumptions.projection_years,
            total_debt=result["total_debt"],
            cash_and_equivalents=result["cash_and_equivalents"],
            shares_outstanding=result["shares_outstanding"],
        )

        scenarios = compute_dcf_scenarios(inputs)

        assert scenarios.base.is_valid
        assert scenarios.base.intrinsic_value_per_share == pytest.approx(
            result["intrinsic_value_per_share"], rel=1e-9
        )
        assert scenarios.base.assumptions.revenue_growth_rate == result["revenue_growth_rate"]
        assert scenarios.base.assumptions.operating_margin == result["operating_margin"]
        assert scenarios.base.assumptions.wacc == result["wacc"]
        assert scenarios.base.assumptions.terminal_growth_rate == assumptions.terminal_growth_rate


class TestDeltasAndCustomBaselineAnchoring:
    def test_bear_and_bull_apply_the_documented_deltas_to_a_comfortably_in_bounds_baseline(self):
        """A baseline in the middle of every bound means no clamping
        applies -- the resulting assumptions must equal baseline +/- the
        exact documented deltas."""
        inputs = _scenario_inputs(
            baseline_wacc=0.10,
            baseline_terminal_growth_rate=0.02,
            baseline_revenue_growth_rate=0.08,
            baseline_operating_margin=0.25,
        )

        scenarios = compute_dcf_scenarios(inputs)

        assert scenarios.bear.assumptions.revenue_growth_rate == pytest.approx(0.08 + BEAR_REVENUE_GROWTH_DELTA)
        assert scenarios.bear.assumptions.operating_margin == pytest.approx(0.25 + BEAR_OPERATING_MARGIN_DELTA)
        assert scenarios.bear.assumptions.wacc == pytest.approx(0.10 + BEAR_WACC_DELTA)
        assert scenarios.bear.assumptions.terminal_growth_rate == pytest.approx(0.02 + BEAR_TERMINAL_GROWTH_DELTA)

        assert scenarios.bull.assumptions.revenue_growth_rate == pytest.approx(0.08 + BULL_REVENUE_GROWTH_DELTA)
        assert scenarios.bull.assumptions.operating_margin == pytest.approx(0.25 + BULL_OPERATING_MARGIN_DELTA)
        assert scenarios.bull.assumptions.wacc == pytest.approx(0.10 + BULL_WACC_DELTA)
        assert scenarios.bull.assumptions.terminal_growth_rate == pytest.approx(0.02 + BULL_TERMINAL_GROWTH_DELTA)

    def test_anchors_to_a_custom_baseline_not_a_hardcoded_default(self):
        """The baseline here (30% growth, 45% margin) looks nothing like
        the model's own historical/default fallbacks -- scenarios must
        still be built relative to THIS baseline, proving they anchor to
        whatever the caller's actual valuation used (a custom slider
        override included), not a hardcoded assumption."""
        inputs = _scenario_inputs(
            baseline_wacc=0.12,
            baseline_terminal_growth_rate=0.03,
            baseline_revenue_growth_rate=0.30,
            baseline_operating_margin=0.45,
        )

        scenarios = compute_dcf_scenarios(inputs)

        assert scenarios.bear.assumptions.revenue_growth_rate == pytest.approx(0.30 + BEAR_REVENUE_GROWTH_DELTA)
        assert scenarios.bear.assumptions.operating_margin == pytest.approx(0.45 + BEAR_OPERATING_MARGIN_DELTA)
        assert scenarios.bull.assumptions.revenue_growth_rate == pytest.approx(0.30 + BULL_REVENUE_GROWTH_DELTA)
        assert scenarios.bull.assumptions.operating_margin == pytest.approx(0.45 + BULL_OPERATING_MARGIN_DELTA)
        assert scenarios.base.assumptions.revenue_growth_rate == 0.30
        assert scenarios.base.assumptions.operating_margin == 0.45


class TestBoundsClamping:
    def test_bull_margin_clamps_to_the_explicit_ceiling_instead_of_exceeding_it(self):
        """Baseline margin is already only 1pp below the ceiling -- Bull's
        +2pp delta must clamp to the ceiling, not silently exceed it."""
        inputs = _scenario_inputs(
            baseline_wacc=0.10,
            baseline_terminal_growth_rate=0.02,
            baseline_operating_margin=MAX_EXPLICIT_OPERATING_MARGIN - 0.01,
        )

        scenarios = compute_dcf_scenarios(inputs)

        assert scenarios.bull.assumptions.operating_margin == MAX_EXPLICIT_OPERATING_MARGIN
        assert scenarios.bull.is_valid

    def test_bull_wacc_clamps_to_the_floor_instead_of_going_below_it(self):
        """Baseline WACC is already at its own floor -- Bull's -1pp delta
        must clamp back to the floor, not go below it."""
        inputs = _scenario_inputs(baseline_wacc=MIN_DISCOUNT_RATE, baseline_terminal_growth_rate=0.0)

        scenarios = compute_dcf_scenarios(inputs)

        assert scenarios.bull.assumptions.wacc == MIN_DISCOUNT_RATE

    def test_bear_wacc_clamps_to_the_ceiling_instead_of_exceeding_it(self):
        inputs = _scenario_inputs(baseline_wacc=MAX_DISCOUNT_RATE, baseline_terminal_growth_rate=0.02)

        scenarios = compute_dcf_scenarios(inputs)

        assert scenarios.bear.assumptions.wacc == MAX_DISCOUNT_RATE


class TestOrdinaryOrdering:
    def test_bear_less_than_base_less_than_bull_in_an_ordinary_scenario(self):
        inputs = _scenario_inputs(baseline_wacc=0.10, baseline_terminal_growth_rate=0.02)

        scenarios = compute_dcf_scenarios(inputs)

        assert scenarios.bear.is_valid and scenarios.base.is_valid and scenarios.bull.is_valid
        bear_value = scenarios.bear.intrinsic_value_per_share
        base_value = scenarios.base.intrinsic_value_per_share
        bull_value = scenarios.bull.intrinsic_value_per_share
        assert bear_value < base_value < bull_value


class TestInvalidScenario:
    def test_bull_wacc_less_than_or_equal_to_terminal_growth_after_clamping_is_null(self):
        """
        Baseline WACC (0.055) minus Bull's -1pp delta (0.045) is below
        MIN_DISCOUNT_RATE, so clamping SELECTS the floor constant exactly
        (0.05) -- and baseline terminal growth is set to exactly
        MAX_EXPLICIT_TERMINAL_GROWTH_RATE (0.05) so Bull's +0.5pp delta
        (0.055) exceeds the ceiling, and clamping SELECTS that same
        ceiling constant exactly (0.05) too. Both sides land on the
        identical literal 0.05 via min()/max() picking one of their
        operands verbatim -- never via two independently-computed
        additions that might not agree to the last floating-point bit
        (e.g. 0.045 + 0.005 != 0.05 - 0.005 in IEEE 754 double
        arithmetic) -- so wacc<=terminal_growth is a deterministic,
        exact equality here, not a hairline float-precision accident.
        """
        inputs = _scenario_inputs(baseline_wacc=0.055, baseline_terminal_growth_rate=MAX_EXPLICIT_TERMINAL_GROWTH_RATE)

        scenarios = compute_dcf_scenarios(inputs)

        assert scenarios.bull.assumptions.wacc == MIN_DISCOUNT_RATE
        assert scenarios.bull.assumptions.terminal_growth_rate == MAX_EXPLICIT_TERMINAL_GROWTH_RATE
        assert scenarios.bull.assumptions.wacc == scenarios.bull.assumptions.terminal_growth_rate
        assert scenarios.bull.is_valid is False
        assert scenarios.bull.intrinsic_value_per_share is None
        assert isinstance(scenarios.bull.invalid_reason, str) and scenarios.bull.invalid_reason.strip() != ""

        # An invalid scenario never affects the others.
        assert scenarios.base.is_valid
        assert scenarios.bear.is_valid

    def test_invalid_scenario_never_reports_nan_or_infinite(self):
        inputs = _scenario_inputs(baseline_wacc=0.055, baseline_terminal_growth_rate=MAX_EXPLICIT_TERMINAL_GROWTH_RATE)

        scenarios = compute_dcf_scenarios(inputs)

        for scenario in (scenarios.bear, scenarios.base, scenarios.bull):
            for value in (
                scenario.assumptions.revenue_growth_rate,
                scenario.assumptions.operating_margin,
                scenario.assumptions.wacc,
                scenario.assumptions.terminal_growth_rate,
            ):
                assert math.isfinite(value)
            if scenario.intrinsic_value_per_share is not None:
                assert math.isfinite(scenario.intrinsic_value_per_share)


class TestNegativeValues:
    def test_negative_intrinsic_value_is_valid_and_finite_across_all_scenarios(self):
        """Debt large enough relative to enterprise value pushes equity
        value negative for every scenario -- still valid and finite, not
        None and not NaN/infinity."""
        inputs = _scenario_inputs(
            baseline_wacc=0.12,
            baseline_terminal_growth_rate=0.02,
            baseline_revenue_growth_rate=0.02,
            baseline_operating_margin=0.10,
            total_debt=1_000_000.0,
            cash_and_equivalents=0.0,
        )

        scenarios = compute_dcf_scenarios(inputs)

        for scenario in (scenarios.bear, scenarios.base, scenarios.bull):
            assert scenario.is_valid
            assert scenario.intrinsic_value_per_share is not None
            assert math.isfinite(scenario.intrinsic_value_per_share)
            assert scenario.intrinsic_value_per_share < 0
