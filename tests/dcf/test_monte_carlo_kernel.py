"""
Scalar-versus-vector validation of the Monte Carlo kernel.

The kernel must reproduce the deterministic DCF exactly (float64 bit equality),
not approximately. Equality is asserted with `==` / `np.array_equal`; no ULP
tolerance is used. If a platform's NumPy/libm ever produced a one-ULP
difference these tests would fail and the discrepancy would have to be
documented before any tolerance was introduced.
"""

import warnings

import numpy as np
import pytest

from src.dcf_model.dcf import (
    DCFAssumptions,
    MultiStageForecastPolicy,
    calculate_intrinsic_value_per_share,
    calculate_terminal_value,
    discount_to_present_value,
    project_free_cash_flows,
    project_free_cash_flows_from_path,
    run_dcf_valuation,
)
from src.dcf_model.monte_carlo import dcf_assumptions_for, resolve_baseline
from src.dcf_model.monte_carlo_config import MonteCarloConfig
from src.dcf_model.monte_carlo_kernel import (
    GROWTH_LE_NEG_100,
    NONFINITE_PROJECTION,
    OK,
    WACC_NOT_ABOVE_TERMINAL_GROWTH,
    KernelInputs,
    evaluate_valuations,
)
from tests.dcf.monte_carlo_fixtures import make_financial_data

FIXTURES = {
    "steady": make_financial_data(),
    "high_growth": make_financial_data(
        revenues=(100.0, 150.0, 220.0, 330.0), ebits=(20.0, 33.0, 52.0, 80.0)
    ),
    "shrinking": make_financial_data(
        revenues=(130_000.0, 120_000.0, 112_000.0, 105_000.0), ebits=(20_000.0, 17_000.0, 15_000.0, 12_000.0)
    ),
    "net_debt_heavy": make_financial_data(total_debt=900_000.0, cash=1_000.0),
    "high_beta": make_financial_data(beta=6.0),
}
MODES = ("maturation", "constant")


