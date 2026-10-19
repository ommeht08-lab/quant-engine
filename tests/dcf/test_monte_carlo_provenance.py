"""Baseline provenance: derived from the resolution path, never from the resulting number."""

import copy
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.dcf_model import monte_carlo as mc
from src.dcf_model.monte_carlo import canonical_payload, resolve_baseline, run_monte_carlo, with_digest
from src.dcf_model.monte_carlo_config import PROVENANCE_SCHEMA, MonteCarloConfig
from src.dcf_model.monte_carlo_digest_compat import LEGACY_V1_DISCLOSURES, legacy_v1_digest
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

CONFIG = MonteCarloConfig()
SEED = 20261101


def run(data=None, **kwargs):
    return run_monte_carlo(data or make_financial_data(), risk_free_rate=0.04, config=CONFIG, seed=SEED, **kwargs)


def provenance(data=None, **kwargs):
    return run(data, **kwargs)["baseline_provenance"]


def with_rows(data, **row_values):
    """Copy `data` with income-statement rows added/overwritten (values oldest->newest)."""
    out = copy.deepcopy(data)
    income = out["income_statement"]
    columns = sorted(income.columns)
    for label, values in row_values.items():
        income.loc[label] = pd.Series(dict(zip(columns, values)), dtype="float64")
    return out


class TestShape:
    def test_block_is_present_complete_and_self_verifying(self):
        block = provenance()
        assert block["schema"] == PROVENANCE_SCHEMA and block["status"] == "ok"
        for key in (
            "timing", "margin", "growth", "tax_rate", "cost_of_debt", "beta", "risk_free_rate",
            "market_risk_premium", "terminal_growth_rate", "capex", "depreciation_and_amortization",
            "working_capital", "cash", "debt", "shares_outstanding", "market_price", "wacc",
            "spread_illustration", "currency",
        ):
            assert key in block
        assert block["provenance_digest"] == provenance_digest(block)

    def test_is_deterministic(self):
        assert provenance() == provenance()


class TestMarginProvenance:
    def test_names_the_row_and_every_period_used(self):
        margin = provenance()["margin"]
        assert margin["rows_used"] == ["EBIT"] and margin["mixed_rows"] is False
        assert [p["period"] for p in margin["periods"]] == ["2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"]
        assert all(p["row_used"] == "EBIT" for p in margin["periods"])
        assert margin["periods_skipped"] == []
        assert margin["periods_reproduce_model_margin"] is True

    def test_center_and_spread_are_the_ones_the_simulation_samples(self):
        result = run()
        margin = result["baseline_provenance"]["margin"]
        assert margin["center"] == result["history"]["margin_center"]
        assert margin["spread"] == result["history"]["margin_sd"]
        assert margin["model_baseline_margin"] == result["baseline"]["operating_margin"]
        assert margin["n_observations"] == result["history"]["n_margin"]

    def test_operating_income_is_reported_when_there_is_no_ebit_row(self):
        data = make_financial_data(ebit_label="Operating Income")
        margin = provenance(data)["margin"]
        assert margin["rows_used"] == ["Operating Income"]

    def test_a_period_falling_back_to_operating_income_is_flagged_as_mixed(self):
        data = make_financial_data()
        income = data["income_statement"]
        income.loc["Operating Income"] = income.loc["EBIT"] * 0.9
        income.loc["EBIT", pd.Timestamp("2022-12-31")] = np.nan
        margin = provenance(data)["margin"]
        by_period = {p["period"]: p["row_used"] for p in margin["periods"]}
        assert by_period["2022-12-31"] == "Operating Income" and by_period["2024-12-31"] == "EBIT"
        assert margin["mixed_rows"] is True and margin["rows_used"] == ["EBIT", "Operating Income"]
        assert margin["periods_reproduce_model_margin"] is True

    def test_skipped_periods_say_why(self):
        data = make_financial_data()
        income = data["income_statement"]
        income.loc["EBIT", pd.Timestamp("2021-12-31")] = np.nan
        income.loc["Total Revenue", pd.Timestamp("2022-12-31")] = np.nan
        margin = provenance(data)["margin"]
        reasons = {s["period"]: s["reason"] for s in margin["periods_skipped"]}
        assert reasons == {
            "2021-12-31": "operating_profit_row_missing",
            "2022-12-31": "revenue_missing_or_nonpositive",
        }
        assert margin["periods_reproduce_model_margin"] is True

    def test_spread_is_absent_when_history_is_refused(self):
        data = make_financial_data(
            revenues=(100_000.0, 108_000.0), ebits=(25_000.0, 28_500.0), periods=("2023-12-31", "2024-12-31")
        )
        block = provenance(data)
        assert block["margin"]["spread"] is None and block["growth"]["spread"] is None
        assert block["spread_illustration"] == {"growth": None, "margin": None}


