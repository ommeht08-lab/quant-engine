"""Frozen and synthetic evidence for interpretation, not DCF arithmetic."""

from types import SimpleNamespace

import pandas as pd
import pytest

from src.dcf_model.dcf import DCFAssumptions, MultiStageForecastPolicy, run_dcf_valuation
from src.dcf_model.quality import assess_valuation_quality
from src.dcf_model.scenarios import ScenarioInputs, compute_dcf_scenarios
from validation.dcf_reconciliation.adapter import load_all


def _assess(financials, tax_rate=None):
    assumptions = DCFAssumptions(
        forecast_policy=MultiStageForecastPolicy(), risk_free_rate=0.04,
        tax_rate=tax_rate,
    )
    result = run_dcf_valuation(financials, assumptions)
    scenarios = compute_dcf_scenarios(ScenarioInputs(
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
    ))
    quality = assess_valuation_quality(
        terminal_fcf=float(result["fcf_projection"]["fcf"].iloc[-1]),
        enterprise_value=result["enterprise_value"],
        pv_terminal_value=result["pv_terminal_value"],
        tax_rate=result["tax_rate"],
        tax_rate_source=result["tax_rate_source"],
        scenarios=scenarios,
    )
    return result, scenarios, quality


@pytest.mark.parametrize("ticker", ["MSFT", "CAT"])
def test_positive_frozen_companies_remain_ordinary(ticker):
    _, financials = load_all()[ticker]
    result, _, quality = _assess(financials)
    assert result["tax_rate_source"] == "historical"
    assert quality.level == "ordinary"
    assert quality.codes == ()
    assert quality.allows_market_comparison
    assert 0 < quality.terminal_value_share_of_enterprise_value < 0.80


def test_intc_frozen_loss_and_reversed_case_values_are_diagnostic():
    _, financials = load_all()["INTC"]
    result, scenarios, quality = _assess(financials)
    assert result["intrinsic_value_per_share"] == scenarios.base.intrinsic_value_per_share
    assert scenarios.bear.intrinsic_value_per_share > scenarios.base.intrinsic_value_per_share
    assert scenarios.base.intrinsic_value_per_share > scenarios.bull.intrinsic_value_per_share
    assert quality.level == "diagnostic_only"
    assert set(quality.codes) == {
        "nonpositive_terminal_fcf", "nonpositive_enterprise_value",
        "reversed_scenario_values", "extreme_observed_tax_rate",
    }
    assert quality.observed_effective_tax_rate == pytest.approx(0.9833, abs=0.0001)
    assert quality.terminal_value_share_of_enterprise_value is None
    assert not quality.allows_market_comparison


def test_vz_terminal_concentration_is_caution_not_arithmetic_failure():
    _, financials = load_all()["VZ"]
    result, scenarios, quality = _assess(financials)
    assert result["wacc_was_clamped"]
    assert result["wacc"] == 0.05
    assert scenarios.bear.intrinsic_value_per_share < scenarios.base.intrinsic_value_per_share
    assert scenarios.base.intrinsic_value_per_share < scenarios.bull.intrinsic_value_per_share
    assert quality.level == "caution"
    assert quality.codes == ("high_terminal_value_concentration",)
    assert quality.terminal_value_share_of_enterprise_value == pytest.approx(0.8831, abs=0.0001)
    assert not quality.allows_market_comparison


def test_negative_historical_margin_is_diagnostic_without_clamping():
    income = pd.DataFrame({
        pd.Timestamp("2022-12-31"): {"Total Revenue": 1000, "Operating Income": -200,
                                     "Pretax Income": 200, "Tax Provision": 50},
        pd.Timestamp("2023-12-31"): {"Total Revenue": 1100, "Operating Income": -220,
                                     "Pretax Income": 220, "Tax Provision": 55},
    })
    financials = {
        "ticker": "SYNTH", "income_statement": income,
        "balance_sheet": pd.DataFrame({pd.Timestamp("2023-12-31"): {
            "Total Debt": 100, "Cash And Cash Equivalents": 50,
        }}),
        "cash_flow": None, "current_price": 50, "shares_outstanding": 100, "beta": 1,
    }
    result, scenarios, quality = _assess(financials)
    assert result["operating_margin"] == pytest.approx(-0.20)
    assert scenarios.bear.assumptions.operating_margin == pytest.approx(-0.22)
    assert scenarios.bear.intrinsic_value_per_share > scenarios.base.intrinsic_value_per_share
    assert scenarios.base.intrinsic_value_per_share > scenarios.bull.intrinsic_value_per_share
    assert quality.level == "diagnostic_only"
    assert "reversed_scenario_values" in quality.codes
    assert "nonpositive_terminal_fcf" in quality.codes
    assert not quality.allows_market_comparison


