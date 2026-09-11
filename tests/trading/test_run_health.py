"""Typed run-health lifecycle events and append-only Postgres persistence."""

from dataclasses import replace

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
    )


def test_schema_rejects_updates_and_deletes():
    assert "BEFORE UPDATE OR DELETE" in db.CREATE_REBALANCE_RUN_EVENTS_APPEND_ONLY_SQL
    assert "RAISE EXCEPTION 'rebalance_run_events is append-only'" in (
        db.CREATE_REBALANCE_RUN_EVENTS_APPEND_ONLY_SQL
    )
