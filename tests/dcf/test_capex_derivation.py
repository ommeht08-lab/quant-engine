"""
Tests for `derive_historical_capex_pct_revenue` (the opt-in, company-
derived CapEx-as-%-of-revenue path for the live Yahoo-backed DCF) and its
wiring into `DCFAssumptions`/`run_dcf_valuation`. No network; all inputs
are synthetic.

Every fixture below is FROZEN: its expected ratio/status/periods was
computed by hand and written into this file BEFORE
`derive_historical_capex_pct_revenue` was run against it, mirroring this
repository's independent-validation discipline (freeze first, then
compare) at unit-test scale. The synthetic shapes (a trending
capital-intensive company, a company with both a standardized and an
"as reported" CapEx row that disagree) are inspired by real shapes
observed in live MSFT/CAT/INTC/VZ data while designing this feature
(see docs/model-specifications/dcf.md's "Historical CapEx derivation"
section and docs/assumptions-register.md A-031) -- they are not fetched
from, or compared against, any live source; they are self-contained,
hand-verifiable synthetic data.
"""

import math

import pandas as pd
import pytest

from src.dcf_model.dcf import (
    DEFAULT_CAPEX_PCT_REVENUE,
    MIN_HISTORICAL_CAPEX_PERIODS,
    DCFAssumptions,
    HistoricalCapexDerivation,
    derive_historical_capex_pct_revenue,
    run_dcf_valuation,
)


def _statements(revenue: dict, capex: dict, capex_row: str = "Capital Expenditure", extra_capex_row: dict = None):
    """Build synthetic (income_stmt, cash_flow) DataFrames from
    {ISO-date-string: value} dicts, matching yfinance's real shape: rows
    = line items, columns = pandas.Timestamp fiscal-period-end dates."""
    income_stmt = pd.DataFrame(
        {pd.Timestamp(date): {"Total Revenue": value} for date, value in revenue.items()}
    )
    cash_flow_data = {pd.Timestamp(date): {capex_row: value} for date, value in capex.items()}
    if extra_capex_row:
        row_name, values = extra_capex_row
        for date, value in values.items():
            cash_flow_data.setdefault(pd.Timestamp(date), {})[row_name] = value
    cash_flow = pd.DataFrame(cash_flow_data)
    return income_stmt, cash_flow


