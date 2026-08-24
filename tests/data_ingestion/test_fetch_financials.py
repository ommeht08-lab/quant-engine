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
from src.data_ingestion.fetch_financials import get_shares_outstanding, get_ticker_object


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


class _RecordingFastInfo:
    """
    Duck-typed stand-in for yfinance's `FastInfo` -- records every key
    requested via `.get(key, default)` (matching `FastInfo.get`'s own
    signature: https://github.com/ranaroussi/yfinance, `scrapers/
    quote.py`) so a test can assert exactly which key(s)
    `get_shares_outstanding` queries, without needing the real yfinance
    class or any network access. `FastInfo.get()` itself never raises
    for an unknown key (it returns `default`) -- the installed
    yfinance's real behavior for `.get("shares_outstanding")` is exactly
    that: a silent `None`, because `"shares_outstanding"` was never in
    `FastInfo.keys()` in the first place. `raise_on_get`, when set,
    simulates the DIFFERENT failure mode this function's `except
    Exception` clause exists for: FastInfo raising outright (e.g. a
    genuine network/data error), not merely returning nothing.
    """

    def __init__(self, values=None, raise_on_get=None):
        self._values = values or {}
        self._raise_on_get = raise_on_get
        self.requested_keys = []

    def get(self, key, default=None):
        self.requested_keys.append(key)
        if self._raise_on_get is not None:
            raise self._raise_on_get
        return self._values.get(key, default)


class _FakeTicker:
    """
    Duck-typed stand-in for yfinance's `Ticker`. `.info` is a PROPERTY
    (matching real yfinance, where accessing `.info` is what actually
    triggers a fetch -- see the installed-package investigation this
    fix is based on) so a test can assert whether it was EVER accessed
    at all, independent of what `.get()` is subsequently called on the
    result. No real `yf.Ticker` is ever constructed and no network call
    is possible through this fake -- it holds no session, no URL, no
    I/O of any kind.
    """

    def __init__(self, fast_info, info_values=None, raise_on_info=None, ticker="TEST"):
        self.ticker = ticker
        self.fast_info = fast_info
        self._info_values = info_values if info_values is not None else {}
        self._raise_on_info = raise_on_info
        self.info_accessed = False

    @property
    def info(self):
        self.info_accessed = True
        if self._raise_on_info is not None:
            raise self._raise_on_info
        return self._info_values


class TestSharesOutstandingUsesTheCorrectFastInfoKey:
    """
    Regression for the confirmed yfinance 1.2.0 FastInfo bug: `FastInfo.
    keys()` never contained `"shares_outstanding"` -- only `"shares"` --
    so `.get("shares_outstanding")` was a silent, permanent no-op (always
    the `default=None` fallback) that made every call fall straight
    through to the `.info["sharesOutstanding"]` fallback, unconditionally,
    for every valuation. `get_shares_outstanding` must now query the
    correct key first and only reach `.info` when FastInfo genuinely has
    no usable value.

    No network is contacted anywhere in this class: every ticker object
    is a pure in-memory `_FakeTicker`/`_RecordingFastInfo`, never a real
    `yf.Ticker` -- there is no session, URL, or I/O for any test here to
    reach, independent of the global test-isolation guards in
    tests/conftest.py.
    """

    def test_requests_the_shares_key_not_shares_outstanding(self):
        fast_info = _RecordingFastInfo(values={"shares": 999.0})
        ticker_obj = _FakeTicker(fast_info=fast_info, info_values={"sharesOutstanding": 111.0})

        get_shares_outstanding(ticker_obj)

        assert "shares" in fast_info.requested_keys
        assert "shares_outstanding" not in fast_info.requested_keys

    def test_never_requests_shares_outstanding_even_when_shares_is_absent(self):
        """The fallback path must not ALSO probe the wrong key on its
        way through -- exactly one FastInfo key is ever requested."""
        fast_info = _RecordingFastInfo(values={})
        ticker_obj = _FakeTicker(fast_info=fast_info, info_values={"sharesOutstanding": 111.0})

        get_shares_outstanding(ticker_obj)

        assert fast_info.requested_keys == ["shares"]

    def test_valid_fast_info_value_is_returned_without_touching_info(self):
        fast_info = _RecordingFastInfo(values={"shares": 123_456_789.0})
        ticker_obj = _FakeTicker(fast_info=fast_info, info_values={"sharesOutstanding": 1.0})

        result = get_shares_outstanding(ticker_obj)

        assert result == 123_456_789.0
        assert ticker_obj.info_accessed is False

    def test_missing_fast_info_value_falls_back_to_info_shares_outstanding(self):
        fast_info = _RecordingFastInfo(values={})  # "shares" absent/falsy
        ticker_obj = _FakeTicker(fast_info=fast_info, info_values={"sharesOutstanding": 42_000_000.0})

        result = get_shares_outstanding(ticker_obj)

        assert result == 42_000_000.0
        assert ticker_obj.info_accessed is True

    def test_zero_fast_info_value_is_treated_as_falsy_and_falls_back(self):
        """Matches the pre-existing `if shares:` truthiness check this
        function has always used -- a reported 0 shares is indistinguishable
        from "no value" here, unchanged by this fix."""
        fast_info = _RecordingFastInfo(values={"shares": 0})
        ticker_obj = _FakeTicker(fast_info=fast_info, info_values={"sharesOutstanding": 7.0})

        result = get_shares_outstanding(ticker_obj)

        assert result == 7.0

    def test_fast_info_raising_falls_back_to_info_and_does_not_propagate(self):
        fast_info = _RecordingFastInfo(raise_on_get=RuntimeError("simulated fast_info failure"))
        ticker_obj = _FakeTicker(fast_info=fast_info, info_values={"sharesOutstanding": 55.0})

        result = get_shares_outstanding(ticker_obj)

        assert result == 55.0
        assert ticker_obj.info_accessed is True

    def test_both_sources_failing_returns_none_not_an_exception(self):
        fast_info = _RecordingFastInfo(values={})
        ticker_obj = _FakeTicker(fast_info=fast_info, info_values={})

        result = get_shares_outstanding(ticker_obj)

        assert result is None

    def test_both_sources_raising_returns_none_not_an_exception(self):
        """The documented contract (see this function's own docstring):
        every fetch function degrades to None rather than raising."""
        fast_info = _RecordingFastInfo(raise_on_get=RuntimeError("simulated fast_info failure"))
        ticker_obj = _FakeTicker(
            fast_info=fast_info, raise_on_info=RuntimeError("simulated info failure")
        )

        result = get_shares_outstanding(ticker_obj)

        assert result is None
