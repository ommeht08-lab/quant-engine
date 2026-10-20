"""Monte Carlo orchestration: experiments, accounting, partial results, reproducibility."""

import copy
import json
import os

import numpy as np
import pytest

from src.dcf_model import monte_carlo as mc
from src.dcf_model.monte_carlo import (
    ExperimentSpecs,
    RawExperiment,
    build_specs,
    canonical_payload,
    resolve_baseline,
    results_identical,
    run_experiments,
    run_monte_carlo,
)
from src.dcf_model.monte_carlo_config import (
    EXPERIMENT_NAMES,
    MonteCarloConfig,
    MonteCarloRefusal,
    canonical_json,
    sha256_hex,
)
from src.dcf_model.monte_carlo_history import HistoricalSpreads
from src.dcf_model.monte_carlo_kernel import NONFINITE_PROJECTION, evaluate_valuations
from src.dcf_model.monte_carlo_sampling import build_truncated_normal_spec, build_wacc_spec
from src.dcf_model.monte_carlo_equivalence import compare_results
from tests.dcf.monte_carlo_fixtures import make_financial_data

RF = 0.04
CONFIG = MonteCarloConfig()
GOLDEN_PAYLOAD_PATH = os.path.join(os.path.dirname(__file__), "golden", "steady_default_seed_payload.json")


def _run(data=None, config=CONFIG, seed=20261101):
    return run_monte_carlo(data or make_financial_data(), risk_free_rate=RF, config=config, seed=seed)


def _history(**overrides):
    values = dict(
        revenue_periods=["a"], revenues=[1.0], yoy_growths=[0.0], growth_center_cagr=0.08, growth_sd=0.02,
        margin_periods=["a"], margins=[0.0], margin_center=0.25, margin_sd=0.02, n_growth=3, n_margin=4,
    )
    values.update(overrides)
    return HistoricalSpreads(**values)


class TestZeroUncertaintyThroughTheExperiments:
    def test_every_experiment_collapses_to_the_deterministic_value(self):
        config = MonteCarloConfig(wacc_half_width=0.0)
        data = make_financial_data()
        baseline = resolve_baseline(data, RF, config)
        specs = ExperimentSpecs(
            growth=build_truncated_normal_spec("growth", baseline.fixed_growth, 0.0, -0.1, 0.25, 1e-6),
            margin=build_truncated_normal_spec("margin", baseline.fixed_margin, 0.0, 0.0, 0.6, 1e-6),
            wacc=build_wacc_spec(baseline.fixed_wacc, False, config),
        )
        ran = run_experiments(baseline, specs, config, seed=1)
        expected = baseline.result["intrinsic_value_per_share"]
        for name in EXPERIMENT_NAMES:
            experiment = ran["experiments"][name]
            assert experiment["status"] == "complete" and experiment["valid"] == 10_000
            stats = experiment["statistics"]
            assert stats["min"] == stats["p10"] == stats["p50"] == stats["p90"] == stats["max"] == expected
            assert stats["mean"] == pytest.approx(expected, rel=1e-12)
            assert experiment["failed_total"] == 0

    def test_zero_uncertainty_histogram_edges_are_still_usable(self):
        config = MonteCarloConfig(wacc_half_width=0.0)
        baseline = resolve_baseline(make_financial_data(), RF, config)
        specs = ExperimentSpecs(
            growth=build_truncated_normal_spec("growth", baseline.fixed_growth, 0.0, -0.1, 0.25, 1e-6),
            margin=build_truncated_normal_spec("margin", baseline.fixed_margin, 0.0, 0.0, 0.6, 1e-6),
            wacc=build_wacc_spec(baseline.fixed_wacc, False, config),
        )
        edges = run_experiments(baseline, specs, config, 1)["histogram_edges"]
        assert len(edges) == config.histogram_bins + 1
        assert all(b > a for a, b in zip(edges, edges[1:]))

    def test_the_run_records_that_the_kernel_matches_the_scalar_baseline(self):
        check = _run()["kernel_baseline_check"]
        assert check["exactly_equal"] is True and check["absolute_difference"] == 0.0

    def test_a_kernel_that_disagrees_with_the_baseline_is_refused(self, monkeypatch):
        real = mc.evaluate_valuations

        def skewed(inputs, g, m, w):
            values, codes = real(inputs, g, m, w)
            return values * 1.01, codes

        monkeypatch.setattr(mc, "evaluate_valuations", skewed)
        with pytest.raises(MonteCarloRefusal) as raised:
            _run()
        assert raised.value.code == "kernel_baseline_mismatch"


