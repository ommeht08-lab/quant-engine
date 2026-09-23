import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from src.backtesting import sec_pilot
from src.backtesting.sec_pilot import (
    PILOT_DISCLAIMER,
    PILOT_RESULT_LABEL,
    ExecutionWindow,
    IssuerDecision,
    PilotConfig,
    PriceProvider,
    equal_weight_control,
    estimate_beta,
    execution_window,
    rsi_at,
    simulate_curve,
    strategy_weights_with_refusals,
    trend_passes_at,
)

DECISION = dt.date(2024, 9, 3)


class FramePrices(PriceProvider):
    """Deterministic business-day prices; ``future`` rewrites every session
    after the decision date to prove signals never read it."""

    def __init__(self, series_by_symbol, future=None):
        self._frames = {}
        for symbol, closes in series_by_symbol.items():
            frame = pd.DataFrame({"Open": closes, "Close": closes})
            if future is not None:
                after = frame.index > pd.Timestamp(DECISION)
                frame.loc[after, ["Open", "Close"]] = future
            self._frames[symbol] = frame

    def adjusted(self, symbol, start, end):
        frame = self._frames[symbol]
        return frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]

    def raw_close(self, symbol, on):
        frame = self._frames[symbol]
        row = frame.loc[frame.index == pd.Timestamp(on)]
        return None if row.empty else float(row["Close"].iloc[0])


def _series(seed, start="2019-01-01", end="2026-01-01"):
    index = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, len(index)))), index=index)


def _prices(future=None):
    return FramePrices({"AAA": _series(1), "SPY": _series(2)}, future=future)


def test_price_signals_ignore_every_session_after_the_decision_date():
    baseline = _prices()
    shocked = _prices(future=1_000_000.0)

    assert estimate_beta(baseline, "AAA", "SPY", DECISION) == estimate_beta(shocked, "AAA", "SPY", DECISION)
    assert rsi_at(baseline, "AAA", DECISION) == rsi_at(shocked, "AAA", DECISION)
    assert trend_passes_at(baseline, "AAA", DECISION) == trend_passes_at(shocked, "AAA", DECISION)
    assert estimate_beta(baseline, "AAA", "SPY", DECISION) is not None
    assert rsi_at(baseline, "AAA", DECISION) is not None


def test_execution_window_enters_the_session_after_the_decision_and_holds_252_sessions():
    window = execution_window(_prices(), PilotConfig())

    assert window.entry_date == dt.date(2024, 9, 4)
    assert len(window.sessions) == 252
    assert window.exit_date == window.sessions[-1]
    assert all(day > DECISION for day in window.sessions)


def _flat_then(values, sessions):
    return pd.Series(values, index=pd.DatetimeIndex([pd.Timestamp(day) for day in sessions]))


def test_simulated_accounting_reconciles_costs_and_unallocated_cash_exactly():
    sessions = (dt.date(2024, 9, 4), dt.date(2024, 9, 5), dt.date(2024, 9, 6))
    prices = FramePrices({"AAA": _flat_then([50.0, 55.0, 60.0], sessions)})
    prices._frames["AAA"]["Open"] = [40.0, 55.0, 60.0]
    window = ExecutionWindow(entry_date=sessions[0], exit_date=sessions[-1], sessions=sessions)

    curve = simulate_curve("test", {"AAA": 0.75}, prices=prices, window=window, capital=100_000.0, cost_bps=10)

    shares = 75_000.0 * (1 - 0.001) / 40.0
    exit_notional = shares * 60.0
    assert curve.unallocated_cash_fraction == pytest.approx(0.25)
    assert curve.costs_paid == pytest.approx(75_000.0 * 0.001 + exit_notional * 0.001)
    assert curve.ending_value == pytest.approx(25_000.0 + exit_notional * (1 - 0.001))
    assert curve.daily_values[0] == ("2024-09-04", pytest.approx(25_000.0 + shares * 50.0))
    assert curve.net_return == pytest.approx(curve.ending_value / 100_000.0 - 1)


def test_all_cash_curve_returns_exactly_zero_with_no_costs():
    sessions = (dt.date(2024, 9, 4), dt.date(2024, 9, 5))
    window = ExecutionWindow(entry_date=sessions[0], exit_date=sessions[-1], sessions=sessions)

    curve = simulate_curve("strategy", {}, prices=_prices(), window=window, capital=100_000.0, cost_bps=25)

    assert curve.ending_value == 100_000.0
    assert curve.net_return == 0.0
    assert curve.costs_paid == 0.0
    assert curve.max_drawdown == 0.0


def test_missing_execution_price_refuses_instead_of_skipping_a_session():
    sessions = (dt.date(2024, 9, 4), dt.date(2024, 9, 5))
    prices = FramePrices({"AAA": _flat_then([10.0], sessions[:1])})
    window = ExecutionWindow(entry_date=sessions[0], exit_date=sessions[-1], sessions=sessions)

    with pytest.raises(ValueError, match="missing prices"):
        simulate_curve("test", {"AAA": 1.0}, prices=prices, window=window, capital=1.0, cost_bps=10)


def test_refused_company_capital_is_held_as_cash_not_redistributed():
    strategy = strategy_weights_with_refusals({"AAPL": 0.6, "MSFT": 0.4}, refused_count=1, universe_size=4)
    control = equal_weight_control(["AAPL", "MSFT", "WMT", "CAT"], ["CAT"])

    assert strategy == {"AAPL": pytest.approx(0.45), "MSFT": pytest.approx(0.30)}
    assert sum(strategy.values()) == pytest.approx(0.75)
    assert control == {"AAPL": 0.25, "MSFT": 0.25, "WMT": 0.25}
    assert "CAT" not in control


def test_yahoo_statements_are_prohibited_in_the_pilot():
    with pytest.raises(ValueError, match="prohibited"):
        sec_pilot._refuse_yahoo("AAPL")


def test_report_is_labelled_pipeline_validation_and_lists_refusals():
    config = PilotConfig()
    sessions = (dt.date(2024, 9, 4), dt.date(2024, 9, 5))
    window = ExecutionWindow(entry_date=sessions[0], exit_date=sessions[-1], sessions=sessions)
    decisions = [
        IssuerDecision(ticker="AAPL", sector="Technology", status="rejected_by_strategy", reason="gate"),
        IssuerDecision(ticker="CAT", sector="Industrials", status="refused", reason="SEC input refused: x"),
    ]
    curve = simulate_curve("spy", {}, prices=_prices(), window=window, capital=100_000.0, cost_bps=10)
    results = {cost: {"strategy": curve, "equal_weight_control": curve, "spy_buy_and_hold": curve} for cost in config.cost_cases_bps}

    report = sec_pilot._report(config, decisions, window, results, results[10], dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc))

    assert report["label"] == PILOT_RESULT_LABEL == "pipeline_validation"
    assert report["disclaimer"] == PILOT_DISCLAIMER
    assert "not evidence of investment performance" in report["disclaimer"]
    assert report["strategy_outcome"] == "no_eligible_candidates"
    assert report["rejected_issuers"] == [
        {"ticker": "CAT", "reason": "SEC input refused: x", "unallocated_capital_fraction": 0.25}
    ]
    assert report["knowledge_cutoff"] == "2024-09-03T16:00:00-04:00"
    assert set(report["cost_cases"]) == {"5", "10", "25"}
    assert math.isclose(report["initial_capital"], 100_000.0)