class TestCostOfDebtProvenance:
    def test_statement_derived_when_interest_and_debt_exist(self):
        data = make_financial_data()
        block = provenance(data)["cost_of_debt"]
        assert block["source"] == "statement_derived" and block["is_fallback"] is False
        assert block["interest_expense_row"] == "Interest Expense" and block["total_debt_row"] == "Total Debt"
        assert block["rate"] == 900.0 / 20_000.0 and block["fallback_reason"] is None

    def test_fallback_is_reported_with_the_default_and_the_reason(self):
        data = make_financial_data()
        data["income_statement"].loc["Interest Expense", pd.Timestamp("2024-12-31")] = np.nan
        block = provenance(data)["cost_of_debt"]
        assert block["is_fallback"] is True and block["source"] == "default_fallback"
        assert block["rate"] == block["default_rate"] == 0.05
        assert block["fallback_reason"] == "interest_expense_unavailable_or_zero"
        assert block["interest_expense"] is None
        # earlier periods exist but are, by the model's rule, not used
        assert block["interest_expense_by_period"]["2023-12-31"] == 900.0

    def test_missing_total_debt_is_a_distinct_reason(self):
        data = make_financial_data()
        data["balance_sheet"] = data["balance_sheet"].drop(index="Total Debt")
        block = provenance(data)["cost_of_debt"]
        assert block["is_fallback"] is True and block["fallback_reason"] == "total_debt_unavailable_or_zero"

    def test_reported_rate_is_the_rate_the_deterministic_model_resolved(self):
        for data in (make_financial_data(), with_rows(make_financial_data(), **{"Interest Expense": [np.nan] * 4})):
            baseline = resolve_baseline(data, 0.04, CONFIG)
            assert provenance(data)["cost_of_debt"]["rate"] == baseline.result["cost_of_debt"]


class TestTaxProvenance:
    def test_statement_derived_rate_names_rows_and_period(self):
        tax = provenance()["tax_rate"]
        assert tax["source"] == "statement_derived" and tax["is_fallback"] is False
        assert tax["period"] == "2024-12-31"
        assert tax["pretax_income_row"] == "Pretax Income" and tax["tax_provision_row"] == "Tax Provision"
        assert math.isclose(tax["rate"], tax["tax_provision"] / tax["pretax_income"])

    def test_missing_pretax_income_falls_back_to_the_default(self):
        data = make_financial_data()
        data["income_statement"] = data["income_statement"].drop(index="Pretax Income")
        tax = provenance(data)["tax_rate"]
        assert tax["is_fallback"] is True and tax["source"] == "default_fallback"
        assert tax["rate"] == tax["default_rate"] == 0.21
        assert tax["fallback_reason"] == "pretax_income_unavailable_or_zero"

    def test_missing_provision_and_out_of_range_rate_are_distinct_reasons(self):
        no_provision = make_financial_data()
        no_provision["income_statement"] = no_provision["income_statement"].drop(index="Tax Provision")
        assert provenance(no_provision)["tax_rate"]["fallback_reason"] == "tax_provision_unavailable"

        absurd = with_rows(make_financial_data(), **{"Tax Provision": [1e9] * 4})
        assert provenance(absurd)["tax_rate"]["fallback_reason"] == "effective_rate_outside_0_to_1"