class TestFullRun:
    def test_four_experiments_each_with_10000_valid_valuations(self):
        result = _run()
        assert result["status"] == "complete"
        assert list(result["experiments"]) == list(EXPERIMENT_NAMES)
        for experiment in result["experiments"].values():
            assert experiment["status"] == "complete"
            assert experiment["valid"] == 10_000 == experiment["target"]
            assert experiment["rejected_proposals"] == 0
            assert sum(experiment["histogram"]["counts"]) + experiment["histogram"]["underflow"] \
                + experiment["histogram"]["overflow"] == experiment["valid"]

    def test_single_input_experiments_vary_only_their_own_input(self):
        experiments = _run()["experiments"]
        for name, sampled in (("growth", ["growth"]), ("margin", ["margin"]), ("wacc", ["wacc"]),
                              ("combined", ["growth", "margin", "wacc"])):
            experiment = experiments[name]
            assert experiment["sampled_inputs"] == sampled
            assert set(experiment["sampled_input_summary"]) == set(sampled)
            assert set(experiment["fixed_inputs_at_baseline"]) == {"growth", "margin", "wacc"} - set(sampled)

    def test_combined_is_wider_than_each_single_input_experiment(self):
        stats = {k: v["statistics"] for k, v in _run()["experiments"].items()}
        width = {k: s["p90"] - s["p10"] for k, s in stats.items()}
        assert width["combined"] > max(width["growth"], width["margin"], width["wacc"])

    def test_growth_draws_are_common_between_growth_only_and_combined(self):
        experiments = _run()["experiments"]
        assert experiments["growth"]["sampled_input_summary"]["growth"] == \
            experiments["combined"]["sampled_input_summary"]["growth"]

    def test_comparison_uses_strictly_greater_than_the_snapshot_price(self):
        result = _run(make_financial_data(price=10_000.0))  # far above every simulated value
        assert result["market_price"] == 10_000.0
        for experiment in result["experiments"].values():
            assert experiment["above_market"] == {"count": 0, "denominator": 10_000, "fraction": 0.0}

    def test_percentiles_and_denominators_from_known_values(self):
        data = make_financial_data()
        baseline = resolve_baseline(data, RF, CONFIG)
        specs = build_specs(baseline, _history(), CONFIG)
        values = np.arange(1.0, 10_001.0)
        raw = RawExperiment(values=values, inputs={"growth": values, "margin": values, "wacc": values},
                            attempted=10_000, failed_by_code={})
        baseline_at_5000 = copy.copy(baseline)
        baseline_at_5000.market_price = 5000.0
        edges = [0.0, 5000.0, 10_000.0]
        out = mc._summarize_experiment("growth", raw, None, (True, False, False), specs, baseline_at_5000,
                                       CONFIG, edges)
        assert out["statistics"]["p50"] == 5000.5
        assert out["statistics"]["p10"] == pytest.approx(np.percentile(values, 10))
        assert out["above_market"] == {"count": 5000, "denominator": 10_000, "fraction": 0.5}  # ties excluded
        assert out["histogram"]["counts"] == [4999, 5001]  # bins are [0,5000) and [5000,10000]
        assert out["histogram"]["underflow"] == 0 and out["histogram"]["overflow"] == 0


