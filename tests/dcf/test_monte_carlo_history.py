"""Historical centers/spreads: alignment with the deterministic model and typed refusals."""

import statistics

import numpy as np
import pandas as pd
import pytest

from src.dcf_model.dcf import (
    calculate_historical_average_operating_margin,
    calculate_historical_revenue_cagr,
)
from src.dcf_model.monte_carlo_config import MonteCarloConfig, MonteCarloRefusal
from src.dcf_model.monte_carlo_history import extract_historical_spreads
from tests.dcf.monte_carlo_fixtures import make_financial_data

CONFIG = MonteCarloConfig()


def _income(**kwargs):
    return make_financial_data(**kwargs)["income_statement"]


class TestAlignment:
    def test_centers_equal_the_deterministic_functions_exactly(self):
        income = _income()
        spreads = extract_historical_spreads(income, CONFIG)
        assert spreads.growth_center_cagr == calculate_historical_revenue_cagr(income)
        assert spreads.margin_center == calculate_historical_average_operating_margin(income)

    def test_growth_center_is_uncapped(self):
        income = _income(revenues=(100.0, 150.0, 225.0, 340.0), ebits=(20.0, 30.0, 45.0, 68.0))
        spreads = extract_historical_spreads(income, CONFIG)
        assert spreads.growth_center_cagr > 0.25
        assert spreads.growth_center_cagr == calculate_historical_revenue_cagr(income)

    def test_yoy_growths_are_consecutive_pairs_in_date_order(self):
        spreads = extract_historical_spreads(_income(), CONFIG)
        assert spreads.revenue_periods == ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"]
        assert spreads.revenues == [100_000.0, 108_000.0, 115_000.0, 125_000.0]
        assert spreads.yoy_growths == pytest.approx([0.08, 115 / 108 - 1, 125 / 115 - 1], abs=1e-15)
        assert spreads.n_growth == 3

    def test_spreads_use_the_sample_standard_deviation(self):
        spreads = extract_historical_spreads(_income(), CONFIG)
        assert spreads.growth_sd == pytest.approx(statistics.stdev(spreads.yoy_growths), rel=1e-12)
        assert spreads.margin_sd == pytest.approx(statistics.stdev(spreads.margins), rel=1e-12)
        population = float(np.std(spreads.yoy_growths, ddof=0))
        assert spreads.growth_sd > population  # ddof=1 is strictly larger

    def test_a_missing_extra_period_is_ignored_like_the_deterministic_model_does(self):
        income = _income()
        income[pd.Timestamp("2020-12-31")] = np.nan
        spreads = extract_historical_spreads(income, CONFIG)
        assert spreads.n_growth == 3
        assert spreads.growth_center_cagr == calculate_historical_revenue_cagr(income)

    def test_margin_falls_back_to_operating_income_exactly_like_the_model(self):
        income = _income(ebit_label="Operating Income")
        spreads = extract_historical_spreads(income, CONFIG)
        assert spreads.margin_center == calculate_historical_average_operating_margin(income)
        assert spreads.n_margin == 4

    def test_margin_periods_are_a_subset_of_revenue_periods(self):
        spreads = extract_historical_spreads(_income(), CONFIG)
        assert set(spreads.margin_periods) <= set(spreads.revenue_periods)

    def test_negative_margin_observations_are_allowed_in_the_spread(self):
        income = _income(ebits=(-5_000.0, 10_000.0, 28_000.0, 33_000.0))
        spreads = extract_historical_spreads(income, CONFIG)
        assert min(spreads.margins) < 0
        assert spreads.margin_sd > 0


class TestRefusals:
    def _code(self, income):
        with pytest.raises(MonteCarloRefusal) as raised:
            extract_historical_spreads(income, CONFIG)
        return raised.value.code

    def test_missing_statement(self):
        assert self._code(None) == "input_incomplete"
        assert self._code(pd.DataFrame()) == "input_incomplete"

    def test_no_revenue_row(self):
        income = _income().drop(index="Total Revenue")
        assert self._code(income) == "input_incomplete"

    def test_three_periods_is_two_growth_observations(self):
        income = _income(
            revenues=(108_000.0, 115_000.0, 125_000.0),
            ebits=(28_500.0, 28_000.0, 33_000.0),
            periods=PERIODS_3,
        )
        assert self._code(income) == "insufficient_history"

    def test_too_few_margin_observations(self):
        income = _income()
        income.loc["EBIT", pd.Timestamp("2021-12-31")] = np.nan
        income.loc["EBIT", pd.Timestamp("2022-12-31")] = np.nan
        assert self._code(income) == "insufficient_history"

    def test_irregular_fiscal_spacing(self):
        income = _income(periods=("2021-12-31", "2022-12-31", "2023-06-30", "2024-06-30"))
        assert self._code(income) == "irregular_fiscal_spacing"

    def test_nonpositive_revenue(self):
        income = _income(revenues=(100_000.0, 0.0, 115_000.0, 125_000.0))
        assert self._code(income) == "nonpositive_revenue"

    def test_unparseable_period_labels(self):
        income = _income()
        income.columns = ["a", "b", "c", "d"]
        assert self._code(income) == "history_alignment_mismatch"

    def test_no_generic_spread_is_ever_substituted(self):
        # A refusal carries no spread at all; there is nothing to fall back to.
        with pytest.raises(MonteCarloRefusal):
            extract_historical_spreads(None, CONFIG)


PERIODS_3 = ("2022-12-31", "2023-12-31", "2024-12-31")
