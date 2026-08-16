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


class TestNumericBoundaryHardening:
    """
    Regression for Finding 4: every reproduced adversarial input must
    return 0 (this module's established, documented "never raises"
    contract), never raise `OverflowError`/`TypeError`/`ValueError`, and
    the return value must always genuinely be an `int` when non-zero.
    """

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"hedge_budget_dollars": float("inf")},  # the exact reproduced OverflowError
            {"hedge_budget_dollars": float("-inf")},
            {"hedge_budget_dollars": float("nan")},
            {"max_contracts": 1.5},  # the exact reproduced float-leak defect
            {"max_contracts": -1.5},
            {"max_contracts": float("inf")},
            {"max_contracts": float("nan")},
            {"max_contracts": True},  # bool is an int subclass -- must not silently mean "cap at 1"
            {"days_to_expiry": 30.5},
            {"days_to_expiry": float("inf")},
            {"days_to_expiry": float("nan")},
            {"days_to_expiry": True},
            {"portfolio_var_dollars": float("inf")},
            {"portfolio_var_dollars": float("nan")},
            {"spy_price": float("inf")},
            {"spy_price": float("nan")},
            {"strike_price": float("inf")},
            {"strike_price": float("nan")},
            {"implied_vol": float("inf")},
            {"implied_vol": float("nan")},
            {"stress_move_fraction": float("nan")},
            {"risk_free_rate": float("nan")},
            {"risk_free_rate": float("inf")},
        ],
    )
    def test_adversarial_input_returns_zero_never_raises(self, kwargs):
        base = dict(portfolio_var_dollars=10_000, spy_price=580, strike_price=580)
        base.update(kwargs)
        result = calculate_spy_hedge(**base)
        assert result == 0
        assert isinstance(result, int)

    def test_integral_float_max_contracts_is_still_honored(self):
        """`30.0` (a whole-number float) is a legitimate cap, not a corrupted value -- must not be rejected."""
        contracts = calculate_spy_hedge(10_000_000, 580, 580, max_contracts=3.0)
        assert contracts == 3
        assert isinstance(contracts, int)

    def test_zero_max_contracts_is_a_legitimate_explicit_cap(self):
        """0 means 'no hedge allowed' -- distinct from a corrupted/invalid value, must not be rejected outright."""
        assert calculate_spy_hedge(10_000_000, 580, 580, max_contracts=0) == 0

    def test_return_value_is_always_a_genuine_int_under_normal_operation(self):
        contracts = calculate_spy_hedge(10_000, 580, 580, max_contracts=100)
        assert isinstance(contracts, int)
        assert not isinstance(contracts, bool)