class TestGrowthCeiling:
    def _fast_grower(self):
        # CAGR ~28% with a wide year-to-year spread: centered above the ceiling, still mostly admissible.
        return make_financial_data(
            revenues=(100.0, 120.0, 170.0, 210.0), ebits=(20.0, 24.0, 34.0, 42.0)
        )

    def test_a_tightly_clustered_center_far_above_the_ceiling_is_refused_for_inadequate_mass(self):
        extreme = make_financial_data(revenues=(100.0, 135.0, 180.0, 247.0), ebits=(20.0, 28.0, 38.0, 51.0))
        result = _run(extreme)
        growth = result["experiments"]["growth"]
        assert result["history"]["growth_center_cagr"] > 0.30
        assert growth["status"] == "refused" and growth["refusal_code"] == "inadmissible_distribution"
        assert result["experiments"]["margin"]["status"] == "complete"

    def test_cagr_above_the_ceiling_is_simulated_not_refused(self):
        result = _run(self._fast_grower())
        history, baseline = result["history"], result["baseline"]
        assert history["growth_center_cagr"] > 0.25
        # Three distinct quantities: uncapped center, capped deterministic baseline, truncated samples.
        assert baseline["revenue_growth_rate"] == 0.25 and baseline["revenue_growth_rate_was_capped"] is True
        growth = result["experiments"]["growth"]
        assert growth["status"] == "complete" and growth["valid"] == 10_000
        spec = growth["distributions"]["growth"]
        assert spec["center"] == history["growth_center_cagr"]
        assert spec["untruncated_mass_above_upper"] > 0.3
        assert 0 < spec["admissible_mass"] < 1
        summary = growth["sampled_input_summary"]["growth"]
        assert summary["max"] <= 0.25 and summary["mean"] < 0.25 < spec["center"]
        assert result["experiments"]["combined"]["status"] == "complete"

    def test_inadequate_admissible_mass_refuses_only_the_affected_experiments(self):
        baseline = resolve_baseline(make_financial_data(), RF, CONFIG)
        specs = build_specs(baseline, _history(growth_center_cagr=0.9, growth_sd=0.01), CONFIG)
        assert isinstance(specs.growth, MonteCarloRefusal) and specs.growth.code == "inadmissible_distribution"
        ran = run_experiments(baseline, specs, CONFIG, 1)["experiments"]
        assert ran["growth"]["status"] == "refused" and ran["combined"]["status"] == "refused"
        assert ran["margin"]["status"] == "complete" and ran["wacc"]["status"] == "complete"

    def test_degenerate_zero_spread_outside_bounds_is_refused(self):
        baseline = resolve_baseline(make_financial_data(), RF, CONFIG)
        specs = build_specs(baseline, _history(growth_center_cagr=0.40, growth_sd=0.0), CONFIG)
        assert specs.growth.code == "degenerate_outside_bounds"

    def test_negative_average_margin_is_truncated_at_zero(self):
        baseline = resolve_baseline(make_financial_data(), RF, CONFIG)
        specs = build_specs(baseline, _history(margin_center=-0.02, margin_sd=0.05), CONFIG)
        assert specs.margin.untruncated_mass_below > 0.3 and specs.margin.admissible_mass < 0.7


class TestWaccClamping:
    def test_clamped_baseline_is_simulated_with_a_disclosed_interval(self):
        result = _run(make_financial_data(beta=6.0))  # CAPM WACC far above the 20% model bound
        assert result["baseline"]["wacc_was_clamped"] is True and result["baseline"]["wacc"] == 0.20
        wacc = result["experiments"]["wacc"]
        assert wacc["status"] == "complete"
        interval = wacc["distributions"]["wacc"]
        assert interval["baseline_was_clamped_to_model_bounds"] is True
        assert interval["lower"] == pytest.approx(0.19) and interval["upper"] == 0.20
        assert interval["binding_constraints"] == ["model_upper_bound"]
        assert result["experiments"]["combined"]["status"] == "complete"

    def test_empty_interval_refuses_wacc_and_combined_but_keeps_the_rest(self):
        config = MonteCarloConfig(terminal_growth_rate=0.045, wacc_min_interval_width=0.01)
        result = _run(make_financial_data(beta=-5.0), config)  # clamped to the 5% floor
        experiments = result["experiments"]
        assert experiments["wacc"]["status"] == "refused"
        assert experiments["wacc"]["refusal_code"] == "wacc_interval_empty"
        assert experiments["combined"]["status"] == "refused"
        assert experiments["growth"]["status"] == "complete" and experiments["margin"]["status"] == "complete"
        assert result["status"] == "partial"


