"""
Group L: end-to-end `main()` orchestration.

Drives the actual `alpaca_execution.main()` entry point against a fully
scripted `FakeTradingClient` subclass, exercising the REAL sequencing
this module's own docstring promises: liquidate non-target/profit-taken
positions -> refresh positions/account -> buy/sell toward target ->
refresh again -> post-fill cap check/trim -> risk calc -> hedge -> final
RISK_SNAPSHOT log. `run_todays_scan`/`refresh_sector_median_cache`/
`execute_spy_var_hedge`/`ensure_schema`/`_safe_log_trade` are
monkeypatched (no real scan, no real cache write, no real hedge pricing,
no real DB) so this test is about ORCHESTRATION — call ordering, data
flowing correctly between phases — not re-testing each phase's own
internals (already covered elsewhere). No network, no real credentials,
no real database.
"""

import types

from src.backtesting.historical_tester import TickerAnalysis
from src.risk.monte_carlo import VaRResult
from src.trading import alpaca_execution as engine
from tests.conftest import FakeTradingClient, make_position


class ScriptedMainClient(FakeTradingClient):
    """
    Extends `FakeTradingClient` with two additional scripting knobs
    `main()`-level integration tests need that no single-function unit
    test does:

    - `position_snapshots`: `get_all_positions()` returns the next
      snapshot in this list on each successive call (clamped to the
      last one once exhausted) — `main()` calls `get_current_positions`
      multiple times in one run (initial, post-liquidation, post-fill),
      and a real account's holdings genuinely differ across those calls
      as orders fill; a static snapshot would defeat the point of an
      orchestration test.
    - `open_for_calls`: reuses `ClockTogglingClient`'s semantics (market
      reports open for the first N `get_clock()` calls, closed after) —
      `None` means always open.
    """

    def __init__(self, *args, position_snapshots, open_for_calls=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._snapshots = list(position_snapshots)
        self._snapshot_index = 0
        self._clock_calls = 0
        self._open_for_calls = open_for_calls

    def get_all_positions(self):
        idx = min(self._snapshot_index, len(self._snapshots) - 1)
        self._snapshot_index += 1
        return list(self._snapshots[idx])

    def get_clock(self):
        self._clock_calls += 1
        is_open = True if self._open_for_calls is None else self._clock_calls <= self._open_for_calls
        return types.SimpleNamespace(is_open=is_open)


def _pick(ticker, sector, beta=1.0, conviction=1.0):
    return TickerAnalysis(
        ticker=ticker, as_of_date="2024-01-01", sector=sector, beta=beta, conviction_score=conviction,
        historical_price=100.0, price_to_intrinsic=0.9, sector_median_price_to_intrinsic=1.1,
    )


def _patch_common(monkeypatch, client, *, top_picks, risk_result):
    """Shared monkeypatching for every main() integration test below."""
    dummy_config = engine.AlpacaConfig(
        api_key="test-key", secret_key="test-secret", base_url="https://paper-api.alpaca.markets"
    )
    monkeypatch.setattr(engine, "load_config", lambda: dummy_config)
    monkeypatch.setattr(engine, "build_trading_client", lambda config: client)
    monkeypatch.setattr(engine, "ensure_schema", lambda: None)

    logged_trades = []
    monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: logged_trades.append(kwargs))

    monkeypatch.setattr(
        engine, "run_todays_scan",
        lambda tickers, assumptions=None: (top_picks, {}, [], 0.04),
    )
    refreshed_caches = []
    monkeypatch.setattr(
        engine, "refresh_sector_median_cache",
        lambda *args, **kwargs: refreshed_caches.append(True),
    )
    monkeypatch.setattr(engine, "calculate_portfolio_var", lambda holdings: risk_result)

    hedge_calls = []
    monkeypatch.setattr(
        engine, "execute_spy_var_hedge",
        lambda *args, **kwargs: hedge_calls.append((args, kwargs)),
    )

    return logged_trades, hedge_calls


