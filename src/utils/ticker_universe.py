"""
Shared S&P 500 top-100-by-market-cap ticker universe constant.

Extracted out of `src.backtesting.historical_tester` into its own
zero-`src.*`-dependency module so a consumer that only needs the ticker
list (the live valuation API's sector-median cache, in particular)
doesn't have to import the whole backtester module — which pulls in
`src.utils.db` (and therefore `psycopg2`) purely for an unrelated
feature (`log_backtest_curve`) that has nothing to do with this
constant. `src.backtesting.historical_tester` and
`src.trading.alpaca_execution` now both import this constant from here
rather than either defining or re-exporting it, so there remains exactly
one copy of the list and one implementation to keep in sync.
"""

# The 100 largest S&P 500 constituents by current market capitalization
# (ranked live via yfinance; membership/ranking drifts over time — see
# src.backtesting.historical_tester's module docstring for the
# survivorship-bias limitation this implies for backtesting). Only one
# share class per company is kept by default (e.g. GOOGL, not both
# GOOGL and GOOG) so a dual-class company doesn't silently get double
# weight in an equal-weighted Top-N portfolio; pass a different
# `tickers` list to `run_backtest` to deliberately include multiple
# classes.
DEFAULT_SP500_TOP_100_TICKERS = [
    "NVDA", "GOOGL", "AAPL", "MSFT", "AMZN", "AVGO", "META", "TSLA", "BRK-B",
    "LLY", "JPM", "MU", "WMT", "AMD", "V", "XOM", "JNJ", "MA", "INTC",
    "CSCO", "BAC", "ABBV", "COST", "AMAT", "ORCL", "CVX", "GE", "CAT", "UNH",
    "KO", "LRCX", "HD", "PG", "MS", "MRK", "NFLX", "GS", "PLTR", "PM",
    "RTX", "PANW", "DELL", "GEV", "WFC", "TXN", "KLAC", "ANET", "AXP", "C",
    "LIN", "TMO", "IBM", "CRWD", "AMGN", "APH", "VZ", "SNDK", "PEP", "TMUS",
    "STX", "MCD", "ABT", "BA", "SCHW", "WDC", "NEE", "ADI", "BLK", "TJX",
    "MRVL", "UNP", "DIS", "ETN", "WELL", "DE", "GILD", "T", "QCOM", "CRM",
    "BKNG", "UBER", "COP", "PFE", "DHR", "APP", "LMT", "PLD", "ISRG", "CVS",
    "CB", "BMY", "COF", "SYK", "GLW", "PH", "SPGI", "PGR", "FTNT", "VRTX",
]
