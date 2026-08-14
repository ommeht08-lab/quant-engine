"""
Shared macroeconomic data helpers.

Centralizes fetches for market-wide inputs (currently just the risk-free
rate) so every consumer — the backtester, the live single-ticker API, and
the sector-median cache generator — can use the exact same value, rather
than each independently hitting yfinance and potentially picking up a
slightly different quote.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data_ingestion.fetch_financials import get_ticker_object
from src.utils.cache import cached

logger = logging.getLogger(__name__)

# Conservative fallback used only when a risk-free rate (^TNX) can't be
# fetched at all (live or historical).
DEFAULT_RISK_FREE_RATE_FALLBACK = 0.042

RISK_FREE_RATE_CACHE_TTL_SECONDS = 3600  # 1 hour
RISK_FREE_RATE_LOOKBACK_DAYS = 10  # calendar days to search back for a historical close


@cached(ttl_seconds=RISK_FREE_RATE_CACHE_TTL_SECONDS, prefix="risk_free_rate")
def get_risk_free_rate(as_of_date: Optional[str] = None) -> float:
    """
    Fetch the risk-free rate as the 10-Year Treasury Note yield (^TNX):
    the most recent close on or before `as_of_date` when given, otherwise
    today's latest close.

    ^TNX is quoted in yield points (e.g. 4.2 for 4.2%), so the resolved
    close is divided by 100 to get a decimal rate.

    Args:
        as_of_date: ISO date string (e.g. "2023-06-01"). When given, this
            looks back up to RISK_FREE_RATE_LOOKBACK_DAYS calendar days
            for the most recent close on or before it — the same
            point-in-time lookup pattern used elsewhere in this codebase
            (`historical_tester._get_price_on_or_before`) — so a
            historical backtest never picks up today's live rate for a
            date years in the past. `@cached`'s key includes this
            argument, so a historical date's answer and "today"'s answer
            are cached separately rather than colliding on one entry.
            Omit for the current/most recent live rate.

    Returns:
        The resolved ^TNX close as a decimal (e.g. 0.042), or
        DEFAULT_RISK_FREE_RATE_FALLBACK if the fetch fails or returns no
        usable close on/before the requested date.
    """
    ticker_obj = get_ticker_object("^TNX")

    try:
        if as_of_date is not None:
            as_of_ts = pd.Timestamp(as_of_date)
            start = (as_of_ts - pd.Timedelta(days=RISK_FREE_RATE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
            end = (as_of_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            history = ticker_obj.history(start=start, end=end)
        else:
            history = ticker_obj.history(period="5d")
    except Exception as exc:  # noqa: BLE001 - a missing macro input must not crash the caller
        logger.warning(
            "Risk-free rate lookup (^TNX, as_of_date=%s) failed: %s; falling back to %.1f%%.",
            as_of_date,
            exc,
            DEFAULT_RISK_FREE_RATE_FALLBACK * 100,
        )
        return DEFAULT_RISK_FREE_RATE_FALLBACK

    valid_history = history.dropna(subset=["Close"]) if history is not None else None
    if valid_history is None or valid_history.empty:
        logger.warning(
            "No valid ^TNX close found on/before %s; falling back to %.1f%%.",
            as_of_date or "today",
            DEFAULT_RISK_FREE_RATE_FALLBACK * 100,
        )
        return DEFAULT_RISK_FREE_RATE_FALLBACK

    return float(valid_history["Close"].iloc[-1]) / 100.0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Current risk-free rate: {get_risk_free_rate():.3%}")
    print(f"Risk-free rate as of 2023-06-01: {get_risk_free_rate(as_of_date='2023-06-01'):.3%}")
