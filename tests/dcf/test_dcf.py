"""
Group H: DCF correctness — assumption override precedence, NWC
calculation, terminal-value boundary, and validation of assumptions and
capital inputs. No network; all inputs are synthetic.
"""

import math
import warnings

import pandas as pd
import pytest

from src.dcf_model import dcf
from src.dcf_model.dcf import (
    DCFAssumptions,
    _validate_capital_inputs,
    calculate_enterprise_value,
    calculate_fcf_yield,
    calculate_intrinsic_value_per_share,
    calculate_terminal_value,
    calculate_wacc,
    discount_to_present_value,
    project_free_cash_flows,
    run_dcf_valuation,
)

# `True`/`False` (bool is an int subclass), a numeric-looking string, an
# arbitrary string, NaN, and +/-infinity -- the full adversarial set the
# task calls for, reused across every boundary test in this module.
ADVERSARIAL_NUMERIC_VALUES = [
    True,
    False,
    "0.05",
    "not-a-number",
    float("nan"),
    float("inf"),
    float("-inf"),
]


def _synthetic_financial_data(pretax_income=100.0, tax_provision=30.0) -> dict:
    income_stmt = pd.DataFrame(
        {pd.Timestamp("2023-12-31"): {"Total Revenue": 1000.0, "Pretax Income": pretax_income, "Tax Provision": tax_provision}}
    )
    balance_sheet = pd.DataFrame(
        {pd.Timestamp("2023-12-31"): {"Total Debt": 0.0, "Cash And Cash Equivalents": 0.0}}
    )
    return {
        "income_statement": income_stmt,
        "balance_sheet": balance_sheet,
        "cash_flow": None,
        "current_price": 50.0,
        "shares_outstanding": 100.0,
        "beta": 1.0,
    }


class TestTaxRateOverridePrecedence:
    def test_explicit_override_wins_over_derived_rate(self):
        """Derived rate from statements is 30/100 = 30%; an explicit 10% override must win."""
        financial_data = _synthetic_financial_data(pretax_income=100.0, tax_provision=30.0)
        assumptions = DCFAssumptions(tax_rate=0.10, revenue_growth_rate=0.05, operating_margin=0.15)

        result = run_dcf_valuation(financial_data, assumptions)

        ebit_y1 = result["fcf_projection"].loc[1, "ebit"]
        nopat_y1 = result["fcf_projection"].loc[1, "nopat"]
        implied_tax_rate = 1 - (nopat_y1 / ebit_y1)
        assert implied_tax_rate == pytest.approx(0.10, abs=1e-9)

    def test_derived_rate_used_when_no_override_given(self):
        financial_data = _synthetic_financial_data(pretax_income=100.0, tax_provision=25.0)
        assumptions = DCFAssumptions(revenue_growth_rate=0.05, operating_margin=0.15)  # tax_rate left None

        result = run_dcf_valuation(financial_data, assumptions)

        ebit_y1 = result["fcf_projection"].loc[1, "ebit"]
        nopat_y1 = result["fcf_projection"].loc[1, "nopat"]
        implied_tax_rate = 1 - (nopat_y1 / ebit_y1)
        assert implied_tax_rate == pytest.approx(0.25, abs=1e-9)

    def test_falls_back_to_default_when_neither_available(self):
        from src.dcf_model.dcf import DEFAULT_TAX_RATE

        financial_data = _synthetic_financial_data(pretax_income=0.0, tax_provision=0.0)  # can't derive a rate
        assumptions = DCFAssumptions(revenue_growth_rate=0.05, operating_margin=0.15)

        result = run_dcf_valuation(financial_data, assumptions)

        ebit_y1 = result["fcf_projection"].loc[1, "ebit"]
        nopat_y1 = result["fcf_projection"].loc[1, "nopat"]
        implied_tax_rate = 1 - (nopat_y1 / ebit_y1)
        assert implied_tax_rate == pytest.approx(DEFAULT_TAX_RATE, abs=1e-9)


class TestNWCCalculation:
    def test_change_in_nwc_uses_revenue_delta_not_gross_revenue(self):
        proj = project_free_cash_flows(
            base_revenue=1000.0, revenue_growth_rate=0.10, operating_margin=0.20,
            nwc_pct_revenue_change=0.5, years=3,
        )
        # year 1: revenue 1100, prior 1000 -> delta 100 -> nwc = 0.5*100 = 50
        # year 2: revenue 1210, prior 1100 -> delta 110 -> nwc = 0.5*110 = 55
        # year 3: revenue 1331, prior 1210 -> delta 121 -> nwc = 0.5*121 = 60.5
        assert proj.loc[1, "change_in_nwc"] == pytest.approx(50.0)
        assert proj.loc[2, "change_in_nwc"] == pytest.approx(55.0)
        assert proj.loc[3, "change_in_nwc"] == pytest.approx(60.5)

    def test_change_in_nwc_is_not_flat_percent_of_gross_revenue(self):
        """The old (buggy) formula would give year 1 == year 2 * (100/110) — a flat % of revenue.
        The fixed formula gives NWC proportional to revenue GROWTH, so it scales differently."""
        proj = project_free_cash_flows(
            base_revenue=1000.0, revenue_growth_rate=0.10, operating_margin=0.20,
            nwc_pct_revenue_change=0.5, years=2,
        )
        wrong_year2_nwc_if_gross_revenue = 0.5 * proj.loc[2, "revenue"]  # what the bug would have produced
        assert proj.loc[2, "change_in_nwc"] != pytest.approx(wrong_year2_nwc_if_gross_revenue)


