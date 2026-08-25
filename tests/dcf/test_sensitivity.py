"""
DCF sensitivity grid (`src.dcf_model.sensitivity`): baseline-cell
consistency with the normal DCF pipeline, WACC/terminal-growth
monotonicity, invalid-combination handling (never NaN/infinity), and
negative-intrinsic-value support.
"""

import math

import pandas as pd
import pytest

from src.dcf_model.dcf import (
    DCFAssumptions,
    MIN_DISCOUNT_RATE,
    project_free_cash_flows,
    run_dcf_valuation,
)
from src.dcf_model.sensitivity import (
    TERMINAL_GROWTH_OFFSET_STEPS,
    TERMINAL_GROWTH_STEP,
    WACC_OFFSET_STEPS,
    WACC_STEP,
    compute_dcf_sensitivity,
)


def _synthetic_fcf_projection(
    base_revenue: float = 1000.0,
    revenue_growth_rate: float = 0.05,
    operating_margin: float = 0.20,
    years: int = 5,
) -> pd.DataFrame:
    return project_free_cash_flows(
        base_revenue=base_revenue,
        revenue_growth_rate=revenue_growth_rate,
        operating_margin=operating_margin,
        years=years,
    )


def _synthetic_financial_data(total_debt: float = 100.0, cash: float = 50.0) -> dict:
    income_stmt = pd.DataFrame(
        {
            pd.Timestamp("2022-12-31"): {"Total Revenue": 1000.0, "Pretax Income": 200.0, "Tax Provision": 50.0},
            pd.Timestamp("2023-12-31"): {"Total Revenue": 1100.0, "Pretax Income": 220.0, "Tax Provision": 55.0},
        }
    )
    balance_sheet = pd.DataFrame(
        {pd.Timestamp("2023-12-31"): {"Total Debt": total_debt, "Cash And Cash Equivalents": cash}}
    )
    return {
        "income_statement": income_stmt,
        "balance_sheet": balance_sheet,
        "cash_flow": None,
        "current_price": 50.0,
        "shares_outstanding": 100.0,
        "beta": 1.0,
    }


class TestBaselineConsistency:
    def test_baseline_cell_matches_normal_intrinsic_value(self):
        """The sensitivity grid's own baseline cell must reproduce the
        SAME intrinsic value per share the ordinary DCF pipeline computed
        — it's derived from the identical fcf_projection/equity-bridge
        inputs via the identical functions."""
        financial_data = _synthetic_financial_data()
        assumptions = DCFAssumptions(revenue_growth_rate=0.05, operating_margin=0.20, terminal_growth_rate=0.025)
        result = run_dcf_valuation(financial_data, assumptions)

        matrix = compute_dcf_sensitivity(
            fcf_projection=result["fcf_projection"],
            baseline_wacc=result["wacc"],
            baseline_terminal_growth_rate=assumptions.terminal_growth_rate,
            total_debt=result["total_debt"],
            cash_and_equivalents=result["cash_and_equivalents"],
            shares_outstanding=result["shares_outstanding"],
        )

        assert matrix.cells[matrix.baseline_row][matrix.baseline_col] == pytest.approx(
            result["intrinsic_value_per_share"], rel=1e-9
        )
        assert matrix.baseline_intrinsic_value_per_share == pytest.approx(
            result["intrinsic_value_per_share"], rel=1e-9
        )
        assert matrix.baseline_wacc == result["wacc"]
        assert matrix.baseline_terminal_growth_rate == assumptions.terminal_growth_rate


class TestAxisShape:
    def test_axis_values_and_baseline_index(self):
        fcf = _synthetic_fcf_projection()
        matrix = compute_dcf_sensitivity(
            fcf_projection=fcf,
            baseline_wacc=0.10,
            baseline_terminal_growth_rate=0.02,
            total_debt=0.0,
            cash_and_equivalents=0.0,
            shares_outstanding=100.0,
        )

        expected_wacc = [0.10 + step * WACC_STEP for step in WACC_OFFSET_STEPS]
        expected_tg = [0.02 + step * TERMINAL_GROWTH_STEP for step in TERMINAL_GROWTH_OFFSET_STEPS]

        assert matrix.wacc_axis.values == pytest.approx(expected_wacc)
        assert matrix.terminal_growth_axis.values == pytest.approx(expected_tg)
        assert matrix.wacc_axis.baseline_index == WACC_OFFSET_STEPS.index(0)
        assert matrix.terminal_growth_axis.baseline_index == TERMINAL_GROWTH_OFFSET_STEPS.index(0)
        assert matrix.baseline_row == matrix.wacc_axis.baseline_index
        assert matrix.baseline_col == matrix.terminal_growth_axis.baseline_index
        assert len(matrix.cells) == 5
        assert all(len(row) == 5 for row in matrix.cells)


class TestMonotonicity:
    def test_higher_wacc_decreases_value_holding_terminal_growth_at_baseline(self):
        fcf = _synthetic_fcf_projection()
        matrix = compute_dcf_sensitivity(
            fcf_projection=fcf,
            baseline_wacc=0.10,
            baseline_terminal_growth_rate=0.02,
            total_debt=100.0,
            cash_and_equivalents=50.0,
            shares_outstanding=100.0,
        )
        column = matrix.baseline_col
        values = [matrix.cells[row][column] for row in range(len(matrix.wacc_axis.values))]

        assert all(v is not None for v in values)
        # WACC axis is ascending -> intrinsic value must be strictly descending.
        assert values == sorted(values, reverse=True)
        assert values[0] > values[-1]

    def test_higher_terminal_growth_increases_value_holding_wacc_at_baseline(self):
        fcf = _synthetic_fcf_projection()
        matrix = compute_dcf_sensitivity(
            fcf_projection=fcf,
            baseline_wacc=0.10,
            baseline_terminal_growth_rate=0.02,
            total_debt=100.0,
            cash_and_equivalents=50.0,
            shares_outstanding=100.0,
        )
        row = matrix.baseline_row
        values = matrix.cells[row]

        assert all(v is not None for v in values)
        # Terminal-growth axis is ascending -> intrinsic value must be strictly ascending.
        assert values == sorted(values)
        assert values[-1] > values[0]


