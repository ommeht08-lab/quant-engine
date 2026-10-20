"""
The cross-platform numerical replay policy.

Integrity (exact hashes over stored content) and numerical equivalence (tolerances over recomputed floats) are
separate claims. These tests pin the policy's three properties:

* it accepts what different CPUs, math libraries and numpy builds legitimately produce (last-bit differences);
* it rejects everything else: formula changes, count changes, structural changes, configuration changes;
* it never weakens an integrity hash: tampering with stored content still fails exactly.
"""

import copy
import json
import math

import numpy as np
import pytest

from src.dcf_model import monte_carlo as mc
from src.dcf_model.monte_carlo import canonical_payload, run_monte_carlo
from src.dcf_model.monte_carlo_config import MonteCarloConfig, canonical_json, sha256_hex
from src.dcf_model.monte_carlo_equivalence import (
    NUMERICAL_POLICY_VERSION,
    KERNEL_SCALAR_AGREEMENT,
    TOLERANCES,
    compare_results,
    float_class,
    ulp_distance,
)
from src.dcf_model.monte_carlo_provenance import provenance_digest
from src.dcf_model.monte_carlo_snapshot import (
    SnapshotError,
    build_replay_bundle,
    build_snapshot,
    compute_from_snapshot,
    replay_bundle,
)
from tests.dcf.monte_carlo_fixtures import make_financial_data
from tests.dcf.test_monte_carlo_snapshot import CAPTURED_AT, RF, make_valuation_input

SEED = 20261101


def run(mode="maturation", seed=SEED, data=None):
    return run_monte_carlo(
        data or make_financial_data(), risk_free_rate=0.04, config=MonteCarloConfig(forecast_mode=mode), seed=seed
    )


