"""
Group A: trading safety.

Covers paper-only enforcement (fail-closed unless explicitly opted into
live trading), --top-n validation, and proof that a dry run never mutates
external state. No test here constructs a real TradingClient or touches
real credentials — `load_config` is exercised purely through environment
variable manipulation (`monkeypatch`), never a real `.env`.
"""

import argparse

import pytest
from alpaca.trading.enums import AssetClass

from src.trading import alpaca_execution as engine
from tests.conftest import FakeTradingClient, make_position


def _clear_alpaca_env(monkeypatch):
    for name in (
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "APCA_API_BASE_URL",
        "ALPACA_LIVE_TRADING_CONFIRM",  # the old, now-removed bypass sentinel — must have no effect
    ):
        monkeypatch.delenv(name, raising=False)
    # load_config() calls load_dotenv(), which would otherwise pull in a
    # real local .env file if one exists — make that a no-op for these tests.
    monkeypatch.setattr(engine, "load_dotenv", lambda *a, **k: None)


def _set_alpaca_env(monkeypatch, base_url: str):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    monkeypatch.setenv("APCA_API_BASE_URL", base_url)


class TestPaperOnlyEnforcement:
    def test_paper_endpoint_succeeds(self, monkeypatch):
        _clear_alpaca_env(monkeypatch)
        _set_alpaca_env(monkeypatch, "https://paper-api.alpaca.markets")
        config = engine.load_config()
        assert config.is_paper is True

    def test_live_endpoint_without_opt_in_raises_before_any_client(self, monkeypatch):
        _clear_alpaca_env(monkeypatch)
        _set_alpaca_env(monkeypatch, "https://api.alpaca.markets")  # the REAL live-trading host
        with pytest.raises(RuntimeError, match="paper"):
            engine.load_config()

    def test_no_environment_variable_can_bypass_the_paper_only_gate(self, monkeypatch):
        """
        There is no environment variable, sentinel value, or other
        configuration that permits a non-paper endpoint — a non-paper
        hostname must always hard-fail. This exhaustively tries every
        plausible bypass attempt (the old, now-removed opt-in var/value,
        common truthy strings, and generic override-ish names) against a
        real live-trading host and asserts every single one still raises.
        """
        _clear_alpaca_env(monkeypatch)
        _set_alpaca_env(monkeypatch, "https://api.alpaca.markets")  # the REAL live-trading host

        candidate_env_vars = [
            "ALPACA_LIVE_TRADING_CONFIRM",  # the old, now-deleted bypass var name
            "LIVE_TRADING_OPT_IN",
            "ALPACA_FORCE_LIVE",
            "ALLOW_LIVE_TRADING",
            "APCA_ALLOW_LIVE",
            "DEBUG",
            "OVERRIDE_PAPER_ONLY",
        ]
        candidate_values = [
            "I_UNDERSTAND_THIS_PLACES_REAL_ORDERS_WITH_REAL_MONEY",  # the old sentinel value
            "1",
            "true",
            "True",
            "yes",
            "live",
            "*",
        ]

        for env_var in candidate_env_vars:
            for value in candidate_values:
                monkeypatch.setenv(env_var, value)
                with pytest.raises(RuntimeError, match="paper"):
                    engine.load_config()
                monkeypatch.delenv(env_var, raising=False)

    def test_no_live_trading_opt_in_constants_exist(self):
        """
        The bypass constants themselves must not exist on the module —
        proves the removal was complete, not just that load_config()
        ignores them while the constants linger for something else to
        read.
        """
        assert not hasattr(engine, "LIVE_TRADING_OPT_IN_ENV_VAR")
        assert not hasattr(engine, "LIVE_TRADING_OPT_IN_VALUE")

    def test_missing_credentials_raises(self, monkeypatch):
        _clear_alpaca_env(monkeypatch)
        with pytest.raises(RuntimeError, match="Missing required"):
            engine.load_config()

    def test_spoofed_host_containing_the_word_paper_is_still_rejected(self, monkeypatch):
        """A loose substring check ("paper" in url) would wrongly accept this; the
        real fix compares the parsed hostname exactly."""
        _clear_alpaca_env(monkeypatch)
        _set_alpaca_env(monkeypatch, "https://paper-api.alpaca.markets.evil.example.com")
        with pytest.raises(RuntimeError):
            engine.load_config()