def _scalar_chain(baseline, config, growth, margin, wacc):
    """The deterministic pipeline, step by step, for one (growth, margin, wacc) triple."""
    result = baseline.result
    assumptions = DCFAssumptions()  # supplies the default D&A and working-capital ratios
    if config.forecast_mode == "maturation":
        path = MultiStageForecastPolicy(
            near_term_years=config.near_term_years, mature_growth_ceiling=config.mature_growth_ceiling
        ).build_path(growth, margin, config.projection_years)
        projection = project_free_cash_flows_from_path(
            result["base_revenue"], path, result["tax_rate"], assumptions.da_pct_revenue,
            result["capex_pct_revenue"], assumptions.nwc_pct_revenue_change,
        )
    else:
        projection = project_free_cash_flows(
            result["base_revenue"], growth, margin, result["tax_rate"], assumptions.da_pct_revenue,
            result["capex_pct_revenue"], assumptions.nwc_pct_revenue_change, config.projection_years,
        )
    terminal_value = calculate_terminal_value(projection["fcf"].iloc[-1], wacc, config.terminal_growth_rate)
    discounting = discount_to_present_value(projection, terminal_value, wacc)
    return calculate_intrinsic_value_per_share(
        discounting["enterprise_value"], result["total_debt"], result["cash_and_equivalents"],
        result["shares_outstanding"],
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", sorted(FIXTURES))
class TestZeroUncertaintyMatchesDeterministicValuation:
    def test_baseline_triple_equals_run_dcf_valuation_exactly(self, name, mode):
        config = MonteCarloConfig(forecast_mode=mode)
        baseline = resolve_baseline(FIXTURES[name], 0.04, config)
        n = 5
        values, codes = evaluate_valuations(
            baseline.kernel_inputs,
            np.full(n, baseline.fixed_growth),
            np.full(n, baseline.fixed_margin),
            np.full(n, baseline.fixed_wacc),
        )
        assert np.all(codes == OK)
        expected = run_dcf_valuation(FIXTURES[name], dcf_assumptions_for(config, 0.04))["intrinsic_value_per_share"]
        assert np.all(values == expected)


@pytest.mark.parametrize("mode", MODES)
class TestRandomizedScalarVsVector:
    def test_500_random_in_bounds_triples_match_the_scalar_pipeline_exactly(self, mode):
        config = MonteCarloConfig(forecast_mode=mode)
        baseline = resolve_baseline(FIXTURES["steady"], 0.04, config)
        rng = np.random.default_rng(20261101)
        growth = rng.uniform(config.growth_lower, config.growth_upper, 500)
        margin = rng.uniform(config.margin_lower, config.margin_upper, 500)
        wacc = rng.uniform(config.wacc_lower_bound, config.wacc_upper_bound, 500)
        values, codes = evaluate_valuations(baseline.kernel_inputs, growth, margin, wacc)
        assert np.all(codes == OK)
        for i in range(500):
            scalar = _scalar_chain(baseline, config, float(growth[i]), float(margin[i]), float(wacc[i]))
            assert values[i] == scalar, f"draw {i} differs: kernel={values[i]!r} scalar={scalar!r}"

    def test_results_do_not_depend_on_batch_composition(self, mode):
        config = MonteCarloConfig(forecast_mode=mode)
        baseline = resolve_baseline(FIXTURES["steady"], 0.04, config)
        rng = np.random.default_rng(5)
        growth = rng.uniform(-0.1, 0.25, 64)
        margin = rng.uniform(0.0, 0.6, 64)
        wacc = rng.uniform(0.05, 0.2, 64)
        together, _ = evaluate_valuations(baseline.kernel_inputs, growth, margin, wacc)
        alone = np.array(
            [evaluate_valuations(baseline.kernel_inputs, growth[i:i + 1], margin[i:i + 1], wacc[i:i + 1])[0][0]
             for i in range(64)]
        )
        assert np.array_equal(together, alone)


def _inputs(**overrides):
    base = dict(
        base_revenue=1000.0, tax_rate=0.2, da_pct_revenue=0.03, capex_pct_revenue=0.04,
        nwc_pct_revenue_change=0.01, terminal_growth_rate=0.025, projection_years=5, total_debt=100.0,
        cash_and_equivalents=50.0, shares_outstanding=10.0, forecast_mode="maturation",
        near_term_years=2, mature_growth_ceiling=0.03,
    )
    base.update(overrides)
    return KernelInputs(**base)


class TestFailureCodes:
    def _one(self, growth, margin, wacc, **overrides):
        values, codes = evaluate_valuations(
            _inputs(**overrides), np.array([growth]), np.array([margin]), np.array([wacc])
        )
        return values[0], int(codes[0])

    def test_wacc_not_above_terminal_growth(self):
        value, code = self._one(0.05, 0.2, 0.025)
        assert code == WACC_NOT_ABOVE_TERMINAL_GROWTH and np.isnan(value)
        with pytest.raises(ValueError):  # the scalar pipeline refuses the same case
            calculate_terminal_value(1.0, 0.025, 0.025)

    def test_nonfinite_wacc(self):
        assert self._one(0.05, 0.2, float("nan"))[1] == WACC_NOT_ABOVE_TERMINAL_GROWTH

    def test_growth_at_or_below_minus_100_percent(self):
        assert self._one(-1.0, 0.2, 0.09)[1] == GROWTH_LE_NEG_100
        with pytest.raises(ValueError):
            MultiStageForecastPolicy().build_path(-1.0, 0.2, 5)

    def test_overflowing_projection(self):
        value, code = self._one(1e300, 0.2, 0.09, forecast_mode="constant")
        assert code == NONFINITE_PROJECTION and np.isnan(value)

    def test_failures_are_per_draw_not_batch_wide(self):
        values, codes = evaluate_valuations(
            _inputs(), np.array([0.05, 0.05, 0.05]), np.array([0.2, 0.2, 0.2]), np.array([0.09, 0.02, 0.09])
        )
        assert list(codes) == [OK, WACC_NOT_ABOVE_TERMINAL_GROWTH, OK]
        assert values[0] == values[2] and np.isnan(values[1])

    def test_failures_never_raise_or_warn(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            evaluate_valuations(
                _inputs(forecast_mode="constant"),
                np.array([1e300, -1.0, 0.0, np.inf]), np.array([0.2] * 4), np.array([0.09, 0.09, 0.0, 0.09]),
            )

    def test_negative_equity_values_are_valid_not_failures(self):
        value, code = self._one(0.05, 0.2, 0.09, total_debt=1e9)
        assert code == OK and value < 0

    def test_sampled_wacc_drives_terminal_value_and_every_discount_factor(self):
        inputs = _inputs()
        low, _ = evaluate_valuations(inputs, np.array([0.05]), np.array([0.2]), np.array([0.07]))
        high, _ = evaluate_valuations(inputs, np.array([0.05]), np.array([0.2]), np.array([0.12]))
        assert low[0] > high[0]
        # Independent recomputation of the single-WACC pipeline at wacc = 0.07 (see _scalar_chain tests).
        projection = project_free_cash_flows_from_path(
            1000.0,
            MultiStageForecastPolicy(2, 0.03).build_path(0.05, 0.2, 5),
            0.2, 0.03, 0.04, 0.01,
        )
        tv = calculate_terminal_value(projection["fcf"].iloc[-1], 0.07, 0.025)
        ev = discount_to_present_value(projection, tv, 0.07)["enterprise_value"]
        assert low[0] == calculate_intrinsic_value_per_share(ev, 100.0, 50.0, 10.0)
