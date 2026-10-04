import pandas as pd
import pytest

from src.api.historical_financials import annual_history


def test_periods_align_by_date_not_column_order_and_fcf_reconciles():
    income = pd.DataFrame({pd.Timestamp("2024-06-30"): {"Total Revenue": 200}, pd.Timestamp("2023-06-30"): {"Total Revenue": 100}})
    cash = pd.DataFrame({pd.Timestamp("2023-06-30"): {"Operating Cash Flow": 25, "Capital Expenditure": -10}, pd.Timestamp("2024-06-30"): {"Operating Cash Flow": 50, "Capital Expenditure": -20}})
    history = annual_history({"income_statement": income, "cash_flow": cash, "reporting_currency": "usd"})
    assert history.currency == "USD"
    assert [(p.period_end, p.revenue, p.capital_expenditures, p.free_cash_flow) for p in history.periods] == [("2023-06-30", 100, 10, 15), ("2024-06-30", 200, 20, 30)]


def test_missing_cash_periods_and_nan_are_not_zero_or_borrowed():
    income = pd.DataFrame({pd.Timestamp("2024-12-31"): {"Total Revenue": 200}})
    cash = pd.DataFrame({pd.Timestamp("2023-12-31"): {"Operating Cash Flow": 50, "Capital Expenditure": float("nan")}})
    periods = annual_history({"income_statement": income, "cash_flow": cash}).periods
    assert periods[0].revenue is None
    assert periods[0].capital_expenditures is None
    assert periods[0].free_cash_flow is None
    assert periods[1].operating_cash_flow is None
    assert periods[1].free_cash_flow is None


@pytest.mark.parametrize("ocf,signed_capex,expected", [(5, -10, -5), (-5, -10, -15), (5, 10, 15), (0, 0, 0)])
def test_signed_cash_flows_are_preserved(ocf, signed_capex, expected):
    cash = pd.DataFrame({pd.Timestamp("2024-12-31"): {"Operating Cash Flow": ocf, "Capital Expenditure": signed_capex}})
    assert annual_history({"cash_flow": cash}).periods[0].free_cash_flow == expected


def test_absent_statements_are_empty_and_currency_is_not_assumed():
    assert annual_history({}).periods == []
    assert annual_history({}).currency is None