class TestTerminalValueBoundary:
    def test_wacc_equal_to_growth_raises(self):
        with pytest.raises(ValueError):
            calculate_terminal_value(final_year_fcf=100.0, wacc=0.05, terminal_growth_rate=0.05)

    def test_wacc_below_growth_raises(self):
        with pytest.raises(ValueError):
            calculate_terminal_value(final_year_fcf=100.0, wacc=0.03, terminal_growth_rate=0.05)

    def test_wacc_slightly_above_growth_succeeds(self):
        value = calculate_terminal_value(final_year_fcf=100.0, wacc=0.0501, terminal_growth_rate=0.05)
        assert math.isfinite(value)
        assert value > 0


class TestAssumptionValidation:
    def test_projection_years_must_be_positive(self):
        with pytest.raises(ValueError):
            DCFAssumptions(projection_years=0)

    def test_negative_projection_years_rejected(self):
        with pytest.raises(ValueError):
            DCFAssumptions(projection_years=-3)

    def test_tax_rate_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            DCFAssumptions(tax_rate=1.5)
        with pytest.raises(ValueError):
            DCFAssumptions(tax_rate=-0.1)

    def test_tax_rate_boundary_values(self):
        DCFAssumptions(tax_rate=0.0)  # allowed
        with pytest.raises(ValueError):
            DCFAssumptions(tax_rate=1.0)  # exclusive upper bound

    def test_non_finite_growth_rate_rejected(self):
        with pytest.raises(ValueError):
            DCFAssumptions(revenue_growth_rate=float("inf"))
        with pytest.raises(ValueError):
            DCFAssumptions(terminal_growth_rate=float("nan"))

    def test_valid_assumptions_construct_cleanly(self):
        DCFAssumptions(revenue_growth_rate=0.08, operating_margin=0.2, tax_rate=0.21, projection_years=5)

    def test_none_growth_and_margin_are_never_bounds_checked(self):
        """None means 'derive from historicals' — must always construct cleanly regardless of bounds."""
        DCFAssumptions(revenue_growth_rate=None, operating_margin=None)


class TestExplicitAssumptionEconomicBounds:
    """
    Explicitly-supplied revenue growth / operating margin / terminal
    growth must be rejected outside a documented, economically sane
    range (MIN/MAX_EXPLICIT_* in src/dcf_model/dcf.py) — matching the
    dashboard's own slider ranges — rather than silently accepted and
    projected into an enormous, meaningless valuation. `None` (derive
    from historicals) is exempt; only explicit overrides are bounded.
    """

    # -- revenue_growth_rate --------------------------------------------

    def test_revenue_growth_rate_at_lower_bound_is_allowed(self):
        DCFAssumptions(revenue_growth_rate=dcf.MIN_EXPLICIT_REVENUE_GROWTH_RATE)

    def test_revenue_growth_rate_at_upper_bound_is_allowed(self):
        DCFAssumptions(revenue_growth_rate=dcf.MAX_EXPLICIT_REVENUE_GROWTH_RATE)

    def test_revenue_growth_rate_inside_bounds_is_allowed(self):
        DCFAssumptions(revenue_growth_rate=0.08)  # the dashboard's own default

    def test_revenue_growth_rate_below_lower_bound_is_rejected(self):
        with pytest.raises(ValueError, match="revenue_growth_rate"):
            DCFAssumptions(revenue_growth_rate=dcf.MIN_EXPLICIT_REVENUE_GROWTH_RATE - 0.01)

    def test_revenue_growth_rate_above_upper_bound_is_rejected(self):
        with pytest.raises(ValueError, match="revenue_growth_rate"):
            DCFAssumptions(revenue_growth_rate=dcf.MAX_EXPLICIT_REVENUE_GROWTH_RATE + 0.01)

    def test_absurd_crafted_revenue_growth_rate_is_rejected(self):
        """The exact scenario this guards against: a crafted 5000% growth 'assumption'."""
        with pytest.raises(ValueError):
            DCFAssumptions(revenue_growth_rate=50.0)

    def test_negative_revenue_growth_within_bounds_is_allowed(self):
        """Preserve support for a legitimate shrinking-company scenario."""
        DCFAssumptions(revenue_growth_rate=-0.05)

    # -- operating_margin -------------------------------------------------

    def test_operating_margin_at_lower_bound_is_allowed(self):
        DCFAssumptions(operating_margin=dcf.MIN_EXPLICIT_OPERATING_MARGIN)

    def test_operating_margin_at_upper_bound_is_allowed(self):
        DCFAssumptions(operating_margin=dcf.MAX_EXPLICIT_OPERATING_MARGIN)

    def test_operating_margin_below_lower_bound_is_rejected(self):
        with pytest.raises(ValueError, match="operating_margin"):
            DCFAssumptions(operating_margin=dcf.MIN_EXPLICIT_OPERATING_MARGIN - 0.01)

    def test_operating_margin_above_upper_bound_is_rejected(self):
        with pytest.raises(ValueError, match="operating_margin"):
            DCFAssumptions(operating_margin=dcf.MAX_EXPLICIT_OPERATING_MARGIN + 0.01)

    # -- terminal_growth_rate ----------------------------------------------

    def test_terminal_growth_rate_at_lower_bound_is_allowed(self):
        DCFAssumptions(terminal_growth_rate=dcf.MIN_EXPLICIT_TERMINAL_GROWTH_RATE)

    def test_terminal_growth_rate_at_upper_bound_is_allowed(self):
        DCFAssumptions(terminal_growth_rate=dcf.MAX_EXPLICIT_TERMINAL_GROWTH_RATE)

    def test_terminal_growth_rate_below_lower_bound_is_rejected(self):
        with pytest.raises(ValueError, match="terminal_growth_rate"):
            DCFAssumptions(terminal_growth_rate=dcf.MIN_EXPLICIT_TERMINAL_GROWTH_RATE - 0.01)

    def test_terminal_growth_rate_above_upper_bound_is_rejected(self):
        with pytest.raises(ValueError, match="terminal_growth_rate"):
            DCFAssumptions(terminal_growth_rate=dcf.MAX_EXPLICIT_TERMINAL_GROWTH_RATE + 0.01)

    def test_default_terminal_growth_rate_is_within_bounds(self):
        """The dataclass default (0.025) must itself satisfy its own bound."""
        DCFAssumptions()


