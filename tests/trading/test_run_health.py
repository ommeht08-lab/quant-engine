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
        None,
        None,
        None,
        None,
        None,
        None,
    )


def test_completed_event_persists_scheduler_and_order_diagnostics(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(run_health, "_get_database_url", lambda: "postgresql://synthetic.invalid/test")
    monkeypatch.setattr(run_health, "_connect", lambda _url: connection)
    started = datetime(2026, 9, 21, 21, 2, tzinfo=timezone.utc)
    identity = replace(
        _identity(),
        mode=TradingMode.EXECUTE,
        trigger="schedule",
        account_epoch="alpaca-paper-100k-v1",
        started_at=started,
        scheduled_for=run_health.scheduled_time_for("37 14 * * 1-5", started),
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
        run_outcome=run_health.RunOutcome.MARKET_CLOSED,
    )

    run_health.append_run_event(event)

    params = connection.cursor_instance.calls[0][1]
    assert params[12:] == (
        "alpaca-paper-100k-v1",
        run_health.account_fingerprint("acct-uuid"),
        datetime(2026, 9, 21, 14, 37, tzinfo=timezone.utc),
        started,
        6 * 3600 + 25 * 60,
        False,
        0,
        0,
        "market_closed",
    )
    assert "acct-uuid" not in repr(params)


@pytest.mark.parametrize(
    ("started", "expected"),
    (
        (datetime(2026, 9, 23, 15, 2, tzinfo=timezone.utc), datetime(2026, 9, 23, 14, 37, tzinfo=timezone.utc)),
        # Before today's slot: the most recent weekday slot, skipping the weekend.
        (datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc), datetime(2026, 9, 18, 14, 37, tzinfo=timezone.utc)),
    ),
)
def test_scheduled_time_is_the_latest_weekday_slot_at_or_before_start(started, expected):
    assert run_health.scheduled_time_for("37 14 * * 1-5", started) == expected


def test_manual_or_unrecognized_cron_has_no_scheduled_time():
    now = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)
    assert run_health.scheduled_time_for(None, now) is None
    assert run_health.scheduled_time_for("*/5 * * * *", now) is None


def test_account_epoch_comes_from_the_environment_and_rejects_free_text(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCOUNT_EPOCH", "alpaca-paper-100k-v1")
    monkeypatch.setenv("SCHEDULED_CRON", "37 14 * * 1-5")
    started = datetime(2026, 9, 23, 14, 40, tzinfo=timezone.utc)
    identity = RunIdentity.from_environment(dry_run=False, now=started)
    assert identity.account_epoch == "alpaca-paper-100k-v1"
    assert identity.queue_delay_seconds == 180

    monkeypatch.setenv("ALPACA_ACCOUNT_EPOCH", "Not A Label; drop table")
    assert RunIdentity.from_environment(dry_run=False, now=started).account_epoch == "unlabelled"
    monkeypatch.delenv("ALPACA_ACCOUNT_EPOCH")
    assert RunIdentity.from_environment(dry_run=True, now=started).account_epoch == "unlabelled"


def test_order_counts_distinguish_fills_skips_and_market_closed():
    records = [
        {"status": "ORDER FILLED"},
        {"status": "LIQUIDATED"},
        {"status": "PARTIALLY FILLED (qty=3)"},
        {"status": "REJECTED"},
        {"status": "SKIPPED (market closed)"},
        {"status": "SKIPPED (within 3% drift threshold)"},
        {"status": "DRY-RUN (would buy)"},
    ]
    assert run_health.order_counts(records) == (4, 3, 1)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    (
        ({"dry_run": True, "has_candidates": True, "attempted": 0, "filled": 0, "skipped_market_closed": 2}, "dry_run"),
        ({"dry_run": False, "has_candidates": True, "attempted": 0, "filled": 0, "skipped_market_closed": 2}, "market_closed"),
        ({"dry_run": False, "has_candidates": False, "attempted": 0, "filled": 0, "skipped_market_closed": 0}, "no_eligible_candidates"),
        ({"dry_run": False, "has_candidates": True, "attempted": 0, "filled": 0, "skipped_market_closed": 0}, "no_orders_needed"),
        ({"dry_run": False, "has_candidates": True, "attempted": 3, "filled": 3, "skipped_market_closed": 0}, "orders_filled"),
        ({"dry_run": False, "has_candidates": True, "attempted": 3, "filled": 2, "skipped_market_closed": 0}, "orders_incomplete"),
    ),
)
def test_run_outcome_is_explicit(kwargs, expected):
    assert run_health.classify_run_outcome(**kwargs).value == expected


def test_run_outcome_only_on_completed_events_and_fills_bounded():
    with pytest.raises(ValueError, match="run_outcome"):
        RunHealthEvent(identity=_identity(), event_type=RunEventType.STARTED, run_outcome=run_health.RunOutcome.DRY_RUN)
    with pytest.raises(ValueError, match="orders_filled"):
        RunHealthEvent(
            identity=_identity(),
            event_type=RunEventType.COMPLETED,
            completion_status=RunCompletionStatus.HEALTHY,
            orders_attempted=1,
            orders_filled=2,
        )


def test_diagnostic_columns_are_added_idempotently_without_touching_rows():
    sql = db.ALTER_REBALANCE_RUN_EVENTS_DIAGNOSTICS_SQL
    assert sql.count("ADD COLUMN IF NOT EXISTS") == 9
    for forbidden in ("DROP", "UPDATE", "DELETE", "TRUNCATE"):
        assert forbidden not in sql.upper()


def test_schema_rejects_updates_and_deletes():
    assert "BEFORE UPDATE OR DELETE" in db.CREATE_REBALANCE_RUN_EVENTS_APPEND_ONLY_SQL
    assert "RAISE EXCEPTION 'rebalance_run_events is append-only'" in (
        db.CREATE_REBALANCE_RUN_EVENTS_APPEND_ONLY_SQL
    )