class TestTopNValidation:
    def test_positive_int_accepts_valid_values(self):
        assert engine._positive_int("1") == 1
        assert engine._positive_int("10") == 10

    @pytest.mark.parametrize("bad_value", ["0", "-1", "-10", "abc", "1.5"])
    def test_positive_int_rejects_invalid_values(self, bad_value):
        with pytest.raises(argparse.ArgumentTypeError):
            engine._positive_int(bad_value)

    def test_top_n_argparse_integration_rejects_zero(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--top-n", type=engine._positive_int, default=engine.DEFAULT_TOP_N)
        with pytest.raises(SystemExit):
            parser.parse_args(["--top-n", "0"])

    def test_top_n_argparse_integration_accepts_positive(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--top-n", type=engine._positive_int, default=engine.DEFAULT_TOP_N)
        args = parser.parse_args(["--top-n", "5"])
        assert args.top_n == 5


class TestMarketClock:
    def test_market_open_true(self):
        client = FakeTradingClient(market_open=True)
        assert engine._market_is_open(client) is True

    def test_market_closed_false(self):
        client = FakeTradingClient(market_open=False)
        assert engine._market_is_open(client) is False

    def test_clock_fetch_failure_fails_safe_to_closed(self):
        from alpaca.common.exceptions import APIError

        class BrokenClockClient(FakeTradingClient):
            def get_clock(self):
                raise APIError("clock unavailable")

        assert engine._market_is_open(BrokenClockClient()) is False


class TestIdempotency:
    def test_open_order_symbols_returned_as_set(self):
        import types

        client = FakeTradingClient(open_orders=[types.SimpleNamespace(symbol="AAPL")])
        result = engine._open_order_symbols(client)
        assert result == {"AAPL"}

    def test_open_order_lookup_failure_returns_none(self):
        from alpaca.common.exceptions import APIError

        class BrokenOrdersClient(FakeTradingClient):
            def get_orders(self, filter=None):
                raise APIError("orders lookup failed")

        assert engine._open_order_symbols(BrokenOrdersClient()) is None

    def test_duplicate_submission_detection(self):
        assert engine._is_duplicate_submission("AAPL", {"AAPL", "MSFT"}) is True
        assert engine._is_duplicate_submission("GOOGL", {"AAPL", "MSFT"}) is False
        # None (lookup failed) fails safe: every symbol looks unsafe.
        assert engine._is_duplicate_submission("AAPL", None) is True


class TestDryRunNeverMutatesExternalState:
    """
    The core safety property: `--dry-run` (or an equivalent effective
    dry run) must never call `submit_order`/`close_position` on the
    trading client, and must never write to the database.
    """

    def test_liquidate_dry_run_never_calls_close_position(self):
        position = make_position("OLD", qty=5, market_value=500.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])

        results = engine.liquidate_non_target_positions(
            client, {"OLD": position}, target_tickers=set(), analyses_by_ticker={}, dry_run=True
        )

        assert client.closed_symbols == []
        assert results[0]["status"] == "DRY-RUN (would liquidate)"

    def test_rebalance_dry_run_never_calls_submit_order(self):
        from src.backtesting.historical_tester import TickerAnalysis

        pick = TickerAnalysis(
            ticker="NEW", as_of_date="2024-01-01", sector="Technology", beta=1.0, conviction_score=1.0
        )
        client = FakeTradingClient(positions=[])

        engine.rebalance_target_positions(client, {}, [pick], equity=100_000.0, dry_run=True)

        assert client.submitted_orders == []

    def test_hedge_dry_run_never_calls_submit_order(self, monkeypatch):
        import datetime

        from tests.conftest import make_option_contract

        # Force the function past the price-fetch and contract-selection
        # steps deterministically, so this test actually exercises the
        # dry_run gate itself rather than passing "by accident" because a
        # real network/price lookup happened to fail first. `get_ticker_object`
        # is faked too (a real `yf.Ticker(...)` construction is blocked in
        # tests, per tests/conftest.py's `_no_real_yfinance`) — the fake
        # object is never actually inspected since `get_current_price` is
        # mocked separately and ignores its argument.
        monkeypatch.setattr(engine, "get_ticker_object", lambda symbol: object())
        monkeypatch.setattr(engine, "get_current_price", lambda ticker_obj: 580.0)
        future_expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        contract = make_option_contract("SPY_TEST_PUT", strike_price=580.0, expiration_date=future_expiry)
        monkeypatch.setattr(engine, "_select_atm_put_contract", lambda client, price, days: contract)

        client = FakeTradingClient()

        engine.execute_spy_var_hedge(client, portfolio_var_dollars=50_000.0, equity=1_000_000.0, dry_run=True)

        assert client.submitted_orders == []


class TestHedgeIncrementalSizing:
    """
    `execute_spy_var_hedge` must top up an existing SPY put hedge
    incrementally, never blindly re-buy the full target size on top of
    what a prior run already bought (the accumulation this guards
    against: N runs each independently deciding "buy 10 contracts" would
    otherwise leave 10*N held rather than 10).
    """

    def _setup(self, monkeypatch, target_contracts: int):
        import datetime

        from tests.conftest import make_option_contract

        monkeypatch.setattr(engine, "get_ticker_object", lambda symbol: object())
        monkeypatch.setattr(engine, "get_current_price", lambda ticker_obj: 580.0)
        future_expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        # Symbol matches the `existing` positions constructed by every test
        # below ("SPY260101P00580000") — incremental sizing now requires an
        # EXACT contract match (same OCC symbol == same strike+expiry), not
        # just "starts with SPY", so the selected contract and the
        # already-held "same contract" positions must agree on symbol.
        contract = make_option_contract(
            "SPY260101P00580000", strike_price=580.0, expiration_date=future_expiry
        )
        monkeypatch.setattr(engine, "_select_atm_put_contract", lambda client, price, days: contract)
        monkeypatch.setattr(engine, "calculate_spy_hedge", lambda **kwargs: target_contracts)
        return contract

    def test_no_existing_hedge_buys_full_target(self, monkeypatch):
        self._setup(monkeypatch, target_contracts=10)
        client = FakeTradingClient()

        engine.execute_spy_var_hedge(
            client,
            portfolio_var_dollars=50_000.0,
            equity=1_000_000.0,
            dry_run=False,
            open_order_symbols=set(),
            existing_positions={},
        )

        assert len(client.submitted_orders) == 1
        assert client.submitted_orders[0].qty == 10

    def test_existing_hedge_fully_covering_target_buys_nothing(self, monkeypatch):
        self._setup(monkeypatch, target_contracts=10)
        client = FakeTradingClient()
        existing = make_position(
            "SPY260101P00580000", qty=10, market_value=9000.0, current_price=9.0,
            asset_class=AssetClass.US_OPTION,
        )

        engine.execute_spy_var_hedge(
            client,
            portfolio_var_dollars=50_000.0,
            equity=1_000_000.0,
            dry_run=False,
            open_order_symbols=set(),
            existing_positions={"SPY260101P00580000": existing},
        )

        assert client.submitted_orders == []

    def test_partial_existing_hedge_buys_only_the_shortfall(self, monkeypatch):
        self._setup(monkeypatch, target_contracts=10)
        client = FakeTradingClient()
        existing = make_position(
            "SPY260101P00580000", qty=4, market_value=3600.0, current_price=9.0,
            asset_class=AssetClass.US_OPTION,
        )

        engine.execute_spy_var_hedge(
            client,
            portfolio_var_dollars=50_000.0,
            equity=1_000_000.0,
            dry_run=False,
            open_order_symbols=set(),
            existing_positions={"SPY260101P00580000": existing},
        )

        assert len(client.submitted_orders) == 1
        assert client.submitted_orders[0].qty == 6

    def test_over_hedged_position_buys_nothing_rather_than_negative(self, monkeypatch):
        self._setup(monkeypatch, target_contracts=5)
        client = FakeTradingClient()
        existing = make_position(
            "SPY260101P00580000", qty=20, market_value=18000.0, current_price=9.0,
            asset_class=AssetClass.US_OPTION,
        )

        engine.execute_spy_var_hedge(
            client,
            portfolio_var_dollars=50_000.0,
            equity=1_000_000.0,
            dry_run=False,
            open_order_symbols=set(),
            existing_positions={"SPY260101P00580000": existing},
        )

        assert client.submitted_orders == []

    def test_non_option_positions_never_count_as_existing_hedge(self, monkeypatch):
        """A plain equity position named e.g. 'SPYX' must never be mistaken for a hedge."""
        self._setup(monkeypatch, target_contracts=10)
        client = FakeTradingClient()
        equity_position = make_position("SPY", qty=999, market_value=99_900.0, current_price=100.0)

        engine.execute_spy_var_hedge(
            client,
            portfolio_var_dollars=50_000.0,
            equity=1_000_000.0,
            dry_run=False,
            open_order_symbols=set(),
            existing_positions={"SPY": equity_position},
        )

        assert len(client.submitted_orders) == 1
        assert client.submitted_orders[0].qty == 10

    def test_dry_run_reports_incremental_amount_and_submits_nothing(self, monkeypatch):
        self._setup(monkeypatch, target_contracts=10)
        client = FakeTradingClient()
        existing = make_position(
            "SPY260101P00580000", qty=4, market_value=3600.0, current_price=9.0,
            asset_class=AssetClass.US_OPTION,
        )

        engine.execute_spy_var_hedge(
            client,
            portfolio_var_dollars=50_000.0,
            equity=1_000_000.0,
            dry_run=True,
            existing_positions={"SPY260101P00580000": existing},
        )

        assert client.submitted_orders == []


class TestHedgeContractNonFungibility:
    """
    A held SPY put at a DIFFERENT strike or expiry than the one selected
    for a new hedge this run must NOT be treated as equivalent
    protection — only an exact contract match (same OCC symbol == same
    strike AND expiry) is credited toward incremental sizing. Different
    contracts are reported separately, never summed into the same
    "already held" figure.
    """

    def _setup(self, monkeypatch, target_contracts: int, selected_symbol: str, selected_strike: float):
        import datetime

        from tests.conftest import make_option_contract

        monkeypatch.setattr(engine, "get_ticker_object", lambda symbol: object())
        monkeypatch.setattr(engine, "get_current_price", lambda ticker_obj: 580.0)
        future_expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        contract = make_option_contract(selected_symbol, strike_price=selected_strike, expiration_date=future_expiry)
        monkeypatch.setattr(engine, "_select_atm_put_contract", lambda client, price, days: contract)
        monkeypatch.setattr(engine, "calculate_spy_hedge", lambda **kwargs: target_contracts)
        return contract

    def test_deep_itm_put_at_different_strike_is_not_credited(self, monkeypatch):
        """
        A deep-ITM put (much higher strike than the newly selected ATM
        contract) pays off very differently per contract under the same
        stress scenario — its raw count must not reduce the new ATM
        contract's target size.
        """
        self._setup(monkeypatch, target_contracts=10, selected_symbol="SPY260101P00580000", selected_strike=580.0)
        client = FakeTradingClient()
        deep_itm_put = make_position(
            "SPY260101P00650000", qty=10, market_value=70_000.0, current_price=700.0,
            asset_class=AssetClass.US_OPTION,
        )

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=50_000.0, equity=10_000_000.0, dry_run=False,
            open_order_symbols=set(), existing_positions={"SPY260101P00650000": deep_itm_put},
        )

        # The deep-ITM put's 10 contracts must NOT be treated as covering
        # the new ATM target — the full 10-contract target is still bought.
        assert len(client.submitted_orders) == 1
        assert client.submitted_orders[0].qty == 10
        assert client.submitted_orders[0].symbol == "SPY260101P00580000"

    def test_same_strike_different_expiry_is_not_credited(self, monkeypatch):
        """Same strike, different expiry -> different OCC symbol -> not fungible."""
        self._setup(monkeypatch, target_contracts=10, selected_symbol="SPY260101P00580000", selected_strike=580.0)
        client = FakeTradingClient()
        different_expiry_put = make_position(
            "SPY260215P00580000", qty=10, market_value=9_000.0, current_price=9.0,
            asset_class=AssetClass.US_OPTION,
        )

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=50_000.0, equity=10_000_000.0, dry_run=False,
            open_order_symbols=set(), existing_positions={"SPY260215P00580000": different_expiry_put},
        )

        assert len(client.submitted_orders) == 1
        assert client.submitted_orders[0].qty == 10

    def test_exact_match_contract_is_credited_but_other_strikes_are_not(self, monkeypatch):
        """
        Mixed holdings: some contracts match the newly selected one
        (credited, reduce the target) and some don't (reported
        separately, not credited) — both in the same run.
        """
        self._setup(monkeypatch, target_contracts=10, selected_symbol="SPY260101P00580000", selected_strike=580.0)
        client = FakeTradingClient()
        matching = make_position(
            "SPY260101P00580000", qty=4, market_value=3_600.0, current_price=9.0,
            asset_class=AssetClass.US_OPTION,
        )
        deep_itm_put = make_position(
            "SPY260101P00650000", qty=7, market_value=49_000.0, current_price=700.0,
            asset_class=AssetClass.US_OPTION,
        )

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=50_000.0, equity=10_000_000.0, dry_run=False,
            open_order_symbols=set(),
            existing_positions={"SPY260101P00580000": matching, "SPY260101P00650000": deep_itm_put},
        )

        # Only the 4 matching contracts reduce the target (10 - 4 = 6),
        # the 7 deep-ITM contracts at a different strike are irrelevant
        # to this contract's incremental sizing.
        assert len(client.submitted_orders) == 1
        assert client.submitted_orders[0].qty == 6

    def test_exposure_helper_reports_other_contracts_separately(self):
        """
        `_existing_spy_hedge_exposure` itself must expose non-matching
        SPY put positions distinctly from the matching-contract quantity
        — never folded into a single fungible total.
        """
        matching = make_position(
            "SPY260101P00580000", qty=4, market_value=3_600.0, current_price=9.0,
            asset_class=AssetClass.US_OPTION,
        )
        deep_itm_put = make_position(
            "SPY260101P00650000", qty=7, market_value=49_000.0, current_price=700.0,
            asset_class=AssetClass.US_OPTION,
        )
        different_expiry_put = make_position(
            "SPY260215P00580000", qty=2, market_value=1_800.0, current_price=9.0,
            asset_class=AssetClass.US_OPTION,
        )

        exposure = engine._existing_spy_hedge_exposure(
            {
                "SPY260101P00580000": matching,
                "SPY260101P00650000": deep_itm_put,
                "SPY260215P00580000": different_expiry_put,
            },
            selected_contract_symbol="SPY260101P00580000",
        )

        assert exposure.matching_contract_qty == 4
        assert exposure.matching_contract_market_value == pytest.approx(3_600.0)
        assert exposure.other_contracts == {"SPY260101P00650000": 7, "SPY260215P00580000": 2}
        assert exposure.other_contracts_qty == 9
        assert exposure.other_contracts_market_value == pytest.approx(50_800.0)
        assert exposure.total_market_value == pytest.approx(54_400.0)

    def test_hedge_budget_is_reduced_by_total_existing_spy_put_market_value(self, monkeypatch):
        """
        The hedge budget must account for BOTH existing (matching and
        other-contract) exposure and the newly requested purchase — not
        just this run's incremental contract count — so total premium
        outstanding stays within HEDGE_BUDGET_FRACTION_OF_EQUITY of equity.
        """
        import datetime

        from tests.conftest import make_option_contract

        monkeypatch.setattr(engine, "get_ticker_object", lambda symbol: object())
        monkeypatch.setattr(engine, "get_current_price", lambda ticker_obj: 580.0)
        future_expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        contract = make_option_contract("SPY260101P00580000", strike_price=580.0, expiration_date=future_expiry)
        monkeypatch.setattr(engine, "_select_atm_put_contract", lambda client, price, days: contract)

        captured_budget = {}

        def fake_calculate_spy_hedge(**kwargs):
            captured_budget["hedge_budget_dollars"] = kwargs["hedge_budget_dollars"]
            return 10

        monkeypatch.setattr(engine, "calculate_spy_hedge", fake_calculate_spy_hedge)

        equity = 1_000_000.0  # HEDGE_BUDGET_FRACTION_OF_EQUITY (2%) -> $20,000 raw budget
        other_contract = make_position(
            "SPY260101P00650000", qty=5, market_value=5_000.0, current_price=1000.0,
            asset_class=AssetClass.US_OPTION,
        )
        client = FakeTradingClient()

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=50_000.0, equity=equity, dry_run=False,
            open_order_symbols=set(), existing_positions={"SPY260101P00650000": other_contract},
        )

        raw_budget = engine.HEDGE_BUDGET_FRACTION_OF_EQUITY * equity
        assert captured_budget["hedge_budget_dollars"] == pytest.approx(raw_budget - 5_000.0)