class TestCapitalInputValidation:
    def test_non_finite_price_raises(self):
        with pytest.raises(ValueError):
            _validate_capital_inputs({"current_price": float("inf")})

    def test_negative_shares_outstanding_raises(self):
        with pytest.raises(ValueError):
            _validate_capital_inputs({"shares_outstanding": -100})

    def test_negative_total_debt_raises(self):
        with pytest.raises(ValueError):
            _validate_capital_inputs({"total_debt": -1})

    def test_none_values_are_left_alone(self):
        # None means "unavailable," a separate, already-handled case —
        # not the same as "present but invalid."
        _validate_capital_inputs({"current_price": None, "shares_outstanding": None})

    def test_run_dcf_valuation_rejects_non_finite_price(self):
        financial_data = _synthetic_financial_data()
        financial_data["current_price"] = float("nan")
        with pytest.raises(ValueError):
            run_dcf_valuation(financial_data, DCFAssumptions(revenue_growth_rate=0.05, operating_margin=0.15))

    @pytest.mark.parametrize("field_name", ["current_price", "shares_outstanding", "total_debt", "cash_and_equivalents"])
    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_present_malformed_field_raises_clean_value_error(self, field_name, bad_value):
        """
        Regression for Track A Phase 1.5C requirement 3: bool, a
        numeric-looking string, an arbitrary string, NaN, and
        +/-infinity must all raise a clean ValueError -- never a raw
        TypeError (previously leaked for strings by a bare
        `math.isfinite` call) or a silently-accepted bool.
        """
        with pytest.raises(ValueError, match=field_name):
            _validate_capital_inputs({field_name: bad_value})

    def test_huge_integer_never_leaks_a_raw_overflow_error(self):
        """
        A Python int is arbitrary-precision and always finite by
        definition -- `10**10000` must not itself be rejected as
        "non-finite" by a raw `math.isfinite` conversion overflow. It is
        accepted here (economically meaningless, but not this helper's
        job to bound) -- see `run_dcf_valuation`'s own downstream
        arithmetic-overflow handling for what happens if it's actually used.
        """
        _validate_capital_inputs({"current_price": 10**10000})

    def test_negative_huge_integer_still_raises_for_negativity_not_overflow(self):
        with pytest.raises(ValueError, match="must not be negative"):
            _validate_capital_inputs({"total_debt": -(10**10000)})


class TestMissingData:
    def test_missing_revenue_raises_clear_error(self):
        financial_data = _synthetic_financial_data()
        financial_data["income_statement"] = pd.DataFrame()  # no revenue row at all
        with pytest.raises(ValueError, match="revenue"):
            run_dcf_valuation(financial_data, DCFAssumptions(revenue_growth_rate=0.05, operating_margin=0.15))


class TestNumericBoundaryHardening:
    """
    Regression for Finding 4: `project_free_cash_flows` must reject
    non-finite/invalid inputs up front rather than silently producing a
    DataFrame full of inf/NaN, and `years`/`DCFAssumptions.projection_years`
    must be genuine positive whole numbers -- a fractional value must
    raise a clean `ValueError`, never leak an internal `range()` `TypeError`.
    """

    def test_infinite_base_revenue_is_rejected_not_silently_propagated(self):
        """The exact reproduced defect: base_revenue=inf used to produce a DataFrame full of inf/NaN."""
        with pytest.raises(ValueError, match="base_revenue"):
            project_free_cash_flows(base_revenue=float("inf"), revenue_growth_rate=0.05, operating_margin=0.15)

    def test_nan_base_revenue_is_rejected(self):
        with pytest.raises(ValueError, match="base_revenue"):
            project_free_cash_flows(base_revenue=float("nan"), revenue_growth_rate=0.05, operating_margin=0.15)

    def test_fractional_years_raises_value_error_not_type_error(self):
        """The exact reproduced defect: years=2.5 used to leak a range() TypeError."""
        with pytest.raises(ValueError, match="years"):
            project_free_cash_flows(base_revenue=1000.0, revenue_growth_rate=0.05, operating_margin=0.15, years=2.5)

    def test_boolean_years_is_rejected(self):
        with pytest.raises(ValueError, match="years"):
            project_free_cash_flows(base_revenue=1000.0, revenue_growth_rate=0.05, operating_margin=0.15, years=True)

    def test_non_finite_years_is_rejected(self):
        with pytest.raises(ValueError, match="years"):
            project_free_cash_flows(
                base_revenue=1000.0, revenue_growth_rate=0.05, operating_margin=0.15, years=float("nan")
            )

    def test_integral_float_years_is_still_honored(self):
        """`5.0` (a whole-number float) is a legitimate value, not a corrupted one -- must not be rejected."""
        result = project_free_cash_flows(
            base_revenue=1000.0, revenue_growth_rate=0.05, operating_margin=0.15, years=5.0
        )
        assert len(result) == 5

    @pytest.mark.parametrize(
        "field_name",
        ["revenue_growth_rate", "operating_margin", "tax_rate", "da_pct_revenue", "capex_pct_revenue", "nwc_pct_revenue_change"],
    )
    def test_non_finite_rate_field_is_rejected(self, field_name):
        kwargs = dict(base_revenue=1000.0, revenue_growth_rate=0.05, operating_margin=0.15)
        kwargs[field_name] = float("nan")
        with pytest.raises(ValueError, match=field_name):
            project_free_cash_flows(**kwargs)

    def test_dcf_assumptions_rejects_fractional_projection_years(self):
        with pytest.raises(ValueError, match="projection_years"):
            DCFAssumptions(projection_years=2.5)

    def test_dcf_assumptions_rejects_boolean_projection_years(self):
        with pytest.raises(ValueError, match="projection_years"):
            DCFAssumptions(projection_years=True)

    def test_dcf_assumptions_accepts_integral_float_projection_years(self):
        assumptions = DCFAssumptions(projection_years=3.0)
        assert assumptions.projection_years == 3
        assert isinstance(assumptions.projection_years, int)

    def test_finite_base_revenue_that_overflows_during_compounding_raises_cleanly(self):
        """
        A single finite base_revenue can still overflow float range partway
        through multi-year compounding (revenue *= (1 + growth) each year)
        -- must raise a clean ValueError the moment a projected value
        becomes non-finite, never return a DataFrame containing `inf`.
        """
        with pytest.raises(ValueError):
            project_free_cash_flows(base_revenue=1e308, revenue_growth_rate=0.40, operating_margin=0.15, years=5)

    def test_huge_integer_base_revenue_never_leaks_a_raw_overflow_error(self):
        """
        A Python int is arbitrary-precision and always finite by
        definition -- `10**10000` passes the input-level finiteness check
        -- but multiplying it by `(1 + revenue_growth_rate)` (a float)
        overflows the float side of that operation. Must raise this
        function's own clean ValueError, never a raw OverflowError.
        """
        with pytest.raises(ValueError):
            project_free_cash_flows(base_revenue=10**10000, revenue_growth_rate=0.05, operating_margin=0.15)