def float_leaves(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from float_leaves(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from float_leaves(value, f"{path}[{index}]")
    elif isinstance(node, float):
        yield path, node


def get_path(node, path):
    for part in path.replace("]", "").replace("[", "/").strip("/").split("/"):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def set_path(node, path, value):
    parts = path.replace("]", "").replace("[", "/").strip("/").split("/")
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    node[int(last) if isinstance(node, list) else last] = value


def nudge(value, ulps):
    for _ in range(abs(ulps)):
        value = float(np.nextafter(value, math.inf if ulps > 0 else -math.inf))
    return value


def platform_variant(result, ulps=4):
    """What another platform could plausibly produce: every compared float off by a few ULP."""
    variant = copy.deepcopy(result)
    for path, value in list(float_leaves(variant)):
        if float_class(path) not in ("exact", "informational", None):
            set_path(variant, path, nudge(value, ulps))
    return variant


@pytest.fixture(scope="module")
def result():
    return run()


@pytest.fixture(scope="module", params=["maturation", "constant"])
def result_by_mode(request):
    return run(request.param)


class TestPolicyDefinition:
    def test_the_policy_is_versioned(self):
        assert NUMERICAL_POLICY_VERSION == "mc-dcf-numerical-equivalence-v1"
        assert compare_results({"a": 1}, {"a": 1})["policy_version"] == NUMERICAL_POLICY_VERSION

    def test_every_float_in_a_real_result_has_a_comparison_class(self, result_by_mode):
        unclassified = [p for p, _ in float_leaves(canonical_view(result_by_mode)) if float_class(p) is None]
        assert unclassified == []

    def test_tolerances_are_explicit_and_field_appropriate(self):
        assert TOLERANCES["value_per_share"].atol == 1e-9  # a billionth of a dollar
        assert TOLERANCES["rate"].atol == 1e-15  # near-zero rates: absolute, not relative
        assert TOLERANCES["tail_mass"].atol == 0.0  # probabilities down to 1e-80: relative only
        assert all(t.rtol == 1e-12 for t in TOLERANCES.values())

    def test_the_kernel_scalar_agreement_matches_the_runs_own_refusal_threshold(self):
        assert KERNEL_SCALAR_AGREEMENT.rtol == mc.KERNEL_MISMATCH_REFUSAL_REL_TOLERANCE

    def test_ulp_distance(self):
        assert ulp_distance(1.0, 1.0) == 0
        assert ulp_distance(1.0, nudge(1.0, 3)) == 3
        assert ulp_distance(-1.0, nudge(-1.0, -2)) == 2


def canonical_view(result):
    return {k: v for k, v in result.items() if k not in ("meta", "result_digest", "disclosures")}


class TestAcceptsPlatformDifferences:
    def test_identical_results_are_bit_identical_and_equivalent(self, result):
        report = compare_results(result, copy.deepcopy(result))
        assert report["numerically_equivalent"] and report["bit_identical"] and report["violation_count"] == 0

    def test_last_bit_differences_in_every_float_are_equivalent_but_not_bit_identical(self, result_by_mode):
        report = compare_results(result_by_mode, platform_variant(result_by_mode, ulps=4))
        assert report["numerically_equivalent"], report["violations"]
        assert not report["bit_identical"] and report["float_differences"] > 100
        assert max(entry["max_ulp"] for entry in report["by_class"].values()) == 4

    def test_the_digest_and_the_informational_diagnostics_are_not_compared(self, result):
        other = copy.deepcopy(result)
        other["result_digest"] = "0" * 64
        other["baseline_provenance"]["provenance_digest"] = "1" * 64
        other["kernel_baseline_check"]["exactly_equal"] = not result["kernel_baseline_check"]["exactly_equal"]
        other["kernel_baseline_check"]["absolute_difference"] = 1e-14
        assert compare_results(result, other)["numerically_equivalent"]

    def test_the_observed_near_cancellation_case_is_accepted(self):
        # Observed: a sampled margin minimum of about 2e-4 differing by 2.8e-17 (1.4e-13 relative, 1024 ULP).
        value = 0.00019788
        assert float_class("/experiments/margin/sampled_input_summary/margin/min") == "rate"
        report = compare_results(
            {"experiments": {"margin": {"sampled_input_summary": {"margin": {"min": value}}}}},
            {"experiments": {"margin": {"sampled_input_summary": {"margin": {"min": value + 2.776e-17}}}}},
        )
        assert report["numerically_equivalent"]

    def test_tiny_tail_probabilities_are_compared_relatively(self):
        path = lambda v: {"experiments": {"growth": {"distributions": {"growth": {"untruncated_mass_above_upper": v}}}}}
        assert compare_results(path(6.7e-80), path(nudge(6.7e-80, 2)))["numerically_equivalent"]
        assert not compare_results(path(6.7e-80), path(6.7e-80 * 1.001))["numerically_equivalent"]
        assert not compare_results(path(6.7e-80), path(0.0))["numerically_equivalent"]


class TestRejectsRealChanges:
    @pytest.mark.parametrize(
        "path",
        [
            "/experiments/combined/statistics/p50",
            "/experiments/growth/statistics/mean",
            "/baseline/intrinsic_value_per_share",
            "/histogram_edges[7]",
            "/baseline/equity_value",
            "/baseline/wacc",
            "/experiments/margin/distributions/margin/center",
            "/experiments/growth/distributions/growth/untruncated_mass_below_lower",
            "/experiments/wacc/sampled_input_summary/wacc/mean",
            "/history/margin_sd",
        ],
    )
    def test_a_relative_change_of_1e_9_is_rejected_in_every_tolerance_class(self, result, path):
        changed = copy.deepcopy(result)
        set_path(changed, path, get_path(changed, path) * (1 + 1e-9))
        report = compare_results(result, changed)
        assert not report["numerically_equivalent"]
        assert any(v["path"] == path for v in report["violations"])

    def test_a_change_below_the_tolerance_is_not_detectable_and_that_is_stated_not_hidden(self, result):
        # The policy cannot see a 1e-13 relative change. The exact digest still does, on a single platform.
        changed = copy.deepcopy(result)
        path = "/experiments/combined/statistics/p50"
        set_path(changed, path, get_path(changed, path) * (1 + 1e-13))
        assert compare_results(result, changed)["numerically_equivalent"]
        assert with_digest_of(changed) != result["result_digest"]

    def test_a_near_zero_rate_cannot_move_by_a_visible_amount(self, result):
        changed = copy.deepcopy(result)
        path = "/experiments/margin/sampled_input_summary/margin/min"
        set_path(changed, path, get_path(changed, path) + 1e-9)
        assert not compare_results(result, changed)["numerically_equivalent"]

    @pytest.mark.parametrize("path", ["/market_price", "/config/terminal_growth_rate", "/config/growth_bounds[0]"])
    def test_copied_inputs_and_configuration_must_be_bit_identical(self, result, path):
        changed = copy.deepcopy(result)
        set_path(changed, path, nudge(get_path(changed, path), 1))
        report = compare_results(result, changed)
        assert not report["numerically_equivalent"]
        assert report["violations"][0]["reason"].startswith("float differs")

    @pytest.mark.parametrize("factor", [1 + 1e-10, 1 + 1e-6, 1.0001])
    def test_a_formula_change_in_the_kernel_is_caught_against_the_recorded_reference(self, monkeypatch, factor):
        golden = json.load(open(_golden_path()))
        real = mc.evaluate_valuations

        def perturbed(inputs, growth, margin, wacc):
            values, codes = real(inputs, growth, margin, wacc)
            # Draws only: the single-point baseline check would refuse a change this large before any result existed.
            return (values * factor if len(values) > 1 else values), codes

        assert compare_results(golden, canonical_payload(run()))["numerically_equivalent"]
        monkeypatch.setattr(mc, "evaluate_valuations", perturbed)
        report = compare_results(golden, canonical_payload(run()))
        assert not report["numerically_equivalent"]
        assert any("/statistics/" in v["path"] or "histogram" in v["path"] for v in report["violations"])

    def test_a_change_the_run_itself_cannot_see_is_still_caught_by_the_policy(self, monkeypatch):
        # 1e-10 is below the run's own 1e-9 kernel/scalar refusal threshold, so the run accepts it. The policy does not.
        golden = json.load(open(_golden_path()))
        real = mc.evaluate_valuations
        monkeypatch.setattr(mc, "evaluate_valuations", lambda *a, **k: (lambda v, c: (v * (1 + 1e-10), c))(*real(*a, **k)))
        report = compare_results(golden, canonical_payload(run()))
        assert not report["numerically_equivalent"]


def with_digest_of(result):
    return sha256_hex(canonical_json(canonical_payload(result)))


def _golden_path():
    import os

    return os.path.join(os.path.dirname(__file__), "golden", "steady_default_seed_payload.json")


class TestStructureAndCountsAreExact:
    """A floating-point tolerance must never hide a changed count or classification."""

    def test_a_histogram_count_moving_between_bins_is_rejected(self, result):
        changed = copy.deepcopy(result)
        counts = changed["experiments"]["combined"]["histogram"]["counts"]
        counts[10] += 1
        counts[11] -= 1  # same total, one draw reclassified
        report = compare_results(result, changed)
        assert not report["numerically_equivalent"]
        assert {v["path"] for v in report["violations"]} == {
            "/experiments/combined/histogram/counts[10]", "/experiments/combined/histogram/counts[11]"
        }

    @pytest.mark.parametrize(
        "path",
        [
            "/experiments/combined/above_market/count",
            "/experiments/growth/valid",
            "/experiments/margin/attempted",
            "/experiments/wacc/rejected_proposals",
            "/experiments/combined/negative_value_count",
            "/experiments/combined/histogram/overflow",
            "/experiments/combined/failed_by_code/nonfinite_projection",
            "/seed",
        ],
    )
    def test_integer_fields_are_exact(self, result, path):
        changed = copy.deepcopy(result)
        set_path(changed, path, get_path(changed, path) + 1)
        assert not compare_results(result, changed)["numerically_equivalent"]

    def test_the_above_market_fraction_is_exact_even_though_it_is_a_float(self, result):
        changed = copy.deepcopy(result)
        path = "/experiments/combined/above_market/fraction"
        set_path(changed, path, nudge(get_path(changed, path), 1))
        assert not compare_results(result, changed)["numerically_equivalent"]

    @pytest.mark.parametrize(
        "edit",
        [
            lambda r: r.__setitem__("status", "partial"),
            lambda r: r["experiments"]["growth"].__setitem__("status", "incomplete"),
            lambda r: r["experiments"]["growth"].__setitem__("refusal_code", "x"),
            lambda r: r["experiments"].pop("wacc"),
            lambda r: r["experiments"]["growth"].__setitem__("new_field", 1),
            lambda r: r["histogram_edges"].pop(),
            lambda r: r["experiments"]["growth"]["histogram"]["counts"].append(0),
            lambda r: r["baseline"].__setitem__("wacc_was_clamped", not r["baseline"]["wacc_was_clamped"]),
            lambda r: r["identity"].__setitem__("snapshot_id", "0" * 64),
            lambda r: r["config"]["distributions"].__setitem__("growth", "other"),
            lambda r: r.__setitem__("seed", float(r["seed"])),  # an int that became a float
            lambda r: r["experiments"]["growth"]["above_market"].__setitem__("count", True),
        ],
    )
    def test_structure_statuses_flags_identity_and_types_are_exact(self, result, edit):
        changed = copy.deepcopy(result)
        edit(changed)
        assert not compare_results(result, changed)["numerically_equivalent"]

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_values_are_rejected(self, result, bad):
        changed = copy.deepcopy(result)
        set_path(changed, "/experiments/combined/statistics/p50", bad)
        assert not compare_results(result, changed)["numerically_equivalent"]

    def test_a_float_field_no_rule_covers_fails_closed(self, result):
        changed = copy.deepcopy(result)
        changed["experiments"]["growth"]["brand_new_metric"] = 1.0
        base = copy.deepcopy(result)
        base["experiments"]["growth"]["brand_new_metric"] = 1.0
        report = compare_results(base, changed)
        assert not report["numerically_equivalent"] and "unclassified" in report["violations"][0]["reason"]

    def test_kernel_and_scalar_baselines_of_the_recomputed_result_must_still_agree(self, result):
        changed = copy.deepcopy(result)
        check = changed["kernel_baseline_check"]
        check["kernel_value_per_share"] = check["scalar_value_per_share"] * (1 + 1e-6)
        expected = copy.deepcopy(changed)
        report = compare_results(expected, changed)
        assert any(v["reason"].startswith("kernel and scalar baselines disagree") for v in report["violations"])


class TestReplayVerificationLevels:
    @pytest.fixture
    def snapshot(self):
        return build_snapshot(make_valuation_input(), RF, CAPTURED_AT)

    @pytest.fixture
    def exported(self, snapshot):
        result = compute_from_snapshot(snapshot, SEED, MonteCarloConfig())
        bundle = build_replay_bundle(
            snapshot, SEED, MonteCarloConfig(), expected_result_digest=result["result_digest"],
            baseline_provenance=result["baseline_provenance"], reference_result=canonical_payload(result),
        )
        return result, bundle

    @staticmethod
    def foreign_bundle(snapshot, result):
        """A bundle as another platform would export it: every float a few ULP away, with its own digests."""
        variant = platform_variant(result)
        variant["baseline_provenance"]["provenance_digest"] = provenance_digest(variant["baseline_provenance"])
        digest = sha256_hex(canonical_json(canonical_payload(variant)))
        return build_replay_bundle(
            snapshot, SEED, MonteCarloConfig(), expected_result_digest=digest,
            baseline_provenance=variant["baseline_provenance"], reference_result=canonical_payload(variant),
        )

    def test_same_platform_replay_is_exact_integrity_verified_and_equivalent(self, exported):
        _, bundle = exported
        outcome = replay_bundle(bundle)
        assert outcome["integrity_verified"] is True and outcome["exact_replay"] is True
        assert outcome["numerically_equivalent_replay"] is True and outcome["verification_level"] == "exact_replay"
        assert outcome["numerical_policy_version"] == NUMERICAL_POLICY_VERSION

    def test_a_foreign_platform_bundle_is_equivalent_but_not_exact(self, snapshot, exported):
        result, _ = exported
        outcome = replay_bundle(self.foreign_bundle(snapshot, result))
        assert outcome["integrity_verified"] is True
        assert outcome["exact_replay"] is False
        assert outcome["numerically_equivalent_replay"] is True
        assert outcome["verification_level"] == "numerically_equivalent_replay"
        assert outcome["matches_expected"] is False  # the legacy field keeps meaning "bit for bit"
        comparison = outcome["numerical_comparison"]
        assert comparison["numerically_equivalent"] and comparison["float_differences"] > 100

    def test_a_reference_from_a_different_formula_is_a_numerical_mismatch(self, snapshot, monkeypatch):
        real = mc.evaluate_valuations
        monkeypatch.setattr(
            mc, "evaluate_valuations",
            lambda *a, **k: (lambda v, c: (v * (1 + 1e-9) if len(v) > 1 else v, c))(*real(*a, **k)),
        )
        wrong = compute_from_snapshot(snapshot, SEED, MonteCarloConfig())
        bundle = build_replay_bundle(
            snapshot, SEED, MonteCarloConfig(), expected_result_digest=wrong["result_digest"],
            baseline_provenance=wrong["baseline_provenance"], reference_result=canonical_payload(wrong),
        )
        monkeypatch.undo()
        outcome = replay_bundle(bundle)
        assert outcome["integrity_verified"] is True  # the bundle is intact; the code under test changed
        assert outcome["exact_replay"] is False and outcome["numerically_equivalent_replay"] is False
        assert outcome["verification_level"] == "numerical_mismatch"

    def test_without_recorded_numbers_a_differing_digest_is_unconfirmed_not_a_mismatch(self, snapshot, exported):
        result, _ = exported
        variant = platform_variant(result)
        digest = sha256_hex(canonical_json(canonical_payload(variant)))
        bundle = build_replay_bundle(snapshot, SEED, MonteCarloConfig(), expected_result_digest=digest)
        outcome = replay_bundle(bundle)
        assert "reference_result" not in bundle
        assert outcome["integrity_verified"] is True and outcome["exact_replay"] is False
        assert outcome["numerically_equivalent_replay"] is None
        assert outcome["verification_level"] == "integrity_verified_only"

    def test_an_externally_supplied_reference_makes_an_archived_bundle_checkable(self, snapshot, exported):
        result, _ = exported
        variant = platform_variant(result)
        digest = sha256_hex(canonical_json(canonical_payload(variant)))
        archived = build_replay_bundle(snapshot, SEED, MonteCarloConfig(), expected_result_digest=digest)
        outcome = replay_bundle(archived, reference_result=variant)  # a full result is accepted; only its payload is used
        assert outcome["numerically_equivalent_replay"] is True
        assert outcome["verification_level"] == "numerically_equivalent_replay"

    def test_a_reference_that_does_not_hash_to_the_recorded_digest_is_refused(self, snapshot, exported):
        result, _ = exported
        archived = build_replay_bundle(snapshot, SEED, MonteCarloConfig(), expected_result_digest=result["result_digest"])
        edited = copy.deepcopy(canonical_payload(result))
        edited["experiments"]["combined"]["statistics"]["p50"] = nudge(edited["experiments"]["combined"]["statistics"]["p50"], 1)
        with pytest.raises(SnapshotError):
            replay_bundle(archived, reference_result=edited)  # inside every tolerance, still refused: integrity is exact

    def test_a_bundle_with_no_digests_at_all_establishes_nothing(self, snapshot):
        bundle = build_replay_bundle(snapshot, SEED, MonteCarloConfig())
        del bundle["bundle_digest"]
        outcome = replay_bundle(bundle)
        assert outcome["verification_level"] == "unverified" and outcome["integrity_verified"] is None


class TestTamperingStillFailsExactly:
    @pytest.fixture
    def bundle(self):
        snapshot = build_snapshot(make_valuation_input(), RF, CAPTURED_AT)
        result = compute_from_snapshot(snapshot, SEED, MonteCarloConfig())
        return build_replay_bundle(
            snapshot, SEED, MonteCarloConfig(), expected_result_digest=result["result_digest"],
            baseline_provenance=result["baseline_provenance"], reference_result=canonical_payload(result),
        )

    def test_the_untouched_bundle_replays(self, bundle):
        assert replay_bundle(copy.deepcopy(bundle))["integrity_verified"] is True

    @pytest.mark.parametrize(
        "edit",
        [
            # One ULP, far inside the numerical tolerance: stored content is still hashed exactly.
            lambda b: b["reference_result"]["experiments"]["combined"]["statistics"].__setitem__(
                "p50", nudge(b["reference_result"]["experiments"]["combined"]["statistics"]["p50"], 1)),
            lambda b: b["reference_result"]["baseline"].__setitem__(
                "wacc", nudge(b["reference_result"]["baseline"]["wacc"], 1)),
            lambda b: b["reference_result"]["experiments"]["combined"]["histogram"]["counts"].__setitem__(0, 1),
            lambda b: b["baseline_provenance"]["margin"].__setitem__("rows_used", ["x"]),
            lambda b: b["replay"].__setitem__("expected_result_digest", "0" * 64),
            lambda b: b["replay"].__setitem__("seed", b["replay"]["seed"] + 1),
            lambda b: b["replay"].__setitem__("numerical_policy_version", "mc-dcf-numerical-equivalence-v0"),
            lambda b: b["replay"]["config"].__setitem__("target_valid", 5),
            lambda b: b["snapshot"].__setitem__("snapshot_id", "0" * 64),
            lambda b: b["snapshot"]["market"].__setitem__("current_price", b["snapshot"]["market"]["current_price"] + 1),
        ],
    )
    def test_any_edit_to_stored_content_is_refused(self, bundle, edit):
        edit(bundle)
        with pytest.raises(SnapshotError):
            replay_bundle(bundle)

    def test_removing_the_reference_cannot_be_used_to_claim_equivalence(self, bundle):
        del bundle["reference_result"]
        outcome = replay_bundle(bundle)
        assert outcome["exact_replay"] is True  # same platform: the digest still matches bit for bit
        assert outcome["reference_result_present"] is False

    def test_a_reference_swapped_for_another_runs_numbers_is_refused(self, bundle):
        other = canonical_payload(run(seed=5))
        bundle["reference_result"] = other
        with pytest.raises(SnapshotError):
            replay_bundle(bundle)


class TestCli:
    def test_exit_codes_distinguish_the_outcomes(self, tmp_path, capsys):
        from src.dcf_model.monte_carlo import _main

        snapshot = build_snapshot(make_valuation_input(), RF, CAPTURED_AT)
        result = compute_from_snapshot(snapshot, SEED, MonteCarloConfig())

        def write(bundle, name="b.json"):
            path = tmp_path / name
            path.write_text(json.dumps(bundle, allow_nan=False))
            return str(path)

        exact = build_replay_bundle(
            snapshot, SEED, MonteCarloConfig(), expected_result_digest=result["result_digest"],
            baseline_provenance=result["baseline_provenance"], reference_result=canonical_payload(result),
        )
        assert _main(["--bundle", write(exact)]) == 0
        assert json.loads(capsys.readouterr().out)["verification_level"] == "exact_replay"

        foreign = TestReplayVerificationLevels.foreign_bundle(snapshot, result)
        assert _main(["--bundle", write(foreign)]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["verification_level"] == "numerically_equivalent_replay" and "agree within" in report["meaning"]

        variant = platform_variant(result)
        unconfirmed = build_replay_bundle(
            snapshot, SEED, MonteCarloConfig(), expected_result_digest=sha256_hex(canonical_json(canonical_payload(variant)))
        )
        assert _main(["--bundle", write(unconfirmed)]) == 2
        capsys.readouterr()
        ref = tmp_path / "ref.json"
        ref.write_text(json.dumps(variant))
        assert _main(["--bundle", write(unconfirmed), "--reference-result", str(ref)]) == 0
        capsys.readouterr()

        tampered = copy.deepcopy(exact)
        tampered["reference_result"]["baseline"]["wacc"] = nudge(tampered["reference_result"]["baseline"]["wacc"], 1)
        assert _main(["--bundle", write(tampered)]) == 1
        assert json.loads(capsys.readouterr().out)["verification_level"] == "integrity_failed"