class TestExactRatioArithmeticAndPeriodAlignment:
    def test_simple_average_across_four_trending_periods(self):
        """FROZEN fixture: revenue 1000/1200/1500/2000, CapEx 10%/15%/20%/25%
        of revenue in the same four years (loosely modeled on MSFT's real,
        sharply-trending CapEx/revenue ratio observed while designing this
        feature). Expected ratio, hand-computed: (10+15+20+25)/4 = 17.5%
        exactly. A 5th, older column (2022-06-30) exists ONLY in cash_flow,
        with CapEx=NaN, and is absent from income_stmt entirely -- exactly
        the shape observed live (yfinance's oldest CapEx column is
        consistently unpopulated) -- and must be silently excluded by the
        date-intersection, not treated as malformed."""
        income_stmt, cash_flow = _statements(
            revenue={"2023-06-30": 1000.0, "2024-06-30": 1200.0, "2025-06-30": 1500.0, "2026-06-30": 2000.0},
            capex={
                "2022-06-30": float("nan"),
                "2023-06-30": -100.0,
                "2024-06-30": -180.0,
                "2025-06-30": -300.0,
                "2026-06-30": -500.0,
            },
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "derived"
        assert result.ratio == pytest.approx(0.175, abs=1e-12)
        assert result.source_periods == ("2026-06-30", "2025-06-30", "2024-06-30", "2023-06-30")
        assert result.excluded_periods == ()

    def test_periods_aligned_by_date_not_by_column_position(self):
        """FROZEN fixture: income_stmt and cash_flow have DIFFERENT column
        counts (3 vs 4) and the extra cash_flow column (2020) has no
        matching revenue at all. If alignment were positional rather than
        by exact date, this would silently pair the wrong year's revenue
        and CapEx. Expected: only the 3 genuinely shared dates are used,
        ratio = simple average of their 3 (identical, for clarity) ratios
        = 10% exactly."""
        income_stmt, cash_flow = _statements(
            revenue={"2023-01-01": 1000.0, "2024-01-01": 1000.0, "2025-01-01": 1000.0},
            capex={
                "2020-01-01": -999.0,  # no matching revenue column at all
                "2023-01-01": -100.0,
                "2024-01-01": -100.0,
                "2025-01-01": -100.0,
            },
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "derived"
        assert result.ratio == pytest.approx(0.10, abs=1e-12)
        assert result.source_periods == ("2025-01-01", "2024-01-01", "2023-01-01")
        assert "2020-01-01" not in result.source_periods
        assert "2020-01-01" not in result.excluded_periods  # absent, not malformed

    def test_asymmetric_ratios_distinguish_mean_from_median(self):
        """FROZEN fixture, deliberately NOT evenly spaced (unlike the
        trending 10/15/20/25% fixture above, whose mean and median happen
        to coincide): CapEx/revenue of 5%, 5%, 5%, 65%. Simple average
        (the documented, actually-implemented method) = 20% exactly.
        Median would be 5% -- a materially different, wrong number if the
        implementation ever silently changed to a median or another
        robust-statistic aggregation. This is the one fixture in this
        suite that can actually tell the two apart."""
        income_stmt, cash_flow = _statements(
            revenue={"2022-01-01": 1000.0, "2023-01-01": 1000.0, "2024-01-01": 1000.0, "2025-01-01": 1000.0},
            capex={"2022-01-01": -50.0, "2023-01-01": -50.0, "2024-01-01": -50.0, "2025-01-01": -650.0},
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "derived"
        assert result.ratio == pytest.approx(0.20, abs=1e-12), (
            f"Expected the documented simple-average ratio (20%); got {result.ratio!r}, "
            f"consistent with a median (5%) or some other aggregation instead."
        )


class TestRowLabelPreferenceAndSignConvention:
    def test_reported_row_preferred_over_standardized_row_when_both_present(self):
        """FROZEN fixture, modeled on a real, observed discrepancy: VZ's
        FY2025 yfinance cash_flow carries BOTH 'Capital Expenditure'
        (-$17,461M) and 'Capital Expenditure Reported' (-$17,011M, the one
        matching Verizon's own reported $17.0B). This fixture reproduces
        that shape with round numbers: 'Capital Expenditure' would give
        15% every year if wrongly used; 'Capital Expenditure Reported'
        (the preferred row) gives 10% every year. Expected ratio: 10.00%
        exactly -- proves the preference, not just that some ratio came out."""
        income_stmt, cash_flow = _statements(
            revenue={"2023-12-31": 1000.0, "2024-12-31": 1000.0, "2025-12-31": 1000.0},
            capex={"2023-12-31": -150.0, "2024-12-31": -150.0, "2025-12-31": -150.0},  # "Capital Expenditure"
            capex_row="Capital Expenditure",
            extra_capex_row=(
                "Capital Expenditure Reported",
                {"2023-12-31": -100.0, "2024-12-31": -100.0, "2025-12-31": -100.0},
            ),
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "derived"
        assert result.ratio == pytest.approx(0.10, abs=1e-12), (
            "Expected the 'Capital Expenditure Reported' row (10%); got a ratio "
            "consistent with the standardized 'Capital Expenditure' row (15%) instead."
        )
        assert result.capex_row == "Capital Expenditure Reported"

    def test_row_with_more_usable_coverage_wins_even_when_not_preferred(self):
        """FROZEN fixture: 'Capital Expenditure Reported' (the normally-
        preferred row) is populated for only ONE year; the standardized
        'Capital Expenditure' is populated for all FOUR. Row selection must
        be coverage-first -- picking the sparser preferred row would throw
        away three perfectly good years and report insufficient_history
        for no reason. Expected: 'Capital Expenditure' selected, ratio =
        10% exactly (4 periods at -100/1000), capex_row names it."""
        income_stmt, cash_flow = _statements(
            revenue={
                "2022-01-01": 1000.0, "2023-01-01": 1000.0,
                "2024-01-01": 1000.0, "2025-01-01": 1000.0,
            },
            capex={
                "2022-01-01": -100.0, "2023-01-01": -100.0,
                "2024-01-01": -100.0, "2025-01-01": -100.0,
            },  # 'Capital Expenditure': all 4 years populated
            capex_row="Capital Expenditure",
            extra_capex_row=(
                "Capital Expenditure Reported",
                # Only one year populated -- the other three are absent
                # (not even present as NaN, mirroring a row that simply
                # wasn't reported for those years).
                {"2025-01-01": -90.0},
            ),
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.capex_row == "Capital Expenditure", (
            f"Expected the fuller 'Capital Expenditure' row (4 usable years) to win over "
            f"the sparser preferred 'Capital Expenditure Reported' row (1 usable year); "
            f"got capex_row={result.capex_row!r}, status={result.status!r}"
        )
        assert result.status == "derived"
        assert result.ratio == pytest.approx(0.10, abs=1e-12)
        assert len(result.source_periods) == 4

    def test_tie_in_coverage_breaks_toward_preferred_row(self):
        """When both candidate rows have the SAME usable-period count, the
        documented priority order (Reported first) breaks the tie -- this
        is exactly `test_reported_row_preferred_over_standardized_row_when_both_present`
        above (3 vs 3), reasserted here explicitly via capex_row for clarity."""
        income_stmt, cash_flow = _statements(
            revenue={"2023-12-31": 1000.0, "2024-12-31": 1000.0, "2025-12-31": 1000.0},
            capex={"2023-12-31": -150.0, "2024-12-31": -150.0, "2025-12-31": -150.0},
            capex_row="Capital Expenditure",
            extra_capex_row=(
                "Capital Expenditure Reported",
                {"2023-12-31": -100.0, "2024-12-31": -100.0, "2025-12-31": -100.0},
            ),
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.capex_row == "Capital Expenditure Reported"

    def test_positive_capex_value_is_malformed_not_silently_flipped(self):
        """FROZEN fixture: every observed live CapEx value (MSFT, CAT, INTC,
        VZ, across every usable period checked) was <= 0 (an outflow). A
        positive value breaks that convention and must be excluded as
        malformed, not silently negated. 3 valid periods (-100 each) plus
        1 malformed positive period (+100) -> expected ratio from the 3
        valid periods only: 10% exactly; the malformed period must appear
        in excluded_periods, not source_periods."""
        income_stmt, cash_flow = _statements(
            revenue={"2023-01-01": 1000.0, "2024-01-01": 1000.0, "2025-01-01": 1000.0, "2026-01-01": 1000.0},
            capex={"2023-01-01": -100.0, "2024-01-01": -100.0, "2025-01-01": 100.0, "2026-01-01": -100.0},
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "derived"
        assert result.ratio == pytest.approx(0.10, abs=1e-12)
        assert result.excluded_periods == ("2025-01-01",)
        assert "2025-01-01" not in result.source_periods
        assert len(result.source_periods) == 3


class TestMissingVersusMalformedDistinction:
    def test_nan_period_silently_excluded_not_reported_as_malformed(self):
        """A period with NaN CapEx (genuinely absent, e.g. yfinance's oldest
        column) is excluded silently -- it must NOT appear in
        excluded_periods, which is reserved for present-but-wrong values."""
        income_stmt, cash_flow = _statements(
            revenue={"2022-01-01": 1000.0, "2023-01-01": 1000.0, "2024-01-01": 1000.0, "2025-01-01": 1000.0},
            capex={
                "2022-01-01": float("nan"),
                "2023-01-01": -100.0,
                "2024-01-01": -100.0,
                "2025-01-01": -100.0,
            },
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "derived"
        assert result.ratio == pytest.approx(0.10, abs=1e-12)
        assert result.excluded_periods == ()
        assert "2022-01-01" not in result.source_periods

    def test_nonpositive_revenue_period_is_malformed(self):
        """A period with CapEx present but revenue <= 0 is excluded as
        malformed (a ratio against zero/negative revenue is meaningless),
        distinct from the period being absent."""
        income_stmt, cash_flow = _statements(
            revenue={"2023-01-01": 0.0, "2024-01-01": 1000.0, "2025-01-01": 1000.0, "2026-01-01": 1000.0},
            capex={"2023-01-01": -50.0, "2024-01-01": -100.0, "2025-01-01": -100.0, "2026-01-01": -100.0},
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "derived"
        assert result.ratio == pytest.approx(0.10, abs=1e-12)
        assert result.excluded_periods == ("2023-01-01",)

    @pytest.mark.parametrize("bad_capex", [True, "not-a-number", float("inf"), float("-inf")])
    def test_present_nonfinite_or_nonnumeric_capex_is_malformed(self, bad_capex):
        income_stmt, cash_flow = _statements(
            revenue={"2023-01-01": 1000.0, "2024-01-01": 1000.0, "2025-01-01": 1000.0, "2026-01-01": 1000.0},
            capex={"2023-01-01": -100.0, "2024-01-01": -100.0, "2025-01-01": bad_capex, "2026-01-01": -100.0},
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "derived"
        assert result.ratio == pytest.approx(0.10, abs=1e-12)
        assert result.excluded_periods == ("2025-01-01",)


class TestInsufficientHistory:
    def test_exactly_at_minimum_succeeds(self):
        """MIN_HISTORICAL_CAPEX_PERIODS usable periods (the frozen default
        is 3) must be sufficient -- the boundary itself is inclusive."""
        assert MIN_HISTORICAL_CAPEX_PERIODS == 3, (
            "This test's fixture assumes the documented default of 3; "
            "update both together if the constant changes."
        )
        income_stmt, cash_flow = _statements(
            revenue={"2023-01-01": 1000.0, "2024-01-01": 1000.0, "2025-01-01": 1000.0},
            capex={"2023-01-01": -100.0, "2024-01-01": -100.0, "2025-01-01": -100.0},
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "derived"
        assert result.ratio == pytest.approx(0.10, abs=1e-12)

    def test_one_below_minimum_falls_back_with_a_clear_reason(self):
        income_stmt, cash_flow = _statements(
            revenue={"2024-01-01": 1000.0, "2025-01-01": 1000.0},
            capex={"2024-01-01": -100.0, "2025-01-01": -100.0},
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "insufficient_history"
        assert result.ratio is None
        assert len(result.source_periods) == 2  # tracked even though not enough to derive from
        assert "3" in result.reason and "2" in result.reason

    def test_zero_usable_periods_after_excluding_malformed_reports_malformed_not_missing(self):
        """Every period present has a value, but every one is malformed --
        status should be malformed_data (there WAS data, all of it bad),
        not missing_data (there was no data at all)."""
        income_stmt, cash_flow = _statements(
            revenue={"2024-01-01": 1000.0, "2025-01-01": 1000.0},
            capex={"2024-01-01": 100.0, "2025-01-01": 100.0},  # both positive: malformed
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "malformed_data"
        assert result.ratio is None
        assert result.excluded_periods == ("2025-01-01", "2024-01-01")


class TestMissingData:
    def test_no_capex_row_at_all(self):
        income_stmt = pd.DataFrame({pd.Timestamp("2025-01-01"): {"Total Revenue": 1000.0}})
        cash_flow = pd.DataFrame({pd.Timestamp("2025-01-01"): {"Operating Cash Flow": 900.0}})
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "missing_data"
        assert result.ratio is None
        assert "capital-expenditure row" in result.reason.lower()

    def test_no_revenue_row_at_all(self):
        income_stmt = pd.DataFrame({pd.Timestamp("2025-01-01"): {"Some Other Line": 1000.0}})
        cash_flow = pd.DataFrame({pd.Timestamp("2025-01-01"): {"Capital Expenditure": -100.0}})
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "missing_data"
        assert result.ratio is None
        assert "revenue" in result.reason.lower()

    @pytest.mark.parametrize("cash_flow,income_stmt", [(None, pd.DataFrame()), (pd.DataFrame(), None)])
    def test_none_or_empty_statements(self, cash_flow, income_stmt):
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)
        assert result.status == "missing_data"
        assert result.ratio is None


class TestDuplicateColumnsAndMalformedShapes:
    """`derive_historical_capex_pct_revenue` must never raise -- a
    duplicate fiscal-period-end column (a real, observed yfinance data-
    quality issue) makes ordinary per-date `.loc[row][date]` lookups
    ambiguous (pandas raises "The truth value of a Series is ambiguous"
    on a bare `pd.isna(...)` of the resulting multi-value slice); this
    must be reported as a data-quality status instead."""

    def test_duplicate_income_statement_column_reported_not_raised(self):
        income_stmt = pd.DataFrame(
            {
                pd.Timestamp("2024-01-01"): {"Total Revenue": 1000.0},
                pd.Timestamp("2025-01-01"): {"Total Revenue": 1000.0},
            }
        )
        income_stmt = pd.concat([income_stmt, income_stmt[[pd.Timestamp("2024-01-01")]]], axis=1)
        cash_flow = pd.DataFrame(
            {
                pd.Timestamp("2024-01-01"): {"Capital Expenditure": -100.0},
                pd.Timestamp("2025-01-01"): {"Capital Expenditure": -100.0},
            }
        )
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)  # must not raise
        assert result.status == "malformed_data"
        assert result.ratio is None
        assert "duplicate" in result.reason.lower()

    def test_duplicate_cash_flow_column_reported_not_raised(self):
        """The exact shape that broke a DEFAULT valuation before this fix
        (see TestRunDcfValuationWiring.test_default_never_invokes_derivation_even_with_duplicate_cash_flow_dates
        below for the end-to-end version) -- checked here in isolation."""
        income_stmt = pd.DataFrame(
            {
                pd.Timestamp("2023-01-01"): {"Total Revenue": 900.0},
                pd.Timestamp("2024-01-01"): {"Total Revenue": 1000.0},
                pd.Timestamp("2025-01-01"): {"Total Revenue": 1100.0},
            }
        )
        cash_flow = pd.DataFrame(
            {
                pd.Timestamp("2024-01-01"): {"Capital Expenditure": -100.0},
                pd.Timestamp("2025-01-01"): {"Capital Expenditure": -110.0},
            }
        )
        cash_flow = pd.concat([cash_flow, cash_flow[[pd.Timestamp("2024-01-01")]]], axis=1)
        result = derive_historical_capex_pct_revenue(cash_flow, income_stmt)  # must not raise
        assert result.status == "malformed_data"
        assert result.ratio is None
        assert "duplicate" in result.reason.lower()


class TestDefaultPolicyPreservation:
    """The single most important guarantee: nobody who doesn't explicitly
    opt in is affected by any of this."""

    def test_dcfassumptions_default_is_unchanged(self):
        assert DCFAssumptions().capex_pct_revenue == DEFAULT_CAPEX_PCT_REVENUE

    def test_default_construction_never_calls_derivation(self, monkeypatch):
        """The actual guarantee, verified directly rather than inferred:
        a default (capex_pct_revenue left untouched) run_dcf_valuation
        call must not invoke derive_historical_capex_pct_revenue AT ALL --
        not "invoke it and ignore the result," genuinely never call it."""
        import src.dcf_model.dcf as dcf_module

        def _fail_if_called(*_args, **_kwargs):
            raise AssertionError(
                "derive_historical_capex_pct_revenue must not be called for a default "
                "(non-opt-in) run_dcf_valuation request."
            )

        monkeypatch.setattr(dcf_module, "derive_historical_capex_pct_revenue", _fail_if_called)
        financial_data = _synthetic_financial_data_with_capex_history([0.30, 0.30, 0.30, 0.30])
        result = run_dcf_valuation(financial_data, DCFAssumptions())  # would raise via the monkeypatch if wrong
        assert result["capex_pct_revenue"] == DEFAULT_CAPEX_PCT_REVENUE
        assert result["capex_pct_revenue_source"] == "default"

    def test_explicit_none_is_accepted_and_distinct_from_default(self):
        assumptions = DCFAssumptions(capex_pct_revenue=None)
        assert assumptions.capex_pct_revenue is None
        assert DCFAssumptions().capex_pct_revenue == DEFAULT_CAPEX_PCT_REVENUE


def _synthetic_financial_data_with_capex_history(capex_pct_revenue_by_year: list, base_revenue: float = 1000.0):
    """Build a full financial_data dict (income_stmt, balance_sheet,
    cash_flow, price, shares, beta) usable by run_dcf_valuation, with a
    controlled multi-year CapEx/revenue history and a flat 15% margin /
    0% growth (held simple deliberately, so the ONLY thing that differs
    between two runs is the CapEx assumption -- isolating its effect)."""
    dates = [f"{2026 - i}-01-01" for i in range(len(capex_pct_revenue_by_year))]
    revenue = {d: base_revenue for d in dates}
    ebit = {d: base_revenue * 0.15 for d in dates}
    capex = {
        d: -(base_revenue * ratio) for d, ratio in zip(dates, capex_pct_revenue_by_year)
    }
    income_stmt = pd.DataFrame(
        {pd.Timestamp(d): {"Total Revenue": revenue[d], "Operating Income": ebit[d]} for d in dates}
    )
    cash_flow = pd.DataFrame(
        {pd.Timestamp(d): {"Capital Expenditure": capex[d]} for d in dates}
    )
    balance_sheet = pd.DataFrame(
        {pd.Timestamp(dates[0]): {"Total Debt": 0.0, "Cash And Cash Equivalents": 0.0}}
    )
    return {
        "income_statement": income_stmt,
        "balance_sheet": balance_sheet,
        "cash_flow": cash_flow,
        "current_price": 100.0,
        "shares_outstanding": 100.0,
        "beta": 1.0,
    }


class TestRunDcfValuationWiring:
    def test_default_capex_mode_matches_prior_behavior_exactly(self):
        """A caller that never touches capex_pct_revenue must get results
        bit-for-bit identical to before this feature existed: the flat
        4% default, source == "default", derivation == None."""
        financial_data = _synthetic_financial_data_with_capex_history([0.30, 0.30, 0.30, 0.30])
        result = run_dcf_valuation(financial_data, DCFAssumptions())
        assert result["capex_pct_revenue"] == DEFAULT_CAPEX_PCT_REVENUE
        assert result["capex_pct_revenue_source"] == "default"
        assert result["capex_derivation"] is None

    def test_explicit_custom_capex_still_works_and_is_labeled_custom(self):
        financial_data = _synthetic_financial_data_with_capex_history([0.30, 0.30, 0.30, 0.30])
        result = run_dcf_valuation(financial_data, DCFAssumptions(capex_pct_revenue=0.10))
        assert result["capex_pct_revenue"] == 0.10
        assert result["capex_pct_revenue_source"] == "custom"
        assert result["capex_derivation"] is None

    def test_opt_in_with_sufficient_history_uses_derived_ratio(self):
        """FROZEN: 4 years all at a genuine 30% CapEx/revenue ratio ->
        expected derived ratio is exactly 0.30, and the resulting
        capex_pct_revenue actually used by the projection must equal it."""
        financial_data = _synthetic_financial_data_with_capex_history([0.30, 0.30, 0.30, 0.30])
        result = run_dcf_valuation(financial_data, DCFAssumptions(capex_pct_revenue=None))
        assert result["capex_pct_revenue"] == pytest.approx(0.30, abs=1e-12)
        assert result["capex_pct_revenue_source"] == "historical"
        assert isinstance(result["capex_derivation"], HistoricalCapexDerivation)
        assert result["capex_derivation"].status == "derived"
        assert len(result["capex_derivation"].source_periods) == 4

    def test_opt_in_with_insufficient_history_falls_back_to_default_with_reason(self):
        financial_data = _synthetic_financial_data_with_capex_history([0.30, 0.30])  # only 2 years
        result = run_dcf_valuation(financial_data, DCFAssumptions(capex_pct_revenue=None))
        assert result["capex_pct_revenue"] == DEFAULT_CAPEX_PCT_REVENUE
        assert result["capex_pct_revenue_source"] == "fallback"
        assert result["capex_derivation"].status == "insufficient_history"
        assert "3" in result["capex_derivation"].reason

    def test_capex_change_moves_fcf_and_per_share_in_the_correct_direction_by_the_expected_amount(self):
        """The full end-to-end case: identical growth/margin/WACC/tax/
        equity-bridge inputs, ONLY the CapEx assumption differs (flat 4%
        default vs. a derived 30% ratio from 4 years of history). FROZEN
        expected effect, hand-computed from the FCF formula
        (FCF = NOPAT + D&A - CapEx - ChangeInNWC; D&A is unaffected,
        revenue is flat/zero-growth so ChangeInNWC = 0 in every year, so
        the entire difference is exactly (0.30 - 0.04) x revenue = 26% of
        revenue LESS free cash flow every year under the historical
        assumption): base_revenue=1000, so FCF must be exactly 260 lower
        in every one of the 5 projection years, and intrinsic value per
        share must be strictly LOWER under the (higher, more realistic)
        historical CapEx assumption -- the same direction of effect this
        feature's own motivating evidence (MSFT, VZ real CapEx both far
        above the flat 4% default) established."""
        financial_data = _synthetic_financial_data_with_capex_history([0.30, 0.30, 0.30, 0.30])

        default_result = run_dcf_valuation(financial_data, DCFAssumptions(revenue_growth_rate=0.0))
        historical_result = run_dcf_valuation(
            financial_data, DCFAssumptions(revenue_growth_rate=0.0, capex_pct_revenue=None)
        )

        assert default_result["capex_pct_revenue"] == pytest.approx(0.04)
        assert historical_result["capex_pct_revenue"] == pytest.approx(0.30)

        default_fcf = default_result["fcf_projection"]["fcf"]
        historical_fcf = historical_result["fcf_projection"]["fcf"]
        for year in default_fcf.index:
            expected_difference = (0.30 - 0.04) * 1000.0  # = 260.0 exactly, every year
            actual_difference = float(default_fcf[year] - historical_fcf[year])
            assert actual_difference == pytest.approx(expected_difference, abs=1e-9), (
                f"Year {year}: expected default FCF to exceed historical-CapEx FCF by exactly "
                f"{expected_difference}, got {actual_difference}."
            )

        assert historical_result["intrinsic_value_per_share"] < default_result["intrinsic_value_per_share"], (
            "A higher, historically-derived CapEx assumption must produce a lower intrinsic "
            "value than the flat 4% default when it is genuinely higher than 4% -- the same "
            "direction this feature's own motivating evidence (MSFT/VZ real CapEx well above "
            "4%) established; this DCF change is not itself validated as economically correct "
            "by this test, only shown to move the arithmetic the documented direction."
        )


class TestDuplicateColumnsEndToEnd:
    """The exact regression this fix targets: a duplicate fiscal-period-end
    column in cash_flow, exercised through the full run_dcf_valuation path
    rather than calling derive_historical_capex_pct_revenue directly."""

    def test_default_dcf_succeeds_with_duplicate_cash_flow_dates_and_retains_flat_4_percent(self):
        """Before this fix: a default (non-opt-in) run_dcf_valuation call
        over a financial_data dict whose cash_flow has a duplicate date
        column raised an uncaught ValueError, because
        extract_valuation_inputs called derive_historical_capex_pct_revenue
        unconditionally. After this fix: the derivation is never invoked
        for a default request (see TestDefaultPolicyPreservation above),
        so this succeeds and retains the flat 4% policy regardless of
        what's wrong with cash_flow."""
        dates = [pd.Timestamp(f"{2026 - i}-01-01") for i in range(4)]
        income_stmt = pd.DataFrame(
            {d: {"Total Revenue": 1000.0, "Operating Income": 150.0} for d in dates}
        )
        cash_flow = pd.DataFrame({d: {"Capital Expenditure": -300.0} for d in dates})
        cash_flow = pd.concat([cash_flow, cash_flow[[dates[0]]]], axis=1)  # duplicate column
        balance_sheet = pd.DataFrame({dates[0]: {"Total Debt": 0.0, "Cash And Cash Equivalents": 0.0}})
        financial_data = {
            "income_statement": income_stmt,
            "balance_sheet": balance_sheet,
            "cash_flow": cash_flow,
            "current_price": 100.0,
            "shares_outstanding": 100.0,
            "beta": 1.0,
        }

        result = run_dcf_valuation(financial_data, DCFAssumptions())  # must not raise

        assert result["capex_pct_revenue"] == DEFAULT_CAPEX_PCT_REVENUE
        assert result["capex_pct_revenue_source"] == "default"
        assert result["capex_derivation"] is None
        assert result["intrinsic_value_per_share"] is not None

    def test_opted_in_dcf_reports_malformed_data_with_duplicate_cash_flow_dates_instead_of_raising(self):
        """The complementary case: capex_mode IS opted in, over the same
        duplicate-column cash_flow. The derivation runs, detects the
        duplicate, and falls back to the flat default with a clear reason
        -- run_dcf_valuation itself never raises."""
        dates = [pd.Timestamp(f"{2026 - i}-01-01") for i in range(4)]
        income_stmt = pd.DataFrame(
            {d: {"Total Revenue": 1000.0, "Operating Income": 150.0} for d in dates}
        )
        cash_flow = pd.DataFrame({d: {"Capital Expenditure": -300.0} for d in dates})
        cash_flow = pd.concat([cash_flow, cash_flow[[dates[0]]]], axis=1)  # duplicate column
        balance_sheet = pd.DataFrame({dates[0]: {"Total Debt": 0.0, "Cash And Cash Equivalents": 0.0}})
        financial_data = {
            "income_statement": income_stmt,
            "balance_sheet": balance_sheet,
            "cash_flow": cash_flow,
            "current_price": 100.0,
            "shares_outstanding": 100.0,
            "beta": 1.0,
        }

        result = run_dcf_valuation(financial_data, DCFAssumptions(capex_pct_revenue=None))  # must not raise

        assert result["capex_pct_revenue"] == DEFAULT_CAPEX_PCT_REVENUE
        assert result["capex_pct_revenue_source"] == "fallback"
        assert result["capex_derivation"].status == "malformed_data"
        assert "duplicate" in result["capex_derivation"].reason.lower()
        assert result["intrinsic_value_per_share"] is not None
