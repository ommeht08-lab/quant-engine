"""
Group H: DCF correctness — assumption override precedence, NWC
calculation, terminal-value boundary, and validation of assumptions and
capital inputs. No network; all inputs are synthetic.
"""

import math

import pandas as pd
import pytest

from src.dcf_model import dcf
from src.dcf_model.dcf import (
    DCFAssumptions,
    _validate_capital_inputs,
    calculate_terminal_value,
    project_free_cash_flows,
    run_dcf_valuation,
)


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


class TestMissingData:
    def test_missing_revenue_raises_clear_error(self):
        financial_data = _synthetic_financial_data()
        financial_data["income_statement"] = pd.DataFrame()  # no revenue row at all
        with pytest.raises(ValueError, match="revenue"):
            run_dcf_valuation(financial_data, DCFAssumptions(revenue_growth_rate=0.05, operating_margin=0.15))