class TestPartialResults:
    def test_histogram_edges_exist_when_combined_is_refused(self):
        config = MonteCarloConfig(terminal_growth_rate=0.045, wacc_min_interval_width=0.01)
        result = _run(make_financial_data(beta=-5.0), config)
        assert result["experiments"]["combined"]["histogram"] is None
        assert result["histogram_edges_basis"] == "pooled"
        edges = result["histogram_edges"]
        assert len(edges) == config.histogram_bins + 1 and all(b > a for a, b in zip(edges, edges[1:]))
        for name in ("growth", "margin"):
            histogram = result["experiments"][name]["histogram"]
            assert sum(histogram["counts"]) + histogram["underflow"] + histogram["overflow"] == 10_000

    def test_history_refusal_keeps_the_wacc_experiment(self):
        data = make_financial_data(revenues=(110.0, 120.0, 130.0), ebits=(20.0, 22.0, 25.0),
                                   periods=("2022-12-31", "2023-12-31", "2024-12-31"))
        result = _run(data)
        assert result["history"]["status"] == "refused" and result["history"]["refusal_code"] == "insufficient_history"
        statuses = {k: v["status"] for k, v in result["experiments"].items()}
        assert statuses == {"growth": "refused", "margin": "refused", "wacc": "complete", "combined": "refused"}
        assert result["status"] == "partial" and result["histogram_edges"] is not None

    def test_nothing_runnable_is_incomplete_with_no_edges(self):
        baseline = resolve_baseline(make_financial_data(), RF, CONFIG)
        refusal = MonteCarloRefusal("insufficient_history", "x")
        specs = ExperimentSpecs(growth=refusal, margin=refusal, wacc=MonteCarloRefusal("wacc_interval_empty", "y"))
        ran = run_experiments(baseline, specs, CONFIG, 1)
        assert ran["histogram_edges"] is None and mc._overall_status(ran["experiments"]) == "incomplete"
        assert all(e["valid"] == 0 and e["above_market"] is None for e in ran["experiments"].values())


class TestFailureAndRetryAccounting:
    def test_failed_draws_are_redrawn_and_counted_separately_from_truncation(self, monkeypatch):
        config = MonteCarloConfig()
        calls = {"n": 0}
        real = evaluate_valuations

        def fail_first_batch_only(inputs, g, m, w):
            calls["n"] += 1
            values, codes = real(inputs, g, m, w)
            if g.shape[0] == config.target_valid:  # only the opening batch of each experiment
                codes = codes.copy()
                codes[::34] = NONFINITE_PROJECTION
                values = np.where(codes == 0, values, np.nan)
            return values, codes

        monkeypatch.setattr(mc, "evaluate_valuations", fail_first_batch_only)
        result = _run(config=config)
        growth = result["experiments"]["growth"]
        assert growth["status"] == "complete" and growth["valid"] == 10_000
        assert growth["failed_by_code"]["nonfinite_projection"] == 295
        assert growth["failed_total"] == 295 and growth["attempted"] == 10_295
        assert growth["rejected_proposals"] == 0  # truncation rejections are a separate, zero quantity

    def test_budget_exhaustion_is_reported_as_incomplete_with_the_true_denominator(self, monkeypatch):
        config = MonteCarloConfig()
        real = evaluate_valuations

        def fail_ten_percent(inputs, g, m, w):
            values, codes = real(inputs, g, m, w)
            if g.shape[0] == 1:  # leave the baseline self-check alone
                return values, codes
            codes = codes.copy()
            codes[::10] = NONFINITE_PROJECTION
            return np.where(codes == 0, values, np.nan), codes

        monkeypatch.setattr(mc, "evaluate_valuations", fail_ten_percent)
        result = _run(config=config)
        growth = result["experiments"]["growth"]
        assert growth["status"] == "incomplete" and result["status"] == "partial"
        assert growth["valid"] < 10_000 and growth["target"] == 10_000
        extra = growth["attempted"] - config.target_valid
        assert 0 < extra <= config.max_extra_attempts
        assert growth["valid"] + growth["failed_total"] == growth["attempted"]
        # Every denominator is the actual valid count, never the target.
        assert growth["above_market"]["denominator"] == growth["valid"]
        assert growth["above_market"]["fraction"] == growth["above_market"]["count"] / growth["valid"]
        histogram = growth["histogram"]
        assert sum(histogram["counts"]) + histogram["underflow"] + histogram["overflow"] == growth["valid"]

    def test_retry_rounds_are_bounded(self, monkeypatch):
        config = MonteCarloConfig(max_retry_rounds=1, max_extra_attempts=500)
        real = evaluate_valuations

        def fail_half(inputs, g, m, w):
            values, codes = real(inputs, g, m, w)
            if g.shape[0] == 1:  # leave the baseline self-check alone
                return values, codes
            codes = codes.copy()
            codes[::2] = NONFINITE_PROJECTION
            return np.where(codes == 0, values, np.nan), codes

        monkeypatch.setattr(mc, "evaluate_valuations", fail_half)
        growth = _run(config=config)["experiments"]["growth"]
        assert growth["attempted"] == config.target_valid + 500  # one retry batch, capped by the budget


