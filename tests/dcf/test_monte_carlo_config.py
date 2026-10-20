"""Configuration validation, hashing and the ticker allowlist."""

import pytest

from src.dcf_model.monte_carlo_config import (
    DEFAULT_TICKER_ALLOWLIST,
    MAX_ALLOWLIST_SIZE,
    MonteCarloConfig,
    MonteCarloRefusal,
    config_hash,
    load_ticker_allowlist,
    validate_seed,
)


class TestConfig:
    def test_defaults_match_the_approved_plan(self):
        config = MonteCarloConfig()
        assert config.forecast_mode == "maturation" and config.near_term_years == 2
        assert config.target_valid == 10_000 and config.projection_years == 5
        assert (config.growth_lower, config.growth_upper) == (-0.10, 0.25)
        assert (config.margin_lower, config.margin_upper) == (0.0, 0.60)
        assert config.wacc_half_width == 0.01 and (config.wacc_lower_bound, config.wacc_upper_bound) == (0.05, 0.20)
        assert config.max_retry_rounds == 3 and config.max_extra_attempts == 500
        assert config.histogram_bins == 40 and config.percentile_method == "linear"

    def test_canonical_round_trip_preserves_equality(self):
        for config in (MonteCarloConfig(), MonteCarloConfig(wacc_half_width=0.0, forecast_mode="constant")):
            assert MonteCarloConfig.from_canonical_dict(config.to_canonical_dict()) == config

    def test_any_changed_parameter_changes_the_hash(self):
        base = config_hash(MonteCarloConfig())
        for override in (
            {"wacc_half_width": 0.02}, {"growth_upper": 0.3}, {"near_term_years": 3}, {"max_extra_attempts": 400},
            {"histogram_bins": 41}, {"min_admissible_mass": 1e-5}, {"terminal_growth_rate": 0.02},
            {"forecast_mode": "constant"}, {"min_growth_observations": 4},
        ):
            assert config_hash(MonteCarloConfig(**override)) != base, override

    @pytest.mark.parametrize(
        "override",
        [
            {"forecast_mode": "other"}, {"target_valid": 0}, {"target_valid": 100_001},
            {"growth_lower": 0.3, "growth_upper": 0.25}, {"wacc_half_width": -0.01},
            {"min_admissible_mass": 0.0}, {"min_admissible_mass": 1.0}, {"percentile_method": "nearest"},
            {"near_term_years": 5, "projection_years": 5}, {"histogram_bins": 0},
            {"mature_growth_ceiling": float("nan")}, {"max_retry_rounds": -1},
            {"histogram_low_percentile": 99.0, "histogram_high_percentile": 1.0},
        ],
    )
    def test_invalid_configurations_are_rejected(self, override):
        with pytest.raises(ValueError):
            MonteCarloConfig(**override)

    def test_malformed_replay_config_is_rejected(self):
        with pytest.raises(ValueError):
            MonteCarloConfig.from_canonical_dict({"forecast_mode": "maturation"})


class TestSeed:
    def test_valid_and_invalid_seeds(self):
        assert validate_seed(0) == 0 and validate_seed(2**53 - 1) == 2**53 - 1
        for bad in (-1, 2**53, 2**63, True, 1.0, "1", None):
            with pytest.raises(ValueError):
                validate_seed(bad)


class TestAllowlist:
    def test_default_supports_three_tickers_within_the_five_ticker_cap(self):
        assert DEFAULT_TICKER_ALLOWLIST[0] == "AAPL" and 3 <= len(DEFAULT_TICKER_ALLOWLIST) <= MAX_ALLOWLIST_SIZE
        assert load_ticker_allowlist({}) == DEFAULT_TICKER_ALLOWLIST

    def test_env_override_is_normalized_and_deduplicated(self):
        assert load_ticker_allowlist({"MONTE_CARLO_TICKERS": " aapl, brk-b ,AAPL,ko "}) == ("AAPL", "BRK-B", "KO")

    def test_five_tickers_are_allowed_and_six_are_not(self):
        five = "A,B,C,D,E"
        assert len(load_ticker_allowlist({"MONTE_CARLO_TICKERS": five})) == 5
        with pytest.raises(ValueError):
            load_ticker_allowlist({"MONTE_CARLO_TICKERS": five + ",F"})

    @pytest.mark.parametrize("raw", ["AAPL,,KO", "AA PL", "../x", "AAPL;DROP", "1ABC"])
    def test_malformed_entries_are_rejected(self, raw):
        with pytest.raises(ValueError):
            load_ticker_allowlist({"MONTE_CARLO_TICKERS": raw})


def test_unknown_refusal_codes_cannot_be_constructed():
    with pytest.raises(ValueError):
        MonteCarloRefusal("made_up", "x")
