"""
Piotroski F-Score fundamental quality screen.

Implements the classic 9-point Piotroski F-Score, comparing the most
recent annual/TTM period (t) against the prior period (t-1) across
profitability, leverage/liquidity, and operating efficiency:

    Profitability
        1. ROA > 0                          (Net Income / Total Assets)
        2. CFO > 0                          (Operating Cash Flow)
        3. CFO > Net Income                 (earnings quality)
        4. Delta ROA > 0                    (ROA_t > ROA_t-1)

    Leverage / Liquidity / Dilution
        5. Delta Leverage < 0               (LT Debt / Assets decreased)
        6. Delta Current Ratio > 0          (Current Assets / Current Liabilities increased)
        7. Zero new shares issued           (Shares_t <= Shares_t-1)

    Operating Efficiency
        8. Delta Gross Margin > 0
        9. Delta Asset Turnover > 0         (Revenue / Total Assets increased)

Each factor is independently binary; a factor that can't be computed
(missing line item, insufficient historical periods) defaults to 0
rather than aborting the whole score, so a company with an incomplete
statement still gets a best-effort score instead of no score at all.

Used as a pre-trade fundamental quality gate: `src.trading.alpaca_execution`
rejects any ticker with an F-Score below its minimum threshold before it's
valued via the (much more expensive) DCF pipeline.

Every yfinance fetch here goes through `src.data_ingestion.fetch_financials`'s
already-`@cached`-wrapped statement getters, so repeated scans reuse the
same Upstash Redis-cached statements as the rest of the engine rather than
hitting Yahoo Finance again.
"""

import logging
from typing import Optional

import pandas as pd

from src.data_ingestion.fetch_financials import (
    get_balance_sheet,
    get_cash_flow_statement,
    get_income_statement,
    get_ticker_object,
)
from src.dcf_model.dcf import _get_row_value

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

NET_INCOME_ROWS = ["Net Income", "NetIncome", "Net Income Common Stockholders"]
TOTAL_ASSETS_ROWS = ["Total Assets"]
CURRENT_ASSETS_ROWS = ["Current Assets"]
CURRENT_LIABILITIES_ROWS = ["Current Liabilities"]
LONG_TERM_DEBT_ROWS = ["Long Term Debt", "LongTermDebt"]
OPERATING_CASH_FLOW_ROWS = [
    "Operating Cash Flow",
    "Cash Flow From Continuing Operating Activities",
    "Total Cash From Operating Activities",
]
TOTAL_REVENUE_ROWS = ["Total Revenue", "TotalRevenue"]
GROSS_PROFIT_ROWS = ["Gross Profit"]
COST_OF_REVENUE_ROWS = ["Cost Of Revenue", "Reconciled Cost Of Revenue"]
SHARES_ISSUED_ROWS = ["Share Issued", "Ordinary Shares Number", "Common Stock Shares Outstanding"]


def _sorted_periods(df: Optional[pd.DataFrame]) -> list:
    """Fiscal period columns of a statement, most recent first."""
    if df is None or df.empty:
        return []
    try:
        return sorted(df.columns, key=pd.Timestamp, reverse=True)
    except (TypeError, ValueError):
        return list(df.columns)


def _two_most_recent_columns(df: Optional[pd.DataFrame]) -> tuple:
    """(current_column, prior_column) for a statement; either may be None if unavailable."""
    periods = _sorted_periods(df)
    current = periods[0] if len(periods) >= 1 else None
    prior = periods[1] if len(periods) >= 2 else None
    return current, prior


def _roa(income_stmt, balance_sheet, income_col, balance_col) -> Optional[float]:
    net_income = _get_row_value(income_stmt, NET_INCOME_ROWS, column=income_col)
    total_assets = _get_row_value(balance_sheet, TOTAL_ASSETS_ROWS, column=balance_col)
    if net_income is None or not total_assets:
        return None
    return net_income / total_assets


def _leverage(balance_sheet, column) -> Optional[float]:
    """Long-Term Debt / Total Assets. A missing LT Debt row is treated as 0 debt
    (yfinance typically omits the row entirely for companies carrying none)."""
    total_assets = _get_row_value(balance_sheet, TOTAL_ASSETS_ROWS, column=column)
    if not total_assets:
        return None
    long_term_debt = _get_row_value(balance_sheet, LONG_TERM_DEBT_ROWS, column=column) or 0.0
    return long_term_debt / total_assets


def _current_ratio(balance_sheet, column) -> Optional[float]:
    current_assets = _get_row_value(balance_sheet, CURRENT_ASSETS_ROWS, column=column)
    current_liabilities = _get_row_value(balance_sheet, CURRENT_LIABILITIES_ROWS, column=column)
    if current_assets is None or not current_liabilities:
        return None
    return current_assets / current_liabilities