class TestHedgeJointBuyingPowerSafety:
    """
    M1: the SPY hedge purchase must be constrained against a FRESH,
    broker-reported buying-power reading taken immediately before sizing
    — not just the fixed HEDGE_BUDGET_FRACTION_OF_EQUITY policy budget —
    so a hedge can never be sized against capacity already consumed by
    the equity-rebalance phase's own buys. A failed or invalid fresh
    reading must skip the hedge entirely rather than fall back to
    unconstrained (guessed) sizing.
    """

    COST_PER_CONTRACT = 1_000.0  # arbitrary fake per-contract premium, used only by the fake below

    def _setup(self, monkeypatch, target_contracts: int):
        import datetime

        from tests.conftest import make_option_contract

        monkeypatch.setattr(engine, "get_ticker_object", lambda symbol: object())
        monkeypatch.setattr(engine, "get_current_price", lambda ticker_obj: 580.0)
        future_expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        contract = make_option_contract(
            "SPY260101P00580000", strike_price=580.0, expiration_date=future_expiry
        )
        monkeypatch.setattr(engine, "_select_atm_put_contract", lambda client, price, days: contract)

        captured = {}

        def fake_calculate_spy_hedge(**kwargs):
            # A realistic-enough stand-in: still genuinely capped by
            # hedge_budget_dollars (via a fake, fixed per-contract cost),
            # unlike a plain fixed-return mock — these tests exist
            # specifically to prove `hedge_budget_dollars` is computed and
            # ENFORCED correctly, so the fake must actually respect it.
            budget = kwargs["hedge_budget_dollars"]
            captured["hedge_budget_dollars"] = budget
            if budget is None:
                return target_contracts
            if budget <= 0:
                return 0
            affordable = int(budget // TestHedgeJointBuyingPowerSafety.COST_PER_CONTRACT)
            return min(target_contracts, affordable)

        monkeypatch.setattr(engine, "calculate_spy_hedge", fake_calculate_spy_hedge)
        return captured

    def test_buying_power_fully_consumed_blocks_the_hedge(self, monkeypatch):
        """Equity purchases used up all buying power -> no hedge order is submitted."""
        captured = self._setup(monkeypatch, target_contracts=10)
        # Policy budget alone would easily afford this (huge equity), but
        # buying power is the binding, exhausted constraint.
        client = FakeTradingClient(equity=50_000_000.0, buying_power=0.0)

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=100_000.0, equity=50_000_000.0, dry_run=False,
            open_order_symbols=set(), existing_positions={},
        )

        assert captured["hedge_budget_dollars"] == pytest.approx(0.0)
        assert client.submitted_orders == []

    def test_partial_buying_power_caps_the_hedge_budget(self, monkeypatch):
        """Some buying power remains (less than the policy budget) -> that remainder becomes the ceiling."""
        captured = self._setup(monkeypatch, target_contracts=10)
        client = FakeTradingClient(equity=50_000_000.0, buying_power=4_321.0)

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=100_000.0, equity=50_000_000.0, dry_run=False,
            open_order_symbols=set(), existing_positions={},
        )

        assert captured["hedge_budget_dollars"] == pytest.approx(4_321.0)
        assert len(client.submitted_orders) == 1
        # 4 contracts affordable at the fake's $1,000/contract, capped below the 10-contract target.
        assert client.submitted_orders[0].qty == 4

    def test_policy_budget_smaller_than_buying_power_remains_binding(self, monkeypatch):
        """Buying power is ample; the fixed HEDGE_BUDGET_FRACTION_OF_EQUITY policy budget is still the binding ceiling."""
        captured = self._setup(monkeypatch, target_contracts=10)
        equity = 100_000.0  # policy budget = 2% * 100,000 = $2,000
        client = FakeTradingClient(equity=equity, buying_power=10_000_000.0)

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=100_000.0, equity=equity, dry_run=False,
            open_order_symbols=set(), existing_positions={},
        )

        assert captured["hedge_budget_dollars"] == pytest.approx(2_000.0)
        assert len(client.submitted_orders) == 1
        # 2 contracts affordable at the fake's $1,000/contract policy budget, far below
        # what the (huge, non-binding) buying power alone would allow.
        assert client.submitted_orders[0].qty == 2

    def test_fresh_account_fetch_failure_skips_the_hedge_entirely(self, monkeypatch):
        """An APIError fetching the account must skip the hedge, never fall back to unconstrained sizing."""
        from alpaca.common.exceptions import APIError

        self._setup(monkeypatch, target_contracts=10)

        class BrokenAccountClient(FakeTradingClient):
            def get_account(self):
                raise APIError("account fetch failed")

        client = BrokenAccountClient(equity=1_000_000.0)

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=100_000.0, equity=1_000_000.0, dry_run=False,
            open_order_symbols=set(), existing_positions={},
        )

        assert client.submitted_orders == []

    @pytest.mark.parametrize("bad_buying_power", ["nan", "inf", "-100", "not-a-number", None])
    def test_invalid_fresh_buying_power_skips_the_hedge(self, monkeypatch, bad_buying_power):
        """NaN, infinite, negative, malformed, or missing buying_power must all skip the hedge."""
        import types

        self._setup(monkeypatch, target_contracts=10)

        class BadBuyingPowerClient(FakeTradingClient):
            def get_account(self):
                return types.SimpleNamespace(
                    equity=str(self.equity), cash=str(self.cash), buying_power=bad_buying_power
                )

        client = BadBuyingPowerClient(equity=1_000_000.0)

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=100_000.0, equity=1_000_000.0, dry_run=False,
            open_order_symbols=set(), existing_positions={},
        )

        assert client.submitted_orders == []

    def test_dry_run_never_calls_get_account_for_this_check(self, monkeypatch):
        """A dry run must never contact the broker for the buying-power check."""
        self._setup(monkeypatch, target_contracts=10)

        class AccountCallCountingClient(FakeTradingClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.get_account_calls = 0

            def get_account(self):
                self.get_account_calls += 1
                return super().get_account()

        client = AccountCallCountingClient(equity=1_000_000.0)

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=100_000.0, equity=1_000_000.0, dry_run=True,
            open_order_symbols=set(), existing_positions={},
        )

        assert client.get_account_calls == 0
        assert client.submitted_orders == []

    def test_existing_exact_contract_holdings_still_subtracted_after_buying_power_cap(self, monkeypatch):
        """
        already_held (exact-contract match) is subtracted from the
        BUDGET-CAPPED target, not some pre-buying-power-cap figure — the
        two safety mechanisms (M1's buying-power cap, the earlier
        exact-contract incremental-sizing fix) must compose correctly.
        """
        captured = self._setup(monkeypatch, target_contracts=10)
        client = FakeTradingClient(equity=50_000_000.0, buying_power=10_000_000.0)  # buying power not binding
        existing = make_position(
            "SPY260101P00580000", qty=3, market_value=2_700.0, current_price=9.0,
            asset_class=AssetClass.US_OPTION,
        )

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=100_000.0, equity=50_000_000.0, dry_run=False,
            open_order_symbols=set(), existing_positions={"SPY260101P00580000": existing},
        )

        assert captured["hedge_budget_dollars"] is not None
        assert len(client.submitted_orders) == 1
        assert client.submitted_orders[0].qty == 7  # target 10 - already_held 3
