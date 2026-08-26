"""
Generates a fresh sector-median snapshot and publishes it to Supabase.

Run manually (`python -m src.api.publish_sector_medians`) or by the
scheduled `.github/workflows/refresh-sector-medians.yml` workflow, which
provides `DATABASE_URL` as a secret. Not part of `src.api.main`'s import
graph — this module is a standalone CLI entry point, never imported by
the live API.

Two INDEPENDENT retry layers, deliberately not one combined "retry the
whole thing" loop:

1. Per-TICKER retries (`_compute_ticker_with_retries`, passed into
   `generate_sector_medians` via its `compute_ticker` parameter): a
   transient failure valuing one ticker (a network blip, a momentary
   rate limit) retries JUST that ticker, in place, with bounded backoff.
   `generate_sector_medians` is still called exactly ONCE per run —
   tickers that already succeeded are never re-fetched, and the whole
   ~100-ticker universe is never re-run just because one ticker needed a
   second attempt.
2. Publish retries (`run`'s own loop): once the snapshot has been fully
   generated, PUBLISHING it (the database write) is retried separately,
   reusing the exact same already-generated snapshot dict — a transient
   database connection failure never triggers regenerating anything.
   `publish_sector_median_snapshot` never overwrites or deletes a prior
   row, so even total, repeated publish failure here leaves the last
   published snapshot exactly as it was — never a discarded/lost
   snapshot, only a missed refresh.
"""

import logging
import sys
import time
from typing import Callable, Dict, Optional, Sequence

from src.api.sector_median_store import publish_sector_median_snapshot
from src.api.sector_medians import _compute_current_price_to_intrinsic, generate_sector_medians
from src.dcf_model.dcf import DCFAssumptions

logger = logging.getLogger(__name__)

DEFAULT_TICKER_MAX_ATTEMPTS = 3
DEFAULT_TICKER_BACKOFF_SECONDS: Sequence[float] = (2.0, 5.0)

DEFAULT_PUBLISH_MAX_ATTEMPTS = 3
DEFAULT_PUBLISH_BACKOFF_SECONDS: Sequence[float] = (30.0, 90.0)


def _compute_ticker_with_retries(
    ticker: str,
    assumptions: DCFAssumptions,
    *,
    max_attempts: int = DEFAULT_TICKER_MAX_ATTEMPTS,
    backoff_seconds: Sequence[float] = DEFAULT_TICKER_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> Optional[Dict]:
    """
    Retries ONE ticker's valuation, individually, with bounded backoff.
    A `None` result (the ticker's data was unavailable or unusable —
    `_compute_current_price_to_intrinsic`'s normal "skip" outcome) and an
    unexpected raised exception are both treated as a retryable failure
    for this ticker specifically; neither affects any other ticker.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            result = _compute_current_price_to_intrinsic(ticker, assumptions)
        except Exception as exc:  # noqa: BLE001 - one ticker's unexpected failure must not stop the run
            result = None
            # Exception CLASS only, never its message — a ticker-level
            # failure can originate from arbitrary provider/library code
            # whose exception text is not guaranteed safe to log verbatim.
            logger.warning("Ticker %s attempt %d/%d raised %s.", ticker, attempt, max_attempts, type(exc).__name__)

        if result is not None:
            return result

        if attempt < max_attempts:
            sleep(backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)])

    logger.warning("Ticker %s excluded from this run after %d attempt(s).", ticker, max_attempts)
    return None


def run(
    ticker_max_attempts: int = DEFAULT_TICKER_MAX_ATTEMPTS,
    ticker_backoff_seconds: Sequence[float] = DEFAULT_TICKER_BACKOFF_SECONDS,
    publish_max_attempts: int = DEFAULT_PUBLISH_MAX_ATTEMPTS,
    publish_backoff_seconds: Sequence[float] = DEFAULT_PUBLISH_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """
    Generates the sector-median snapshot exactly once — see the module
    docstring for why per-ticker retries live inside that single
    generation call rather than around it — then publishes it, retrying
    the PUBLISH step alone (reusing the same snapshot dict, never
    regenerating it) up to `publish_max_attempts` times on a transient
    failure.

    Returns True the moment the snapshot publishes; False (never raises)
    once every publish attempt has failed, OR if generation itself raises
    unexpectedly — an unexpected generation failure never calls the
    publisher at all, so the previously published snapshot in the
    database is left exactly as it was. Stops retrying immediately
    (without sleeping out the remaining attempts) if the snapshot was
    rejected by publish-time validation rather than a transient
    connection/write failure — retrying the identical snapshot dict
    against the same validation would only ever produce the same
    rejection.
    """
    def _compute_ticker(ticker: str, assumptions: DCFAssumptions) -> Optional[Dict]:
        return _compute_ticker_with_retries(
            ticker,
            assumptions,
            max_attempts=ticker_max_attempts,
            backoff_seconds=ticker_backoff_seconds,
            sleep=sleep,
        )

    try:
        snapshot = generate_sector_medians(compute_ticker=_compute_ticker)
    except Exception as exc:  # noqa: BLE001 - generation must never crash the runner or reach the publisher
        logger.error(
            "Sector median generation raised unexpectedly (%s); not publishing. "
            "The previously published snapshot, if any, is unchanged.",
            type(exc).__name__,
        )
        return False

    last_reason = None
    for attempt in range(1, publish_max_attempts + 1):
        published, reason = publish_sector_median_snapshot(snapshot)
        if published:
            logger.info(
                "Published a new sector median snapshot on publish attempt %d/%d.", attempt, publish_max_attempts
            )
            return True

        last_reason = reason
        logger.warning("Publish attempt %d/%d did not publish: %s", attempt, publish_max_attempts, reason)

        if reason is not None and reason.startswith("snapshot rejected:"):
            logger.error("Snapshot failed publish-time validation; not retrying: %s", reason)
            return False

        if attempt < publish_max_attempts:
            delay = publish_backoff_seconds[min(attempt - 1, len(publish_backoff_seconds) - 1)]
            sleep(delay)

    logger.error(
        "All %d publish attempt(s) failed; last reason: %s. "
        "The previously published snapshot, if any, is unchanged.",
        publish_max_attempts,
        last_reason,
    )
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(0 if run() else 1)
