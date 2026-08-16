"""
Group L: ticker-symbol input validation.

Regression for Finding 4: `get_ticker_object` must reject a whitespace-
only ticker string before it ever reaches `yf.Ticker(...)` -- a
whitespace-only string is truthy in Python, so the OLD `not ticker`
check let it through, stripped down to an empty symbol, and reached the
yfinance constructor. Every case here must raise `ValueError` before any
network-backed construction happens; the test-isolation guard
(`tests/test_isolation.py`) would independently block a real
`yf.Ticker()` call too, but this test proves the validation itself
catches it first, with a clean, documented exception.
"""

import pytest

from src.data_ingestion import fetch_financials
from src.data_ingestion.fetch_financials import get_ticker_object


class TestTickerSymbolValidation:
    @pytest.mark.parametrize(
        "ticker",
        [
            "",
            "   ",
            "\t",
            "\n",
            "\t\n  \t",
            None,
            123,
            [],
        ],
    )
    def test_invalid_ticker_raises_value_error(self, ticker):
        """
        Every case here must raise BEFORE reaching `yf.Ticker(...)` --
        left unpatched deliberately, so a bug that let one of these slip
        through would hit the test-isolation guard's own RuntimeError
        instead of the expected ValueError, still failing the test
        loudly rather than silently passing.
        """
        with pytest.raises(ValueError):
            get_ticker_object(ticker)

    def test_valid_ticker_with_surrounding_whitespace_is_accepted_and_normalized(self, monkeypatch):
        """
        `yf.Ticker` itself is monkeypatched to a lightweight stand-in
        here (rather than left to the real class) so this test exercises
        ONLY `get_ticker_object`'s own validation/normalization logic,
        without depending on the separate test-isolation guard that
        blocks real `yf.Ticker` construction (see `tests/test_isolation.py`).
        """
        calls = []
        monkeypatch.setattr(fetch_financials.yf, "Ticker", lambda symbol: calls.append(symbol) or symbol)

        result = get_ticker_object("  aapl  ")

        assert result == "AAPL"
        assert calls == ["AAPL"]

    def test_valid_ticker_is_accepted(self, monkeypatch):
        calls = []
        monkeypatch.setattr(fetch_financials.yf, "Ticker", lambda symbol: calls.append(symbol) or symbol)

        result = get_ticker_object("MSFT")

        assert result == "MSFT"
        assert calls == ["MSFT"]