# ----------------------------------------------------------------------------
# Track A Phase 1.5B discrepancy 2: booleans, numeric-looking strings,
# arbitrary strings, None-where-prohibited, NaN, and +/-infinity must be
# rejected with a clean, documented ValueError at every public DCF/WACC
# numeric boundary -- never a raw TypeError leaked from a bare comparison
# or from `math.isfinite` on a non-numeric type, and never a `bool`
# silently accepted because Python treats it as an `int` subclass.
# ----------------------------------------------------------------------------


class TestBooleanAndNonnumericRejectedAtProjectFCF:
    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_base_revenue_rejects_adversarial_values(self, bad_value):
        with pytest.raises(ValueError, match="base_revenue"):
            project_free_cash_flows(base_revenue=bad_value, revenue_growth_rate=0.05, operating_margin=0.15)

    def test_base_revenue_none_is_rejected(self):
        with pytest.raises(ValueError, match="base_revenue"):
            project_free_cash_flows(base_revenue=None, revenue_growth_rate=0.05, operating_margin=0.15)

    def test_base_revenue_valid_whole_number_float_is_still_accepted(self):
        result = project_free_cash_flows(base_revenue=1000.0, revenue_growth_rate=0.05, operating_margin=0.15)
        assert len(result) == 5

    def test_base_revenue_normal_valid_case_no_regression(self):
        result = project_free_cash_flows(base_revenue=1_000_000.0, revenue_growth_rate=0.08, operating_margin=0.20)
        assert result.loc[1, "revenue"] == pytest.approx(1_000_000.0 * 1.08)

    @pytest.mark.parametrize(
        "field_name",
        [
            "revenue_growth_rate",
            "operating_margin",
            "tax_rate",
            "da_pct_revenue",
            "capex_pct_revenue",
            "nwc_pct_revenue_change",
        ],
    )
    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_rate_field_rejects_adversarial_values(self, field_name, bad_value):
        kwargs = dict(base_revenue=1000.0, revenue_growth_rate=0.05, operating_margin=0.15)
        kwargs[field_name] = bad_value
        with pytest.raises(ValueError, match=field_name):
            project_free_cash_flows(**kwargs)

    @pytest.mark.parametrize("bad_value", ["5", "abc", True, float("nan"), float("inf")])
    def test_years_rejects_adversarial_values(self, bad_value):
        with pytest.raises(ValueError, match="years"):
            project_free_cash_flows(
                base_revenue=1000.0, revenue_growth_rate=0.05, operating_margin=0.15, years=bad_value
            )


class TestBooleanAndNonnumericRejectedAtDCFAssumptions:
    @pytest.mark.parametrize(
        "field_name",
        ["tax_rate", "risk_free_rate", "market_risk_premium", "da_pct_revenue", "capex_pct_revenue", "nwc_pct_revenue_change"],
    )
    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_field_rejects_adversarial_values(self, field_name, bad_value):
        with pytest.raises(ValueError, match=field_name):
            DCFAssumptions(**{field_name: bad_value})

    @pytest.mark.parametrize("field_name", ["revenue_growth_rate", "operating_margin", "terminal_growth_rate"])
    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_bounded_field_rejects_adversarial_values(self, field_name, bad_value):
        with pytest.raises(ValueError, match=field_name):
            DCFAssumptions(**{field_name: bad_value})

    @pytest.mark.parametrize(
        "field_name",
        [
            "terminal_growth_rate",
            "risk_free_rate",
            "market_risk_premium",
            "da_pct_revenue",
            "capex_pct_revenue",
            "nwc_pct_revenue_change",
        ],
    )
    def test_required_field_rejects_none(self, field_name):
        """These fields are never legitimately None, even though the dataclass itself doesn't enforce it."""
        with pytest.raises(ValueError, match=field_name):
            DCFAssumptions(**{field_name: None})

    @pytest.mark.parametrize("bad_value", ["5", "abc", float("nan"), float("inf")])
    def test_projection_years_rejects_nonnumeric_and_nonfinite(self, bad_value):
        with pytest.raises(ValueError, match="projection_years"):
            DCFAssumptions(projection_years=bad_value)

    def test_default_construction_still_succeeds(self):
        """Normal valid inputs (the dataclass's own defaults) must be entirely unaffected by this hardening."""
        DCFAssumptions()

    def test_valid_whole_number_float_projection_years_still_works(self):
        assumptions = DCFAssumptions(projection_years=7.0)
        assert assumptions.projection_years == 7

    def test_normal_explicit_construction_no_regression(self):
        assumptions = DCFAssumptions(
            revenue_growth_rate=0.08,
            operating_margin=0.20,
            terminal_growth_rate=0.025,
            tax_rate=0.21,
            risk_free_rate=0.04,
            market_risk_premium=0.055,
        )
        assert assumptions.revenue_growth_rate == 0.08


