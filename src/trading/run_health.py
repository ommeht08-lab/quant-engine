"""Typed, append-only operational health events for autonomous trading runs."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Optional, Tuple

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


class RunOutcome(str, Enum):
    """What the run actually did, so a quiet day and a missed window differ."""

    DRY_RUN = "dry_run"
    # Every intended order was skipped because the market was closed.
    MARKET_CLOSED = "market_closed"
    # Earlier attempts produced no fills before later orders hit the close.
    MARKET_CLOSED_AFTER_ORDER_ATTEMPTS = "market_closed_after_order_attempts"
    # At least one order filled in whole or part before later orders hit the close.
    MARKET_CLOSED_AFTER_PARTIAL_EXECUTION = "market_closed_after_partial_execution"
    NO_ELIGIBLE_CANDIDATES = "no_eligible_candidates"
    NO_ORDERS_NEEDED = "no_orders_needed"
    ORDERS_FILLED = "orders_filled"
    ORDERS_INCOMPLETE = "orders_incomplete"


UNLABELLED_ACCOUNT_EPOCH = "unlabelled"
_EPOCH_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FIXED_WEEKDAY_CRON = re.compile(r"^(\d{1,2}) (\d{1,2}) \* \* 1-5$")


SCHEDULE_ESTIMATE_MAX_DELAY = timedelta(hours=24)


def scheduled_time_estimate(cron: Optional[str], started_at: datetime) -> Tuple[Optional[datetime], bool]:
    """Estimate which fixed ``M H * * 1-5`` UTC cron slot triggered this run.

    GitHub supplies the cron string but not the slot time, so this is an
    ESTIMATE: the most recent weekday slot at or before ``started_at``,
    assuming the run belongs to it. Returns ``(estimate, ambiguous)``.

    Fails closed: if that slot is 24 hours or more before the start, an
    earlier slot's delayed run is equally plausible, so no estimate is
    returned and ``ambiguous`` is True. A run delayed past a whole later
    slot and started shortly after it cannot be detected from inside the
    run; the estimate's name and this docstring record that limitation.
    Manual runs and other cron shapes return ``(None, False)``.
    """

    match = _FIXED_WEEKDAY_CRON.match((cron or "").strip())
    if not match:
        return None, False
    minute, hour = int(match.group(1)), int(match.group(2))
    start = started_at.astimezone(timezone.utc)
    candidate = start.replace(hour=hour, minute=minute, second=0, microsecond=0)
    while candidate > start or candidate.weekday() > 4:
        candidate -= timedelta(days=1)
    if start - candidate >= SCHEDULE_ESTIMATE_MAX_DELAY:
        return None, True
    return candidate, False


def account_fingerprint(account_id: Optional[object]) -> Optional[str]:
    """A short one-way fingerprint so runs from different paper accounts can
    never be mixed in analysis, without storing the account identifier."""

    if account_id is None or not str(account_id).strip():
        return None
    return hashlib.sha256(str(account_id).encode("utf-8")).hexdigest()[:16]


class OrderResult(str, Enum):
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    NOT_FILLED = "not_filled"  # rejected, canceled/expired without fill, pending, or unconfirmed
    SUBMISSION_FAILED = "submission_failed"
    SKIPPED_MARKET_CLOSED = "skipped_market_closed"


@dataclass(frozen=True)
class OrderCounts:
    attempted: int
    filled: int
    partially_filled: int
    skipped_market_closed: int


@dataclass
class OrderLedger:
    """Every real order decision in one run: liquidations, rebalances, the
    SPY hedge, and post-fill cap trims all record here."""

    entries: List[Tuple[str, str, OrderResult]] = field(default_factory=list)

    def skipped_market_closed(self, kind: str, symbol: str) -> None:
        self.entries.append((kind, symbol, OrderResult.SKIPPED_MARKET_CLOSED))

    def submission_failed(self, kind: str, symbol: str) -> None:
        self.entries.append((kind, symbol, OrderResult.SUBMISSION_FAILED))

    def resolved(self, kind: str, symbol: str, order: object) -> None:
        self.entries.append((kind, symbol, classify_order(order)))

    def counts(self) -> OrderCounts:
        results = [result for _kind, _symbol, result in self.entries]
        return OrderCounts(
            attempted=sum(result is not OrderResult.SKIPPED_MARKET_CLOSED for result in results),
            filled=results.count(OrderResult.FILLED),
            partially_filled=results.count(OrderResult.PARTIALLY_FILLED),
            skipped_market_closed=results.count(OrderResult.SKIPPED_MARKET_CLOSED),
        )


def classify_order(order: object) -> OrderResult:
    """Only a FILLED status is a full fill; any other status with a filled
    quantity is partial; everything else (including no confirmation) is not filled."""

    if order is None:
        return OrderResult.NOT_FILLED
    status = getattr(getattr(order, "status", None), "value", getattr(order, "status", None))
    try:
        filled_qty = float(getattr(order, "filled_qty", 0) or 0)
    except (TypeError, ValueError):
        filled_qty = 0.0
    if status == "filled":
        return OrderResult.FILLED
    if filled_qty > 0:
        return OrderResult.PARTIALLY_FILLED
    return OrderResult.NOT_FILLED


def classify_run_outcome(*, dry_run: bool, has_candidates: bool, counts: OrderCounts) -> "RunOutcome":
    if dry_run:
        return RunOutcome.DRY_RUN
    if counts.skipped_market_closed:
        if counts.filled or counts.partially_filled:
            return RunOutcome.MARKET_CLOSED_AFTER_PARTIAL_EXECUTION
        if counts.attempted:
            return RunOutcome.MARKET_CLOSED_AFTER_ORDER_ATTEMPTS
        return RunOutcome.MARKET_CLOSED
    if counts.attempted == 0:
        return RunOutcome.NO_ORDERS_NEEDED if has_candidates else RunOutcome.NO_ELIGIBLE_CANDIDATES
    if counts.filled == counts.attempted:
        return RunOutcome.ORDERS_FILLED
    return RunOutcome.ORDERS_INCOMPLETE


@dataclass(frozen=True)
class RunIdentity:
    """Stable identity and source metadata shared by every event in one run."""

    run_id: str
    git_sha: Optional[str]
    trigger: str
    mode: TradingMode
    account_epoch: str = UNLABELLED_ACCOUNT_EPOCH
    started_at: Optional[datetime] = None
    scheduled_for_estimate: Optional[datetime] = None
    schedule_estimate_ambiguous: bool = False

    def __post_init__(self) -> None:
        if not _EPOCH_PATTERN.match(self.account_epoch):
            raise ValueError("account_epoch must be a short lowercase label.")

    @property
    def queue_delay_estimate_seconds(self) -> Optional[int]:
        if self.started_at is None or self.scheduled_for_estimate is None:
            return None
        return max(0, int((self.started_at - self.scheduled_for_estimate).total_seconds()))

    @classmethod
    def from_environment(cls, *, dry_run: bool, now: Optional[datetime] = None) -> "RunIdentity":
        started_at = now or datetime.now(timezone.utc)
        estimate, ambiguous = scheduled_time_estimate(os.getenv("SCHEDULED_CRON"), started_at)
        epoch = (os.getenv("ALPACA_ACCOUNT_EPOCH") or "").strip() or UNLABELLED_ACCOUNT_EPOCH
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
            account_epoch=epoch if _EPOCH_PATTERN.match(epoch) else UNLABELLED_ACCOUNT_EPOCH,
            started_at=started_at,
            scheduled_for_estimate=estimate,
            schedule_estimate_ambiguous=ambiguous,
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
    market_open_at_start: Optional[bool] = None
    account_fingerprint: Optional[str] = None
    orders_attempted: Optional[int] = None
    orders_filled: Optional[int] = None
    orders_partially_filled: Optional[int] = None
    orders_skipped_market_closed: Optional[int] = None
    run_outcome: Optional[RunOutcome] = None

    def __post_init__(self) -> None:
        if self.run_outcome is not None and self.event_type != RunEventType.COMPLETED:
            raise ValueError("Only a completed run event may carry run_outcome.")
        if self.orders_attempted is not None and self.orders_attempted < 0:
            raise ValueError("orders_attempted must be non-negative.")
        if self.orders_filled is not None and (
            self.orders_filled < 0
            or (self.orders_attempted is not None and self.orders_filled > self.orders_attempted)
        ):
            raise ValueError("orders_filled must be between 0 and orders_attempted.")
        if (
            self.orders_attempted is not None
            and (self.orders_filled or 0) + (self.orders_partially_filled or 0) > self.orders_attempted
        ):
            raise ValueError("Filled plus partially filled orders cannot exceed orders_attempted.")
        if any(
            value is not None and value < 0
            for value in (self.orders_partially_filled, self.orders_skipped_market_closed)
        ):
            raise ValueError("Order counts must be non-negative.")
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
    failure_stage, failure_code,
    account_epoch, account_fingerprint, scheduled_for_estimate, schedule_estimate_ambiguous,
    started_at, queue_delay_estimate_seconds, market_open_at_start, orders_attempted,
    orders_filled, orders_partially_filled, orders_skipped_market_closed, run_outcome
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    event.identity.account_epoch,
                    event.account_fingerprint,
                    event.identity.scheduled_for_estimate,
                    event.identity.schedule_estimate_ambiguous,
                    event.identity.started_at,
                    event.identity.queue_delay_estimate_seconds,
                    event.market_open_at_start,
                    event.orders_attempted,
                    event.orders_filled,
                    event.orders_partially_filled,
                    event.orders_skipped_market_closed,
                    event.run_outcome.value if event.run_outcome else None,
                ),
            )
        connection.commit()
    finally:
        if connection is not None:
            connection.close()
