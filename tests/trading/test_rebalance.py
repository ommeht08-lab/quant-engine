"""
Group B: rebalancing and fills.

Bidirectional rebalance, partial fills, rejected/pending orders,
duplicate-run idempotency, sector-cap enforcement with explicit cash, and
option-hedge-position protection during liquidation — all against
`FakeTradingClient`, never a real Alpaca connection.
"""

import pytest
from alpaca.trading.enums import AssetClass, OrderStatus

from src.backtesting.historical_tester import TickerAnalysis
from src.trading import alpaca_execution as engine
from tests.conftest import ClockTogglingClient, FakeTradingClient, make_position


def _pick(ticker, sector="Technology", beta=1.0, conviction=1.0):
    return TickerAnalysis(
        ticker=ticker,
        as_of_date="2024-01-01",
        sector=sector,
        beta=beta,
        conviction_score=conviction,
        historical_price=100.0,
    )


class TestBidirectionalRebalance:
    def test_underweight_pick_is_bought(self, monkeypatch):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert len(client.submitted_orders) == 1
        order = client.submitted_orders[0]
        assert order.symbol == "AAPL"
        assert order.side.value == "buy"
        assert results[0]["side"] == "buy"
        assert results[0]["status"] in ("ORDER FILLED",)

    def test_overweight_pick_is_sold_down(self, monkeypatch):
        pick = _pick("AAPL")
        # Held far above the target weight (target ~100% of equity for a
        # single pick, held position is already worth $95k of $100k
        # equity — no drift here — so instead hold above 100% target to
        # force a genuine overweight/sell scenario).
        position = make_position("AAPL", qty=1000, market_value=99_000.0, current_price=150.0)
        client = FakeTradingClient(positions=[position])
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)

        # A single pick's target weight is ~100% (inverse-beta weighting
        # over one name normalizes to 1.0), so make the held position
        # LARGER than target by holding way more equity's worth than the
        # account has, forcing target_notional < current_notional.
        results = engine.rebalance_target_positions(
            client, {"AAPL": position}, [pick], equity=50_000.0, dry_run=False, open_order_symbols=set()
        )

        assert len(client.submitted_orders) == 1
        order = client.submitted_orders[0]
        assert order.symbol == "AAPL"
        assert order.side.value == "sell"
        assert results[0]["side"] == "sell"
        assert results[0]["order_notional"] < 0

    def test_within_drift_threshold_is_skipped(self):
        pick = _pick("AAPL")
        # Held very close to the single-pick 100% target weight.
        position = make_position("AAPL", qty=1000, market_value=99_500.0, current_price=150.0)
        client = FakeTradingClient(positions=[position])

        results = engine.rebalance_target_positions(
            client, {"AAPL": position}, [pick], equity=100_000.0, dry_run=False
        )

        assert client.submitted_orders == []
        assert "SKIPPED" in results[0]["status"]


class TestFillHandling:
    def test_partial_fill_records_actual_filled_quantity_not_requested(self, monkeypatch):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        # PARTIALLY_FILLED is deliberately non-terminal (a real order can
        # still fill more), so this polls the full window — mock the
        # sleep between polls so the test stays fast.
        monkeypatch.setattr(engine.time, "sleep", lambda *_: None)
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        client.script_fill(
            "AAPL",
            statuses=[OrderStatus.PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED],
            filled_qtys=[3.0, 3.0, 3.0],
            filled_avg_prices=[150.0, 150.0, 150.0],
        )

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert "PARTIALLY FILLED" in results[0]["status"]
        assert "qty=3.0" in results[0]["status"]

    def test_rejected_order_is_not_logged_as_a_trade(self, monkeypatch):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        client.script_fill(
            "AAPL", statuses=[OrderStatus.REJECTED], filled_qtys=[None], filled_avg_prices=[None]
        )

        logged = []
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: logged.append(kwargs))

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert results[0]["status"] == "REJECTED"
        assert logged == []

    def test_still_pending_order_is_not_logged_as_filled(self, monkeypatch):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        # Stays NEW for every poll attempt (never resolves within the window).
        client.script_fill(
            "AAPL",
            statuses=[OrderStatus.NEW] * engine.ORDER_POLL_MAX_ATTEMPTS,
            filled_qtys=[None] * engine.ORDER_POLL_MAX_ATTEMPTS,
            filled_avg_prices=[None] * engine.ORDER_POLL_MAX_ATTEMPTS,
        )
        monkeypatch.setattr(engine.time, "sleep", lambda *_: None)  # don't actually wait in tests

        logged = []
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: logged.append(kwargs))

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert "PENDING" in results[0]["status"]
        assert logged == []


class TestDuplicateRunIdempotency:
    def test_open_order_prevents_duplicate_submission(self):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols={"AAPL"}
        )

        assert client.submitted_orders == []
        assert results[0]["status"] == "SKIPPED (open order already pending)"

    def test_failed_open_orders_lookup_skips_every_submission(self):
        """None (lookup failed) must fail safe: skip everything rather than risk a duplicate."""
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=None
        )

        assert client.submitted_orders == []
        assert results[0]["status"] == "SKIPPED (open order already pending)"