class TestCalculateWaccBoundaryHardening:
    def test_normal_valid_case_unaffected(self):
        wacc = calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=500.0, beta=1.2)
        assert dcf.MIN_DISCOUNT_RATE <= wacc <= dcf.MAX_DISCOUNT_RATE

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_current_price_rejects_adversarial_values(self, bad_value):
        with pytest.raises(ValueError, match="current_price"):
            calculate_wacc(current_price=bad_value, shares_outstanding=1000.0, total_debt=0.0)

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_shares_outstanding_rejects_adversarial_values(self, bad_value):
        with pytest.raises(ValueError, match="shares_outstanding"):
            calculate_wacc(current_price=100.0, shares_outstanding=bad_value, total_debt=0.0)

    def test_none_current_price_is_rejected(self):
        with pytest.raises(ValueError, match="current_price"):
            calculate_wacc(current_price=None, shares_outstanding=1000.0, total_debt=0.0)

    def test_none_shares_outstanding_is_rejected(self):
        with pytest.raises(ValueError, match="shares_outstanding"):
            calculate_wacc(current_price=100.0, shares_outstanding=None, total_debt=0.0)

    # -- risk_free_rate / market_risk_premium: no missing-data fallback exists
    # on this function, so these reject bool/nonnumeric/nonfinite AND None. --

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_risk_free_rate_rejects_adversarial_values(self, bad_value):
        with pytest.raises(ValueError, match="risk_free_rate"):
            calculate_wacc(
                current_price=100.0, shares_outstanding=1000.0, total_debt=0.0, risk_free_rate=bad_value
            )

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_market_risk_premium_rejects_adversarial_values(self, bad_value):
        with pytest.raises(ValueError, match="market_risk_premium"):
            calculate_wacc(
                current_price=100.0, shares_outstanding=1000.0, total_debt=0.0, market_risk_premium=bad_value
            )

    def test_none_risk_free_rate_is_rejected(self):
        with pytest.raises(ValueError, match="risk_free_rate"):
            calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=0.0, risk_free_rate=None)

    def test_none_market_risk_premium_is_rejected(self):
        with pytest.raises(ValueError, match="market_risk_premium"):
            calculate_wacc(
                current_price=100.0, shares_outstanding=1000.0, total_debt=0.0, market_risk_premium=None
            )

    # -- beta / cost_of_debt / tax_rate / total_debt: None still uses the
    # documented default fallback; a PRESENT malformed value now raises
    # instead of silently falling back (the missing-vs-malformed distinction
    # this whole pass exists to enforce). --

    def test_none_beta_still_falls_back_to_default(self):
        """The missing-data contract (None -> DEFAULT_BETA) must be entirely unaffected by this hardening."""
        wacc = calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=0.0, beta=None)
        assert dcf.MIN_DISCOUNT_RATE <= wacc <= dcf.MAX_DISCOUNT_RATE

    @pytest.mark.parametrize("bad_beta", ADVERSARIAL_NUMERIC_VALUES)
    def test_malformed_present_beta_raises(self, bad_beta):
        with pytest.raises(ValueError, match="beta"):
            calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=0.0, beta=bad_beta)

    def test_none_total_debt_still_falls_back_to_zero(self):
        wacc = calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=None, beta=1.0)
        assert dcf.MIN_DISCOUNT_RATE <= wacc <= dcf.MAX_DISCOUNT_RATE

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_malformed_present_total_debt_raises(self, bad_value):
        with pytest.raises(ValueError, match="total_debt"):
            calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=bad_value, beta=1.0)

    def test_false_total_debt_is_rejected_not_treated_as_zero(self):
        """False must be rejected the same way any other bool is -- not silently treated as a legitimate 0."""
        with pytest.raises(ValueError, match="total_debt"):
            calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=False, beta=1.0)

    def test_none_cost_of_debt_still_falls_back_to_default(self):
        wacc = calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=500.0, cost_of_debt=None)
        assert dcf.MIN_DISCOUNT_RATE <= wacc <= dcf.MAX_DISCOUNT_RATE

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_malformed_present_cost_of_debt_raises(self, bad_value):
        with pytest.raises(ValueError, match="cost_of_debt"):
            calculate_wacc(
                current_price=100.0, shares_outstanding=1000.0, total_debt=500.0, cost_of_debt=bad_value
            )

    def test_none_tax_rate_still_falls_back_to_default(self):
        wacc = calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=500.0, tax_rate=None)
        assert dcf.MIN_DISCOUNT_RATE <= wacc <= dcf.MAX_DISCOUNT_RATE

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_malformed_present_tax_rate_raises(self, bad_value):
        with pytest.raises(ValueError, match="tax_rate"):
            calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=500.0, tax_rate=bad_value)

    @pytest.mark.parametrize("bad_tax_rate", [-0.2, 1.5, 1.0])
    def test_out_of_range_but_genuinely_numeric_tax_rate_now_raises(self, bad_tax_rate):
        """
        Track A Phase 1.5D: a present, well-typed, FINITE tax_rate outside
        [0, 1) is economically invalid, not merely "missing" -- it must now
        be rejected with a clean ValueError instead of silently falling
        back to the 21% default (the Phase 1.5C behavior this supersedes).
        """
        with pytest.raises(ValueError, match="tax_rate"):
            calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=500.0, tax_rate=bad_tax_rate)

    # -- Sign/range invariants directly enforced by this function (Track A
    # Phase 1.5D) -- not just by the orchestration path's
    # _validate_capital_inputs pre-check. --

    @pytest.mark.parametrize("bad_price", [0.0, -1.0, -100.0])
    def test_non_positive_current_price_is_rejected(self, bad_price):
        with pytest.raises(ValueError, match="current_price"):
            calculate_wacc(current_price=bad_price, shares_outstanding=1000.0, total_debt=0.0)

    @pytest.mark.parametrize("bad_shares", [0.0, -1.0, -1000.0])
    def test_non_positive_shares_outstanding_is_rejected(self, bad_shares):
        with pytest.raises(ValueError, match="shares_outstanding"):
            calculate_wacc(current_price=100.0, shares_outstanding=bad_shares, total_debt=0.0)

    def test_negative_total_debt_is_rejected(self):
        with pytest.raises(ValueError, match="total_debt"):
            calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=-500.0)

    def test_negative_cost_of_debt_is_rejected(self):
        with pytest.raises(ValueError, match="cost_of_debt"):
            calculate_wacc(
                current_price=100.0, shares_outstanding=1000.0, total_debt=500.0, cost_of_debt=-0.05
            )

    def test_negative_beta_is_not_rejected_on_sign_alone(self):
        """A negative beta (e.g. a defensive/inverse-correlated stock) is economically legitimate."""
        wacc = calculate_wacc(current_price=100.0, shares_outstanding=1000.0, total_debt=0.0, beta=-0.5)
        assert dcf.MIN_DISCOUNT_RATE <= wacc <= dcf.MAX_DISCOUNT_RATE

    def test_negative_risk_free_rate_is_not_rejected_on_sign_alone(self):
        """A negative risk-free rate (e.g. certain sovereign yields) is economically legitimate."""
        wacc = calculate_wacc(
            current_price=100.0, shares_outstanding=1000.0, total_debt=0.0, risk_free_rate=-0.01
        )
        assert dcf.MIN_DISCOUNT_RATE <= wacc <= dcf.MAX_DISCOUNT_RATE

    # -- Arithmetic overflow: finite inputs whose combination would overflow. --

    def test_astronomically_large_finite_beta_raises_cleanly_not_overflowerror(self):
        """
        A huge but technically-finite float (passes _is_valid_finite_number)
        can still overflow `beta * market_risk_premium` in the CAPM leg --
        must surface as this function's own ValueError, never a raw
        OverflowError.
        """
        with pytest.raises(ValueError):
            calculate_wacc(
                current_price=100.0, shares_outstanding=1000.0, total_debt=0.0, beta=1e308,
                market_risk_premium=1e300,
            )

    def test_huge_integer_current_price_never_leaks_a_raw_exception(self):
        """
        A Python int is arbitrary-precision and always finite by
        definition -- `10**10000` passes the input-level finiteness check
        -- but multiplying it against a float (`shares_outstanding`)
        overflows the float side of that operation. Must raise this
        function's own clean ValueError, never a raw OverflowError.
        """
        with pytest.raises(ValueError):
            calculate_wacc(current_price=10**10000, shares_outstanding=1000.0, total_debt=0.0)


