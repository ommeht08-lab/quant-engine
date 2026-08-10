"""
Macro context and qualitative news sentiment for a ticker.

Pulls the most recent news headlines for a company (via
`yfinance.Ticker.news`) alongside two standard macro risk indicators —
the 10-Year Treasury yield and the CBOE Volatility Index (VIX) — so
qualitative, non-fundamental context can sit alongside the quantitative
DCF / Altman Z-Score screen on the Tear Sheet.

This is supplementary qualitative context, not a scored input to the DCF
or Conviction Score pipeline. News and macro data availability is far
less reliable than financial statement data (Yahoo Finance rate-limits
or reshapes these endpoints more often), so every field here degrades
gracefully to None / an empty list rather than raising.
"""

import logging
from typing import Dict, List, Optional

from src.data_ingestion.fetch_financials import get_ticker_object
from src.utils.macro import get_risk_free_rate

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

RECENT_HEADLINE_COUNT = 5


def _get_vix() -> Optional[float]:
    """Most recent CBOE Volatility Index (VIX) close, or None if unavailable."""
    try:
        history = get_ticker_object("^VIX").history(period="5d")
        valid_history = history.dropna(subset=["Close"])
        if valid_history.empty:
            return None
        return float(valid_history["Close"].iloc[-1])
    except Exception as exc:  # noqa: BLE001 - macro context must never raise
        logger.warning("Could not retrieve VIX: %s", exc)
        return None


def _get_recent_headlines(ticker_obj) -> List[Dict[str, Optional[str]]]:
    """
    The `RECENT_HEADLINE_COUNT` most recent news headlines for `ticker_obj`,
    each as {"title", "publisher", "link", "published_at"}.
    """
    try:
        news_items = ticker_obj.news or []
    except Exception as exc:  # noqa: BLE001 - macro context must never raise
        logger.warning("Could not retrieve news for %s: %s", ticker_obj.ticker, exc)
        return []

    headlines: List[Dict[str, Optional[str]]] = []
    for item in news_items[:RECENT_HEADLINE_COUNT]:
        # Modern yfinance nests the actual story under "content"; tolerate
        # the older flat shape too in case that ever changes back.
        content = item.get("content", item) if isinstance(item, dict) else {}
        title = content.get("title")
        if not title:
            continue

        provider = content.get("provider") or {}
        link_info = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}

        headlines.append(
            {
                "title": title,
                "publisher": provider.get("displayName"),
                "link": link_info.get("url"),
                "published_at": content.get("pubDate"),
            }
        )
    return headlines


def get_ticker_sentiment_and_macro(ticker_symbol: str) -> Dict:
    """
    Fetch recent news headlines for a ticker plus macro risk context.

    Args:
        ticker_symbol: Stock ticker symbol, e.g. "AAPL".

    Returns:
        dict with keys:
            ticker (str)
            macro_indicators (dict):
                treasury_10y (float | None) — 10-Year Treasury yield as a
                    decimal (e.g. 0.047 for 4.7%), or the same fallback
                    `get_risk_free_rate` uses elsewhere in this project if
                    the live yield can't be fetched.
                vix (float | None) — CBOE Volatility Index level (e.g.
                    15.3), or None if unavailable.
            recent_headlines (list[dict]): up to RECENT_HEADLINE_COUNT
                {"title", "publisher", "link", "published_at"} dicts, most
                recent first, as reported by Yahoo Finance. Empty if no
                headlines could be fetched.
    """
    ticker_obj = get_ticker_object(ticker_symbol)

    return {
        "ticker": ticker_obj.ticker,
        "macro_indicators": {
            "treasury_10y": get_risk_free_rate(),
            "vix": _get_vix(),
        },
        "recent_headlines": _get_recent_headlines(ticker_obj),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(get_ticker_sentiment_and_macro("AAPL"), indent=2, default=str))