class TestSectorCapAndCash:
    def test_capped_weights_leave_explicit_cash(self):
        # 4 picks in the same sector, each with equal beta -> equal raw
        # weight (25% each) before caps; MAX_SECTOR_WEIGHT (25%) caps the
        # WHOLE sector's combined weight, so with only one sector
        # represented, nothing else can absorb the excess -> leftover
        # cash rather than a cap breach.
        picks = [_pick(f"T{i}", sector="Technology", beta=1.0) for i in range(4)]
        weights = engine.calculate_inverse_beta_weights(picks)

        total_allocated = sum(weights.values())
        assert total_allocated <= engine.MAX_SECTOR_WEIGHT + 1e-9
        cash_weight = 1.0 - total_allocated
        assert cash_weight >= 0.0


class TestPostFillCapEnforcement:
    """
    `_check_post_fill_caps` must do more than warn: a single position
    over MAX_POSITION_WEIGHT is corrected with a trim-to-cap SELL (an
    unambiguous single ticker to act on); a SECTOR over MAX_SECTOR_WEIGHT
    spans multiple tickers and is left for the caller to report as an
    incomplete rebalance instead of being guessed at.
    """

    def test_position_within_caps_returns_true_no_orders(self):
        position = make_position("AAPL", qty=100, market_value=10_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False
        )

        assert result is True
        assert client.submitted_orders == []

    def test_position_over_cap_is_trimmed_and_reports_complete(self, monkeypatch):
        # weight = 20_000 / 100_000 = 20% > MAX_POSITION_WEIGHT (15%)
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is True
        assert len(client.submitted_orders) == 1
        order = client.submitted_orders[0]
        assert order.symbol == "AAPL"
        assert order.side.value == "sell"
        assert order.notional == pytest.approx(5_000.0)  # 20_000 - (0.15 * 100_000)

    def test_dry_run_detects_breach_without_trimming(self):
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=True
        )

        assert result is False
        assert client.submitted_orders == []

    def test_failed_trim_reports_incomplete(self):
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])
        client.script_fill(
            "AAPL", statuses=[OrderStatus.REJECTED], filled_qtys=[None], filled_avg_prices=[None]
        )

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is False
        assert len(client.submitted_orders) == 1  # the trim was attempted

    def test_tiny_partial_fill_remains_over_cap_and_reports_incomplete(self, monkeypatch):
        """
        Regression for Finding 1: a partial fill must be judged by its
        CONFIRMED notional, never treated as proof the cap was restored
        just because the fill was positive. weight = 20_000/100_000 =
        20% > 15% cap; a full trim needs $5,000. Here only 1 share @
        $100 actually fills — remaining weight is still ~19.9%, nowhere
        near the 15% cap — so this must be reported incomplete.
        """
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        client.script_fill(
            "AAPL", statuses=[OrderStatus.FILLED], filled_qtys=[1.0], filled_avg_prices=[100.0]
        )

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is False
        assert len(client.submitted_orders) == 1  # the trim was attempted, just insufficient

    def test_sufficient_partial_fill_reaches_the_cap(self, monkeypatch):
        """
        A partial fill whose CONFIRMED notional actually covers the
        required trim amount must still report complete — the fix must
        not become "any partial fill is incomplete," only "an
        insufficient one is."
        """
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        # 50 shares @ $100 = exactly the $5,000 required to reach the cap.
        client.script_fill(
            "AAPL", statuses=[OrderStatus.FILLED], filled_qtys=[50.0], filled_avg_prices=[100.0]
        )

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is True

    def test_pending_trim_order_reports_incomplete(self, monkeypatch):
        """A trim SELL still non-terminal after the full polling window must report incomplete, not filled."""
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])
        monkeypatch.setattr(engine.time, "sleep", lambda *_: None)
        client.script_fill(
            "AAPL",
            statuses=[OrderStatus.NEW] * engine.ORDER_POLL_MAX_ATTEMPTS,
            filled_qtys=[0.0] * engine.ORDER_POLL_MAX_ATTEMPTS,
            filled_avg_prices=[None] * engine.ORDER_POLL_MAX_ATTEMPTS,
        )

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is False

    def test_unconfirmed_trim_order_reports_incomplete(self, monkeypatch):
        """If the order's status can't be confirmed at all (lookup failure), must report incomplete."""
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])
        monkeypatch.setattr(engine, "_await_order_resolution", lambda *a, **k: None)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is False

    def test_sector_breach_across_multiple_positions_is_not_auto_corrected(self):
        # Each position is individually within MAX_POSITION_WEIGHT (10% each),
        # but three of them in the same sector sum to 30% > MAX_SECTOR_WEIGHT (25%).
        positions = {
            f"T{i}": make_position(f"T{i}", qty=100, market_value=10_000.0, current_price=100.0)
            for i in range(3)
        }
        client = FakeTradingClient(positions=list(positions.values()))
        top_picks = [_pick(f"T{i}", sector="Technology") for i in range(3)]

        result = engine._check_post_fill_caps(client, positions, equity=100_000.0, top_picks=top_picks, dry_run=False)

        assert result is False
        assert client.submitted_orders == []  # no per-ticker trim attempted for a sector-level breach

    def test_mixed_position_and_sector_breach_compose_correctly_in_one_call(self, monkeypatch):
        """
        Both breach types in the SAME call: T0 individually exceeds
        MAX_POSITION_WEIGHT (auto-corrected via a trim) while T0+T1+T2
        together (all Technology) also exceed MAX_SECTOR_WEIGHT (left
        uncorrected). The position-level trim must not be skipped just
        because a sector-level breach also exists, and the sector
        breach must still be reported even after the position breach is
        corrected — both code paths must compose, not short-circuit
        each other.
        """
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        # T0: 20% of equity -> exceeds MAX_POSITION_WEIGHT (15%) on its own.
        # T1, T2: 8% each. Sector total = 20 + 8 + 8 = 36% -> exceeds
        # MAX_SECTOR_WEIGHT (25%) even after T0 gets trimmed to 15%
        # (15 + 8 + 8 = 31% still > 25%).
        positions = {
            "T0": make_position("T0", qty=200, market_value=20_000.0, current_price=100.0),
            "T1": make_position("T1", qty=80, market_value=8_000.0, current_price=100.0),
            "T2": make_position("T2", qty=80, market_value=8_000.0, current_price=100.0),
        }
        client = FakeTradingClient(positions=list(positions.values()))
        top_picks = [_pick(f"T{i}", sector="Technology") for i in range(3)]

        result = engine._check_post_fill_caps(
            client, positions, equity=100_000.0, top_picks=top_picks, dry_run=False, open_order_symbols=set()
        )

        # Overall result must still report incomplete (the sector breach
        # was never resolved)...
        assert result is False
        # ...but the position-level breach WAS still corrected: exactly
        # one trim SELL, for T0, sized to bring it down to the 15% cap.
        assert len(client.submitted_orders) == 1
        order = client.submitted_orders[0]
        assert order.symbol == "T0"
        assert order.side.value == "sell"
        assert order.notional == pytest.approx(5_000.0)  # 20_000 - (0.15 * 100_000)