def test_only_observed_tax_is_labeled_extreme():
    _, financials = load_all()["INTC"]
    result, _, quality = _assess(financials, tax_rate=0.61)
    assert result["tax_rate_source"] == "custom"
    assert quality.observed_effective_tax_rate is None
    assert "extreme_observed_tax_rate" not in quality.codes


def test_quality_thresholds_are_inclusive_and_uncomputable_order_is_not_guessed():
    ordered = SimpleNamespace(
        bear=SimpleNamespace(intrinsic_value_per_share=1),
        base=SimpleNamespace(intrinsic_value_per_share=2),
        bull=SimpleNamespace(intrinsic_value_per_share=3),
    )
    quality = assess_valuation_quality(
        terminal_fcf=1, enterprise_value=100, pv_terminal_value=80,
        tax_rate=0.60, tax_rate_source="historical", scenarios=ordered,
    )
    assert quality.level == "caution"
    assert quality.codes == (
        "extreme_observed_tax_rate", "high_terminal_value_concentration",
    )
    assert not quality.allows_market_comparison

    incomplete = SimpleNamespace(
        bear=SimpleNamespace(intrinsic_value_per_share=None),
        base=ordered.base, bull=ordered.bull,
    )
    quality = assess_valuation_quality(
        terminal_fcf=0, enterprise_value=0, pv_terminal_value=-1,
        tax_rate=0.60, tax_rate_source="fallback", scenarios=incomplete,
    )
    assert quality.level == "diagnostic_only"
    assert quality.codes == ("nonpositive_terminal_fcf", "nonpositive_enterprise_value")
    assert quality.terminal_value_share_of_enterprise_value is None


@pytest.mark.parametrize("terminal_fcf,enterprise_value,expected_codes", [
    (0, 100, ("nonpositive_terminal_fcf",)),
    (-1, 100, ("nonpositive_terminal_fcf",)),
    (1, 0, ("nonpositive_enterprise_value",)),
    (1, -100, ("nonpositive_enterprise_value",)),
])
def test_nonpositive_boundaries_are_diagnostic_and_never_form_negative_ev_share(
    terminal_fcf, enterprise_value, expected_codes
):
    ordered = SimpleNamespace(**{
        key: SimpleNamespace(intrinsic_value_per_share=value)
        for key, value in (("bear", 1), ("base", 2), ("bull", 3))
    })
    quality = assess_valuation_quality(
        terminal_fcf=terminal_fcf, enterprise_value=enterprise_value,
        pv_terminal_value=50, tax_rate=0.59, tax_rate_source="historical",
        scenarios=ordered,
    )
    assert quality.level == "diagnostic_only"
    assert quality.codes == expected_codes
    assert quality.terminal_value_share_of_enterprise_value == (
        0.5 if enterprise_value > 0 else None
    )
    assert not quality.allows_market_comparison


def test_below_review_thresholds_and_fallback_tax_do_not_flag():
    ordered = SimpleNamespace(**{
        key: SimpleNamespace(intrinsic_value_per_share=value)
        for key, value in (("bear", 1), ("base", 2), ("bull", 3))
    })
    quality = assess_valuation_quality(
        terminal_fcf=1, enterprise_value=100, pv_terminal_value=79.999,
        tax_rate=0.5999, tax_rate_source="historical", scenarios=ordered,
    )
    assert quality.level == "ordinary"
    assert quality.allows_market_comparison

    _, financials = load_all()["MSFT"]
    financials["income_statement"] = financials["income_statement"].drop(
        index=["Pretax Income", "Tax Provision"]
    )
    result, _, quality = _assess(financials)
    assert result["tax_rate_source"] == "fallback"
    assert quality.observed_effective_tax_rate is None
    assert "extreme_observed_tax_rate" not in quality.codes


def test_incomplete_scenarios_do_not_claim_reversed_order():
    scenarios = SimpleNamespace(
        bear=SimpleNamespace(intrinsic_value_per_share=3),
        base=SimpleNamespace(intrinsic_value_per_share=2),
        bull=SimpleNamespace(intrinsic_value_per_share=None),
    )
    quality = assess_valuation_quality(
        terminal_fcf=1, enterprise_value=100, pv_terminal_value=50,
        tax_rate=0.21, tax_rate_source="historical", scenarios=scenarios,
    )
    assert "reversed_scenario_values" not in quality.codes
    assert quality.level == "ordinary"