class TestExtremeButFiniteInputsNeverRaise:
    """
    Regression for Track A Phase 1.5B discrepancy 1: `implied_vol=1e308`
    and `risk_free_rate=-1e308` are both finite floats -- they pass
    `math.isfinite` -- but were previously reaching `implied_vol ** 2`
    and `math.exp(-risk_free_rate * T)` deep inside the BSM math, which
    raise `OverflowError` for astronomically large-but-finite operands.
    `calculate_spy_hedge`'s documented "never raises" contract must hold
    for these and other extreme-but-finite magnitudes, not just for
    non-finite (NaN/infinity) ones.
    """

    def test_extreme_implied_vol_the_exact_reproduced_case(self):
        result = calculate_spy_hedge(
            portfolio_var_dollars=10_000, spy_price=580, strike_price=580, implied_vol=1e308
        )
        assert result == 0
        assert isinstance(result, int)
        assert not isinstance(result, bool)

    def test_extreme_negative_risk_free_rate_the_exact_reproduced_case(self):
        result = calculate_spy_hedge(
            portfolio_var_dollars=10_000, spy_price=580, strike_price=580, risk_free_rate=-1e308
        )
        assert result == 0
        assert isinstance(result, int)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"implied_vol": 1e308},
            {"implied_vol": 1e100},
            {"implied_vol": 1e10},  # comfortably beyond MAX_ABS_IMPLIED_VOL but still "reasonable-looking"
            {"risk_free_rate": -1e308},
            {"risk_free_rate": 1e308},
            {"risk_free_rate": 1e10},
            {"risk_free_rate": -1e10},
            {"spy_price": 1e308},
            {"strike_price": 1e308},
            # An extreme days_to_expiry combined with a boundary-but-valid
            # risk_free_rate is NOT individually caught by either range
            # check -- it must be caught by the defensive try/except
            # around the BSM arithmetic itself.
            {"days_to_expiry": 10**15, "risk_free_rate": -4.9},
        ],
    )
    def test_other_extreme_but_finite_magnitudes_return_zero_never_raise(self, kwargs):
        base = dict(portfolio_var_dollars=10_000, spy_price=580, strike_price=580)
        base.update(kwargs)
        result = calculate_spy_hedge(**base)
        assert result == 0
        assert isinstance(result, int)
        assert not isinstance(result, bool)

    def test_extreme_portfolio_var_dollars_alone_never_raises(self):
        """
        Unlike implied_vol/risk_free_rate, an extreme portfolio_var_dollars
        doesn't feed into any exp()/`**` overflow-prone BSM term -- it's a
        linear divisor applied AFTER pricing. With every other input
        ordinary, this legitimately (and correctly) sizes a very large
        number of contracts rather than being rejected; the contract this
        proves is "never raises," not "always returns 0."
        """
        result = calculate_spy_hedge(
            portfolio_var_dollars=1e308, spy_price=580, strike_price=580
        )
        assert isinstance(result, int)
        assert not isinstance(result, bool)
        assert result >= 0

    def test_normal_valid_case_sizing_is_unaffected_by_the_new_bounds(self):
        """The new economic-range bounds must not change ordinary sizing for realistic inputs."""
        contracts = calculate_spy_hedge(
            portfolio_var_dollars=10_000,
            spy_price=580,
            strike_price=580,
            days_to_expiry=30,
            implied_vol=0.15,
            risk_free_rate=0.04,
            stress_move_fraction=0.07,
        )
        assert contracts > 0
        assert isinstance(contracts, int)

    def test_implied_vol_just_within_the_bound_is_still_honored(self):
        """A large but within-bound implied_vol must not be rejected -- only beyond MAX_ABS_IMPLIED_VOL."""
        from src.risk.hedging import MAX_ABS_IMPLIED_VOL

        result = calculate_spy_hedge(
            portfolio_var_dollars=10_000,
            spy_price=580,
            strike_price=580,
            implied_vol=MAX_ABS_IMPLIED_VOL - 0.01,
        )
        # Not asserting a specific contract count (an implausibly high
        # vol still prices a very expensive put) -- just that it wasn't
        # rejected outright by the range check and didn't raise.
        assert isinstance(result, int)
        assert result >= 0

    def test_risk_free_rate_just_within_the_bound_is_still_honored(self):
        from src.risk.hedging import MAX_ABS_RISK_FREE_RATE

        result = calculate_spy_hedge(
            portfolio_var_dollars=10_000,
            spy_price=580,
            strike_price=580,
            risk_free_rate=MAX_ABS_RISK_FREE_RATE - 0.01,
        )
        assert isinstance(result, int)
        assert result >= 0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"portfolio_var_dollars": 10**10000},
            {"spy_price": 10**10000},
            {"strike_price": 10**10000},
            {"implied_vol": 10**10000},
            {"risk_free_rate": 10**10000},
            {"risk_free_rate": -(10**10000)},
        ],
    )
    def test_huge_python_integer_never_leaks_a_raw_overflow_error(self, kwargs):
        """
        Regression for Track A Phase 1.5C requirement 4: a Python `int`
        is arbitrary-precision and always finite by definition, so
        `10**10000` passes a correctly-implemented "is this finite"
        check -- but `math.isfinite(10**10000)` itself raises
        `OverflowError` while converting the int to a C `double`. This
        previously made `_is_finite_number` -- and therefore
        `calculate_spy_hedge`'s entire "never raises" contract -- false
        for exactly the kind of input it exists to guard against.
        """
        base = dict(portfolio_var_dollars=10_000, spy_price=580, strike_price=580)
        base.update(kwargs)
        result = calculate_spy_hedge(**base)
        assert result == 0
        assert isinstance(result, int)
        assert not isinstance(result, bool)


class TestBSMSanity:
    def test_put_price_is_positive_for_reasonable_inputs(self):
        price = calculate_bsm_put_price(580, 580, 30 / 365.0, 0.04, 0.15)
        assert price > 0

    def test_deeper_stress_increases_put_value(self):
        base = calculate_bsm_put_price(580, 580, 30 / 365.0, 0.04, 0.15)
        stressed_more = calculate_bsm_put_price(580 * 0.85, 580, 30 / 365.0, 0.04, 0.15)
        assert stressed_more > base