class TestPostFillCapNotionalTolerance:
    """
    Regression for Track A Phase 1.5B discrepancy 4: the post-trim
    tolerance must be a small, fixed DOLLAR amount (one cent + a
    float-precision epsilon), never a fixed WEIGHT-FRACTION percentage
    that scales into real dollars on a larger account. All scenarios use
    a $100,000 equity account: MAX_POSITION_WEIGHT (15%) caps a position
    at $15,000; a position starting at $20,000 (20%) needs a $5,000 trim.
    """

    @staticmethod
    def _scripted_position_and_client(filled_qty, filled_price=100.0):
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])
        client.script_fill(
            "AAPL", statuses=[OrderStatus.FILLED], filled_qtys=[filled_qty], filled_avg_prices=[filled_price]
        )
        return position, client

    def test_remaining_value_exactly_at_the_cap_reports_complete(self, monkeypatch):
        """Trim fills exactly $5,000 (50 shares @ $100) -- remaining lands exactly at the $15,000 cap."""
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        position, client = self._scripted_position_and_client(filled_qty=50.0)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is True

    def test_sub_cent_rounding_residual_reports_complete(self, monkeypatch):
        """A fraction-of-a-cent residual ($0.005 over cap) is pure float noise, not a real shortfall."""
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        # 49.99995 shares @ $100 = $4,999.995 filled -> remaining = $15,000.005 (cap + $0.005).
        position, client = self._scripted_position_and_client(filled_qty=49.99995)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is True

    def test_one_cent_residual_reports_complete(self, monkeypatch):
        """Exactly a one-cent residual ($15,000.01) is within POST_TRIM_NOTIONAL_TOLERANCE_DOLLARS."""
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        # 49.9999 shares @ $100 = $4,999.99 filled -> remaining = $15,000.01 (cap + $0.01).
        position, client = self._scripted_position_and_client(filled_qty=49.9999)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is True

    def test_one_dollar_residual_reports_incomplete(self, monkeypatch):
        """A genuine $1 residual ($15,001) is a real shortfall -- must be reported incomplete."""
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        # 49.99 shares @ $100 = $4,999 filled -> remaining = $15,001 (cap + $1).
        position, client = self._scripted_position_and_client(filled_qty=49.99)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is False

    def test_fifty_dollar_residual_reports_incomplete(self, monkeypatch):
        """A genuine $50 residual ($15,050) must be reported incomplete."""
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        # 49.5 shares @ $100 = $4,950 filled -> remaining = $15,050 (cap + $50).
        position, client = self._scripted_position_and_client(filled_qty=49.5)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is False

    def test_one_hundred_dollar_residual_reports_incomplete(self, monkeypatch):
        """
        A genuine $100 residual ($15,100) must be reported incomplete --
        this is the EXACT margin the old 0.1-percentage-point weight
        tolerance (worth $100 on a $100,000 account) would have wrongly
        accepted as 'close enough.'
        """
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        # 49 shares @ $100 = $4,900 filled -> remaining = $15,100 (cap + $100).
        position, client = self._scripted_position_and_client(filled_qty=49.0)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is False

    def test_tiny_partial_fill_reports_incomplete(self, monkeypatch):
        """A tiny partial fill (1 share of the 50 needed) is nowhere close -- must be incomplete."""
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        position, client = self._scripted_position_and_client(filled_qty=1.0)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is False

    def test_full_fill_reports_complete(self, monkeypatch):
        """A full $5,000 fill (default unscripted fill path) restores the cap exactly -- must be complete."""
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])  # no script -> default full fill at requested notional

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is True

    def test_sufficient_partial_fill_reports_complete(self, monkeypatch):
        """A partial fill that still fully covers the required trim (55 of 50 shares needed) must be complete."""
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        position, client = self._scripted_position_and_client(filled_qty=55.0)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is True

    def test_dollar_tolerance_is_not_wide_enough_to_be_a_weight_fraction_in_disguise(self):
        """
        Sanity check on the constant itself: on a very large account, the
        old 0.1-percentage-point tolerance would have been worth
        thousands of dollars. The new tolerance must stay a fixed, tiny
        dollar amount regardless of account size.
        """
        assert engine.POST_TRIM_NOTIONAL_TOLERANCE_DOLLARS < 1.0  # comfortably under $1, on ANY account size
        assert engine.POST_TRIM_NOTIONAL_TOLERANCE_DOLLARS > 0.01 - 1e-9  # at least covers a literal cent

    def test_sector_total_uses_proven_remaining_weight_not_assumed_restored_weight(self, monkeypatch, caplog):
        """
        The sector-level aggregate must be computed from T0's ACTUAL
        proven remaining weight (16%, after an insufficient trim) --
        never from the weight it WOULD have had if the trim were assumed
        to have fully restored it to the 15% cap. With the correct
        (proven) weight, T0 (16%) + T1 (5%) + T2 (4.99%) = 25.99% >
        MAX_SECTOR_WEIGHT (25%) and the sector-breach warning must fire.
        With the (buggy) assumed weight, 15% + 5% + 4.99% = 24.99% would
        NOT have exceeded 25%, and the warning would never have appeared.
        """
        import logging

        caplog.set_level(logging.WARNING, logger="src.trading.alpaca_execution")
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)

        # T0: $20,000 (20%) -> needs a $5,000 trim to reach the $15,000
        # cap, but only $4,000 fills -> remaining = $16,000 (16%).
        t0 = make_position("T0", qty=200, market_value=20_000.0, current_price=100.0)
        t1 = make_position("T1", qty=50, market_value=5_000.0, current_price=100.0)
        t2 = make_position("T2", qty=49, market_value=4_990.0, current_price=100.0)
        positions = {"T0": t0, "T1": t1, "T2": t2}
        client = FakeTradingClient(positions=list(positions.values()))
        client.script_fill("T0", statuses=[OrderStatus.FILLED], filled_qtys=[40.0], filled_avg_prices=[100.0])
        top_picks = [_pick("T0", sector="Technology"), _pick("T1", sector="Technology"), _pick("T2", sector="Technology")]

        result = engine._check_post_fill_caps(
            client, positions, equity=100_000.0, top_picks=top_picks, dry_run=False, open_order_symbols=set()
        )

        assert result is False  # T0's own insufficient trim already makes this incomplete
        sector_breach_logged = any(
            "sector" in record.message.lower() and "MAX_SECTOR_WEIGHT" in record.message
            for record in caplog.records
        )
        assert sector_breach_logged, (
            "Sector total must have used T0's PROVEN remaining weight (16%), not an assumed "
            "restored-to-cap weight (15%) -- only the proven weight pushes the sector total "
            "(16% + 5% + 4.99% = 25.99%) over MAX_SECTOR_WEIGHT (25%)."
        )


