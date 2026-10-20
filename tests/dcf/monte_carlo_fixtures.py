"""
Offline fixtures for the Monte Carlo DCF tests. No network, no provider calls:
plain synthetic pandas statements shaped like the provider's output.
"""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd

PERIODS = ("2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31")


def make_financial_data(
    revenues: Sequence[float] = (100_000.0, 108_000.0, 115_000.0, 125_000.0),
    ebits: Optional[Sequence[float]] = (25_000.0, 28_500.0, 28_000.0, 33_000.0),
    periods: Sequence[str] = PERIODS,
    *,
    ticker: str = "TEST",
    price: float = 150.0,
    shares: float = 1_000.0,
    beta: float = 1.1,
    total_debt: float = 20_000.0,
    cash: float = 15_000.0,
    ebit_label: str = "EBIT",
) -> dict:
    """Annual statements, oldest to newest, with the columns the DCF reads."""
    columns = [pd.Timestamp(period) for period in periods]
    rows = {"Total Revenue": list(revenues)}
    if ebits is not None:
        rows[ebit_label] = list(ebits)
    rows["Pretax Income"] = [value * 0.95 for value in (ebits or revenues)]
    rows["Tax Provision"] = [value * 0.95 * 0.18 for value in (ebits or revenues)]
    rows["Interest Expense"] = [900.0] * len(columns)
    income_statement = pd.DataFrame(rows, index=columns).T
    income_statement.columns = pd.DatetimeIndex(columns)
    # Provider layout: most recent period first.
    income_statement = income_statement[sorted(income_statement.columns, reverse=True)]
    balance_sheet = pd.DataFrame(
        {columns[-1]: {"Total Debt": total_debt, "Cash And Cash Equivalents": cash}}
    )
    return {
        "ticker": ticker,
        "sector": "Technology",
        "reporting_currency": "USD",
        "income_statement": income_statement,
        "balance_sheet": balance_sheet,
        "cash_flow": None,
        "current_price": price,
        "shares_outstanding": shares,
        "beta": beta,
    }
