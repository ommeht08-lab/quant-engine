"""
Historical DCF backtesting engine.

Reconstructs point-in-time financial statements, market price, and share
count for a universe of tickers as of a past target date, runs each
through the existing `run_dcf_valuation` pipeline
(`src.dcf_model.dcf`) to derive a historical intrinsic value, ranks
tickers by a "Conviction Score", and measures how a theoretical portfolio
of the top-ranked names actually performed from that date to today versus
the S&P 500 (SPY).

Sector-Relative Valuation filter
----------------------------------
Rather than screening every ticker against one absolute P/IV threshold,
the backtester runs in two passes:

    Pass 1 — value every ticker in the universe (`compute_valuation`) and
    record its Price / Intrinsic Value (P/IV) ratio and GICS sector.
    Group P/IV ratios by sector and take each sector's median.

    Pass 2 — a ticker is only eligible to be scored and ranked
    (`score_ticker`) if its own P/IV is <= its sector's median P/IV
    within the current universe. This avoids uniformly excluding entire
    sectors that structurally trade at higher multiples (e.g.
    technology), since the bar is relative to sector peers rather than a
    fixed 1.0x cutoff.

Conviction Score
-----------------
    (Historical FCF Growth Rate * Historical ROIC) / (Historical Price / Historical Intrinsic Value)

Rewards companies that were compounding free cash flow, earning a high
return on invested capital, and trading cheaply relative to their
model-derived intrinsic value as of the target date.

Dynamic CAPM discount rate
----------------------------
`compute_valuation` overrides `DCFAssumptions.risk_free_rate` with a live
10-Year Treasury yield (`get_risk_free_rate`, ^TNX) before running the
DCF, rather than using `dcf.py`'s static default. This feeds directly
into `calculate_wacc`'s existing CAPM cost-of-equity leg
(`risk_free_rate + beta * market_risk_premium`) — WACC itself is
unchanged in shape, just fed a live rate instead of a fixed 4% assumption.
`calculate_wacc` also clamps the final WACC to [MIN_DISCOUNT_RATE,
MAX_DISCOUNT_RATE] (5%–20%) so a degenerate beta/rate combination can't
produce an economically nonsensical discount rate.

Known limitations (please read before trusting results)
---------------------------------------------------------
yfinance is not a true point-in-time data provider — it mirrors what
Yahoo Finance currently shows, which for annual financial statements is
only a **trailing ~4-5 fiscal years from today**, not from the target
date. If the target date is old enough that the required fiscal period
has already rolled out of that window, the ticker is skipped with an
explicit reason rather than silently substituting the wrong period.

Two additional approximations are used, both logged when triggered:
    - Beta: yfinance does not expose historical beta, so the current
      beta is used as a best-effort proxy for the target date.
    - Shares outstanding: pulled from Yahoo's historical shares
      timeseries (`Ticker.get_shares_full`) when available for the
      target date; falls back to the current share count otherwise.
      Because yfinance's price history is split-adjusted through today
      but this raw share count is not, it is scaled by any stock splits
      that occurred between the target date and today (see
      `_cumulative_split_factor_since`) so market cap / per-share
      figures stay internally consistent.
"""

import logging
import math
import statistics
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data_ingestion.fetch_financials import (
    get_balance_sheet,
    get_beta,
    get_cash_flow_statement,
    get_income_statement,
    get_sector,
    get_shares_outstanding,
    get_ticker_object,
)
from src.dcf_model.dcf import (
    DEFAULT_TAX_RATE,
    DCFAssumptions,
    _get_row_value,
    _most_recent_column,
    extract_valuation_inputs,
    run_dcf_valuation,
)
from src.utils.cache import cached
from src.utils.db import log_backtest_curve
from src.utils.macro import get_risk_free_rate

