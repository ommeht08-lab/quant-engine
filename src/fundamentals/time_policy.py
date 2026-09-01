"""
Time-cutoff policy for point-in-time fundamentals: how a daily backtest
date maps onto an exact knowledge cutoff, and how a filing's exact
eligibility instant is determined when SEC's own acceptance timestamp is
unavailable.

Uses the standard-library `zoneinfo` (available since Python 3.9, no
extra dependency) rather than a fixed UTC offset, specifically so both
functions below are correct across DST transitions — a fixed offset would
silently be off by an hour for half the year.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")

# Matches historical_tester._get_price_on_or_before's existing convention
# of treating a backtest date's CLOSING price as the reference for that
# date — a filing accepted any time up to this same moment is known; one
# accepted later that same day (including an after-hours filing) is not,
# until the next cutoff.
DEFAULT_MARKET_CLOSE = time(16, 0)


def is_aware(value: datetime) -> bool:
    """
    The robust timezone-awareness check: `value.utcoffset() is not None`,
    not merely `value.tzinfo is not None`. Per the datetime docs, a
    datetime is genuinely "aware" only if its tzinfo is set AND that
    tzinfo's `utcoffset()` doesn't return None — a `tzinfo is not None`
    check alone would pass a pathological tzinfo that returns None from
    `utcoffset()`, silently treating a value as aware when arithmetic and
    comparisons against it would still behave like a naive one.
    """
    return value.utcoffset() is not None


def knowledge_cutoff_for_date(
    as_of_date: date,
    market_time: time = DEFAULT_MARKET_CLOSE,
    tz: ZoneInfo = US_EASTERN,
) -> datetime:
    """
    Maps a daily backtest date onto an exact, timezone-aware instant.

    A caller that wants a stricter "as of the OPEN of this date"
    convention (e.g. a strategy that decides and trades at the open)
    should pass `market_time=time(9, 30)` — this is a caller decision,
    never hardcoded here.
    """
    return datetime.combine(as_of_date, market_time, tzinfo=tz)


def eligible_at(accepted_at: Optional[datetime], filed_date: date) -> datetime:
    """
    The exact instant a filing's facts became knowable — the single
    function this policy is computed through; nothing else in this
    package is allowed to derive or store this independently (see
    `types.FilingProvenance.eligible_at`, a property that always calls
    back into this function, so a stored value can never contradict its
    own `accepted_at`/`filed_date`).

    `accepted_at` (SEC's own acceptance timestamp) is used whenever
    present, and must be timezone-aware — a naive value is almost always
    a caller bug (an unconverted timestamp) and is rejected rather than
    silently assumed to be in some particular zone.

    When `accepted_at` is genuinely unavailable (e.g. an accession
    missing from the submissions history), the conservative stand-in is
    the true end of `filed_date` in US/Eastern (`time.max` —
    23:59:59.999999, not merely 23:59:59) — the LATEST moment consistent
    with the known date, never an earlier guess. This mirrors this
    codebase's own existing conservatism principle
    (`src.backtesting.historical_tester.STATEMENT_FILING_LAG_DAYS`'s
    docstring: treating data as available later than it might really
    have been is the safe failure direction; a look-ahead bias — using
    data before it was genuinely public — is the failure mode that must
    never happen).
    """
    if accepted_at is not None:
        if not is_aware(accepted_at):
            raise ValueError("eligible_at: accepted_at must be timezone-aware if provided.")
        return accepted_at
    return datetime.combine(filed_date, time.max, tzinfo=US_EASTERN)
