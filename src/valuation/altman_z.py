"""
Altman Z-Score corporate distress screen.

Implements the original (1968) Altman Z-Score, a linear combination of
five balance-sheet/income-statement ratios used to estimate a public
company's near-term bankruptcy risk:

    Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5

    X1 = Working Capital (Current Assets - Current Liabilities) / Total Assets
    X2 = Retained Earnings / Total Assets
    X3 = EBIT / Total Assets
    X4 = Market Value of Equity (Shares Outstanding * Current Price) / Total Liabilities
    X5 = Total Revenue / Total Assets

Conventional interpretation:
    Z > 2.99        — "Safe" zone
    1.8 <= Z <= 2.99 — "Grey" zone
    Z < 1.8          — "Distress" zone

Used as a pre-trade credit health filter: `src.trading.alpaca_execution`
rejects any ticker whose Z-Score is unavailable or falls in the Distress
zone before it's considered for valuation or position sizing.
"""

import logging
from typing import Optional

from src.data_ingestion.fetch_financials import (
    get_balance_sheet,
    get_current_price,
    get_income_statement,
    get_shares_outstanding,
    get_ticker_object,
)
from src.dcf_model.dcf import _get_row_value, _most_recent_column

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def calculate_altman_z(ticker_symbol: str) -> Optional[float]:
    """
    Calculate the Altman Z-Score for a ticker from its most recent annual
    balance sheet and income statement.

    Args:
        ticker_symbol: Stock ticker symbol, e.g. "AAPL".

    Returns:
        The Z-Score as a float, or None if any required line item
        (current assets/liabilities, total assets, retained earnings,
        EBIT, total liabilities, total revenue, current price, or shares
        outstanding) is unavailable from Yahoo Finance.
    """
    try:
        ticker_obj = get_ticker_object(ticker_symbol)
    except ValueError as exc:
        logger.warning("Invalid ticker for Altman Z-Score: %s", exc)
        return None

    balance_sheet = get_balance_sheet(ticker_obj)
    income_stmt = get_income_statement(ticker_obj)
    if balance_sheet is None or income_stmt is None:
        logger.warning(
            "Missing balance sheet or income statement for %s; cannot compute Altman Z-Score.",
            ticker_obj.ticker,
        )
        return None

    bs_column = _most_recent_column(balance_sheet)
    inc_column = _most_recent_column(income_stmt)

    current_assets = _get_row_value(balance_sheet, ["Current Assets"], column=bs_column)
    current_liabilities = _get_row_value(balance_sheet, ["Current Liabilities"], column=bs_column)
    total_assets = _get_row_value(balance_sheet, ["Total Assets"], column=bs_column)
    retained_earnings = _get_row_value(balance_sheet, ["Retained Earnings"], column=bs_column)
    total_liabilities = _get_row_value(
        balance_sheet,
        ["Total Liabilities Net Minority Interest", "Total Liab"],
        column=bs_column,
    )

    ebit = _get_row_value(income_stmt, ["EBIT"], column=inc_column)
    if ebit is None:
        ebit = _get_row_value(income_stmt, ["Operating Income"], column=inc_column)
    total_revenue = _get_row_value(income_stmt, ["Total Revenue"], column=inc_column)

    current_price = get_current_price(ticker_obj)
    shares_outstanding = get_shares_outstanding(ticker_obj)

    required = {
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "total_assets": total_assets,
        "retained_earnings": retained_earnings,
        "total_liabilities": total_liabilities,
        "ebit": ebit,
        "total_revenue": total_revenue,
        "current_price": current_price,
        "shares_outstanding": shares_outstanding,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        logger.warning(
            "Cannot compute Altman Z-Score for %s; missing: %s.",
            ticker_obj.ticker,
            ", ".join(missing),
        )
        return None

    if total_assets == 0 or total_liabilities == 0:
        logger.warning(
            "Cannot compute Altman Z-Score for %s: total assets or total liabilities is zero.",
            ticker_obj.ticker,
        )
        return None

    working_capital = current_assets - current_liabilities
    market_value_of_equity = shares_outstanding * current_price

    x1 = working_capital / total_assets
    x2 = retained_earnings / total_assets
    x3 = ebit / total_assets
    x4 = market_value_of_equity / total_liabilities
    x5 = total_revenue / total_assets

    z_score = (1.2 * x1) + (1.4 * x2) + (3.3 * x3) + (0.6 * x4) + (1.0 * x5)
    return float(z_score)


if __name__ == "__main__":
    for symbol in ["AAPL", "MSFT", "GOOGL"]:
        print(f"{symbol}: Altman Z-Score = {calculate_altman_z(symbol)}")