class TestCalculateTerminalValueBoundaryHardening:
    @pytest.mark.parametrize("field_name", ["final_year_fcf", "wacc", "terminal_growth_rate"])
    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_rejects_adversarial_values(self, field_name, bad_value):
        kwargs = dict(final_year_fcf=100.0, wacc=0.08, terminal_growth_rate=0.025)
        kwargs[field_name] = bad_value
        with pytest.raises(ValueError, match=field_name):
            calculate_terminal_value(**kwargs)

    def test_normal_valid_case_unaffected(self):
        value = calculate_terminal_value(final_year_fcf=100.0, wacc=0.08, terminal_growth_rate=0.025)
        assert value > 0

    def test_finite_inputs_that_overflow_the_perpetuity_formula_raise_cleanly(self):
        """
        wacc > terminal_growth_rate technically holds (0.000000001 > 0.0),
        but the resulting denominator is small enough, combined with a
        huge final_year_fcf, to blow the division up past float range --
        must raise this function's own clean ValueError, not silently
        return `inf` as if it were a legitimate terminal value.
        """
        with pytest.raises(ValueError):
            calculate_terminal_value(final_year_fcf=1e300, wacc=1e-9, terminal_growth_rate=0.0)


class TestDiscountToPresentValueBoundaryHardening:
    @staticmethod
    def _valid_projection():
        return project_free_cash_flows(base_revenue=1000.0, revenue_growth_rate=0.05, operating_margin=0.15)

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_wacc_rejects_adversarial_values(self, bad_value):
        with pytest.raises(ValueError, match="wacc"):
            discount_to_present_value(self._valid_projection(), terminal_value=1000.0, wacc=bad_value)

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_terminal_value_rejects_adversarial_values(self, bad_value):
        with pytest.raises(ValueError, match="terminal_value"):
            discount_to_present_value(self._valid_projection(), terminal_value=bad_value, wacc=0.08)

    def test_not_a_dataframe_raises_cleanly(self):
        with pytest.raises(ValueError, match="DataFrame"):
            discount_to_present_value([1, 2, 3], terminal_value=1000.0, wacc=0.08)

    def test_empty_dataframe_raises_cleanly(self):
        with pytest.raises(ValueError, match="DataFrame"):
            discount_to_present_value(pd.DataFrame(), terminal_value=1000.0, wacc=0.08)

    def test_missing_fcf_column_raises_cleanly(self):
        malformed = pd.DataFrame({"revenue": [100.0, 110.0]}, index=[1, 2])
        with pytest.raises(ValueError, match="fcf"):
            discount_to_present_value(malformed, terminal_value=1000.0, wacc=0.08)

    @pytest.mark.parametrize("bad_fcf_value", [float("nan"), float("inf"), float("-inf"), True, "abc"])
    def test_nonfinite_or_malformed_fcf_value_raises_cleanly(self, bad_fcf_value):
        malformed = pd.DataFrame({"fcf": [100.0, bad_fcf_value]}, index=[1, 2])
        with pytest.raises(ValueError, match="fcf"):
            discount_to_present_value(malformed, terminal_value=1000.0, wacc=0.08)

    @pytest.mark.parametrize("bad_year", [0, -1, float("nan"), float("inf")])
    def test_invalid_projection_year_index_raises_cleanly(self, bad_year):
        malformed = pd.DataFrame({"fcf": [100.0, 110.0]}, index=[1, bad_year])
        with pytest.raises(ValueError):
            discount_to_present_value(malformed, terminal_value=1000.0, wacc=0.08)

    def test_finite_inputs_that_overflow_the_discounting_arithmetic_raise_cleanly(self):
        """
        An astronomically large terminal_value combined with a WACC near
        -1 (so `(1 + wacc)` is a tiny base raised to a positive power,
        shrinking the denominator toward zero) overflows the discounting
        division past float range -- must raise, not silently return `inf`.
        """
        projection = self._valid_projection()
        with pytest.raises(ValueError):
            discount_to_present_value(projection, terminal_value=1e308, wacc=-0.9999)

    def test_overflow_raises_value_error_without_an_unhandled_runtime_warning(self):
        """
        Track A Phase 1.5D: this exact deliberate-overflow scenario
        previously raised the correct ValueError but also leaked a numpy
        RuntimeWarning ("overflow encountered in scalar divide") past this
        function's documented failure contract. `np.errstate` around the
        vectorized arithmetic must convert that floating-point condition
        into the same ValueError, with no RuntimeWarning escaping at all.
        """
        projection = self._valid_projection()
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            with pytest.raises(ValueError):
                discount_to_present_value(projection, terminal_value=1e308, wacc=-0.9999)

    def test_normal_valid_case_unaffected(self):
        result = discount_to_present_value(self._valid_projection(), terminal_value=5000.0, wacc=0.08)
        assert result["enterprise_value"] > 0