HISTORICAL_PRICE_CACHE_TTL_SECONDS = 86400  # 24 hours — a past close price never changes

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# The 100 largest S&P 500 constituents by current market capitalization
# (ranked live via yfinance; membership/ranking drifts over time).
DEFAULT_SP500_TOP_100_TICKERS = [
    "NVDA", "GOOGL", "GOOG", "AAPL", "MSFT", "AMZN", "AVGO", "META", "TSLA", "BRK-B",
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

DEFAULT_BENCHMARK_TICKER = "SPY"
DEFAULT_TOP_N = 10


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------

@dataclass
class ValuationResult:
    """
    Pass 1 output: point-in-time DCF valuation for a single ticker,
    before the sector-relative filter (Pass 2) is applied.

    Carries the point-in-time statements and resolved DCF inputs forward
    so Pass 2 can compute FCF growth / ROIC for survivors without
    re-fetching anything.
    """

    ticker: str
    as_of_date: str
    sector: str = "Unknown"
    historical_price: Optional[float] = None
    historical_intrinsic_value: Optional[float] = None
    price_to_intrinsic: Optional[float] = None
    wacc: Optional[float] = None
    beta: Optional[float] = None
    altman_z_score: Optional[float] = None
    income_stmt: Optional[pd.DataFrame] = field(default=None, repr=False)
    balance_sheet: Optional[pd.DataFrame] = field(default=None, repr=False)
    cash_flow: Optional[pd.DataFrame] = field(default=None, repr=False)
    tax_rate: Optional[float] = None
    total_debt: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    skip_reason: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """Whether Pass 1 produced a usable P/IV ratio for the sector filter."""
        return self.skip_reason is None and self.price_to_intrinsic is not None


@dataclass
class TickerAnalysis:
    """Final sector-relative valuation and Conviction Score for a single ticker."""

    ticker: str
    as_of_date: str
    sector: str = "Unknown"
    historical_price: Optional[float] = None
    historical_intrinsic_value: Optional[float] = None
    price_to_intrinsic: Optional[float] = None
    sector_median_price_to_intrinsic: Optional[float] = None
    wacc: Optional[float] = None
    beta: Optional[float] = None
    altman_z_score: Optional[float] = None
    fcf_growth_rate: Optional[float] = None
    roic: Optional[float] = None
    conviction_score: Optional[float] = None
    skip_reason: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """Whether this ticker produced a usable Conviction Score."""
        return self.skip_reason is None and self.conviction_score is not None


@dataclass
class TickerPerformance:
    """Actual price performance of a ticker from the target date to today."""

    ticker: str
    entry_price: float
    entry_date: str
    exit_price: float
    exit_date: str
    total_return: float  # decimal, e.g. 0.42 == +42%


@dataclass
class BacktestResult:
    """Full output of a historical DCF backtest run."""

    as_of_date: str
    analyses: List[TickerAnalysis] = field(default_factory=list)
    top_picks: List[TickerAnalysis] = field(default_factory=list)
    performance: List[TickerPerformance] = field(default_factory=list)
    portfolio_return: Optional[float] = None
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER
    benchmark_return: Optional[float] = None
    alpha: Optional[float] = None
    sector_medians: Dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Point-in-time helpers
# --------------------------------------------------------------------------

def _to_naive(value) -> pd.Timestamp:
    """Normalize any timestamp-like value to a timezone-naive pd.Timestamp."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts


def _columns_on_or_before(
    statement: Optional[pd.DataFrame], as_of_ts: pd.Timestamp
) -> Optional[pd.DataFrame]:
    """
    Restrict a financial statement DataFrame to only the fiscal periods
    (columns) that ended on or before the target date, so downstream
    consumers can never see future data.

    Args:
        statement: A statement DataFrame as returned by yfinance, or None.
        as_of_ts: The point-in-time cutoff.

    Returns:
        A DataFrame containing only eligible columns, or None if the
        statement is unavailable or no period ended on/before the cutoff.
    """
    if statement is None or statement.empty:
        return None

    as_of_naive = _to_naive(as_of_ts)
    valid_columns = []
    for column in statement.columns:
        try:
            if _to_naive(column) <= as_of_naive:
                valid_columns.append(column)
        except (TypeError, ValueError):
            continue

    if not valid_columns:
        return None
    return statement[valid_columns]


@cached(ttl_seconds=HISTORICAL_PRICE_CACHE_TTL_SECONDS, prefix="price_on_or_before")
def _get_price_on_or_before(
    ticker_obj, as_of_ts: pd.Timestamp, lookback_days: int = 10
) -> Optional[Tuple[float, pd.Timestamp]]:
    """
    Fetch the most recent closing price on or before a target date.

    Looks back up to `lookback_days` calendar days to land on a trading
    day even if the target date falls on a weekend or market holiday.
    yfinance occasionally returns a `NaN` Close for the most recent bar
    (e.g. an unsettled/incomplete session very close to "today"), so rows
    with a missing Close are dropped before taking the last one, rather
    than blindly trusting the final row.

    Cached for 24h, keyed by (ticker, as_of_ts, lookback_days): for a
    genuine past `as_of_ts` (backtesting) the close price never changes,
    so this is a pure win. For `as_of_ts = today` (the live trading
    engine reusing this same point-in-time machinery — see this module's
    docstring), a same-day re-run within the TTL window will reuse that
    day's first-fetched price rather than a fresher intraday one; this
    is an accepted tradeoff since it's a soft valuation reference, not
    the authoritative trade execution price (Alpaca's own fill price is).

    Returns:
        (close_price, trading_date) tuple, or None if unavailable.
    """
    start = (as_of_ts - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end = (as_of_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        history = ticker_obj.history(start=start, end=end)
    except Exception as exc:
        logger.warning("Price history lookup failed for %s: %s", ticker_obj.ticker, exc)
        return None

    if history is None or history.empty:
        return None

    valid_history = history.dropna(subset=["Close"])
    if valid_history.empty:
        return None

    last_row = valid_history.iloc[-1]
    return float(last_row["Close"]), valid_history.index[-1]


def get_historical_price(ticker_obj, as_of_ts: pd.Timestamp) -> Optional[float]:
    """Convenience wrapper around `_get_price_on_or_before` returning just the price."""
    result = _get_price_on_or_before(ticker_obj, as_of_ts)
    return result[0] if result else None


def _cumulative_split_factor_since(ticker_obj, as_of_ts: pd.Timestamp) -> float:
    """
    Product of all stock-split ratios that occurred strictly after
    `as_of_ts` (e.g. 10.0 for a subsequent 10-for-1 split).

    yfinance's price history is split-adjusted using every split up to
    today, so a *true* historical share count (which reflects the share
    count that actually existed on `as_of_ts`, before any later splits)
    must be scaled by this same factor to stay on a consistent basis with
    that price series for market cap / per-share calculations.
    """
    try:
        splits = ticker_obj.splits
    except Exception as exc:
        logger.warning("Split history lookup failed for %s: %s", ticker_obj.ticker, exc)
        return 1.0

    if splits is None or splits.empty:
        return 1.0

    as_of_naive = _to_naive(as_of_ts)
    factor = 1.0
    for split_date, ratio in splits.items():
        if ratio and _to_naive(split_date) > as_of_naive:
            factor *= float(ratio)
    return factor


def get_historical_shares_outstanding(ticker_obj, as_of_ts: pd.Timestamp) -> Optional[float]:
    """
    Fetch shares outstanding as of a target date, on the same split-
    adjusted basis as `_get_price_on_or_before`'s price series.

    Uses Yahoo's historical shares-outstanding timeseries
    (`get_shares_full`) when it covers the target date — scaled by any
    stock splits that happened between the target date and today, since
    yfinance's price history is split-adjusted through today but a raw
    historical share count is not — and falls back to the current share
    count (already on today's basis, so left unscaled) otherwise.

    Args:
        ticker_obj: A yfinance.Ticker instance.
        as_of_ts: The point-in-time cutoff.

    Returns:
        Shares outstanding as a float, or None if no value is available
        at all (including the current-share-count fallback).
    """
    as_of_naive = _to_naive(as_of_ts)
    lookback_start = (as_of_ts - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    lookback_end = (as_of_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        shares_series = ticker_obj.get_shares_full(start=lookback_start, end=lookback_end)
    except Exception as exc:
        logger.warning("Historical shares lookup failed for %s: %s", ticker_obj.ticker, exc)
        shares_series = None

    if shares_series is not None and not shares_series.empty:
        eligible = shares_series[[_to_naive(idx) <= as_of_naive for idx in shares_series.index]]
        if not eligible.empty:
            raw_shares = float(eligible.iloc[-1])
            split_factor = _cumulative_split_factor_since(ticker_obj, as_of_ts)
            return raw_shares * split_factor

    logger.warning(
        "No historical share count found for %s on/before %s; falling back to the "
        "current share count as an approximation.",
        ticker_obj.ticker,
        as_of_naive.date(),
    )
    return get_shares_outstanding(ticker_obj)


# --------------------------------------------------------------------------
# Fundamental metrics: FCF growth and ROIC
# --------------------------------------------------------------------------

def _get_free_cash_flow(cash_flow: pd.DataFrame, column) -> Optional[float]:
    """Read (or derive) Free Cash Flow for a single period of a cash flow statement."""
    direct = _get_row_value(cash_flow, ["Free Cash Flow", "FreeCashFlow"], column=column)
    if direct is not None:
        return direct

    operating_cf = _get_row_value(
        cash_flow,
        [
            "Operating Cash Flow",
            "Cash Flow From Continuing Operating Activities",
            "Total Cash From Operating Activities",
        ],
        column=column,
    )
    capex = _get_row_value(
        cash_flow, ["Capital Expenditure", "CapitalExpenditure", "Purchase Of PPE"], column=column
    )
    if operating_cf is None or capex is None:
        return None
    return operating_cf + capex  # yfinance reports CapEx as a negative outflow


def calculate_fcf_growth_rate(cash_flow: Optional[pd.DataFrame]) -> Optional[float]:
    """
    Calculate year-over-year Free Cash Flow growth using the two most
    recent periods available on or before the target date.

    Args:
        cash_flow: Cash flow statement already restricted to periods
            on/before the target date (see `_columns_on_or_before`).

    Returns:
        Growth rate as a decimal (e.g. 0.15 == +15%), or None if fewer
        than two periods are available or the base period's FCF is not
        positive (a growth rate off a non-positive base is not
        meaningful).
    """
    if cash_flow is None or cash_flow.empty or cash_flow.shape[1] < 2:
        return None

    sorted_columns = sorted(cash_flow.columns, key=_to_naive, reverse=True)
    latest_column, prior_column = sorted_columns[0], sorted_columns[1]

    latest_fcf = _get_free_cash_flow(cash_flow, latest_column)
    prior_fcf = _get_free_cash_flow(cash_flow, prior_column)

    if latest_fcf is None or prior_fcf is None or prior_fcf <= 0:
        return None

    return (latest_fcf / prior_fcf) - 1


def calculate_roic(
    income_stmt: pd.DataFrame,
    balance_sheet: pd.DataFrame,
    tax_rate: float,
    total_debt: Optional[float],
    cash_and_equivalents: Optional[float],
) -> Optional[float]:
    """
    Calculate Return on Invested Capital (ROIC) for the most recent
    period on or before the target date.

        NOPAT = EBIT * (1 - tax_rate)
        Invested Capital = Total Debt + Stockholders' Equity - Cash & Equivalents
        ROIC = NOPAT / Invested Capital

    Args:
        income_stmt: Income statement restricted to periods on/before
            the target date.
        balance_sheet: Balance sheet restricted to periods on/before the
            target date.
        tax_rate: Effective tax rate to apply to EBIT.
        total_debt: Total interest-bearing debt (0 if missing).
        cash_and_equivalents: Cash and cash equivalents (0 if missing).

    Returns:
        ROIC as a decimal, or None if EBIT/equity can't be determined or
        invested capital is not positive.
    """
    income_column = _most_recent_column(income_stmt)
    balance_column = _most_recent_column(balance_sheet)

    ebit = _get_row_value(income_stmt, ["EBIT", "Ebit"], column=income_column)
    if ebit is None:
        ebit = _get_row_value(income_stmt, ["Operating Income", "OperatingIncome"], column=income_column)
    if ebit is None:
        return None

    equity = _get_row_value(
        balance_sheet,
        [
            "Stockholders Equity",
            "Common Stock Equity",
            "Total Stockholder Equity",
            "Total Equity Gross Minority Interest",
        ],
        column=balance_column,
    )
    if equity is None:
        return None

    invested_capital = (total_debt or 0.0) + equity - (cash_and_equivalents or 0.0)
    if invested_capital <= 0:
        return None

    nopat = ebit * (1 - tax_rate)
    return nopat / invested_capital


# --------------------------------------------------------------------------
# Pass 1: per-ticker valuation
# --------------------------------------------------------------------------

def compute_valuation(ticker: str, as_of_date: str, assumptions: DCFAssumptions) -> ValuationResult:
    """
    Pass 1: reconstruct point-in-time financials for a ticker, run the
    DCF, and compute its Price / Intrinsic Value (P/IV) ratio and sector.

    Stops short of the sector-relative filter and Conviction Score, since
    the sector median P/IV isn't known until every ticker in the universe
    has been valued (see `calculate_sector_median_price_to_intrinsic`).

    Args:
        ticker: Stock ticker symbol.
        as_of_date: Target date, e.g. "2024-08-01". Only data dated on or
            before this date is used.
        assumptions: DCF assumptions (growth, margin, terminal growth)
            applied to the point-in-time financials.

    Returns:
        A ValuationResult. If any required input is unavailable, the
        result carries a `skip_reason` instead of raising.
    """
    ticker_obj = get_ticker_object(ticker)
    as_of_ts = pd.Timestamp(as_of_date)
    sector = get_sector(ticker_obj)

    income_stmt = _columns_on_or_before(get_income_statement(ticker_obj), as_of_ts)
    balance_sheet = _columns_on_or_before(get_balance_sheet(ticker_obj), as_of_ts)
    cash_flow = _columns_on_or_before(get_cash_flow_statement(ticker_obj), as_of_ts)

    if income_stmt is None or balance_sheet is None:
        return ValuationResult(
            ticker=ticker,
            as_of_date=as_of_date,
            sector=sector,
            skip_reason=(
                f"No income statement/balance sheet available on or before {as_of_date} "
                "(yfinance's trailing statement window doesn't reach that far back)."
            ),
        )

    historical_price = get_historical_price(ticker_obj, as_of_ts)
    if historical_price is None or not math.isfinite(historical_price):
        return ValuationResult(
            ticker=ticker,
            as_of_date=as_of_date,
            sector=sector,
            skip_reason=f"No valid market price found on or before {as_of_date}.",
        )

    historical_shares = get_historical_shares_outstanding(ticker_obj, as_of_ts)
    if historical_shares is None:
        return ValuationResult(
            ticker=ticker, as_of_date=as_of_date, sector=sector,
            skip_reason="Shares outstanding unavailable.",
        )

    # yfinance has no historical beta endpoint; current beta is used as a
    # best-effort proxy and is logged as such by get_beta's caller here.
    beta = get_beta(ticker_obj)

    financial_data = {
        "ticker": ticker_obj.ticker,
        "income_statement": income_stmt,
        "balance_sheet": balance_sheet,
        "cash_flow": cash_flow,
        "current_price": historical_price,
        "shares_outstanding": historical_shares,
        "beta": beta,
    }

    # Use a live CAPM risk-free rate (10Y Treasury yield) for WACC's cost-of-
    # equity leg instead of DCFAssumptions' static default, without forking
    # calculate_wacc()'s existing (already CAPM-based) logic.
    assumptions = replace(assumptions, risk_free_rate=get_risk_free_rate())

    try:
        dcf_result = run_dcf_valuation(financial_data, assumptions)
    except ValueError as exc:
        return ValuationResult(
            ticker=ticker, as_of_date=as_of_date, sector=sector,
            skip_reason=f"DCF valuation failed: {exc}",
        )

    historical_intrinsic_value = dcf_result["intrinsic_value_per_share"]
    if not math.isfinite(historical_intrinsic_value) or historical_intrinsic_value <= 0:
        return ValuationResult(
            ticker=ticker,
            as_of_date=as_of_date,
            sector=sector,
            skip_reason="Historical intrinsic value is not positive or is not a finite number.",
        )

    price_to_intrinsic = historical_price / historical_intrinsic_value
    if not math.isfinite(price_to_intrinsic):
        return ValuationResult(
            ticker=ticker,
            as_of_date=as_of_date,
            sector=sector,
            skip_reason="Price-to-intrinsic-value ratio is not a finite number.",
        )

    inputs = extract_valuation_inputs(financial_data)
    tax_rate = inputs["tax_rate"] if inputs["tax_rate"] is not None else DEFAULT_TAX_RATE

    return ValuationResult(
        ticker=ticker,
        as_of_date=as_of_date,
        sector=sector,
        historical_price=historical_price,
        historical_intrinsic_value=historical_intrinsic_value,
        price_to_intrinsic=price_to_intrinsic,
        wacc=dcf_result["wacc"],
        beta=beta,
        income_stmt=income_stmt,
        balance_sheet=balance_sheet,
        cash_flow=cash_flow,
        tax_rate=tax_rate,
        total_debt=inputs["total_debt"],
        cash_and_equivalents=inputs["cash_and_equivalents"],
    )


def calculate_sector_median_price_to_intrinsic(valuations: List[ValuationResult]) -> Dict[str, float]:
    """
    Group Pass 1 P/IV ratios by sector and compute each sector's median,
    based on the tickers actually present in the current universe.

    Args:
        valuations: ValuationResult list from Pass 1. Only entries with a
            valid P/IV (`is_valid`) contribute to their sector's median.

    Returns:
        dict mapping sector name -> median P/IV ratio. Sectors with no
        valid valuations in this universe are simply absent.
    """
    ratios_by_sector: Dict[str, List[float]] = {}
    for valuation in valuations:
        if valuation.is_valid:
            ratios_by_sector.setdefault(valuation.sector, []).append(valuation.price_to_intrinsic)

    return {sector: statistics.median(ratios) for sector, ratios in ratios_by_sector.items()}


# --------------------------------------------------------------------------
# Pass 2: sector-relative filter + Conviction Score
# --------------------------------------------------------------------------

def score_ticker(
    valuation: ValuationResult, sector_medians: Dict[str, float]
) -> TickerAnalysis:
    """
    Pass 2: apply the sector-relative valuation filter and, for
    survivors, compute FCF growth, ROIC, and the Conviction Score.

    A ticker is only eligible to be scored if its P/IV is less than or
    equal to its own sector's median P/IV within the current universe.

    Args:
        valuation: Pass 1 ValuationResult for a single ticker.
        sector_medians: dict from `calculate_sector_median_price_to_intrinsic`.

    Returns:
        A TickerAnalysis. If the ticker failed Pass 1, fails the
        sector-relative filter, or fails any downstream calculation, it
        carries a `skip_reason` instead of a Conviction Score.
    """
    if not valuation.is_valid:
        return TickerAnalysis(
            ticker=valuation.ticker,
            as_of_date=valuation.as_of_date,
            sector=valuation.sector,
            skip_reason=valuation.skip_reason,
        )

    sector_median = sector_medians.get(valuation.sector)
    if sector_median is None:
        return TickerAnalysis(
            ticker=valuation.ticker,
            as_of_date=valuation.as_of_date,
            sector=valuation.sector,
            historical_price=valuation.historical_price,
            historical_intrinsic_value=valuation.historical_intrinsic_value,
            price_to_intrinsic=valuation.price_to_intrinsic,
            wacc=valuation.wacc,
            skip_reason=f"No sector median P/IV available for sector '{valuation.sector}'.",
        )

    if valuation.price_to_intrinsic > sector_median:
        return TickerAnalysis(
            ticker=valuation.ticker,
            as_of_date=valuation.as_of_date,
            sector=valuation.sector,
            historical_price=valuation.historical_price,
            historical_intrinsic_value=valuation.historical_intrinsic_value,
            price_to_intrinsic=valuation.price_to_intrinsic,
            sector_median_price_to_intrinsic=sector_median,
            wacc=valuation.wacc,
            skip_reason=(
                f"Failed sector-relative filter: P/IV of {valuation.price_to_intrinsic:.2f}x "
                f"exceeds the {valuation.sector} sector median of {sector_median:.2f}x."
            ),
        )

    fcf_growth_rate = calculate_fcf_growth_rate(valuation.cash_flow)
    if fcf_growth_rate is None:
        return TickerAnalysis(
            ticker=valuation.ticker,
            as_of_date=valuation.as_of_date,
            sector=valuation.sector,
            historical_price=valuation.historical_price,
            historical_intrinsic_value=valuation.historical_intrinsic_value,
            price_to_intrinsic=valuation.price_to_intrinsic,
            sector_median_price_to_intrinsic=sector_median,
            wacc=valuation.wacc,
            skip_reason="Could not compute historical FCF growth rate (insufficient cash flow history).",
        )

    roic = calculate_roic(
        valuation.income_stmt,
        valuation.balance_sheet,
        tax_rate=valuation.tax_rate,
        total_debt=valuation.total_debt,
        cash_and_equivalents=valuation.cash_and_equivalents,
    )
    if roic is None:
        return TickerAnalysis(
            ticker=valuation.ticker,
            as_of_date=valuation.as_of_date,
            sector=valuation.sector,
            historical_price=valuation.historical_price,
            historical_intrinsic_value=valuation.historical_intrinsic_value,
            price_to_intrinsic=valuation.price_to_intrinsic,
            sector_median_price_to_intrinsic=sector_median,
            wacc=valuation.wacc,
            skip_reason="Could not compute ROIC (missing EBIT or stockholders' equity).",
        )

    conviction_score = (fcf_growth_rate * roic) / valuation.price_to_intrinsic

    return TickerAnalysis(
        ticker=valuation.ticker,
        as_of_date=valuation.as_of_date,
        sector=valuation.sector,
        historical_price=valuation.historical_price,
        historical_intrinsic_value=valuation.historical_intrinsic_value,
        price_to_intrinsic=valuation.price_to_intrinsic,
        sector_median_price_to_intrinsic=sector_median,
        wacc=valuation.wacc,
        beta=valuation.beta,
        altman_z_score=valuation.altman_z_score,
        fcf_growth_rate=fcf_growth_rate,
        roic=roic,
        conviction_score=conviction_score,
    )


# --------------------------------------------------------------------------
# Forward performance
# --------------------------------------------------------------------------

def get_forward_performance(ticker: str, as_of_date: str) -> Optional[TickerPerformance]:
    """
    Measure actual price performance of a ticker from the target date to
    the most recent available trading day.

    Args:
        ticker: Stock ticker symbol.
        as_of_date: Target (entry) date.

    Returns:
        A TickerPerformance, or None if entry/exit prices can't be found.
    """
    ticker_obj = get_ticker_object(ticker)
    as_of_ts = pd.Timestamp(as_of_date)

    entry = _get_price_on_or_before(ticker_obj, as_of_ts)
    if entry is None:
        logger.warning("No entry price found for %s on/before %s.", ticker, as_of_date)
        return None
    entry_price, entry_date = entry

    try:
        recent_history = ticker_obj.history(period="5d")
    except Exception as exc:
        logger.warning("Failed to fetch recent price history for %s: %s", ticker, exc)
        return None

    if recent_history is None or recent_history.empty:
        logger.warning("No recent price history available for %s.", ticker)
        return None

    valid_recent_history = recent_history.dropna(subset=["Close"])
    if valid_recent_history.empty:
        logger.warning("No recent trading day with a valid Close found for %s.", ticker)
        return None

    exit_price = float(valid_recent_history["Close"].iloc[-1])
    exit_date = valid_recent_history.index[-1]

    total_return = (exit_price / entry_price) - 1

    return TickerPerformance(
        ticker=ticker_obj.ticker,
        entry_price=entry_price,
        entry_date=str(_to_naive(entry_date).date()),
        exit_price=exit_price,
        exit_date=str(_to_naive(exit_date).date()),
        total_return=total_return,
    )


def _monthly_close_series(ticker: str, as_of_ts: pd.Timestamp, today_ts: pd.Timestamp) -> Optional[pd.Series]:
    """Monthly Close price series for `ticker` from `as_of_ts` to `today_ts`, or None if unavailable."""
    try:
        ticker_obj = get_ticker_object(ticker)
        history = ticker_obj.history(
            start=as_of_ts.strftime("%Y-%m-%d"),
            end=(today_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="1mo",
        )
    except Exception as exc:  # noqa: BLE001 - one bad ticker shouldn't kill the whole curve
        logger.warning("Equity curve price history failed for %s: %s", ticker, exc)
        return None

    if history is None or history.empty:
        return None

    closes = history["Close"].dropna()
    if closes.empty:
        return None

    closes.index = [_to_naive(idx) for idx in closes.index]
    return closes


def build_equity_curve(
    top_picks: List[TickerAnalysis],
    as_of_date: str,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    start_value: float = 100_000.0,
) -> Tuple[List[str], List[float], List[float]]:
    """
    Build a monthly, equal-weighted equity curve for the Top-N portfolio
    (buy-and-hold from `as_of_date`, no rebalancing) alongside a
    same-starting-value `benchmark_ticker` curve, both normalized to
    start at `start_value`.

    Args:
        top_picks: The backtest's Top-N TickerAnalysis picks.
        as_of_date: The portfolio's entry date, e.g. "2024-08-01".
        benchmark_ticker: Ticker to build the comparison curve from.
        start_value: Starting notional value for both curves.

    Returns:
        (dates, strategy_values, spy_values) — three same-length lists,
        one point per month common to every pick and the benchmark. All
        three lists are empty if no common monthly date could be found
        (e.g. every ticker's price history failed to load).
    """
    tickers = [pick.ticker for pick in top_picks]
    if not tickers:
        return [], [], []

    as_of_ts = pd.Timestamp(as_of_date)
    today_ts = pd.Timestamp.today().normalize()

    per_ticker_series: Dict[str, pd.Series] = {}
    for ticker in tickers:
        series = _monthly_close_series(ticker, as_of_ts, today_ts)
        if series is not None:
            per_ticker_series[ticker] = series

    benchmark_series = _monthly_close_series(benchmark_ticker, as_of_ts, today_ts)
    if not per_ticker_series or benchmark_series is None:
        return [], [], []

    common_dates = sorted(
        set.intersection(*(set(series.index) for series in per_ticker_series.values()))
        & set(benchmark_series.index)
    )
    if not common_dates:
        return [], [], []

    per_ticker_weight = start_value / len(per_ticker_series)
    # `.loc`/`.iloc` on a pandas Series yield numpy.float64, which psycopg2
    # cannot adapt directly — cast to native Python floats for the DB layer.
    strategy_values = [
        float(
            sum(
                per_ticker_weight * (series.loc[date] / series.iloc[0])
                for series in per_ticker_series.values()
            )
        )
        for date in common_dates
    ]

    benchmark_entry = benchmark_series.iloc[0]
    spy_values = [
        float(start_value * (benchmark_series.loc[date] / benchmark_entry)) for date in common_dates
    ]

    dates = [str(date.date()) for date in common_dates]
    return dates, strategy_values, spy_values


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run_backtest(
    tickers: List[str],
    as_of_date: str,
    top_n: int = DEFAULT_TOP_N,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    assumptions: Optional[DCFAssumptions] = None,
) -> BacktestResult:
    """
    Run the full historical DCF backtest end-to-end, in two passes.

    Pass 1: value every ticker (`compute_valuation`) and record its P/IV
    ratio and sector. Pass 2: apply the sector-relative filter and
    calculate a Conviction Score for survivors (`score_ticker`). Rank by
    Conviction Score, take the top N, and measure their actual price
    performance from `as_of_date` to today against `benchmark_ticker`.

    Args:
        tickers: Universe of ticker symbols to evaluate.
        as_of_date: Target date, e.g. "2024-08-01".
        top_n: Number of top-ranked tickers to carry into the
            theoretical portfolio.
        benchmark_ticker: Ticker to compare portfolio performance against.
        assumptions: DCF assumptions applied uniformly to every ticker.
            Defaults to DCFAssumptions() if omitted.

    Returns:
        A BacktestResult with every analysis, the sector median P/IVs,
        the top picks, their forward performance, and the
        portfolio-vs-benchmark comparison.
    """
    assumptions = assumptions or DCFAssumptions()

    # Pass 1: value every ticker and compute its P/IV ratio + sector.
    valuations: List[ValuationResult] = []
    for ticker in tickers:
        try:
            valuation = compute_valuation(ticker, as_of_date, assumptions)
        except Exception as exc:  # noqa: BLE001 - never let one bad ticker kill the run
            logger.warning("Unexpected failure valuing %s: %s", ticker, exc)
            valuation = ValuationResult(
                ticker=ticker, as_of_date=as_of_date, skip_reason=f"Unexpected error: {exc}"
            )
        valuations.append(valuation)

    sector_medians = calculate_sector_median_price_to_intrinsic(valuations)

    # Pass 2: apply the sector-relative filter and score survivors.
    analyses: List[TickerAnalysis] = [score_ticker(v, sector_medians) for v in valuations]

    ranked = sorted(
        (a for a in analyses if a.is_valid),
        key=lambda a: a.conviction_score,
        reverse=True,
    )
    top_picks = ranked[:top_n]

    performance: List[TickerPerformance] = []
    for pick in top_picks:
        perf = get_forward_performance(pick.ticker, as_of_date)
        if perf is not None:
            performance.append(perf)

    portfolio_return = (
        sum(p.total_return for p in performance) / len(performance) if performance else None
    )

    dates, strategy_values, spy_values = build_equity_curve(top_picks, as_of_date, benchmark_ticker)
    if dates:
        try:
            log_backtest_curve(dates, strategy_values, spy_values)
        except Exception as exc:  # noqa: BLE001 - telemetry must never block the backtest
            logger.warning("Failed to log backtest equity curve to Postgres: %s", exc)

    benchmark_perf = get_forward_performance(benchmark_ticker, as_of_date)
    benchmark_return = benchmark_perf.total_return if benchmark_perf else None

    alpha = (
        portfolio_return - benchmark_return
        if portfolio_return is not None and benchmark_return is not None
        else None
    )

    return BacktestResult(
        as_of_date=as_of_date,
        analyses=analyses,
        top_picks=top_picks,
        performance=performance,
        portfolio_return=portfolio_return,
        benchmark_ticker=benchmark_ticker,
        benchmark_return=benchmark_return,
        alpha=alpha,
        sector_medians=sector_medians,
    )


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def print_summary(result: BacktestResult) -> None:
    """Print a clean, human-readable summary of a BacktestResult to stdout."""
    valid_count = sum(1 for a in result.analyses if a.is_valid)

    print("=" * 88)
    print(f"HISTORICAL DCF BACKTEST — as of {result.as_of_date}")
    print("=" * 88)
    print(f"\nEvaluated {len(result.analyses)} tickers; {valid_count} produced a valid Conviction Score.")

    if result.sector_medians:
        print("\nSector Median P/IV (Pass 1, current universe):")
        for sector, median in sorted(result.sector_medians.items(), key=lambda kv: kv[1]):
            print(f"  - {sector}: {median:.2f}x")

    skipped = [a for a in result.analyses if not a.is_valid]
    if skipped:
        print("\nSkipped tickers:")
        for analysis in skipped:
            print(f"  - {analysis.ticker}: {analysis.skip_reason}")

    print(f"\nTop {len(result.top_picks)} by Conviction Score:")
    if result.top_picks:
        header = (
            f"{'Ticker':<8}{'Sector':<24}{'Conviction':>11}{'FCF Growth':>12}"
            f"{'ROIC':>8}{'Price':>10}{'Intrinsic':>11}{'P/IV':>8}{'Sector Med':>12}"
        )
        print(header)
        print("-" * len(header))
        for analysis in result.top_picks:
            print(
                f"{analysis.ticker:<8}{analysis.sector:<24}{analysis.conviction_score:>11.3f}"
                f"{analysis.fcf_growth_rate:>11.1%}{analysis.roic:>8.1%}"
                f"{analysis.historical_price:>10,.2f}{analysis.historical_intrinsic_value:>11,.2f}"
                f"{analysis.price_to_intrinsic:>7.2f}x{analysis.sector_median_price_to_intrinsic:>11.2f}x"
            )
    else:
        print("  (none — no ticker produced a usable Conviction Score)")

    print(f"\nForward Performance ({result.as_of_date} → today):")
    if result.performance:
        header = f"{'Ticker':<8}{'Entry Date':>12}{'Entry $':>12}{'Exit Date':>12}{'Exit $':>12}{'Return':>10}"
        print(header)
        print("-" * len(header))
        for perf in result.performance:
            print(
                f"{perf.ticker:<8}{perf.entry_date:>12}{perf.entry_price:>12,.2f}"
                f"{perf.exit_date:>12}{perf.exit_price:>12,.2f}{perf.total_return:>10.1%}"
            )
    else:
        print("  (no performance data available)")

    print()
    if result.portfolio_return is not None:
        print(
            f"Theoretical Portfolio Return (equal-weighted, top {len(result.top_picks)}): "
            f"{result.portfolio_return:+.1%}"
        )
    else:
        print("Theoretical Portfolio Return: N/A (no valid picks)")

    if result.benchmark_return is not None:
        print(f"{result.benchmark_ticker} Benchmark Return over same period: {result.benchmark_return:+.1%}")
    else:
        print(f"{result.benchmark_ticker} Benchmark Return: N/A")

    if result.alpha is not None:
        print(f"Alpha vs. {result.benchmark_ticker}: {result.alpha:+.1%}")

    print("=" * 88)


if __name__ == "__main__":
    backtest_result = run_backtest(
        tickers=DEFAULT_SP500_TOP_100_TICKERS,
        as_of_date="2024-08-01",
        top_n=DEFAULT_TOP_N,
        benchmark_ticker=DEFAULT_BENCHMARK_TICKER,
    )
    print_summary(backtest_result)
