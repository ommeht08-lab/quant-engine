"""
Financial data ingestion utilities.

Fetches the income statement, balance sheet, cash flow statement, current
share price, shares outstanding, and beta for a given ticker using
yfinance. Every fetch function degrades gracefully (returns None / logs a
warning) rather than raising, so downstream consumers (e.g. the DCF model)
can decide how to handle missing data.
"""

import logging
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def get_ticker_object(ticker: str) -> yf.Ticker:
    """
    Build a yfinance Ticker object for the given symbol.

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL").

    Returns:
        A yfinance.Ticker instance.

    Raises:
        ValueError: If ticker is missing or not a string.
    """
    if not ticker or not isinstance(ticker, str):
        raise ValueError("A valid ticker symbol string must be provided.")
    return yf.Ticker(ticker.strip().upper())


def get_income_statement(ticker_obj: yf.Ticker) -> Optional[pd.DataFrame]:
    """
    Fetch the annual income statement for a company.

    Args:
        ticker_obj: A yfinance.Ticker instance.

    Returns:
        DataFrame of line items (rows) by fiscal period (columns), or
        None if the data is unavailable.
    """
    try:
        statement = ticker_obj.income_stmt
        if statement is None or statement.empty:
            logger.warning("Income statement is empty for %s.", ticker_obj.ticker)
            return None
        return statement
    except Exception as exc:  # yfinance can raise a variety of network/parsing errors
        logger.warning("Failed to fetch income statement for %s: %s", ticker_obj.ticker, exc)
        return None


def get_balance_sheet(ticker_obj: yf.Ticker) -> Optional[pd.DataFrame]:
    """
    Fetch the annual balance sheet for a company.

    Args:
        ticker_obj: A yfinance.Ticker instance.

    Returns:
        DataFrame of line items (rows) by fiscal period (columns), or
        None if the data is unavailable.
    """
    try:
        statement = ticker_obj.balance_sheet
        if statement is None or statement.empty:
            logger.warning("Balance sheet is empty for %s.", ticker_obj.ticker)
            return None
        return statement
    except Exception as exc:
        logger.warning("Failed to fetch balance sheet for %s: %s", ticker_obj.ticker, exc)
        return None


def get_cash_flow_statement(ticker_obj: yf.Ticker) -> Optional[pd.DataFrame]:
    """
    Fetch the annual cash flow statement for a company.

    Args:
        ticker_obj: A yfinance.Ticker instance.

    Returns:
        DataFrame of line items (rows) by fiscal period (columns), or
        None if the data is unavailable.
    """
    try:
        statement = ticker_obj.cashflow
        if statement is None or statement.empty:
            logger.warning("Cash flow statement is empty for %s.", ticker_obj.ticker)
            return None
        return statement
    except Exception as exc:
        logger.warning("Failed to fetch cash flow statement for %s: %s", ticker_obj.ticker, exc)
        return None


def get_current_price(ticker_obj: yf.Ticker) -> Optional[float]:
    """
    Fetch the current (or most recent) share price.

    Tries the lightweight `fast_info` accessor first, falling back to the
    fuller `info` dict if needed.

    Args:
        ticker_obj: A yfinance.Ticker instance.

    Returns:
        The current share price as a float, or None if unavailable.
    """
    try:
        price = ticker_obj.fast_info.get("last_price")
        if price:
            return float(price)
    except Exception as exc:
        logger.warning("fast_info price lookup failed for %s: %s", ticker_obj.ticker, exc)

    try:
        info = ticker_obj.info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price:
            return float(price)
    except Exception as exc:
        logger.warning("info price lookup failed for %s: %s", ticker_obj.ticker, exc)

    logger.warning("Could not retrieve current price for %s.", ticker_obj.ticker)
    return None


def get_shares_outstanding(ticker_obj: yf.Ticker) -> Optional[float]:
    """
    Fetch the number of shares outstanding.

    Args:
        ticker_obj: A yfinance.Ticker instance.

    Returns:
        Shares outstanding as a float, or None if unavailable.
    """
    try:
        shares = ticker_obj.fast_info.get("shares_outstanding")
        if shares:
            return float(shares)
    except Exception as exc:
        logger.warning("fast_info shares lookup failed for %s: %s", ticker_obj.ticker, exc)

    try:
        shares = ticker_obj.info.get("sharesOutstanding")
        if shares:
            return float(shares)
    except Exception as exc:
        logger.warning("info shares lookup failed for %s: %s", ticker_obj.ticker, exc)

    logger.warning("Could not retrieve shares outstanding for %s.", ticker_obj.ticker)
    return None


def get_beta(ticker_obj: yf.Ticker) -> Optional[float]:
    """
    Fetch the company's (levered) beta, as reported by Yahoo Finance.

    Args:
        ticker_obj: A yfinance.Ticker instance.

    Returns:
        Beta as a float, or None if unavailable.
    """
    try:
        beta = ticker_obj.info.get("beta")
        if beta is not None:
            return float(beta)
    except Exception as exc:
        logger.warning("Could not retrieve beta for %s: %s", ticker_obj.ticker, exc)
    return None


def get_sector(ticker_obj: yf.Ticker) -> str:
    """
    Fetch the company's GICS sector, as reported by Yahoo Finance.

    Args:
        ticker_obj: A yfinance.Ticker instance.

    Returns:
        The sector name, or "Unknown" if unavailable (rather than None,
        since callers typically use this as a grouping key).
    """
    try:
        sector = ticker_obj.info.get("sector")
        if sector:
            return sector
    except Exception as exc:
        logger.warning("Could not retrieve sector for %s: %s", ticker_obj.ticker, exc)
    return "Unknown"


def fetch_company_financials(ticker: str) -> dict:
    """
    Fetch every input the DCF model needs for a given ticker in one call.

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL").

    Returns:
        dict with keys:
            ticker (str)
            income_statement (pd.DataFrame | None)
            balance_sheet (pd.DataFrame | None)
            cash_flow (pd.DataFrame | None)
            current_price (float | None)
            shares_outstanding (float | None)
            beta (float | None)
        Any field that could not be retrieved is None rather than raising,
        so callers can decide how to handle gaps.
    """
    ticker_obj = get_ticker_object(ticker)
    return {
        "ticker": ticker_obj.ticker,
        "income_statement": get_income_statement(ticker_obj),
        "balance_sheet": get_balance_sheet(ticker_obj),
        "cash_flow": get_cash_flow_statement(ticker_obj),
        "current_price": get_current_price(ticker_obj),
        "shares_outstanding": get_shares_outstanding(ticker_obj),
        "beta": get_beta(ticker_obj),
    }


if __name__ == "__main__":
    data = fetch_company_financials("AAPL")

    print(f"Ticker: {data['ticker']}")
    print(f"Current Price: {data['current_price']}")
    print(f"Shares Outstanding: {data['shares_outstanding']}")
    print(f"Beta: {data['beta']}")

    print("\nIncome Statement:")
    print(data["income_statement"])

    print("\nBalance Sheet:")
    print(data["balance_sheet"])

    print("\nCash Flow Statement:")
    print(data["cash_flow"])