class TestCalculateIntrinsicValuePerShareBoundaryHardening:
    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES)
    def test_enterprise_value_rejects_adversarial_values(self, bad_value):
        with pytest.raises(ValueError, match="enterprise_value"):
            calculate_intrinsic_value_per_share(
                bad_value, total_debt=0.0, cash_and_equivalents=0.0, shares_outstanding=100.0
            )

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES + [None])
    def test_shares_outstanding_rejects_adversarial_values(self, bad_value):
        with pytest.raises(ValueError, match="shares_outstanding"):
            calculate_intrinsic_value_per_share(
                1000.0, total_debt=0.0, cash_and_equivalents=0.0, shares_outstanding=bad_value
            )

    def test_none_total_debt_still_treated_as_zero(self):
        """The missing-data contract (None -> 0) must be entirely unaffected by this hardening."""
        result = calculate_intrinsic_value_per_share(
            1000.0, total_debt=None, cash_and_equivalents=0.0, shares_outstanding=100.0
        )
        assert result == pytest.approx(10.0)

    def test_none_cash_still_treated_as_zero(self):
        result = calculate_intrinsic_value_per_share(
            1000.0, total_debt=0.0, cash_and_equivalents=None, shares_outstanding=100.0
        )
        assert result == pytest.approx(10.0)

    @pytest.mark.parametrize("bad_value", [True, "abc", float("nan"), float("inf")])
    def test_malformed_present_total_debt_raises(self, bad_value):
        """A PRESENT malformed value is a data-integrity problem, not an absence -- it must raise, not silently become 0."""
        with pytest.raises(ValueError, match="total_debt"):
            calculate_intrinsic_value_per_share(
                1000.0, total_debt=bad_value, cash_and_equivalents=0.0, shares_outstanding=100.0
            )

    @pytest.mark.parametrize("bad_value", [True, "abc", float("nan"), float("inf")])
    def test_malformed_present_cash_raises(self, bad_value):
        with pytest.raises(ValueError, match="cash_and_equivalents"):
            calculate_intrinsic_value_per_share(
                1000.0, total_debt=0.0, cash_and_equivalents=bad_value, shares_outstanding=100.0
            )

    # -- Sign invariants (Track A Phase 1.5D): a present debt/cash value must
    # be non-negative -- a negative balance is economically nonsensical, not
    # a type malformation, but is rejected the same way. --

    def test_negative_present_total_debt_raises(self):
        with pytest.raises(ValueError, match="total_debt"):
            calculate_intrinsic_value_per_share(
                1000.0, total_debt=-1.0, cash_and_equivalents=0.0, shares_outstanding=100.0
            )

    def test_negative_present_cash_raises(self):
        with pytest.raises(ValueError, match="cash_and_equivalents"):
            calculate_intrinsic_value_per_share(
                1000.0, total_debt=0.0, cash_and_equivalents=-1.0, shares_outstanding=100.0
            )

    def test_astronomically_large_finite_enterprise_value_raises_cleanly(self):
        """
        A huge but technically-finite float can overflow the equity-value
        arithmetic -- must raise ValueError, not leak OverflowError. Uses a
        huge (non-negative) cash balance, not a negative debt balance, so
        this tests arithmetic overflow specifically, not the sign check
        above.
        """
        with pytest.raises(ValueError):
            calculate_intrinsic_value_per_share(
                1e308, total_debt=0.0, cash_and_equivalents=1e308, shares_outstanding=1e-300
            )

    def test_normal_valid_case_unaffected(self):
        result = calculate_intrinsic_value_per_share(
            1000.0, total_debt=200.0, cash_and_equivalents=100.0, shares_outstanding=100.0
        )
        assert result == pytest.approx(9.0)


