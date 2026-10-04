"""Display-only twelve-month history from the exact statements selected for valuation."""
import math
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel


class HistoricalFinancialPeriod(BaseModel):
    period_end: str
    revenue: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    capital_expenditures: Optional[float] = None
    free_cash_flow: Optional[float] = None


class HistoricalFinancials(BaseModel):
    currency: Optional[str] = None
    period_basis: Literal["annual", "trailing_twelve_months"] = "annual"
    periods: list[HistoricalFinancialPeriod]


def annual_history(financial_data: dict) -> HistoricalFinancials:
    """Never interpolate periods, fill missing cells, or fetch another source.

    Both supported adapters supply CapEx as a signed cash-flow line. Present
    cash outflow as positive spending; a positive source line is a cash inflow
    and remains negative spending. Cash FCF is OCF minus that signed spending,
    not the valuation engine's projected unlevered FCFF.
    """
    income = financial_data.get("income_statement")
    cash = financial_data.get("cash_flow")

    def columns(frame):
        result = {}
        if not isinstance(frame, pd.DataFrame):
            return result
        for column in frame.columns:
            try:
                date = pd.Timestamp(column)
                if pd.isna(date):
                    continue
                key = date.date().isoformat()
                # Ambiguous duplicate periods cannot supply a display value.
                result[key] = column if key not in result else None
            except (ValueError, TypeError):
                continue
        return result

    income_columns, cash_columns = columns(income), columns(cash)

    def value(frame, mapping, period, aliases):
        column = mapping.get(period)
        if column is None:
            return None
        for row in aliases:
            if row not in frame.index:
                continue
            try:
                number = float(frame.loc[row, column])
                if math.isfinite(number):
                    return number
            except (TypeError, ValueError):
                continue
        return None

    periods = []
    for period in sorted(set(income_columns) | set(cash_columns)):
        revenue = value(income, income_columns, period, ("Total Revenue", "Operating Revenue"))
        ocf = value(cash, cash_columns, period, ("Operating Cash Flow", "Total Cash From Operating Activities"))
        signed_capex = value(cash, cash_columns, period, ("Capital Expenditure", "Capital Expenditures"))
        capex = -signed_capex if signed_capex is not None else None
        if revenue is None and ocf is None and capex is None:
            continue
        periods.append(HistoricalFinancialPeriod(
            period_end=period, revenue=revenue, operating_cash_flow=ocf,
            capital_expenditures=capex,
            free_cash_flow=ocf - capex if ocf is not None and capex is not None else None,
        ))
    currency = financial_data.get("reporting_currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        currency = None
    return HistoricalFinancials(currency=currency.upper() if currency else None,
        period_basis=financial_data.get("statement_period_basis", "annual"), periods=periods[-5:])