class TestFixedAssumptions:
    def test_capex_da_and_working_capital_are_labeled_fixed_with_their_values(self):
        block = provenance()
        assert block["capex"]["fixed"] is True and block["capex"]["pct_of_revenue"] == 0.04
        assert block["capex"]["source"] == "default"
        assert block["depreciation_and_amortization"] == {
            "value": 0.03, "fixed": True, "source": "model_default", "basis": block["depreciation_and_amortization"]["basis"],
        }
        assert block["working_capital"]["value"] == 0.01 and block["working_capital"]["fixed"] is True
        assert block["market_risk_premium"]["fixed"] is True and block["terminal_growth_rate"]["fixed"] is True

    def test_historical_capex_ratio_is_context_only(self):
        capex = provenance()["capex"]["historical_ratio_for_context"]
        assert capex["used_in_valuation"] is False

    def test_historical_capex_ratio_is_reported_but_not_used_when_a_cash_flow_exists(self):
        data = make_financial_data()
        columns = data["income_statement"].columns
        data["cash_flow"] = pd.DataFrame({c: {"Capital Expenditure": -2_500.0} for c in columns})
        capex = provenance(data)["capex"]
        assert capex["historical_ratio_for_context"]["status"] == "derived"
        assert capex["historical_ratio_for_context"]["ratio"] != capex["pct_of_revenue"]
        assert capex["pct_of_revenue"] == 0.04 and capex["historical_ratio_for_context"]["used_in_valuation"] is False

    def test_beta_fallback_is_flagged(self):
        assert provenance()["beta"] == {"value": 1.1, "source": "provider", "is_fallback": False}
        data = make_financial_data()
        data["beta"] = None
        assert provenance(data)["beta"] == {"value": 1.0, "source": "default_fallback", "is_fallback": True}

    def test_risk_free_rate_path_is_not_claimed(self):
        rf = provenance()["risk_free_rate"]
        assert rf["value"] == 0.04 and rf["resolution_path_recorded"] is False and rf["lookup_path"] == "unknown"
        # No value, including one equal to the lookup helper's fallback constant, changes that.
        from src.utils.macro import DEFAULT_RISK_FREE_RATE_FALLBACK

        for value in (DEFAULT_RISK_FREE_RATE_FALLBACK, 0.05098):
            block = run_monte_carlo(make_financial_data(), risk_free_rate=value, config=CONFIG, seed=SEED)["baseline_provenance"]
            assert block["risk_free_rate"]["lookup_path"] == "unknown"
            assert block["risk_free_rate"]["value"] == value


class TestCashDefinition:
    def test_excluded_short_term_investments_are_reported_with_their_per_share_value(self):
        data = make_financial_data(cash=15_000.0, shares=1_000.0)
        data["balance_sheet"].loc["Other Short Term Investments"] = 4_000.0
        cash = provenance(data)["cash"]
        assert cash["row_used"] == "Cash And Cash Equivalents" and cash["value"] == 15_000.0
        assert cash["includes_short_term_investments"] is False
        assert cash["excluded_short_term_investments"] == 4_000.0 and cash["excluded_per_share"] == 4.0
        assert cash["excluded_short_term_investments_row"] == "Other Short Term Investments"

    def test_unknown_when_the_statement_does_not_report_them(self):
        cash = provenance()["cash"]
        assert cash["excluded_short_term_investments"] is None and cash["excluded_per_share"] is None

    def test_a_combined_cash_row_includes_them(self):
        data = make_financial_data()
        balance = data["balance_sheet"]
        balance.loc["Cash Cash Equivalents And Short Term Investments"] = balance.loc["Cash And Cash Equivalents"]
        balance.drop(index="Cash And Cash Equivalents", inplace=True)
        balance.loc["Other Short Term Investments"] = 4_000.0
        cash = provenance(data)["cash"]
        assert cash["row_used"] == "Cash Cash Equivalents And Short Term Investments"
        assert cash["includes_short_term_investments"] is True and cash["excluded_short_term_investments"] is None


class TestWaccClampVersusIntervalRestriction:
    def test_unclamped_baseline_can_still_have_a_bound_restricted_interval(self):
        data = make_financial_data(beta=0.3)
        wacc = provenance(data)["wacc"]
        assert wacc["baseline_was_clamped"] is False
        assert wacc["sampled_interval"]["restricted_by_bounds"] is True
        assert wacc["sampled_interval"]["binding_constraints"] == ["model_lower_bound"]
        assert wacc["sampled_interval"]["lower"] == 0.05

    def test_clamped_baseline_is_reported_with_the_pre_clamp_rate(self):
        data = make_financial_data(beta=5.0)
        wacc = provenance(data)["wacc"]
        assert wacc["baseline_was_clamped"] is True
        assert wacc["baseline_applied"] == 0.20 and wacc["baseline_pre_clamp"] > 0.20
        assert wacc["sampled_interval"]["upper"] == 0.20

    def test_an_ordinary_baseline_has_neither(self):
        wacc = provenance()["wacc"]
        assert wacc["baseline_was_clamped"] is False
        assert wacc["sampled_interval"]["restricted_by_bounds"] is False
        assert wacc["sampled_interval"]["binding_constraints"] == []

    def test_agrees_with_the_experiment_distribution(self):
        result = run(make_financial_data(beta=0.3))
        spec = result["experiments"]["wacc"]["distributions"]["wacc"]
        sampled = result["baseline_provenance"]["wacc"]["sampled_interval"]
        assert (sampled["lower"], sampled["upper"]) == (spec["lower"], spec["upper"])