class TestCalculateEnterpriseValueBoundaryHardening:
    """calculate_enterprise_value keeps its established 'return None for unavailable data' contract."""

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES + [None])
    def test_current_price_degrades_to_none(self, bad_value):
        assert calculate_enterprise_value(bad_value, 100.0, 0.0, 0.0) is None

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES + [None])
    def test_shares_outstanding_degrades_to_none(self, bad_value):
        assert calculate_enterprise_value(100.0, bad_value, 0.0, 0.0) is None

    def test_none_total_debt_still_treated_as_zero(self):
        """The missing-data contract (None -> 0) must be entirely unaffected by this hardening."""
        result = calculate_enterprise_value(100.0, 10.0, None, 0.0)
        assert result == pytest.approx(1000.0)

    def test_none_cash_still_treated_as_zero(self):
        result = calculate_enterprise_value(100.0, 10.0, 0.0, None)
        assert result == pytest.approx(1000.0)

    @pytest.mark.parametrize("bad_value", [True, "abc", float("nan"), float("inf")])
    def test_malformed_present_total_debt_degrades_to_none_not_zero(self, bad_value):
        """A PRESENT malformed value must degrade to None (this function's own graceful-failure contract), never silently become 0."""
        result = calculate_enterprise_value(100.0, 10.0, bad_value, 0.0)
        assert result is None

    @pytest.mark.parametrize("bad_value", [True, "abc", float("nan"), float("inf")])
    def test_malformed_present_cash_degrades_to_none_not_zero(self, bad_value):
        result = calculate_enterprise_value(100.0, 10.0, 0.0, bad_value)
        assert result is None

    def test_astronomically_large_finite_inputs_degrade_to_none_not_raise(self):
        """Finite inputs whose product/sum would overflow float range must degrade to None, never leak OverflowError."""
        result = calculate_enterprise_value(1e308, 1e308, 0.0, 0.0)
        assert result is None

    def test_huge_integer_current_price_never_leaks_a_raw_exception(self):
        """A Python int is always finite by definition and passes the input check -- the resulting overflow must degrade to None."""
        result = calculate_enterprise_value(10**10000, 1000.0, 0.0, 0.0)
        assert result is None

    # -- Sign invariants (Track A Phase 1.5D). --

    @pytest.mark.parametrize("bad_price", [0.0, -1.0])
    def test_non_positive_current_price_degrades_to_none(self, bad_price):
        assert calculate_enterprise_value(bad_price, 100.0, 0.0, 0.0) is None

    @pytest.mark.parametrize("bad_shares", [0.0, -1.0])
    def test_non_positive_shares_outstanding_degrades_to_none(self, bad_shares):
        assert calculate_enterprise_value(100.0, bad_shares, 0.0, 0.0) is None

    def test_negative_present_total_debt_degrades_to_none(self):
        assert calculate_enterprise_value(100.0, 10.0, -1.0, 0.0) is None

    def test_negative_present_cash_degrades_to_none(self):
        assert calculate_enterprise_value(100.0, 10.0, 0.0, -1.0) is None

    def test_legitimate_negative_final_enterprise_value_is_allowed(self):
        """
        A cash-rich company (cash > market cap + debt) can legitimately have
        a negative market Enterprise Value -- only malformed/nonfinite
        arithmetic is rejected, never a merely-negative-but-valid result.
        """
        result = calculate_enterprise_value(10.0, 10.0, 50.0, 1000.0)
        assert result == pytest.approx(10 * 10 + 50 - 1000)
        assert result < 0

    def test_normal_valid_case_unaffected(self):
        result = calculate_enterprise_value(100.0, 10.0, 500.0, 200.0)
        assert result == pytest.approx(100 * 10 + 500 - 200)


class TestCalculateFcfYieldBoundaryHardening:
    """calculate_fcf_yield keeps its established 'return None for unavailable data' contract."""

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES + [None])
    def test_operating_cash_flow_degrades_to_none(self, bad_value):
        assert calculate_fcf_yield(bad_value, -10.0, 1000.0) is None

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES + [None])
    def test_capital_expenditures_degrades_to_none(self, bad_value):
        assert calculate_fcf_yield(100.0, bad_value, 1000.0) is None

    @pytest.mark.parametrize("bad_value", ADVERSARIAL_NUMERIC_VALUES + [None])
    def test_enterprise_value_degrades_to_none(self, bad_value):
        assert calculate_fcf_yield(100.0, -10.0, bad_value) is None

    def test_astronomically_large_finite_inputs_degrade_to_none_not_raise(self):
        """Finite inputs whose sum would overflow float range must degrade to None, never leak OverflowError."""
        result = calculate_fcf_yield(1e308, 1e308, 1000.0)
        assert result is None

    def test_huge_integer_operating_cash_flow_never_leaks_a_raw_exception(self):
        """A Python int is always finite by definition and passes the input check -- resulting overflow must degrade to None."""
        result = calculate_fcf_yield(10**10000, -20.0, 1000.0)
        assert result is None

    def test_normal_valid_case_unaffected(self):
        result = calculate_fcf_yield(100.0, -20.0, 1000.0)
        assert result == pytest.approx(0.08)
