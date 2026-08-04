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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data_ingestion.fetch_financials import get_ticker_object

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Conservative fallback used only when a live risk-free rate (^TNX) can't
# be fetched.
DEFAULT_RISK_FREE_RATE_FALLBACK = 0.042


def get_risk_free_rate() -> float:
    """
    Fetch the current risk-free rate as the latest 10-Year Treasury Note
    yield (^TNX), for use as CAPM's risk-free rate input to WACC.

    ^TNX is quoted in yield points (e.g. 4.2 for 4.2%), so the latest
    close is divided by 100 to get a decimal rate.

    Returns:
        The latest ^TNX close as a decimal (e.g. 0.042), or
        DEFAULT_RISK_FREE_RATE_FALLBACK if the fetch fails or returns no
        usable data.
    """
    try:
        history = get_ticker_object("^TNX").history(period="5d")
    except Exception as exc:
        logger.warning(
            "Risk-free rate lookup (^TNX) failed: %s; falling back to %.1f%%.",
            exc,
            DEFAULT_RISK_FREE_RATE_FALLBACK * 100,
        )
        return DEFAULT_RISK_FREE_RATE_FALLBACK

    valid_history = history.dropna(subset=["Close"]) if history is not None else None
    if valid_history is None or valid_history.empty:
        logger.warning(
            "No valid ^TNX close found; falling back to %.1f%%.", DEFAULT_RISK_FREE_RATE_FALLBACK * 100
        )
        return DEFAULT_RISK_FREE_RATE_FALLBACK

    return float(valid_history["Close"].iloc[-1]) / 100.0