class TestOptionHedgePositionProtection:
    def test_option_position_is_never_liquidated_as_non_target_equity(self):
        hedge_position = make_position(
            "SPY260101P00580000", qty=2, market_value=1800.0, current_price=9.0,
            asset_class=AssetClass.US_OPTION,
        )
        client = FakeTradingClient(positions=[hedge_position])

        results = engine.liquidate_non_target_positions(
            client,
            {"SPY260101P00580000": hedge_position},
            target_tickers=set(),  # the hedge symbol is (correctly) not a Top-N target
            analyses_by_ticker={},
            dry_run=False,
            open_order_symbols=set(),
        )

        assert client.closed_symbols == []
        assert results == []

    def test_equity_position_not_in_targets_is_still_liquidated(self, monkeypatch):
        equity_position = make_position("OLD", qty=5, market_value=500.0, current_price=100.0)
        client = FakeTradingClient(positions=[equity_position])
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)

        results = engine.liquidate_non_target_positions(
            client, {"OLD": equity_position}, target_tickers=set(), analyses_by_ticker={}, dry_run=False,
            open_order_symbols=set(),
        )

        assert client.closed_symbols == ["OLD"]
        assert results[0]["status"] == "LIQUIDATED"


class TestMarketClockRecheckedPerOrder:
    """
    The market clock must be rechecked immediately before EACH individual
    order submission — liquidations, rebalance buys/sells, post-fill cap
    trims, and the hedge buy — not once for the whole run before a
    (multi-minute) scan starts. A closed-market recheck must skip only
    THAT specific submission with an explicit status, never silently
    convert the entire run into a dry run.
    """

    def test_liquidation_market_closes_after_first_order_skips_second(self, monkeypatch):
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pos_a = make_position("AAA", qty=10, market_value=1_000.0, current_price=100.0)
        pos_b = make_position("BBB", qty=10, market_value=1_000.0, current_price=100.0)
        client = ClockTogglingClient(positions=[pos_a, pos_b], open_for_calls=1)

        results = engine.liquidate_non_target_positions(
            client, {"AAA": pos_a, "BBB": pos_b}, target_tickers=set(), analyses_by_ticker={},
            dry_run=False, open_order_symbols=set(),
        )

        statuses = {r["symbol"]: r["status"] for r in results}
        assert statuses["AAA"] == "LIQUIDATED"
        assert statuses["BBB"] == "SKIPPED (market closed)"
        assert client.closed_symbols == ["AAA"]

    def test_rebalance_market_closed_at_submission_skips_with_explicit_status(self):
        pick = _pick("AAPL")
        client = ClockTogglingClient(positions=[], default_fill_price=150.0, open_for_calls=0)

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert client.submitted_orders == []
        assert results[0]["status"] == "SKIPPED (market closed)"

    def test_rebalance_two_picks_second_skipped_when_market_closes_mid_batch(self, monkeypatch):
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pick_a = _pick("AAA")
        pick_b = _pick("BBB")
        client = ClockTogglingClient(positions=[], default_fill_price=100.0, open_for_calls=1)

        results = engine.rebalance_target_positions(
            client, {}, [pick_a, pick_b], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        statuses = {r["symbol"]: r["status"] for r in results}
        # Whichever pick submits first gets through (clock open on call #1);
        # the other must be explicitly skipped for a closed market, not
        # silently treated as though the whole run were a dry run.
        assert list(statuses.values()).count("ORDER FILLED") == 1
        assert "SKIPPED (market closed)" in statuses.values()
        assert len(client.submitted_orders) == 1

    def test_hedge_market_closed_at_submission_skips(self, monkeypatch):
        import datetime

        from tests.conftest import make_option_contract

        monkeypatch.setattr(engine, "get_ticker_object", lambda symbol: object())
        monkeypatch.setattr(engine, "get_current_price", lambda ticker_obj: 580.0)
        future_expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        contract = make_option_contract("SPY_TEST_PUT", strike_price=580.0, expiration_date=future_expiry)
        monkeypatch.setattr(engine, "_select_atm_put_contract", lambda client, price, days: contract)
        monkeypatch.setattr(engine, "calculate_spy_hedge", lambda **kwargs: 5)

        client = ClockTogglingClient(open_for_calls=0)

        engine.execute_spy_var_hedge(
            client, portfolio_var_dollars=50_000.0, equity=1_000_000.0, dry_run=False,
            open_order_symbols=set(), existing_positions={},
        )

        assert client.submitted_orders == []

    def test_trim_market_closed_at_submission_skips_and_reports_incomplete(self):
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = ClockTogglingClient(positions=[position], open_for_calls=0)

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=set(),
        )

        assert result is False
        assert client.submitted_orders == []

    def test_scan_start_open_reading_does_not_pin_later_order_decisions(self):
        """
        An upfront `_market_is_open` reading (e.g. the informational check
        `main()` logs before the scan) must not be cached and reused to
        gate a later order submission — each submission independently
        rechecks. Clock reports open once (consumed here as the "scan
        start" check), then closed for everything after.
        """
        pick = _pick("AAPL")
        client = ClockTogglingClient(positions=[], default_fill_price=150.0, open_for_calls=1)

        assert engine._market_is_open(client) is True  # the upfront/scan-start check

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert client.submitted_orders == []
        assert results[0]["status"] == "SKIPPED (market closed)"


