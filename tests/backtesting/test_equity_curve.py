"""
Group G: equity-curve correctness — shared first-common-date
normalization (a $100,000 curve must begin at exactly $100,000 even when
one constituent's history starts later than the others), and recorded
(not silent) weight redistribution for dropped tickers.
"""

from unittest.mock import patch

import pandas as pd
import pytest

from src.backtesting.historical_tester import TickerAnalysis, build_equity_curve


def _series(dates, values):
    return pd.Series(values, index=pd.to_datetime(dates))


class TestFirstCommonDateNormalization:
    def test_curve_with_a_late_starting_constituent_begins_at_exactly_start_value(self):
        # MSFT and SPY have data from Jan; AAPL only starts in Feb — the
        # regression case the fix targets.
        series_msft = _series(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"], [100, 110, 121, 133.1])
        series_aapl = _series(["2024-02-01", "2024-03-01", "2024-04-01"], [50, 55, 60.5])
        series_spy = _series(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"], [400, 410, 420, 430])

        def fake_monthly_close_series(ticker, as_of_ts, today_ts):
            return {"MSFT": series_msft, "AAPL": series_aapl, "SPY": series_spy}.get(ticker)

        picks = [
            TickerAnalysis(ticker="MSFT", as_of_date="2024-01-01"),
            TickerAnalysis(ticker="AAPL", as_of_date="2024-01-01"),
        ]

        with patch(
            "src.backtesting.historical_tester._monthly_close_series", side_effect=fake_monthly_close_series
        ):
            dates, strategy_values, spy_values, dropped = build_equity_curve(
                picks, "2024-01-01", "SPY", start_value=100_000.0
            )

        assert dropped == []
        assert dates[0] == "2024-02-01"  # the first date common to ALL series
        assert strategy_values[0] == pytest.approx(100_000.0)
        assert spy_values[0] == pytest.approx(100_000.0)

    def test_all_series_starting_together_still_works(self):
        series_a = _series(["2024-01-01", "2024-02-01"], [10, 11])
        series_b = _series(["2024-01-01", "2024-02-01"], [20, 22])
        series_spy = _series(["2024-01-01", "2024-02-01"], [100, 105])

        def fake(ticker, as_of_ts, today_ts):
            return {"A": series_a, "B": series_b, "SPY": series_spy}.get(ticker)

        picks = [TickerAnalysis(ticker="A", as_of_date="2024-01-01"), TickerAnalysis(ticker="B", as_of_date="2024-01-01")]

        with patch("src.backtesting.historical_tester._monthly_close_series", side_effect=fake):
            dates, strategy_values, spy_values, dropped = build_equity_curve(
                picks, "2024-01-01", "SPY", start_value=50_000.0
            )

        assert strategy_values[0] == pytest.approx(50_000.0)
        assert spy_values[0] == pytest.approx(50_000.0)


class TestDroppedTickersAreRecorded:
    def test_ticker_with_no_history_is_recorded_not_silently_dropped(self):
        series_a = _series(["2024-01-01", "2024-02-01"], [10, 11])
        series_spy = _series(["2024-01-01", "2024-02-01"], [100, 105])

        def fake(ticker, as_of_ts, today_ts):
            return {"A": series_a, "SPY": series_spy}.get(ticker)  # "B" returns None (unavailable)

        picks = [TickerAnalysis(ticker="A", as_of_date="2024-01-01"), TickerAnalysis(ticker="B", as_of_date="2024-01-01")]

        with patch("src.backtesting.historical_tester._monthly_close_series", side_effect=fake):
            dates, strategy_values, spy_values, dropped = build_equity_curve(
                picks, "2024-01-01", "SPY", start_value=100_000.0
            )

        assert dropped == ["B"]
        assert strategy_values[0] == pytest.approx(100_000.0)  # survivor A gets 100% weight, not silently 50%

    def test_no_picks_returns_empty_without_error(self):
        dates, strategy_values, spy_values, dropped = build_equity_curve([], "2024-01-01")
        assert dates == [] and strategy_values == [] and spy_values == [] and dropped == []
