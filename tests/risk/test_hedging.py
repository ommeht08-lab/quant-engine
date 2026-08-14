"""
Group F: hedge sizing units — scenario-based P&L sizing, budget/max-contract
caps, ceil-not-floor rounding, and BSM math sanity. Pure math, no network.
"""

import math

import pytest

from src.risk.hedging import (
    calculate_bsm_put_price,
    calculate_spy_hedge,
)


class TestScenarioBasedSizing:
    def test_larger_var_needs_more_contracts(self):
        small = calculate_spy_hedge(5_000, 580, 580)
        large = calculate_spy_hedge(50_000, 580, 580)
        assert large >= small

    def test_sizing_matches_hand_computed_scenario_payoff(self):
        var_dollars = 10_000.0
        spy_price = 580.0
        strike = 580.0
        days = 30
        stress = 0.07
        implied_vol = 0.15
        rf = 0.04

        current_price = calculate_bsm_put_price(spy_price, strike, days / 365.0, rf, implied_vol)
        stressed_price = calculate_bsm_put_price(spy_price * (1 - stress), strike, days / 365.0, rf, implied_vol)
        pnl_per_contract = (stressed_price - current_price) * 100
        expected = math.ceil(var_dollars / pnl_per_contract)

        actual = calculate_spy_hedge(
            var_dollars, spy_price, strike, days_to_expiry=days, implied_vol=implied_vol,
            risk_free_rate=rf, stress_move_fraction=stress,
        )
        assert actual == expected

    def test_rounds_up_not_down(self):
        """Contracts must be ceil'd: a sizing that needs 4.2 contracts must return 5, not 4."""
        # Pick a VaR dollar amount deliberately not a clean multiple of the
        # per-contract payoff, then verify against the hand-computed ceil.
        current_price = calculate_bsm_put_price(580, 580, 30 / 365.0, 0.04, 0.15)
        stressed_price = calculate_bsm_put_price(580 * 0.93, 580, 30 / 365.0, 0.04, 0.15)
        pnl_per_contract = (stressed_price - current_price) * 100
        var_dollars = pnl_per_contract * 4.2  # deliberately fractional

        contracts = calculate_spy_hedge(var_dollars, 580, 580, stress_move_fraction=0.07)
        assert contracts == 5  # ceil(4.2) == 5, never 4


class TestBudgetAndMaxContractCaps:
    def test_budget_caps_below_uncapped_size(self):
        uncapped = calculate_spy_hedge(100_000, 580, 580)
        current_price = calculate_bsm_put_price(580, 580, 30 / 365.0, 0.04, 0.15)
        tiny_budget = current_price * 100 * 2  # afford ~2 contracts
        capped = calculate_spy_hedge(100_000, 580, 580, hedge_budget_dollars=tiny_budget)
        assert capped < uncapped
        assert capped <= 2

    def test_max_contracts_hard_cap(self):
        contracts = calculate_spy_hedge(10_000_000, 580, 580, max_contracts=3)
        assert contracts <= 3

    def test_zero_budget_yields_zero_contracts(self):
        assert calculate_spy_hedge(50_000, 580, 580, hedge_budget_dollars=0) == 0

    def test_capped_hedge_can_undercover_the_stated_var_by_design(self):
        """Budget/max caps are hard ceilings — leaving the hedge covering less
        than the full stated VaR is intentional, not a bug."""
        contracts = calculate_spy_hedge(1_000_000, 580, 580, max_contracts=1)
        assert contracts == 1  # nowhere near enough to cover $1M of VaR, by design


class TestInvalidInputsReturnZero:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"portfolio_var_dollars": 0},
            {"portfolio_var_dollars": -100},
            {"spy_price": 0},
            {"spy_price": -1},
            {"strike_price": 0},
            {"days_to_expiry": 0},
            {"days_to_expiry": -5},
            {"implied_vol": 0},
            {"stress_move_fraction": 0},
            {"stress_move_fraction": 1.0},
            {"stress_move_fraction": 1.5},
        ],
    )
    def test_invalid_input_returns_zero_not_raise(self, kwargs):
        base = dict(portfolio_var_dollars=10_000, spy_price=580, strike_price=580)
        base.update(kwargs)
        assert calculate_spy_hedge(**base) == 0


class TestBSMSanity:
    def test_put_price_is_positive_for_reasonable_inputs(self):
        price = calculate_bsm_put_price(580, 580, 30 / 365.0, 0.04, 0.15)
        assert price > 0

    def test_deeper_stress_increases_put_value(self):
        base = calculate_bsm_put_price(580, 580, 30 / 365.0, 0.04, 0.15)
        stressed_more = calculate_bsm_put_price(580 * 0.85, 580, 30 / 365.0, 0.04, 0.15)
        assert stressed_more > base