class TestCapTrimDuplicateOrderProtection:
    """
    A corrective post-fill cap trim must not stack an overlapping SELL on
    top of an order still open for the same symbol from earlier in the
    same run (e.g. a rebalance sell that hasn't resolved yet).
    """

    def test_open_order_for_symbol_blocks_the_trim(self):
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols={"AAPL"},  # a still-open order already exists for AAPL
        )

        assert result is False
        assert client.submitted_orders == []

    def test_none_open_order_symbols_fails_safe_and_blocks_the_trim(self):
        """`None` means the open-orders lookup itself failed — every submission must be treated as unsafe."""
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols=None,
        )

        assert result is False
        assert client.submitted_orders == []

    def test_unrelated_symbol_open_order_does_not_block_the_trim(self, monkeypatch):
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        position = make_position("AAPL", qty=200, market_value=20_000.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])

        result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[_pick("AAPL")], dry_run=False,
            open_order_symbols={"MSFT"},  # unrelated open order must not block AAPL's trim
        )

        assert result is True
        assert len(client.submitted_orders) == 1

    def test_rebalance_sell_still_open_prevents_overlapping_corrective_trim(self, monkeypatch):
        """
        End-to-end version of the exact scenario this guards against: a
        rebalance SELL for a symbol is submitted and never resolves to a
        terminal status (still working after the polling window), and
        the post-fill cap-trim phase for that SAME symbol must see it as
        unsafe and skip rather than submitting a second, overlapping SELL.
        """
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pick = _pick("AAPL")
        # Way overweight vs. a single-pick ~100% target, so rebalance
        # issues a SELL.
        position = make_position("AAPL", qty=1000, market_value=99_000.0, current_price=150.0)
        client = FakeTradingClient(positions=[position])
        # Script the rebalance SELL to stay NEW (non-terminal) across every poll.
        client.script_fill(
            "AAPL",
            statuses=[OrderStatus.NEW] * engine.ORDER_POLL_MAX_ATTEMPTS,
            filled_qtys=[0.0] * engine.ORDER_POLL_MAX_ATTEMPTS,
            filled_avg_prices=[None] * engine.ORDER_POLL_MAX_ATTEMPTS,
        )
        open_order_symbols = set()

        rebalance_results = engine.rebalance_target_positions(
            client, {"AAPL": position}, [pick], equity=1_000.0, dry_run=False,
            open_order_symbols=open_order_symbols,
        )
        assert rebalance_results[0]["side"] == "sell"
        assert len(client.submitted_orders) == 1
        # The rebalance sell never resolved to a terminal status, so the
        # in-memory tracker must still consider AAPL pending.
        assert "AAPL" in open_order_symbols

        # Now the post-fill cap-trim phase runs for the SAME symbol.
        trim_result = engine._check_post_fill_caps(
            client, {"AAPL": position}, equity=100_000.0, top_picks=[pick], dry_run=False,
            open_order_symbols=open_order_symbols,
        )

        assert trim_result is False
        # Still exactly one submitted order overall — the trim did NOT
        # stack a second, overlapping SELL on top of the still-open one.
        assert len(client.submitted_orders) == 1