class TestReproducibility:
    def test_same_snapshot_config_and_seed_give_identical_canonical_payloads(self):
        a, b = _run(), _run()
        assert results_identical(a, b)
        assert canonical_json(canonical_payload(a)) == canonical_json(canonical_payload(b))
        assert a["result_digest"] == b["result_digest"]

    def test_a_different_seed_changes_the_numbers(self):
        a, b = _run(seed=1), _run(seed=2)
        assert a["result_digest"] != b["result_digest"]
        assert a["experiments"]["combined"]["statistics"] != b["experiments"]["combined"]["statistics"]

    def test_a_different_configuration_changes_the_hash_and_digest(self):
        a, b = _run(), _run(config=MonteCarloConfig(wacc_half_width=0.005))
        assert a["config_hash"] != b["config_hash"] and a["result_digest"] != b["result_digest"]

    def test_the_payload_carries_no_volatile_fields_and_survives_json_round_trip(self):
        result = _run()
        assert "meta" not in result
        round_tripped = json.loads(json.dumps(result, allow_nan=False))
        assert results_identical(result, round_tripped)

    def test_meta_is_excluded_from_the_comparison(self):
        a = _run()
        b = {**_run(), "meta": {"cache": "hit", "computed_at": "later"}}
        assert results_identical(a, b)

    def test_invalid_seeds_are_rejected(self):
        for seed in (-1, 2**63, True, 1.5):
            with pytest.raises(ValueError):
                _run(seed=seed)


class TestDisclosures:
    def test_required_disclosures_are_present(self):
        ids = {d["id"] for d in _run()["disclosures"]}
        assert {"independence", "persistent_rates", "noisy_spreads", "truncation", "wacc",
                "failed_draw_resampling", "not_prediction"} <= ids


class TestCrossEnvironmentStability:
    def test_means_do_not_depend_on_summation_order(self):
        rng = np.random.default_rng(3)
        values = rng.normal(100.0, 20.0, 10_000)
        shuffled = values.copy()
        rng.shuffle(shuffled)
        assert mc._exact_mean(values) == mc._exact_mean(shuffled) == __import__("math").fsum(values.tolist()) / values.size

    # The steady fixture at the default seed is pinned two separate ways, because "the same hash" and
    # "the same numbers" are different claims once the CPU, math library or numpy build changes:
    #
    # * integrity: tests/dcf/golden/steady_default_seed_payload.json is the recorded numerical payload. It must
    #   still hash to GOLDEN_STEADY_DIGEST, byte for byte, on every platform (it is a stored file, not a
    #   recomputation), so the recorded reference cannot drift or be edited unnoticed.
    # * numerical reference: a fresh run must be numerically equivalent to that payload under the versioned
    #   policy in monte_carlo_equivalence. Floating-point output is not bit-identical across platforms (observed
    #   macOS arm64 vs Linux x86-64 differences of a few ULP), so a hash of a fresh run is not a portable test.
    #
    # The digest covers the numerical payload only (not provenance or disclosure copy); the digest recorded
    # before that scope change is pinned in test_monte_carlo_provenance.py.
    GOLDEN_STEADY_DIGEST = "01437171336f6e78f9f7a2457567719fd790e8687716773ba8c89d88419126d4"

    @staticmethod
    def _golden_payload():
        with open(GOLDEN_PAYLOAD_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_the_recorded_reference_payload_still_hashes_to_the_golden_digest(self):
        assert sha256_hex(canonical_json(self._golden_payload())) == self.GOLDEN_STEADY_DIGEST

    def test_a_fresh_run_is_numerically_equivalent_to_the_recorded_reference(self):
        report = compare_results(self._golden_payload(), mc.canonical_payload(_run()))
        assert report["numerically_equivalent"], report["violations"]

    def test_a_fresh_run_reproduces_the_golden_digest_where_the_platform_allows_it(self):
        # Informational: bit-exact on the platform that recorded the reference, not required elsewhere.
        if _run()["result_digest"] != self.GOLDEN_STEADY_DIGEST:
            pytest.skip("this platform's floating-point output differs in the last bits; equivalence is tested above")
