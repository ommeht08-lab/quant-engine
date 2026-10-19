"""Snapshots and replay bundles: identity, fidelity, tamper detection, exact replay."""

import copy
import json
from datetime import date, datetime, timezone

import pytest

from src.dcf_model import monte_carlo_snapshot as snap
from src.dcf_model.monte_carlo import canonical_payload, run_monte_carlo
from src.dcf_model.monte_carlo_config import MonteCarloConfig, canonical_json
from src.dcf_model.monte_carlo_snapshot import (
    SnapshotError,
    build_replay_bundle,
    build_snapshot,
    compute_from_snapshot,
    replay_bundle,
    snapshot_financial_data,
    verify_snapshot,
)
from src.valuation_input import (
    ValuationInput,
    ValuationInputProvenance,
    ValuationInputSource,
)
from tests.dcf.monte_carlo_fixtures import make_financial_data

CAPTURED_AT = datetime(2026, 10, 20, 15, 30, tzinfo=timezone.utc)
RF = 0.0425


def make_valuation_input(data=None, ticker="TEST"):
    data = data or make_financial_data(ticker=ticker)
    provenance = ValuationInputProvenance(
        source=ValuationInputSource.YAHOO,
        source_selection_reason="Yahoo was explicitly requested.",
        knowledge_cutoff=CAPTURED_AT,
        data_vintage_cutoff=CAPTURED_AT,
        statement_period_start=None,
        statement_period_end=date(2024, 12, 31),
        policy_version="yahoo-live-statements-v1",
        source_adapter="yahoo_finance",
    )
    return ValuationInput(ticker=ticker, financial_data=data, provenance=provenance)


@pytest.fixture
def snapshot():
    return build_snapshot(make_valuation_input(), RF, CAPTURED_AT)


class TestSnapshotIdentity:
    def test_id_is_deterministic_and_content_derived(self, snapshot):
        assert snapshot["snapshot_id"] == build_snapshot(make_valuation_input(), RF, CAPTURED_AT)["snapshot_id"]
        later = build_snapshot(make_valuation_input(), RF, CAPTURED_AT.replace(hour=16))
        assert later["snapshot_id"] != snapshot["snapshot_id"]
        different = build_snapshot(make_valuation_input(make_financial_data(price=151.0)), RF, CAPTURED_AT)
        assert different["snapshot_id"] != snapshot["snapshot_id"]
        assert build_snapshot(make_valuation_input(), RF + 0.001, CAPTURED_AT)["snapshot_id"] != snapshot["snapshot_id"]

    def test_records_dated_inputs_market_price_and_provenance(self, snapshot):
        assert snapshot["captured_at"] == CAPTURED_AT.isoformat()
        assert snapshot["market"]["current_price"] == 150.0
        assert snapshot["market"]["risk_free_rate"] == RF
        assert snapshot["market"]["price_observed_at"] == CAPTURED_AT.isoformat()
        assert snapshot["market"]["price_observed_at_is_capture_time"] is True
        assert snapshot["provenance"]["statement_period_end"] == "2024-12-31"
        assert snapshot["provenance"]["source_adapter"] == "yahoo_finance"
        assert set(snapshot["capture_environment"]) >= {"python", "numpy", "pandas", "scipy", "model_version"}

    def test_verify_accepts_a_good_snapshot_and_survives_a_json_round_trip(self, snapshot):
        verify_snapshot(snapshot)
        verify_snapshot(json.loads(json.dumps(snapshot, allow_nan=False)))

    @pytest.mark.parametrize(
        "tamper",
        [
            lambda s: s["market"].__setitem__("current_price", 1.0),
            lambda s: s["statements"]["income_statement"]["value"]["data"][0].__setitem__(0, 9.9),
            lambda s: s.__setitem__("ticker", "OTHER"),
            lambda s: s["market"].__setitem__("risk_free_rate", 0.2),
        ],
    )
    def test_tampering_is_detected(self, snapshot, tamper):
        broken = copy.deepcopy(snapshot)
        tamper(broken)
        with pytest.raises(SnapshotError):
            verify_snapshot(broken)

    def test_unsupported_schema_is_rejected(self, snapshot):
        with pytest.raises(SnapshotError):
            verify_snapshot({**snapshot, "schema": "other"})

    def test_nonfinite_market_values_and_missing_risk_free_rate_are_rejected(self):
        with pytest.raises(SnapshotError):
            build_snapshot(make_valuation_input(make_financial_data(price=float("inf"))), RF, CAPTURED_AT)
        with pytest.raises(SnapshotError):
            build_snapshot(make_valuation_input(), None, CAPTURED_AT)

    def test_naive_capture_time_is_rejected(self):
        with pytest.raises(SnapshotError):
            build_snapshot(make_valuation_input(), RF, datetime(2026, 10, 20))


class TestFidelity:
    def test_decoded_statements_reproduce_the_original_numbers(self, snapshot):
        original = make_financial_data()
        decoded = snapshot_financial_data(json.loads(json.dumps(snapshot)))
        assert decoded["current_price"] == original["current_price"]
        assert list(decoded["income_statement"].columns) == list(original["income_statement"].columns)
        assert list(decoded["income_statement"].index) == list(original["income_statement"].index)
        assert (decoded["income_statement"].values == original["income_statement"].values).all()
        assert decoded["cash_flow"] is None

    def test_a_run_from_the_snapshot_equals_a_run_from_the_original_data(self, snapshot):
        config = MonteCarloConfig()
        direct = run_monte_carlo(make_financial_data(), risk_free_rate=RF, config=config, seed=11)
        via_snapshot = compute_from_snapshot(json.loads(json.dumps(snapshot)), 11, config)
        for key in ("baseline", "history", "experiments", "histogram_edges", "market_price", "status"):
            assert canonical_json(direct[key]) == canonical_json(via_snapshot[key]), key

    def test_result_identity_names_the_snapshot(self, snapshot):
        result = compute_from_snapshot(snapshot, 1, MonteCarloConfig())
        assert result["identity"] == {
            "ticker": "TEST",
            "source": "yahoo",
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_captured_at": CAPTURED_AT.isoformat(),
            "statement_period_end": "2024-12-31",
            "market_price_observed_at": CAPTURED_AT.isoformat(),
        }


