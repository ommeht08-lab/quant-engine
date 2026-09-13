"""Synthetic, offline checks for the optional multi-stage forecast policy."""

import pandas as pd
import pytest

from src.api.sector_medians import _serialize_comparable_assumptions
from src.dcf_model.dcf import (
    DCFAssumptions,
    MultiStageForecastPolicy,
    project_free_cash_flows,
    project_free_cash_flows_from_path,
    run_dcf_valuation,
)
from src.dcf_model.scenarios import ScenarioInputs, compute_dcf_scenarios
from validation.dcf_reconciliation.adapter import TICKERS, load_all


def _financials(latest_revenue=1100, operating_margin=.20):
    return {
        "ticker": "TEST",
        "income_statement": pd.DataFrame({
            pd.Timestamp("2022-12-31"): {
                "Total Revenue": 1000, "Operating Income": 1000 * operating_margin,
                "Pretax Income": 200, "Tax Provision": 50,
            },
            pd.Timestamp("2023-12-31"): {
                "Total Revenue": latest_revenue,
                "Operating Income": latest_revenue * operating_margin,
                "Pretax Income": 220, "Tax Provision": 55,
            },
        }),
        "balance_sheet": pd.DataFrame({
            pd.Timestamp("2023-12-31"): {"Total Debt": 100, "Cash And Cash Equivalents": 50},
        }),
        "cash_flow": None,
        "current_price": 50,
        "shares_outstanding": 100,
        "beta": 1,
    }


def _scenario_inputs(result, assumptions):
    return ScenarioInputs(
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
        baseline_forecast_path=result["forecast_path"],
    )


def test_maturation_fades_only_excess_growth_and_preserves_margin():
    path = MultiStageForecastPolicy().build_path(.10, .20, 5)
    assert [step.stage for step in path] == ["near_term", "near_term", "maturation", "maturation", "maturation"]
    assert [step.revenue_growth_rate for step in path] == pytest.approx([.10, .10, .10 - .07 / 3, .10 - 2 * .07 / 3, .03])
    assert [step.operating_margin for step in path] == [.20] * 5
    weak_path = MultiStageForecastPolicy().build_path(-.02, -.10, 5)
    assert [step.revenue_growth_rate for step in weak_path] == [-.02] * 5
    assert [step.operating_margin for step in weak_path] == [-.10] * 5


def test_path_projection_matches_constant_formula_when_years_are_flat():
    path = MultiStageForecastPolicy().build_path(.02, .20, 5)
    actual = project_free_cash_flows_from_path(1000, path)
    expected = project_free_cash_flows(1000, .02, .20, years=5)
    pd.testing.assert_frame_equal(actual, expected)


def test_base_scenario_reproduces_multistage_valuation_exactly():
    assumptions = DCFAssumptions(forecast_policy=MultiStageForecastPolicy(), risk_free_rate=.04)
    result = run_dcf_valuation(_financials(), assumptions)
    cases = compute_dcf_scenarios(_scenario_inputs(result, assumptions))
    assert result["forecast_method"] == "maturation"
    assert cases.base.intrinsic_value_per_share == result["intrinsic_value_per_share"]
    assert cases.bear.intrinsic_value_per_share < cases.base.intrinsic_value_per_share
    assert cases.bull.intrinsic_value_per_share > cases.base.intrinsic_value_per_share


@pytest.mark.parametrize("latest_revenue,margin", [
    (750, -.20), (980, -.02), (1000, .05), (1100, .20), (2000, .80),
])
def test_staged_cash_flow_and_scenario_inputs_across_issuer_shapes(latest_revenue, margin):
    assumptions = DCFAssumptions(forecast_policy=MultiStageForecastPolicy(), risk_free_rate=.04)
    result = run_dcf_valuation(_financials(latest_revenue, margin), assumptions)
    cases = compute_dcf_scenarios(_scenario_inputs(result, assumptions))

    assert cases.base.intrinsic_value_per_share == result["intrinsic_value_per_share"]
    assert cases.bear.assumptions.revenue_growth_rate < cases.base.assumptions.revenue_growth_rate
    assert cases.bull.assumptions.revenue_growth_rate > cases.base.assumptions.revenue_growth_rate
    assert cases.bear.assumptions.operating_margin < cases.base.assumptions.operating_margin
    assert cases.bull.assumptions.operating_margin > cases.base.assumptions.operating_margin

    prior_revenue = result["base_revenue"]
    for step, (_, row) in zip(result["forecast_path"], result["fcf_projection"].iterrows()):
        revenue = prior_revenue * (1 + step.revenue_growth_rate)
        ebit = revenue * step.operating_margin
        fcf = (
            ebit * (1 - result["tax_rate"])
            + revenue * assumptions.da_pct_revenue
            - revenue * assumptions.capex_pct_revenue
            - assumptions.nwc_pct_revenue_change * (revenue - prior_revenue)
        )
        assert row["revenue"] == pytest.approx(revenue)
        assert row["ebit"] == pytest.approx(ebit)
        assert row["fcf"] == pytest.approx(fcf)
        prior_revenue = revenue


@pytest.mark.parametrize("ticker", TICKERS)
def test_staged_path_on_frozen_multi_company_snapshots(ticker):
    # Four committed offline statement bundles, with no provider or network call.
    _, financial_data = load_all()[ticker]
    assumptions = DCFAssumptions(forecast_policy=MultiStageForecastPolicy())
    result = run_dcf_valuation(financial_data, assumptions)
    cases = compute_dcf_scenarios(_scenario_inputs(result, assumptions))

    assert result["forecast_method"] == "maturation"
    assert [step.stage for step in result["forecast_path"]] == [
        "near_term", "near_term", "maturation", "maturation", "maturation"
    ]
    assert cases.base.intrinsic_value_per_share == result["intrinsic_value_per_share"]


def test_flat_peer_snapshot_is_incompatible_with_multistage_policy():
    flat = _serialize_comparable_assumptions(DCFAssumptions())
    staged = _serialize_comparable_assumptions(DCFAssumptions(forecast_policy=MultiStageForecastPolicy()))
    assert flat != staged
    assert "forecast_policy" not in flat
    assert staged["forecast_policy"]["method"] == "maturation"


@pytest.mark.parametrize("bad", [True, 0, -1, 1.5])
def test_invalid_near_term_horizon_rejected(bad):
    with pytest.raises(ValueError):
        MultiStageForecastPolicy(near_term_years=bad)


def test_nonconsecutive_path_refused():
    path = MultiStageForecastPolicy().build_path(.10, .20, 5)
    with pytest.raises(ValueError):
        project_free_cash_flows_from_path(1000, path[1:])
