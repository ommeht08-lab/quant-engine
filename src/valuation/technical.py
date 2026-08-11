"""
Technical momentum utilities.

Currently implements the 14-day Relative Strength Index (RSI) using
Wilder's Smoothing Method (the original, standard RSI formulation — an
exponential moving average of gains/losses seeded by a simple average of
the first `period` observations, not a plain rolling/simple moving
average). Used by `src.trading.alpaca_execution` as a pre-trade "micro-dip"
entry gate: a ticker is only bought while it's technically oversold/cooling
off, not while it's already hot.

The underlying price history fetch is wrapped in the existing Upstash
Redis caching layer (`src.utils.cache.cached`) with a 1-hour TTL — short
enough to stay reasonably fresh for an intraday momentum signal, long
enough to absorb repeated calls (e.g. a dry run immediately followed by a
live run) without re-hitting Yahoo Finance and risking rate limits.
"""

import logging
import math
from typing import Optional

from src.data_ingestion.fetch_financials import get_ticker_object
from src.utils.cache import cached

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

RSI_HISTORY_CACHE_TTL_SECONDS = 3600  # 1 hour
RSI_HISTORY_PERIOD = "3mo"  # ~63 trading days — comfortably covers the 60-day minimum
DEFAULT_RSI_PERIOD = 14


@cached(ttl_seconds=RSI_HISTORY_CACHE_TTL_SECONDS, prefix="rsi_history")
def _get_recent_price_history(ticker_obj):
    """Fetch ~3 months of daily price history for RSI, or None on failure/empty."""
    try:
        history = ticker_obj.history(period=RSI_HISTORY_PERIOD)
    except Exception as exc:  # noqa: BLE001 - a missing price signal must not crash the scan
        logger.warning("RSI price history fetch failed for %s: %s", ticker_obj.ticker, exc)
        return None

    if history is None or history.empty:
        return None
    return history


def calculate_rsi(ticker_symbol: str, period: int = DEFAULT_RSI_PERIOD) -> Optional[float]:
    """
    Calculate the most recent `period`-day RSI using Wilder's Smoothing
    Method.

        avg_gain_0 = mean(gains[:period])   avg_loss_0 = mean(losses[:period])
        avg_gain_i = (avg_gain_{i-1} * (period - 1) + gain_i) / period   (i > 0)
        avg_loss_i analogous
        RS  = avg_gain / avg_loss
        RSI = 100 - 100 / (1 + RS)

    Args:
        ticker_symbol: Stock ticker symbol, e.g. "AAPL".
        period: Look-back window for the smoothing (default 14).

    Returns:
        The most recent RSI as a native Python float (0-100), or None if
        insufficient/unusable price history is available (fails safe,
        the same posture as the Altman Z-Score and 200-day SMA gates) or
        the computed value is not finite. A period with zero average loss
        (uninterrupted gains) returns 100.0.
    """
    try:
        ticker_obj = get_ticker_object(ticker_symbol)
    except ValueError as exc:
        logger.warning("Invalid ticker for RSI: %s", exc)
        return None

    history = _get_recent_price_history(ticker_obj)
    if history is None:
        return None

    closes = history["Close"].dropna()
    if len(closes) < period + 1:
        logger.warning(
            "Insufficient price history for %s RSI (%d day(s) available, need >= %d).",
            ticker_symbol,
            len(closes),
            period + 1,
        )
        return None

    changes = closes.diff().dropna()
    gains = changes.clip(lower=0.0)
    losses = -changes.clip(upper=0.0)

    avg_gain = float(gains.iloc[:period].mean())
    avg_loss = float(losses.iloc[:period].mean())

    for gain, loss in zip(gains.iloc[period:], losses.iloc[period:]):
        avg_gain = (avg_gain * (period - 1) + float(gain)) / period
        avg_loss = (avg_loss * (period - 1) + float(loss)) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))

    if not math.isfinite(rsi):
        logger.warning("Computed non-finite RSI for %s; treating as unavailable.", ticker_symbol)
        return None

    return float(rsi)


if __name__ == "__main__":
    for symbol in ["AAPL", "MSFT", "GOOGL"]:
        print(f"{symbol}: 14-Day RSI (Wilder's Smoothing) = {calculate_rsi(symbol)}")