class TestBuyingPowerConstraint:
    """
    Aggregate planned BUY notional must never exceed available buying
    power (refetched after liquidations settle, not the pre-liquidation
    estimate) — insufficient capital scales or skips buys deterministically
    rather than submitting orders that will simply be rejected.
    """

    def test_no_buying_power_arg_disables_the_check(self, monkeypatch):
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        # A single pick's raw inverse-beta weight is 100%, but
        # MAX_POSITION_WEIGHT (15%) caps it — the actual target is 15% of
        # equity, not the full 100%.
        expected_target = engine.MAX_POSITION_WEIGHT * 100_000.0

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert results[0]["status"] == "ORDER FILLED"
        assert client.submitted_orders[0].notional == pytest.approx(expected_target)

    def test_sufficient_buying_power_is_unaffected(self, monkeypatch):
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        expected_target = engine.MAX_POSITION_WEIGHT * 100_000.0

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set(),
            buying_power=100_000.0,  # well above the $15k (position-cap-limited) target
        )

        assert results[0]["status"] == "ORDER FILLED"
        assert client.submitted_orders[0].notional == pytest.approx(expected_target)

    def test_insufficient_buying_power_scales_single_buy_down(self, monkeypatch):
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        # Target (position-cap-limited) is $15k; use a buying power below that.
        buying_power = 6_000.0

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set(),
            buying_power=buying_power,
        )

        assert len(client.submitted_orders) == 1
        assert client.submitted_orders[0].notional == pytest.approx(buying_power)
        assert "capital-constrained" not in results[0]["status"]  # only dry-run status carries that suffix

    def test_insufficient_buying_power_scales_multiple_buys_proportionally(self, monkeypatch):
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        # Different sectors so only the (identical, 15%-each) position
        # cap applies — the sector cap doesn't also kick in and complicate
        # the expected aggregate target.
        pick_a = _pick("AAA", sector="Technology")
        pick_b = _pick("BBB", sector="Healthcare")
        client = FakeTradingClient(positions=[], default_fill_price=100.0)
        aggregate_target = 2 * engine.MAX_POSITION_WEIGHT * 100_000.0  # $30k
        buying_power = 0.6 * aggregate_target  # $18k — 60% of aggregate target

        engine.rebalance_target_positions(
            client, {}, [pick_a, pick_b], equity=100_000.0, dry_run=False, open_order_symbols=set(),
            buying_power=buying_power,
        )

        assert len(client.submitted_orders) == 2
        total_notional = sum(o.notional for o in client.submitted_orders)
        assert total_notional == pytest.approx(buying_power, rel=1e-6)
        # Proportional, not order-dependent: both picks scaled by the same 0.6 factor.
        for order in client.submitted_orders:
            assert order.notional == pytest.approx(0.6 * engine.MAX_POSITION_WEIGHT * 100_000.0, rel=1e-6)

    def test_buying_power_below_min_notional_skips_the_buy_with_reason(self):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set(),
            buying_power=0.50,  # below MIN_ORDER_NOTIONAL_USD once scaled
        )

        assert client.submitted_orders == []
        assert results[0]["status"] == "SKIPPED (insufficient buying power)"

    def test_buying_power_never_constrains_sells(self, monkeypatch):
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pick = _pick("AAPL")
        position = make_position("AAPL", qty=1000, market_value=99_000.0, current_price=150.0)
        client = FakeTradingClient(positions=[position])

        results = engine.rebalance_target_positions(
            client, {"AAPL": position}, [pick], equity=1_000.0, dry_run=False, open_order_symbols=set(),
            buying_power=0.0,  # zero buying power must not block a SELL
        )

        assert results[0]["side"] == "sell"
        assert len(client.submitted_orders) == 1

    def test_dry_run_reports_capital_constrained_without_submitting(self):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        # Target (position-cap-limited) is $15k; use a buying power below that
        # so scaling actually kicks in.
        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=True, buying_power=6_000.0,
        )

        assert client.submitted_orders == []
        assert "DRY-RUN" in results[0]["status"]
        assert "capital-constrained" in results[0]["status"]