class TestTiming:
    def test_capture_time_is_never_reported_as_the_quote_time(self):
        snapshot = build_snapshot(make_valuation_input(), RF, CAPTURED_AT)
        assert snapshot["market"]["price_observed_at"] == CAPTURED_AT.isoformat()  # legacy stand-in field
        timing = compute_from_snapshot(snapshot, SEED, CONFIG)["baseline_provenance"]["timing"]
        assert timing["market_price"]["observed_at"] is None
        assert timing["market_price"]["status"] == "not_provided_by_provider"
        assert timing["snapshot_captured_at"] == CAPTURED_AT.isoformat()

    def test_a_genuine_quote_time_is_passed_through(self):
        context = {
            "snapshot_captured_at": "2026-10-19T04:00:00+00:00",
            "statement_period_end": "2024-12-31",
            "price_observed_at": "2026-10-16T20:00:01+00:00",
            "price_observed_at_is_capture_time": False,
        }
        price = provenance(provenance_context=context)["timing"]["market_price"]
        assert price["observed_at"] == "2026-10-16T20:00:01+00:00" and price["status"] == "provider_quote_time"

    def test_without_a_snapshot_the_time_is_simply_not_recorded(self):
        timing = provenance()["timing"]
        assert timing["snapshot_captured_at"] is None
        assert timing["market_price"] == {
            "observed_at": None, "status": "not_recorded", "note": timing["market_price"]["note"],
        }

    def test_statement_dates_and_age_at_capture(self):
        snapshot = build_snapshot(make_valuation_input(), RF, CAPTURED_AT)
        timing = compute_from_snapshot(snapshot, SEED, CONFIG)["baseline_provenance"]["timing"]
        assert timing["latest_statement_period_end"] == "2024-12-31"
        assert timing["latest_statement_age_days_at_capture"] == (CAPTURED_AT.date() - date(2024, 12, 31)).days
        assert timing["statements"]["income_statement"]["periods"] == [
            "2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31",
        ]
        assert timing["statements"]["cash_flow"] == {"periods": [], "latest_period": None}
        assert "trailing twelve months" in timing["revenue_basis"]


class TestSpreadIllustration:
    def test_multipliers_match_the_chi_square_interval_for_the_observation_counts(self):
        illustration = provenance()["spread_illustration"]
        growth, margin = illustration["growth"], illustration["margin"]
        assert (growth["n_observations"], growth["degrees_of_freedom"]) == (3, 2)
        assert math.isclose(growth["lower_multiplier"], 0.5778, abs_tol=5e-5)
        assert math.isclose(growth["upper_multiplier"], 4.4154, abs_tol=5e-5)
        assert (margin["n_observations"], margin["degrees_of_freedom"]) == (4, 3)
        assert math.isclose(margin["lower_multiplier"], 0.6196, abs_tol=5e-5)
        assert math.isclose(margin["upper_multiplier"], 2.9200, abs_tol=5e-5)

    def test_it_is_labeled_an_illustration_with_its_assumptions(self):
        growth = provenance()["spread_illustration"]["growth"]
        assert growth["assumptions"] == [
            "independent observations", "a single normal distribution", "no change in the underlying process",
        ]
        assert "Illustration only" in growth["statement"] and "not a distribution-free" in growth["statement"]

    def test_the_disclosure_no_longer_states_the_interval_unconditionally(self):
        text = next(d["text"] for d in mc.DISCLOSURES if d["id"] == "noisy_spreads")
        assert "illustration only" in text.lower() and "independent" in text and "normal" in text
        assert "not a distribution-free confidence statement" in text


class TestDigestScope:
    def test_provenance_and_disclosure_text_do_not_change_the_numerical_digest(self):
        result = run()
        changed = copy.deepcopy(result)
        changed["baseline_provenance"]["margin"]["rows_used"] = ["Operating Income"]
        changed["disclosures"] = [{"id": "x", "text": "different copy"}]
        assert with_digest(changed)["result_digest"] == result["result_digest"]

    def test_the_numerical_payload_still_hashes_to_the_original_digest_scheme(self):
        # Digest of the steady fixture recorded before the digest scope changed.
        assert legacy_v1_digest(canonical_payload(run())) == (
            "477d8ddfbf7621ebdfe19898f12f143e0dc92007070e29bda86d200d295e3024"
        )

    def test_legacy_disclosures_are_the_frozen_original_text(self):
        ids = [d["id"] for d in LEGACY_V1_DISCLOSURES]
        assert "margin_definition" not in ids and len(ids) == 11
        noisy = next(d["text"] for d in LEGACY_V1_DISCLOSURES if d["id"] == "noisy_spreads")
        assert noisy.endswith("roughly 0.58x to 4.4x the estimate.")

    def test_tampered_provenance_fails_its_own_digest(self):
        block = copy.deepcopy(provenance())
        block["tax_rate"]["is_fallback"] = True
        assert provenance_digest(block) != block["provenance_digest"]

    def test_a_provenance_failure_never_voids_the_simulation(self, monkeypatch, caplog):
        expected = run()

        def boom(*args, **kwargs):
            raise RuntimeError("unexpected statement layout")

        monkeypatch.setattr(mc, "build_baseline_provenance", boom)
        degraded = run()
        assert degraded["baseline_provenance"]["status"] == "unavailable"
        assert degraded["baseline_provenance"]["error_type"] == "RuntimeError"
        assert degraded["result_digest"] == expected["result_digest"]
        assert degraded["experiments"] == expected["experiments"]