class TestNumberFormattingIsPartOfTheHash:
    """Why the Next export route must forward bundle bytes untouched."""

    def test_rewriting_integral_floats_as_ints_breaks_the_snapshot_id(self, snapshot):
        text = json.dumps(snapshot, allow_nan=False)
        assert ".0" in text  # statement values are stored as floats
        rewritten = json.loads(text.replace(".0,", ",").replace(".0}", "}").replace(".0]", "]"))
        assert canonical_json(rewritten) != canonical_json(snapshot)  # equal as numbers, different as hashed text
        with pytest.raises(SnapshotError):
            verify_snapshot(rewritten)

    def test_a_byte_identical_round_trip_verifies(self, snapshot):
        verify_snapshot(json.loads(json.dumps(snapshot, allow_nan=False)))


class TestReplayBundle:
    CONFIG = MonteCarloConfig(wacc_half_width=0.005, near_term_years=3, histogram_bins=25)

    def _bundle(self, snapshot, expected=True, seed=987654321, digest=None):
        if digest is None and expected:
            digest = compute_from_snapshot(snapshot, seed, self.CONFIG)["result_digest"]
        return build_replay_bundle(snapshot, seed, self.CONFIG, expected_result_digest=digest)

    def test_bundle_carries_everything_needed_to_replay(self, snapshot):
        bundle = self._bundle(snapshot)
        replay = bundle["replay"]
        assert replay["seed"] == 987654321
        assert MonteCarloConfig.from_canonical_dict(replay["config"]) == self.CONFIG
        assert replay["model_version"] == snap.MONTE_CARLO_MODEL_VERSION
        assert replay["expected_result_digest"] and bundle["snapshot"]["snapshot_id"] == snapshot["snapshot_id"]

    def test_replay_reproduces_the_identical_numerical_payload(self, snapshot):
        bundle = json.loads(json.dumps(self._bundle(snapshot), allow_nan=False))
        outcome = replay_bundle(bundle)
        assert outcome["matches_expected"] is True
        original = compute_from_snapshot(snapshot, 987654321, self.CONFIG)
        assert canonical_json(canonical_payload(outcome["result"])) == canonical_json(canonical_payload(original))
        assert outcome["model_version_matches"] is True
        assert outcome["replayed_with_environment"] == snap.capture_environment()

    def test_a_non_default_configuration_is_replayed_not_the_defaults(self, snapshot):
        bundle = self._bundle(snapshot)
        outcome = replay_bundle(bundle)
        assert outcome["result"]["config"]["wacc"]["half_width"] == 0.005
        assert outcome["result"]["config_hash"] != compute_from_snapshot(snapshot, 987654321, MonteCarloConfig())["config_hash"]

    def test_a_wrong_expected_digest_is_reported(self, snapshot):
        bundle = self._bundle(snapshot, digest="0" * 64)
        assert replay_bundle(bundle)["matches_expected"] is False

    @pytest.mark.parametrize(
        "edit",
        [
            lambda b: b["replay"].__setitem__("expected_result_digest", "0" * 64),
            lambda b: b["replay"].__setitem__("seed", 1),
            lambda b: b["replay"].__setitem__("model_version", "mc-dcf-v0"),
            lambda b: b.__setitem__("schema", b["schema"]) or b["replay"]["config"].__setitem__("histogram_bins", 30),
        ],
    )
    def test_editing_the_replay_section_after_export_is_refused_not_reported(self, snapshot, edit):
        bundle = self._bundle(snapshot)
        assert replay_bundle(copy.deepcopy(bundle))["bundle_integrity_ok"] is True
        edit(bundle)
        with pytest.raises(SnapshotError):
            replay_bundle(bundle)

    def test_no_expected_digest_gives_none(self, snapshot):
        assert replay_bundle(self._bundle(snapshot, expected=False))["matches_expected"] is None

    def test_edited_config_or_snapshot_is_refused(self, snapshot):
        bundle = self._bundle(snapshot)
        edited = copy.deepcopy(bundle)
        edited["replay"]["config"]["wacc"]["half_width"] = 0.02
        with pytest.raises(SnapshotError):
            replay_bundle(edited)
        edited = copy.deepcopy(bundle)
        edited["snapshot"]["market"]["current_price"] = 1.0
        with pytest.raises(SnapshotError):
            replay_bundle(edited)

    def test_unsupported_bundle_schema_is_refused(self, snapshot):
        with pytest.raises(SnapshotError):
            replay_bundle({"schema": "nope"})

    def test_cli_replays_a_bundle_file_offline(self, snapshot, tmp_path, capsys):
        from src.dcf_model.monte_carlo import _main

        path = tmp_path / "bundle.json"
        path.write_text(json.dumps(self._bundle(snapshot), allow_nan=False))
        out_path = tmp_path / "result.json"
        assert _main(["--bundle", str(path), "--output", str(out_path)]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["matches_expected"] is True
        assert json.loads(out_path.read_text())["result_digest"] == report["actual_result_digest"]

        broken = self._bundle(snapshot, digest="f" * 64)
        path.write_text(json.dumps(broken))
        assert _main(["--bundle", str(path)]) == 1
