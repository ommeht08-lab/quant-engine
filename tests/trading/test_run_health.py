"""Typed run-health lifecycle events and append-only Postgres persistence."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.trading import run_health
from src.trading.run_health import (
    RunCompletionStatus,
    RunEventType,
    RunHealthEvent,
    RunIdentity,
    StrategyDecision,
    TradingMode,
)
from src.utils import db


class FakeCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.commits = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def _identity():
    return RunIdentity(
        run_id="github:123:1",
        git_sha="abc123",
        trigger="workflow_dispatch",
        mode=TradingMode.DRY_RUN,
    )


def test_completed_event_requires_health_status():
    with pytest.raises(ValueError, match="requires completion_status"):
        RunHealthEvent(identity=_identity(), event_type=RunEventType.COMPLETED)


def test_failed_event_requires_sanitized_code():
    with pytest.raises(ValueError, match="requires a sanitized failure_code"):
        RunHealthEvent(identity=_identity(), event_type=RunEventType.FAILED)


def test_count_invariants_fail_closed():
    event = RunHealthEvent(identity=_identity(), event_type=RunEventType.STARTED)
    with pytest.raises(ValueError, match="valued_count cannot exceed"):
        replace(event, universe_count=10, valued_count=11)


def test_append_run_event_inserts_typed_values_once(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(run_health, "_get_database_url", lambda: "postgresql://synthetic.invalid/test")
    monkeypatch.setattr(run_health, "_connect", lambda _url: connection)
    event = RunHealthEvent(
        identity=_identity(),
        event_type=RunEventType.COMPLETED,
        completion_status=RunCompletionStatus.HEALTHY,
        decision=StrategyDecision.NO_CANDIDATES,
        universe_count=100,
        valued_count=92,
        eligible_count=0,
    )

    run_health.append_run_event(event)

    assert connection.commits == 1
    assert connection.closed is True
    sql, params = connection.cursor_instance.calls[0]
    assert "ON CONFLICT (run_id, event_type) DO NOTHING" in sql
    assert params == (
        "github:123:1",
        "completed",
        "healthy",
        "no_candidates",
        "dry-run",
        "abc123",
        "workflow_dispatch",
        100,
        92,
        0,
        None,
        None,
        "unlabelled",
        None,
        None,
        False,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


class _Order:
    def __init__(self, status, filled_qty):
        self.status = type("S", (), {"value": status})()
        self.filled_qty = filled_qty


def test_completed_event_persists_scheduler_and_order_diagnostics(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(run_health, "_get_database_url", lambda: "postgresql://synthetic.invalid/test")
    monkeypatch.setattr(run_health, "_connect", lambda _url: connection)
    started = datetime(2026, 9, 21, 21, 2, tzinfo=timezone.utc)
    estimate, ambiguous = run_health.scheduled_time_estimate("37 14 * * 1-5", started)
    identity = replace(
        _identity(),
        mode=TradingMode.EXECUTE,
        trigger="schedule",
        account_epoch="alpaca-paper-100k-v1",
        started_at=started,
        scheduled_for_estimate=estimate,
        schedule_estimate_ambiguous=ambiguous,
    )
    event = RunHealthEvent(
        identity=identity,
        event_type=RunEventType.COMPLETED,
        completion_status=RunCompletionStatus.INCOMPLETE,
        decision=StrategyDecision.CANDIDATES,
        market_open_at_start=False,
        account_fingerprint=run_health.account_fingerprint("acct-uuid"),
        orders_attempted=0,
        orders_filled=0,
        orders_partially_filled=0,
        orders_skipped_market_closed=3,
        run_outcome=run_health.RunOutcome.MARKET_CLOSED,
    )

    run_health.append_run_event(event)

    params = connection.cursor_instance.calls[0][1]
    assert params[12:] == (
        "alpaca-paper-100k-v1",
        run_health.account_fingerprint("acct-uuid"),
        datetime(2026, 9, 21, 14, 37, tzinfo=timezone.utc),
        False,
        started,
        6 * 3600 + 25 * 60,
        False,
        0,
        0,
        0,
        3,
        "market_closed",
    )
    assert "acct-uuid" not in repr(params)


@pytest.mark.parametrize(
    ("started", "expected"),
    (
        (datetime(2026, 9, 23, 15, 2, tzinfo=timezone.utc), datetime(2026, 9, 23, 14, 37, tzinfo=timezone.utc)),
        (datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc), datetime(2026, 9, 21, 14, 37, tzinfo=timezone.utc)),
    ),
)
def test_schedule_estimate_is_the_latest_weekday_slot_within_a_day(started, expected):
    assert run_health.scheduled_time_estimate("37 14 * * 1-5", started) == (expected, False)


def test_schedule_estimate_fails_closed_when_an_earlier_slot_is_equally_plausible():
    # Monday 14:00: the latest slot is Friday 14:37, more than 24h earlier.
    monday = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
    assert run_health.scheduled_time_estimate("37 14 * * 1-5", monday) == (None, True)
    exactly_a_day = datetime(2026, 9, 24, 14, 37, tzinfo=timezone.utc) - run_health.SCHEDULE_ESTIMATE_MAX_DELAY
    assert run_health.scheduled_time_estimate("37 14 * * 1-5", datetime(2026, 9, 24, 14, 36, tzinfo=timezone.utc)) == (
        exactly_a_day,
        False,
    )


def test_manual_or_unrecognized_cron_has_no_estimate():
    now = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)
    assert run_health.scheduled_time_estimate(None, now) == (None, False)
    assert run_health.scheduled_time_estimate("*/5 * * * *", now) == (None, False)


def test_ambiguous_schedule_records_no_queue_delay(monkeypatch):
    monkeypatch.setenv("SCHEDULED_CRON", "37 14 * * 1-5")
    identity = RunIdentity.from_environment(dry_run=False, now=datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc))
    assert identity.scheduled_for_estimate is None
    assert identity.schedule_estimate_ambiguous is True
    assert identity.queue_delay_estimate_seconds is None


def test_account_epoch_comes_from_the_environment_and_rejects_free_text(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCOUNT_EPOCH", "alpaca-paper-100k-v1")
    monkeypatch.setenv("SCHEDULED_CRON", "37 14 * * 1-5")
    started = datetime(2026, 9, 23, 14, 40, tzinfo=timezone.utc)
    identity = RunIdentity.from_environment(dry_run=False, now=started)
    assert identity.account_epoch == "alpaca-paper-100k-v1"
    assert identity.queue_delay_estimate_seconds == 180

    monkeypatch.setenv("ALPACA_ACCOUNT_EPOCH", "Not A Label; drop table")
    assert RunIdentity.from_environment(dry_run=False, now=started).account_epoch == "unlabelled"
    monkeypatch.delenv("ALPACA_ACCOUNT_EPOCH")
    assert RunIdentity.from_environment(dry_run=True, now=started).account_epoch == "unlabelled"


def test_only_a_filled_status_counts_as_a_full_fill():
    assert run_health.classify_order(_Order("filled", 10)) is run_health.OrderResult.FILLED
    assert run_health.classify_order(_Order("partially_filled", 3)) is run_health.OrderResult.PARTIALLY_FILLED
    assert run_health.classify_order(_Order("canceled", 3)) is run_health.OrderResult.PARTIALLY_FILLED
    assert run_health.classify_order(_Order("expired", 2)) is run_health.OrderResult.PARTIALLY_FILLED
    assert run_health.classify_order(_Order("rejected", 0)) is run_health.OrderResult.NOT_FILLED
    assert run_health.classify_order(_Order("new", 0)) is run_health.OrderResult.NOT_FILLED
    assert run_health.classify_order(None) is run_health.OrderResult.NOT_FILLED


def test_ledger_counts_every_order_kind():
    ledger = run_health.OrderLedger()
    ledger.resolved("liquidation", "AAA", _Order("filled", 1))
    ledger.resolved("rebalance", "BBB", _Order("partially_filled", 1))
    ledger.submission_failed("rebalance", "CCC")
    ledger.resolved("hedge", "SPY260101P00500000", _Order("filled", 2))
    ledger.resolved("cap_trim", "DDD", _Order("rejected", 0))
    ledger.skipped_market_closed("cap_trim", "EEE")

    assert ledger.counts() == run_health.OrderCounts(
        attempted=5, filled=2, partially_filled=1, skipped_market_closed=1
    )


def _counts(attempted, filled=0, partial=0, closed=0):
    return run_health.OrderCounts(attempted=attempted, filled=filled, partially_filled=partial, skipped_market_closed=closed)


@pytest.mark.parametrize(
    ("dry_run", "has_candidates", "counts", "expected"),
    (
        (True, True, _counts(0, closed=2), "dry_run"),
        (False, True, _counts(0, closed=2), "market_closed"),
        (False, True, _counts(2, filled=2, closed=1), "market_closed_after_partial_execution"),
        (False, False, _counts(0), "no_eligible_candidates"),
        (False, True, _counts(0), "no_orders_needed"),
        (False, True, _counts(3, filled=3), "orders_filled"),
        (False, True, _counts(3, filled=2, partial=1), "orders_incomplete"),
        (False, True, _counts(1, partial=1), "orders_incomplete"),
    ),
)
def test_run_outcome_is_explicit(dry_run, has_candidates, counts, expected):
    assert run_health.classify_run_outcome(dry_run=dry_run, has_candidates=has_candidates, counts=counts).value == expected


def test_run_outcome_only_on_completed_events_and_counts_bounded():
    with pytest.raises(ValueError, match="run_outcome"):
        RunHealthEvent(identity=_identity(), event_type=RunEventType.STARTED, run_outcome=run_health.RunOutcome.DRY_RUN)
    with pytest.raises(ValueError, match="partially filled"):
        RunHealthEvent(
            identity=_identity(),
            event_type=RunEventType.COMPLETED,
            completion_status=RunCompletionStatus.HEALTHY,
            orders_attempted=2,
            orders_filled=1,
            orders_partially_filled=2,
        )


def test_diagnostic_columns_are_added_idempotently_without_touching_rows():
    sql = db.ALTER_REBALANCE_RUN_EVENTS_DIAGNOSTICS_SQL
    assert sql.count("ADD COLUMN IF NOT EXISTS") == 12
    assert "market_closed_after_partial_execution" in sql
    for forbidden in ("DROP", "UPDATE", "DELETE", "TRUNCATE"):
        assert forbidden not in sql.upper()


def test_schema_rejects_updates_and_deletes():
    assert "BEFORE UPDATE OR DELETE" in db.CREATE_REBALANCE_RUN_EVENTS_APPEND_ONLY_SQL
    assert "RAISE EXCEPTION 'rebalance_run_events is append-only'" in (
        db.CREATE_REBALANCE_RUN_EVENTS_APPEND_ONLY_SQL
    )