class TestReplayBundleProvenance:
    @pytest.fixture
    def exported(self):
        snapshot = build_snapshot(make_valuation_input(), RF, CAPTURED_AT)
        result = compute_from_snapshot(snapshot, SEED, CONFIG)
        bundle = build_replay_bundle(
            snapshot, SEED, CONFIG,
            expected_result_digest=result["result_digest"], baseline_provenance=result["baseline_provenance"],
        )
        return snapshot, result, bundle

    def test_bundle_carries_the_provenance_and_its_digest(self, exported):
        _, result, bundle = exported
        assert bundle["baseline_provenance"] == result["baseline_provenance"]
        assert bundle["replay"]["expected_provenance_digest"] == result["baseline_provenance"]["provenance_digest"]

    def test_replay_verifies_both_digests(self, exported):
        _, _, bundle = exported
        outcome = replay_bundle(bundle)
        assert outcome["matches_expected"] is True and outcome["matched_digest_scheme"] == "current"
        assert outcome["provenance_matches_expected"] is True

    def test_the_exported_provenance_block_and_the_replay_section_are_integrity_protected(self, exported):
        _, _, bundle = exported
        assert replay_bundle(copy.deepcopy(bundle))["bundle_integrity_ok"] is True
        edits = {
            "block edited, digest left": lambda b: b["baseline_provenance"]["tax_rate"].__setitem__("is_fallback", True),
            "expected provenance digest edited": lambda b: b["replay"].__setitem__("expected_provenance_digest", "0" * 64),
            "configuration edited": lambda b: b["replay"]["config"].__setitem__("target_valid", 5),
            "seed edited": lambda b: b["replay"].__setitem__("seed", 7),
            "block removed after export": lambda b: b.pop("baseline_provenance"),
        }
        for name, edit in edits.items():
            tampered = copy.deepcopy(bundle)
            edit(tampered)
            with pytest.raises(SnapshotError):
                replay_bundle(tampered)
            assert name

    def test_a_block_whose_digest_was_recomputed_but_not_the_bundle_digest_is_refused(self, exported):
        _, _, bundle = exported
        bundle["baseline_provenance"]["tax_rate"]["is_fallback"] = True
        bundle["baseline_provenance"]["provenance_digest"] = provenance_digest(bundle["baseline_provenance"])
        bundle["replay"]["expected_provenance_digest"] = bundle["baseline_provenance"]["provenance_digest"]
        with pytest.raises(SnapshotError):
            replay_bundle(bundle)  # the bundle digest still binds the original provenance digest

    def test_a_bundle_without_a_bundle_digest_is_replayed_but_not_vouched_for(self, exported):
        _, _, bundle = exported
        del bundle["bundle_digest"]
        assert replay_bundle(bundle)["bundle_integrity_ok"] is None

    def test_a_bundle_exported_before_provenance_still_replays(self, exported):
        snapshot, result, _ = exported
        bundle = build_replay_bundle(snapshot, SEED, CONFIG, expected_result_digest=result["result_digest"])
        assert "baseline_provenance" not in bundle
        outcome = replay_bundle(bundle)
        assert outcome["matches_expected"] is True and outcome["provenance_matches_expected"] is None

    def test_a_bundle_carrying_the_original_digest_still_verifies(self, exported):
        snapshot, result, _ = exported
        original = legacy_v1_digest(canonical_payload(result))
        outcome = replay_bundle(build_replay_bundle(snapshot, SEED, CONFIG, expected_result_digest=original))
        assert outcome["matches_expected"] is True and outcome["matched_digest_scheme"] == "legacy_v1"

    def test_a_wrong_digest_still_fails(self, exported):
        snapshot, _, _ = exported
        outcome = replay_bundle(build_replay_bundle(snapshot, SEED, CONFIG, expected_result_digest="f" * 64))
        assert outcome["matches_expected"] is False and outcome["matched_digest_scheme"] is None