class TestInvalidCombinations:
    """
    Exercises the invalid-combination rules entirely through the public
    `compute_dcf_sensitivity()` matrix output — never the private
    `_compute_cell` helper, which stays an internal implementation
    detail of the module (not part of its tested public contract).

    Every test below reads the BASELINE cell (offset 0, 0), since that's
    the one cell whose wacc/terminal_growth values are the baseline
    inputs themselves with no `+ step * STEP` arithmetic applied — this
    avoids any floating-point-equality assumption about independently
    computed offsets landing on the same value.
    """

    def test_wacc_equal_to_terminal_growth_is_null(self):
        """
        0.05 is simultaneously MIN_DISCOUNT_RATE (WACC's own floor) and
        MAX_EXPLICIT_TERMINAL_GROWTH_RATE (terminal growth's own
        ceiling) in this codebase — the only value where wacc == terminal
        growth can hold with BOTH individually still inside their own
        declared bounds, isolating the wacc<=terminal_growth rule itself
        from an out-of-bounds rejection.
        """
        fcf = _synthetic_fcf_projection()
        matrix = compute_dcf_sensitivity(
            fcf_projection=fcf,
            baseline_wacc=0.05,
            baseline_terminal_growth_rate=0.05,
            total_debt=0.0,
            cash_and_equivalents=0.0,
            shares_outstanding=100.0,
        )

        assert matrix.baseline_wacc == matrix.baseline_terminal_growth_rate
        assert matrix.cells[matrix.baseline_row][matrix.baseline_col] is None

    def test_wacc_below_terminal_growth_is_null(self):
        """
        Given MIN_DISCOUNT_RATE == MAX_EXPLICIT_TERMINAL_GROWTH_RATE ==
        0.05 in this codebase, a WACC strictly below terminal growth is
        only reachable with WACC also below its own floor -- there is no
        configuration where wacc < terminal_growth while both remain
        individually in-bounds. That doesn't weaken this test: it still
        proves a cell with wacc < terminal_growth is null through the
        public interface, which is exactly the behavior being checked
        (test_matrix_marks_out_of_bounds_cells_null_never_nan_or_infinite
        below separately isolates an out-of-bounds rejection where
        wacc > terminal_growth still holds).
        """
        fcf = _synthetic_fcf_projection()
        matrix = compute_dcf_sensitivity(
            fcf_projection=fcf,
            baseline_wacc=0.03,
            baseline_terminal_growth_rate=0.04,
            total_debt=0.0,
            cash_and_equivalents=0.0,
            shares_outstanding=100.0,
        )

        assert matrix.baseline_wacc < matrix.baseline_terminal_growth_rate
        assert matrix.cells[matrix.baseline_row][matrix.baseline_col] is None

    def test_matrix_marks_out_of_bounds_cells_null_even_when_wacc_exceeds_terminal_growth(self):
        """A baseline WACC near the model's own floor pushes the lowest
        offset row below MIN_DISCOUNT_RATE — those cells must be `None`,
        and every other cell must be a genuine finite number. This cell's
        WACC (0.035) is still greater than its terminal growth (0.02), so
        the rejection here is provably the bounds check, not the separate
        wacc<=terminal_growth rule already covered above."""
        fcf = _synthetic_fcf_projection()
        matrix = compute_dcf_sensitivity(
            fcf_projection=fcf,
            baseline_wacc=MIN_DISCOUNT_RATE + 0.005,  # -2 steps (-2%) goes below the floor
            baseline_terminal_growth_rate=0.02,
            total_debt=0.0,
            cash_and_equivalents=0.0,
            shares_outstanding=100.0,
        )

        out_of_bounds_wacc = matrix.wacc_axis.values[0]
        assert out_of_bounds_wacc < MIN_DISCOUNT_RATE
        assert out_of_bounds_wacc > matrix.baseline_terminal_growth_rate
        assert matrix.cells[0][matrix.baseline_col] is None
        for row in matrix.cells:
            for cell in row:
                if cell is not None:
                    assert math.isfinite(cell)


class TestNegativeIntrinsicValue:
    def test_negative_intrinsic_value_is_valid_and_finite(self):
        """Debt large enough relative to enterprise value pushes equity
        value negative -- the model must still return a finite negative
        number, not None and not NaN/infinity."""
        fcf = _synthetic_fcf_projection(base_revenue=1000.0, revenue_growth_rate=0.02, operating_margin=0.10)
        matrix = compute_dcf_sensitivity(
            fcf_projection=fcf,
            baseline_wacc=0.12,
            baseline_terminal_growth_rate=0.02,
            total_debt=1_000_000.0,
            cash_and_equivalents=0.0,
            shares_outstanding=100.0,
        )

        baseline_cell = matrix.baseline_intrinsic_value_per_share
        assert baseline_cell is not None
        assert math.isfinite(baseline_cell)
        assert baseline_cell < 0

        # Every valid (non-null) cell in this scenario should also stay
        # negative and finite -- the equity bridge dominates every cell.
        for row in matrix.cells:
            for cell in row:
                if cell is not None:
                    assert math.isfinite(cell)
                    assert cell < 0
