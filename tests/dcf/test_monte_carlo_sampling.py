"""Truncated-normal and uniform samplers, WACC interval rules, and seeded streams."""

import math

import numpy as np
import pytest
from scipy import stats

from src.dcf_model.monte_carlo_config import MonteCarloConfig, MonteCarloRefusal
from src.dcf_model.monte_carlo_sampling import (
    SeededStreams,
    build_truncated_normal_spec,
    build_wacc_spec,
    draw_inputs,
    sample_truncated_normal,
    sample_uniform,
)

MIN_MASS = 1e-6


def _spec(center, sd, lower=-0.10, upper=0.25, min_mass=MIN_MASS):
    return build_truncated_normal_spec("growth", center, sd, lower, upper, min_mass)


def _draw(spec, n=200_000, seed=7):
    return sample_truncated_normal(spec, SeededStreams(seed).uniform("growth", n))


class TestTruncatedNormal:
    @pytest.mark.parametrize(
        "center,sd",
        [(0.08, 0.02), (0.08, 0.30), (0.28, 0.05), (0.25, 0.10), (-0.15, 0.05), (0.0, 1.0)],
    )
    def test_every_sample_lies_inside_the_bounds(self, center, sd):
        spec = _spec(center, sd)
        samples = _draw(spec, n=50_000)
        assert samples.min() >= spec.lower and samples.max() <= spec.upper

    @pytest.mark.parametrize("center,sd", [(0.08, 0.10), (0.28, 0.05), (-0.15, 0.08), (0.12, 0.4)])
    def test_moments_match_the_analytic_truncated_normal(self, center, sd):
        spec = _spec(center, sd)
        samples = _draw(spec)
        a, b = (spec.lower - center) / sd, (spec.upper - center) / sd
        analytic = stats.truncnorm(a, b, loc=center, scale=sd)
        standard_error = analytic.std() / math.sqrt(samples.size)
        assert abs(samples.mean() - analytic.mean()) < 4 * standard_error
        assert samples.std() == pytest.approx(analytic.std(), rel=0.02)

    def test_excluded_mass_matches_the_closed_form(self):
        spec = _spec(0.20, 0.10)
        assert spec.untruncated_mass_below == pytest.approx(stats.norm.cdf((-0.10 - 0.20) / 0.10), rel=1e-12)
        assert spec.untruncated_mass_above == pytest.approx(stats.norm.sf((0.25 - 0.20) / 0.10), rel=1e-12)
        assert spec.admissible_mass == pytest.approx(
            1 - spec.untruncated_mass_below - spec.untruncated_mass_above, rel=1e-12
        )

    def test_center_above_the_ceiling_is_admissible_and_truncated_below_its_center(self):
        # Amendment: a normal centered at/above the 25% cap is NOT automatically refused.
        spec = _spec(center=0.28, sd=0.05)
        assert spec.untruncated_mass_above == pytest.approx(stats.norm.sf((0.25 - 0.28) / 0.05))
        assert spec.untruncated_mass_above > 0.7
        assert spec.admissible_mass == pytest.approx(0.2743, abs=1e-3)
        samples = _draw(spec)
        assert samples.max() <= 0.25
        assert samples.mean() < 0.25 < spec.center

    def test_tails_keep_precision_in_the_upper_region(self):
        # Slice [5, 6] of N(0, 1): mass ~2.87e-7. Accepted only with a lowered threshold.
        with pytest.raises(MonteCarloRefusal):
            _spec(0.0, 1.0, lower=5.0, upper=6.0, min_mass=1e-6)
        spec = _spec(0.0, 1.0, lower=5.0, upper=6.0, min_mass=1e-9)
        assert spec.admissible_mass == pytest.approx(stats.norm.sf(5) - stats.norm.sf(6), rel=1e-6)
        samples = _draw(spec, n=20_000)
        assert samples.min() >= 5.0 and samples.max() <= 6.0
        assert samples.mean() == pytest.approx(stats.truncnorm(5, 6).mean(), abs=5e-3)

    def test_numerically_inadequate_mass_is_refused(self):
        with pytest.raises(MonteCarloRefusal) as raised:
            _spec(center=0.90, sd=0.01)
        assert raised.value.code == "inadmissible_distribution"

    def test_zero_spread_inside_bounds_is_a_point_mass(self):
        spec = _spec(0.07, 0.0)
        assert spec.degenerate and spec.admissible_mass == 1.0
        assert np.all(_draw(spec, n=100) == 0.07)

    def test_zero_spread_outside_bounds_is_refused(self):
        for center in (0.30, -0.20):
            with pytest.raises(MonteCarloRefusal) as raised:
                _spec(center, 0.0)
            assert raised.value.code == "degenerate_outside_bounds"

    @pytest.mark.parametrize("args", [(float("nan"), 0.1), (0.1, float("inf")), (0.1, -0.1)])
    def test_invalid_parameters_are_refused(self, args):
        with pytest.raises(MonteCarloRefusal):
            _spec(*args)

    def test_same_seed_gives_identical_samples(self):
        spec = _spec(0.08, 0.05)
        assert np.array_equal(_draw(spec, n=1000, seed=3), _draw(spec, n=1000, seed=3))
        assert not np.array_equal(_draw(spec, n=1000, seed=3), _draw(spec, n=1000, seed=4))


