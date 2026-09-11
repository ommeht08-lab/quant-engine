"""Typed, append-only operational health events for autonomous trading runs."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.utils.db import _connect, _get_database_url


class RunEventType(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class RunCompletionStatus(str, Enum):
    HEALTHY = "healthy"
    INCOMPLETE = "incomplete"


class StrategyDecision(str, Enum):
    CANDIDATES = "candidates"
    NO_CANDIDATES = "no_candidates"
    NOT_REACHED = "not_reached"


class TradingMode(str, Enum):
    DRY_RUN = "dry-run"
    EXECUTE = "execute"


@dataclass(frozen=True)
class RunIdentity:
    """Stable identity and source metadata shared by every event in one run."""

    run_id: str
    git_sha: Optional[str]
    trigger: str
    mode: TradingMode

    @classmethod
    def from_environment(cls, *, dry_run: bool) -> "RunIdentity":
        github_run_id = os.getenv("GITHUB_RUN_ID")
        github_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
        run_id = (
            f"github:{github_run_id}:{github_attempt}"
            if github_run_id
            else f"local:{uuid.uuid4()}"
        )
        return cls(
            run_id=run_id,
            git_sha=os.getenv("GITHUB_SHA") or None,
            trigger=os.getenv("GITHUB_EVENT_NAME") or "local",
            mode=TradingMode.DRY_RUN if dry_run else TradingMode.EXECUTE,
        )


@dataclass(frozen=True)
class RunHealthEvent:
    """One immutable lifecycle fact; later facts are appended, never updated."""

    identity: RunIdentity
    event_type: RunEventType
    completion_status: Optional[RunCompletionStatus] = None
    decision: StrategyDecision = StrategyDecision.NOT_REACHED
    universe_count: Optional[int] = None
    valued_count: Optional[int] = None
    eligible_count: Optional[int] = None
    failure_stage: Optional[str] = None
    failure_code: Optional[str] = None

    def __post_init__(self) -> None:
        if self.event_type == RunEventType.COMPLETED and self.completion_status is None:
            raise ValueError("A completed run event requires completion_status.")
        if self.event_type != RunEventType.COMPLETED and self.completion_status is not None:
            raise ValueError("Only a completed run event may carry completion_status.")
        if self.event_type == RunEventType.FAILED and not self.failure_code:
            raise ValueError("A failed run event requires a sanitized failure_code.")
        counts = (self.universe_count, self.valued_count, self.eligible_count)
        if any(value is not None and value < 0 for value in counts):
            raise ValueError("Run-health counts must be non-negative.")
        if (
            self.universe_count is not None
            and self.valued_count is not None
            and self.valued_count > self.universe_count
        ):
            raise ValueError("valued_count cannot exceed universe_count.")
        if (
            self.valued_count is not None
            and self.eligible_count is not None
            and self.eligible_count > self.valued_count
        ):
            raise ValueError("eligible_count cannot exceed valued_count.")


INSERT_RUN_EVENT_SQL = """
INSERT INTO rebalance_run_events (
    run_id, event_type, completion_status, decision_outcome, mode,
    git_sha, trigger, universe_count, valued_count, eligible_count,
    failure_stage, failure_code
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id, event_type) DO NOTHING;
"""


def append_run_event(event: RunHealthEvent) -> None:
    """Append one idempotent lifecycle event using the configured telemetry DB."""
    connection = None
    try:
        connection = _connect(_get_database_url())
        with connection.cursor() as cursor:
            cursor.execute(
                INSERT_RUN_EVENT_SQL,
                (
                    event.identity.run_id,
                    event.event_type.value,
                    event.completion_status.value if event.completion_status else None,
                    event.decision.value,
                    event.identity.mode.value,
                    event.identity.git_sha,
                    event.identity.trigger,
                    event.universe_count,
                    event.valued_count,
                    event.eligible_count,
                    event.failure_stage,
                    event.failure_code,
                ),
            )
        connection.commit()
    finally:
        if connection is not None:
            connection.close()