class TestFullOrchestration:
    """
    Two targets (AAA underweight -> bought, BBB new -> bought), one
    non-target held position (CCC -> liquidated), and a post-fill
    breach on AAA (-> corrective trim) — exercises every phase in one run.
    """

    def _build(self, monkeypatch):
        top_picks = [_pick("AAA", "Technology"), _pick("BBB", "Healthcare")]

        snapshot_initial = [
            make_position("CCC", qty=20, market_value=2_000.0, current_price=100.0),
            make_position("AAA", qty=5, market_value=500.0, current_price=100.0),  # far underweight
        ]
        snapshot_post_liquidation = [
            make_position("AAA", qty=5, market_value=500.0, current_price=100.0),
        ]
        snapshot_post_fill = [
            # AAA now overweight after its buy fill (16% > 15% cap) -> triggers a trim.
            make_position("AAA", qty=160, market_value=16_000.0, current_price=100.0),
            make_position("BBB", qty=150, market_value=15_000.0, current_price=100.0),
        ]

        client = ScriptedMainClient(
            positions=snapshot_initial,
            equity=100_000.0,
            buying_power=100_000.0,
            position_snapshots=[snapshot_initial, snapshot_post_liquidation, snapshot_post_fill],
        )
        risk_result = VaRResult(status="ok", var_95=-0.05, cvar_95=-0.08)
        logged_trades, hedge_calls = _patch_common(monkeypatch, client, top_picks=top_picks, risk_result=risk_result)
        return client, top_picks, logged_trades, hedge_calls

    def test_full_run_liquidates_buys_trims_and_hedges_in_order(self, monkeypatch):
        client, top_picks, logged_trades, hedge_calls = self._build(monkeypatch)
        monkeypatch.setattr(engine.sys, "argv", ["alpaca_execution.py"])

        engine.main()

        # 1. Liquidation: CCC (not a target) was closed; AAA (a target) was not.
        assert client.closed_symbols == ["CCC"]

        # 2. Rebalance buys: both AAA (underweight) and BBB (new) were bought.
        buy_orders = [o for o in client.submitted_orders if o.side.value == "buy"]
        assert {o.symbol for o in buy_orders} == {"AAA", "BBB"}

        # 3. Post-fill cap trim: AAA's post-fill snapshot breaches
        # MAX_POSITION_WEIGHT -> a corrective SELL was submitted for it.
        sell_orders = [o for o in client.submitted_orders if o.side.value == "sell"]
        assert any(o.symbol == "AAA" for o in sell_orders)

        # 4. Hedge phase was reached (VaR was "ok") with the CONFIRMED
        # post-fill positions snapshot, not an earlier one.
        assert len(hedge_calls) == 1
        hedge_kwargs = hedge_calls[0][1]
        existing_positions_arg = hedge_kwargs.get("existing_positions") or hedge_calls[0][0][4]
        assert set(existing_positions_arg.keys()) == {"AAA", "BBB"}

        # 5. Final RISK_SNAPSHOT row logged for a real (non-dry-run) invocation.
        actions_logged = [t["action"] for t in logged_trades]
        assert "RISK_SNAPSHOT" in actions_logged
        assert actions_logged.count("SELL") >= 2  # CCC liquidation + AAA trim
        assert actions_logged.count("BUY") == 2  # AAA + BBB

    def test_dry_run_never_submits_any_order(self, monkeypatch):
        client, top_picks, logged_trades, hedge_calls = self._build(monkeypatch)
        monkeypatch.setattr(engine.sys, "argv", ["alpaca_execution.py", "--dry-run"])

        engine.main()

        assert client.closed_symbols == []
        assert client.submitted_orders == []
        # No real trade logging in a dry run (only a real run logs
        # RISK_SNAPSHOT/trades) — see the module docstring.
        assert logged_trades == []


class TestMarketClosesMidRun:
    """
    The market clock reports OPEN for the first few checks (the scan-
    start informational check, the CCC liquidation, and the AAA buy),
    then CLOSED for everything after — proving each submission
    independently rechecks rather than the whole run being gated by one
    upfront reading, AND that a market closing partway through does not
    silently convert the rest of the run into a dry run (the orders
    already submitted before the close remain real).
    """

    def test_orders_after_the_close_are_skipped_but_earlier_ones_are_not(self, monkeypatch):
        top_picks = [_pick("AAA", "Technology"), _pick("BBB", "Healthcare")]
        snapshot_initial = [
            make_position("CCC", qty=20, market_value=2_000.0, current_price=100.0),
            make_position("AAA", qty=5, market_value=500.0, current_price=100.0),
        ]
        snapshot_post_liquidation = [
            make_position("AAA", qty=5, market_value=500.0, current_price=100.0),
        ]
        snapshot_post_fill = [
            make_position("AAA", qty=155, market_value=15_500.0, current_price=100.0),
        ]

        # Clock call order: (1) scan-start informational check, (2) CCC
        # liquidation, (3) AAA buy, (4) BBB buy, (5) AAA post-fill trim.
        # Open for the first 3 -> CCC liquidates and AAA buys for real;
        # BBB's buy and AAA's trim must each be explicitly skipped.
        client = ScriptedMainClient(
            positions=snapshot_initial,
            equity=100_000.0,
            buying_power=100_000.0,
            position_snapshots=[snapshot_initial, snapshot_post_liquidation, snapshot_post_fill],
            open_for_calls=3,
        )
        risk_result = VaRResult(status="insufficient_data", message="no data in this test")
        logged_trades, hedge_calls = _patch_common(monkeypatch, client, top_picks=top_picks, risk_result=risk_result)
        monkeypatch.setattr(engine.sys, "argv", ["alpaca_execution.py"])

        engine.main()

        assert client.closed_symbols == ["CCC"]  # succeeded (clock open at call #2)
        buy_symbols = {o.symbol for o in client.submitted_orders if o.side.value == "buy"}
        assert buy_symbols == {"AAA"}  # AAA succeeded (call #3), BBB skipped (call #4, closed)
        # No trim SELL for AAA — the market was closed by the time the
        # post-fill cap-trim phase ran (call #5).
        sell_symbols = {o.symbol for o in client.submitted_orders if o.side.value == "sell"}
        assert "AAA" not in sell_symbols
        # VaR was unavailable this run -> hedge phase never invoked at all.
        assert hedge_calls == []