def _shares_issued(balance_sheet, column) -> Optional[float]:
    return _get_row_value(balance_sheet, SHARES_ISSUED_ROWS, column=column)


def _gross_margin(income_stmt, column) -> Optional[float]:
    revenue = _get_row_value(income_stmt, TOTAL_REVENUE_ROWS, column=column)
    if not revenue:
        return None
    gross_profit = _get_row_value(income_stmt, GROSS_PROFIT_ROWS, column=column)
    if gross_profit is None:
        cost_of_revenue = _get_row_value(income_stmt, COST_OF_REVENUE_ROWS, column=column)
        if cost_of_revenue is None:
            return None
        gross_profit = revenue - cost_of_revenue
    return gross_profit / revenue


def _asset_turnover(income_stmt, balance_sheet, income_col, balance_col) -> Optional[float]:
    revenue = _get_row_value(income_stmt, TOTAL_REVENUE_ROWS, column=income_col)
    total_assets = _get_row_value(balance_sheet, TOTAL_ASSETS_ROWS, column=balance_col)
    if revenue is None or not total_assets:
        return None
    return revenue / total_assets


def calculate_f_score(ticker_symbol: str) -> int:
    """
    Calculate the Piotroski F-Score (0-9) for a ticker from its most
    recent two annual/TTM statement periods.

    Args:
        ticker_symbol: Stock ticker symbol, e.g. "AAPL".

    Returns:
        Integer 0-9: the count of the 9 binary factors that evaluated
        True. Any factor whose required line item(s) or prior-period
        comparison is unavailable defaults to 0 (does not contribute to
        the score) rather than aborting the whole calculation.
    """
    try:
        ticker_obj = get_ticker_object(ticker_symbol)
    except ValueError as exc:
        logger.warning("Invalid ticker for Piotroski F-Score: %s", exc)
        return 0

    income_stmt = get_income_statement(ticker_obj)
    balance_sheet = get_balance_sheet(ticker_obj)
    cash_flow = get_cash_flow_statement(ticker_obj)

    income_t, income_t1 = _two_most_recent_columns(income_stmt)
    balance_t, balance_t1 = _two_most_recent_columns(balance_sheet)
    cash_t, _ = _two_most_recent_columns(cash_flow)

    net_income_t = _get_row_value(income_stmt, NET_INCOME_ROWS, column=income_t)
    cfo_t = _get_row_value(cash_flow, OPERATING_CASH_FLOW_ROWS, column=cash_t)
    roa_t = _roa(income_stmt, balance_sheet, income_t, balance_t)
    roa_t1 = _roa(income_stmt, balance_sheet, income_t1, balance_t1)
    leverage_t = _leverage(balance_sheet, balance_t)
    leverage_t1 = _leverage(balance_sheet, balance_t1)
    current_ratio_t = _current_ratio(balance_sheet, balance_t)
    current_ratio_t1 = _current_ratio(balance_sheet, balance_t1)
    shares_t = _shares_issued(balance_sheet, balance_t)
    shares_t1 = _shares_issued(balance_sheet, balance_t1)
    gross_margin_t = _gross_margin(income_stmt, income_t)
    gross_margin_t1 = _gross_margin(income_stmt, income_t1)
    asset_turnover_t = _asset_turnover(income_stmt, balance_sheet, income_t, balance_t)
    asset_turnover_t1 = _asset_turnover(income_stmt, balance_sheet, income_t1, balance_t1)

    factors = [
        roa_t is not None and roa_t > 0,
        cfo_t is not None and cfo_t > 0,
        cfo_t is not None and net_income_t is not None and cfo_t > net_income_t,
        roa_t is not None and roa_t1 is not None and roa_t > roa_t1,
        leverage_t is not None and leverage_t1 is not None and leverage_t < leverage_t1,
        current_ratio_t is not None and current_ratio_t1 is not None and current_ratio_t > current_ratio_t1,
        shares_t is not None and shares_t1 is not None and shares_t <= shares_t1,
        gross_margin_t is not None and gross_margin_t1 is not None and gross_margin_t > gross_margin_t1,
        asset_turnover_t is not None and asset_turnover_t1 is not None and asset_turnover_t > asset_turnover_t1,
    ]

    return sum(1 for factor in factors if factor)


if __name__ == "__main__":
    for symbol in ["AAPL", "MSFT", "GOOGL"]:
        print(f"{symbol}: Piotroski F-Score = {calculate_f_score(symbol)}")