class TestTerminalOrderStatusLabels:
    """
    Every terminal order status (`_TERMINAL_ORDER_STATUSES`) must produce
    an accurate, non-misleading operator-facing label — in particular, an
    EXPIRED order with no fill must be labeled EXPIRED, never "PENDING"
    (EXPIRED is terminal; nothing further will happen to that order).
    """

    def test_expired_no_fill_is_labeled_expired_not_pending(self, monkeypatch):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        client.script_fill(
            "AAPL", statuses=[OrderStatus.EXPIRED], filled_qtys=[0.0], filled_avg_prices=[None]
        )
        logged = []
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: logged.append(kwargs))

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert results[0]["status"] == "EXPIRED (no fill)"
        assert "PENDING" not in results[0]["status"]
        assert logged == []

    def test_expired_with_partial_fill_still_logs_the_trade(self, monkeypatch):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        client.script_fill(
            "AAPL", statuses=[OrderStatus.EXPIRED], filled_qtys=[2.0], filled_avg_prices=[150.0]
        )
        logged = []
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: logged.append(kwargs))

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert "EXPIRED" in results[0]["status"]
        assert "qty=2.0" in results[0]["status"]
        assert len(logged) == 1  # the real partial fill must still be recorded

    def test_filled_is_labeled_order_filled(self, monkeypatch):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert results[0]["status"] == "ORDER FILLED"

    def test_partially_filled_is_labeled_with_quantity(self, monkeypatch):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        monkeypatch.setattr(engine.time, "sleep", lambda *_: None)
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        client.script_fill(
            "AAPL",
            statuses=[OrderStatus.PARTIALLY_FILLED] * 3,
            filled_qtys=[4.0] * 3,
            filled_avg_prices=[150.0] * 3,
        )

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert "PARTIALLY FILLED" in results[0]["status"]
        assert "qty=4.0" in results[0]["status"]

    def test_canceled_is_labeled_canceled(self, monkeypatch):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        client.script_fill(
            "AAPL", statuses=[OrderStatus.CANCELED], filled_qtys=[0.0], filled_avg_prices=[None]
        )

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert "CANCELED" in results[0]["status"]

    def test_rejected_is_labeled_rejected(self):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        client.script_fill(
            "AAPL", statuses=[OrderStatus.REJECTED], filled_qtys=[None], filled_avg_prices=[None]
        )

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert results[0]["status"] == "REJECTED"

    def test_still_working_after_poll_timeout_is_labeled_pending(self, monkeypatch):
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)
        monkeypatch.setattr(engine.time, "sleep", lambda *_: None)
        client.script_fill(
            "AAPL",
            statuses=[OrderStatus.NEW] * engine.ORDER_POLL_MAX_ATTEMPTS,
            filled_qtys=[None] * engine.ORDER_POLL_MAX_ATTEMPTS,
            filled_avg_prices=[None] * engine.ORDER_POLL_MAX_ATTEMPTS,
        )

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set()
        )

        assert "PENDING" in results[0]["status"]
        assert "EXPIRED" not in results[0]["status"]

    def test_liquidation_expired_no_fill_is_labeled_expired_not_pending(self, monkeypatch):
        position = make_position("OLD", qty=5, market_value=500.0, current_price=100.0)
        client = FakeTradingClient(positions=[position])
        client.script_fill(
            "OLD", statuses=[OrderStatus.EXPIRED], filled_qtys=[0.0], filled_avg_prices=[None]
        )
        logged = []
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: logged.append(kwargs))

        results = engine.liquidate_non_target_positions(
            client, {"OLD": position}, target_tickers=set(), analyses_by_ticker={}, dry_run=False,
            open_order_symbols=set(),
        )

        assert results[0]["status"] == "EXPIRED (no fill)"
        assert "PENDING" not in results[0]["status"]
        assert logged == []