class TestWaccInterval:
    CONFIG = MonteCarloConfig()

    def test_default_interval_is_baseline_plus_minus_one_point(self):
        spec = build_wacc_spec(0.09, False, self.CONFIG)
        assert (spec.lower, spec.upper) == (pytest.approx(0.08), pytest.approx(0.10))
        assert spec.binding_constraints == [] and not spec.baseline_was_clamped

    def test_clamped_upper_baseline_is_sampled_and_disclosed_not_refused(self):
        spec = build_wacc_spec(0.20, True, self.CONFIG)
        assert (spec.lower, spec.upper) == (pytest.approx(0.19), 0.20)
        assert spec.baseline_was_clamped
        assert spec.binding_constraints == ["model_upper_bound"]

    def test_clamped_lower_baseline(self):
        spec = build_wacc_spec(0.05, True, self.CONFIG)
        assert (spec.lower, spec.upper) == (0.05, pytest.approx(0.06))
        assert "model_lower_bound" in spec.binding_constraints

    def test_terminal_growth_spread_can_bind(self):
        config = MonteCarloConfig(terminal_growth_rate=0.045)
        spec = build_wacc_spec(0.05, True, config)
        assert spec.lower == pytest.approx(0.055)
        assert "terminal_growth_spread" in spec.binding_constraints

    def test_empty_interval_is_refused(self):
        config = MonteCarloConfig(terminal_growth_rate=0.05, wacc_half_width=0.0)
        with pytest.raises(MonteCarloRefusal) as raised:
            build_wacc_spec(0.05, True, config)
        assert raised.value.code == "wacc_interval_empty"

    def test_too_narrow_interval_is_refused(self):
        config = MonteCarloConfig(terminal_growth_rate=0.045, wacc_min_interval_width=0.01)
        with pytest.raises(MonteCarloRefusal):
            build_wacc_spec(0.05, True, config)

    def test_nonfinite_baseline_is_refused(self):
        with pytest.raises(MonteCarloRefusal):
            build_wacc_spec(float("nan"), False, self.CONFIG)

    def test_zero_half_width_is_a_point(self):
        config = MonteCarloConfig(wacc_half_width=0.0)
        spec = build_wacc_spec(0.09, False, config)
        assert spec.degenerate and spec.lower == spec.upper == 0.09
        assert np.all(sample_uniform(spec, np.array([0.0, 0.5, 0.999])) == 0.09)

    def test_samples_are_uniform_inside_the_interval_and_above_terminal_growth(self):
        spec = build_wacc_spec(0.09, False, self.CONFIG)
        samples = sample_uniform(spec, SeededStreams(11).uniform("wacc", 100_000))
        assert samples.min() >= spec.lower and samples.max() <= spec.upper
        assert samples.min() > self.CONFIG.terminal_growth_rate
        assert samples.mean() == pytest.approx(0.09, abs=2e-4)
        assert stats.kstest(samples, "uniform", args=(spec.lower, spec.upper - spec.lower)).pvalue > 0.001


class TestStreams:
    def test_same_seed_same_draws_and_streams_are_independent(self):
        a, b = SeededStreams(5), SeededStreams(5)
        assert np.array_equal(a.uniform("growth", 50), b.uniform("growth", 50))
        c = SeededStreams(5)
        assert not np.array_equal(c.uniform("growth", 50), SeededStreams(5).uniform("margin", 50))

    def test_growth_draws_are_common_across_experiments(self):
        growth, margin = _spec(0.08, 0.03), build_truncated_normal_spec("margin", 0.25, 0.02, 0.0, 0.6, MIN_MASS)
        wacc = build_wacc_spec(0.09, False, MonteCarloConfig())
        fixed = dict(fixed_growth=0.08, fixed_margin=0.25, fixed_wacc=0.09)
        only = draw_inputs(SeededStreams(9), 500, growth, None, None, **fixed)
        combined = draw_inputs(SeededStreams(9), 500, growth, margin, wacc, **fixed)
        assert np.array_equal(only["growth"], combined["growth"])
        assert np.all(only["margin"] == 0.25) and np.all(only["wacc"] == 0.09)

    def test_retry_batches_continue_the_stream(self):
        spec = _spec(0.08, 0.03)
        streams = SeededStreams(2)
        first = sample_truncated_normal(spec, streams.uniform("growth", 100))
        second = sample_truncated_normal(spec, streams.uniform("growth", 50))
        both = sample_truncated_normal(spec, SeededStreams(2).uniform("growth", 150))
        assert np.array_equal(np.concatenate([first, second]), both)