class TestCentSafeBuyAllocation:
    """
    M2: aggregate submitted BUY notional must be mathematically incapable
    of exceeding the buying-power cap because of rounding — replacing
    "scale as floats, then let each order's independent round(x, 2) at
    submission time land wherever it lands" with a deterministic
    Decimal/ROUND_DOWN allocation plus a running remaining-cents budget.
    """

    def test_adversarial_half_cent_pressure_cannot_exceed_the_cap(self):
        """
        A real found case: 6 orders whose individually-`round(x, 2)`ed
        values sum to $100.01 against a $100.00 cap (proven below) — the
        cent-safe allocator must keep the actual allocated sum at or
        under the cap.
        """
        records = [
            {"order_notional": 7.226557},
            {"order_notional": 18.787745},
            {"order_notional": 25.689029},
            {"order_notional": 0.520336},
            {"order_notional": 25.575832},
            {"order_notional": 22.2005},
        ]
        buying_power = 100.00

        # Prove the adversarial premise: naive per-order round(x, 2) on
        # these exact values DOES exceed the cap.
        naive_sum = round(sum(round(r["order_notional"], 2) for r in records), 6)
        assert naive_sum > buying_power

        engine._allocate_cent_safe_buy_notionals(records, buying_power)

        total = sum(r["order_notional"] for r in records)
        assert total <= buying_power
        # Every allocated value is a non-negative, exact multiple of a cent.
        for r in records:
            assert r["order_notional"] >= 0.0
            cents = r["order_notional"] * 100
            assert abs(cents - round(cents)) < 1e-9

    def test_many_orders_adversarial_search_never_exceeds_cap(self):
        """
        Broader sweep (not just one hand-picked case): many randomly
        generated batches, several of which are independently confirmed
        (via the same search that produced the case above) to defeat
        naive per-order rounding — the invariant must hold across all of them.
        """
        import random

        rng = random.Random(1234)
        buying_power = 100.00
        violations_found_naively = 0

        for _ in range(500):
            n = rng.choice([3, 4, 5, 6, 7, 8])
            raw = [rng.uniform(10, 1000) for _ in range(n)]
            raw_sum = sum(raw)
            if raw_sum <= buying_power:
                continue
            scale = buying_power / raw_sum
            scaled = [r * scale for r in raw]

            naive_sum = sum(round(v, 2) for v in scaled)
            if naive_sum > buying_power:
                violations_found_naively += 1

            records = [{"order_notional": v} for v in scaled]
            engine._allocate_cent_safe_buy_notionals(records, buying_power)
            total = sum(r["order_notional"] for r in records)
            assert total <= buying_power + 1e-9, (n, scaled, total)

        # Sanity: the search space genuinely contains adversarial cases —
        # otherwise this test would trivially pass without exercising anything.
        assert violations_found_naively > 0

    def test_below_minimum_after_cent_safe_floor_is_skipped_not_submitted_over_cap(self, monkeypatch):
        """
        A single pick scaled down near the $1.00 minimum: buying_power =
        $0.999 means the OLD code (`round(0.999, 2)` == `1.00`, which is
        NOT below MIN_ORDER_NOTIONAL_USD) would have submitted a $1.00
        order — one-tenth of a cent OVER the $0.999 buying-power ceiling.
        The cent-safe allocator instead floors to $0.99, correctly under
        both the cap and the minimum, and the order is skipped rather
        than submitted.
        """
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pick = _pick("AAPL")
        client = FakeTradingClient(positions=[], default_fill_price=150.0)

        results = engine.rebalance_target_positions(
            client, {}, [pick], equity=100_000.0, dry_run=False, open_order_symbols=set(),
            buying_power=0.999,
        )

        assert client.submitted_orders == []
        assert results[0]["status"] == "SKIPPED (insufficient buying power)"

    def test_aggregate_can_equal_but_never_exceed_the_cap(self, monkeypatch):
        """Cleanly-divisible case: aggregate submitted notional reaches exactly the cap, never over."""
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pick_a = _pick("AAA", sector="Technology")
        pick_b = _pick("BBB", sector="Healthcare")
        client = FakeTradingClient(positions=[], default_fill_price=100.0)
        # Each individually capped at MAX_POSITION_WEIGHT (15%) -> $30,000
        # aggregate raw target; cap the buying power to exactly half of
        # that, evenly divisible in cents.
        aggregate_target = 2 * engine.MAX_POSITION_WEIGHT * 100_000.0  # $30,000
        buying_power = round(aggregate_target / 2, 2)  # $15,000.00

        engine.rebalance_target_positions(
            client, {}, [pick_a, pick_b], equity=100_000.0, dry_run=False, open_order_symbols=set(),
            buying_power=buying_power,
        )

        total_notional = sum(o.notional for o in client.submitted_orders)
        assert total_notional <= buying_power
        assert total_notional == pytest.approx(buying_power, abs=0.01)

    def test_sell_orders_are_never_run_through_cent_safe_allocation(self, monkeypatch):
        """SELL notionals must be completely unaffected by the buying-power/cent-safe machinery."""
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pick = _pick("AAPL")
        position = make_position("AAPL", qty=1000, market_value=99_000.0, current_price=150.0)
        client = FakeTradingClient(positions=[position])

        results = engine.rebalance_target_positions(
            client, {"AAPL": position}, [pick], equity=1_000.0, dry_run=False, open_order_symbols=set(),
            buying_power=0.0,  # zero buying power must not touch a SELL's notional at all
        )

        assert results[0]["side"] == "sell"
        assert len(client.submitted_orders) == 1
        sell_order = client.submitted_orders[0]
        assert sell_order.side.value == "sell"
        # The sell notional is exactly the (unrounded-by-cent-safe-logic)
        # amount rebalance math produced, just the ordinary round(x, 2).
        expected_notional = round(abs(results[0]["order_notional"]), 2)
        assert sell_order.notional == pytest.approx(expected_notional)

    def test_proportional_scaling_intent_still_roughly_preserved(self, monkeypatch):
        """
        Cent-safe floor-rounding must not distort the intended
        proportional split beyond sub-cent noise — two equal-target picks
        under a shared cap should still end up within a cent of each other.
        """
        monkeypatch.setattr(engine, "_safe_log_trade", lambda **kwargs: None)
        pick_a = _pick("AAA", sector="Technology")
        pick_b = _pick("BBB", sector="Healthcare")
        client = FakeTradingClient(positions=[], default_fill_price=100.0)
        aggregate_target = 2 * engine.MAX_POSITION_WEIGHT * 100_000.0  # $30,000, equal split
        buying_power = 10_000.33  # deliberately not evenly divisible in cents
        assert buying_power < aggregate_target  # scaling must actually engage for this test to be meaningful

        engine.rebalance_target_positions(
            client, {}, [pick_a, pick_b], equity=100_000.0, dry_run=False, open_order_symbols=set(),
            buying_power=buying_power,
        )

        assert len(client.submitted_orders) == 2
        notionals = sorted(o.notional for o in client.submitted_orders)
        assert sum(notionals) <= buying_power
        assert notionals[1] - notionals[0] < 0.02  # within a cent or two of each other
