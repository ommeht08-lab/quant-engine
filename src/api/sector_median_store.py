"""
Supabase-backed persistence for sector-median snapshots.

Replaces the old bundled `src/api/data/sector_medians.json` artifact,
which shipped frozen into the Vercel deployment and could never be
refreshed once deployed there — see `src.api.sector_medians`'s own
top-of-file docstring for the full background. Instead, a scheduled
GitHub workflow calls `generate_sector_medians()` and
`publish_sector_median_snapshot()` here to append a new row to a
Postgres table (the same `DATABASE_URL`-identified database
`src.utils.db` already writes to), and the live `/api/evaluate` endpoint
reads the newest row back through `get_cached_latest_snapshot()`.

Schema: one append-only table, `sector_median_snapshots`. A snapshot is
only ever INSERTed after `_passes_publish_thresholds` accepts it — a run
that fails validation (or raises) publishes nothing, so
`fetch_latest_snapshot`'s "highest `generated_at`" read always returns
the most recent snapshot that WAS good, never a partial or unhealthy
one. A single-statement INSERT is atomic in Postgres by construction: no
reader can ever observe a half-written row.

`psycopg2` is imported lazily, inside `_connect`, never at module import
time — mirroring `src.utils.cache._get_redis_client`'s lazy-client
pattern. This module IS part of `src.api.main`'s import graph (through
`src.api.sector_medians.get_live_sector_median_price_to_intrinsic`), and
`tests/api/test_deployment.py::TestImportsWithoutOptionalHeavyDependencies`
requires `import src.api.main` to succeed even in a process where
`psycopg2` is genuinely not installed — true as long as nothing here
imports it before a database call is actually attempted. The real
Vercel deployment does install `psycopg2-binary` (see `pyproject.toml`),
so the lazy import succeeds there once it's actually reached.

Every function here degrades to `(None, reason)` / `(False, reason)`
rather than raising, and never puts raw exception text, a traceback, or
connection details into a returned `reason` string OR a log line —
`reason` can reach `EvaluationResponse.sector_median_unavailable_reason`,
a field returned to any API caller, and a log line could otherwise
capture `DATABASE_URL`'s host/user/password if a driver ever embeds the
DSN in an error message. Every `except` block here logs only a fixed
operation description plus `type(exc).__name__` — never `exc` itself,
never `logger.exception`/`exc_info=True` (which would attach the full
traceback, and with it any exception argument text).

The read path (`fetch_latest_snapshot`, and therefore
`get_cached_latest_snapshot`) is STRICTLY READ-ONLY: it never runs
`CREATE TABLE`/`CREATE INDEX`, and every connection it opens is bounded
by a short connect timeout and a short server-side statement timeout
(see `READ_CONNECT_TIMEOUT_SECONDS`/`READ_STATEMENT_TIMEOUT_MS` below) —
`/api/evaluate` shares Vercel's 40s function ceiling with a live
yfinance fetch and a full DCF valuation, and a slow or unreachable
database must degrade in a few seconds, not threaten that budget.
Schema creation is the publisher's job alone (`publish_sector_median_snapshot`
-> `ensure_schema`), run against a separate, more generous connection
profile — a scheduled batch job, not a live request.
"""

import datetime
import json
import logging
import os
import time
from typing import Optional, Tuple

from src.api.sector_median_thresholds import (
    MAX_FUTURE_SKEW,
    MIN_OVERALL_COVERAGE_FRACTION,
    MIN_SECTOR_SAMPLE_SIZE,
    _is_valid_finite_number,
    _is_valid_nonneg_int,
)

logger = logging.getLogger(__name__)

# Read path (the live /api/evaluate request path): must degrade quickly
# and never risk Vercel's 40s function ceiling. `READ_CONNECT_TIMEOUT_SECONDS`
# bounds how long TCP/auth negotiation can take; `READ_STATEMENT_TIMEOUT_MS`
# (a Postgres session option, enforced server-side) bounds how long the
# single SELECT can run. Together, worst case, a few seconds -- leaving
# comfortable headroom for the yfinance fetch and DCF valuation the same
# request also has to do.
READ_APPLICATION_NAME = "valuation-engine-api-read"
READ_CONNECT_TIMEOUT_SECONDS = 3
READ_STATEMENT_TIMEOUT_MS = 3000

# Publish path (the scheduled/manual GitHub workflow): not latency-bound
# against a live HTTP request, so more generous, but still bounded so a
# hung connection or a runaway DDL/INSERT can't wedge the workflow run
# indefinitely.
PUBLISH_APPLICATION_NAME = "valuation-engine-publisher"
PUBLISH_CONNECT_TIMEOUT_SECONDS = 10
PUBLISH_STATEMENT_TIMEOUT_MS = 15000

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sector_median_snapshots (
    id BIGSERIAL PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL,
    universe_size INTEGER NOT NULL,
    tickers_used INTEGER NOT NULL,
    risk_free_rate DOUBLE PRECISION NOT NULL,
    assumptions JSONB NOT NULL,
    sector_medians JSONB NOT NULL,
    sector_sample_counts JSONB NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS sector_median_snapshots_generated_at_idx
ON sector_median_snapshots (generated_at DESC);
"""

INSERT_SQL = """
INSERT INTO sector_median_snapshots (
    generated_at, universe_size, tickers_used, risk_free_rate,
    assumptions, sector_medians, sector_sample_counts
) VALUES (%s, %s, %s, %s, %s, %s, %s);
"""

SELECT_LATEST_SQL = """
SELECT generated_at, universe_size, tickers_used, risk_free_rate,
       assumptions, sector_medians, sector_sample_counts
FROM sector_median_snapshots
ORDER BY generated_at DESC, id DESC
LIMIT 1;
"""

# Per-process guard so `ensure_schema` (a `CREATE TABLE IF NOT EXISTS` +
# `CREATE INDEX IF NOT EXISTS` round trip — cheap, but not free) runs at
# most once per long-lived process/function instance rather than before
# every single publish/fetch, mirroring `src.utils.cache._get_redis_client`'s
# "lazy-construct once" pattern. A fresh process (e.g. each GitHub
# workflow run) naturally re-checks it once, which is exactly right for
# a brand-new database.
_schema_ensured = False

# Short in-process cache for `get_cached_latest_snapshot` — long enough
# to absorb a burst of nearby `/api/evaluate` requests hitting the same
# warm function instance without a fresh database round trip per
# request, short enough that a newly published (or newly rejected, in
# which case the prior snapshot keeps being served) run becomes visible
# to new requests well within one staleness window.
_CACHE_TTL_SECONDS = 30.0
_cached_snapshot: Optional[dict] = None
_cached_reason: Optional[str] = None
_cached_at_monotonic: Optional[float] = None


def _get_database_url() -> Optional[str]:
    from dotenv import load_dotenv

    load_dotenv()
    return os.getenv("DATABASE_URL") or None


def _connect(
    database_url: str,
    *,
    application_name: str,
    connect_timeout_seconds: int,
    statement_timeout_ms: int,
):
    """
    Opens a bounded psycopg2 connection. `connect_timeout` bounds TCP/auth
    negotiation; the `statement_timeout` session option (applied via
    `options`, enforced server-side on every statement over this
    connection) bounds how long any single query can run. Together they
    put a hard ceiling on how long a database hiccup can hold up the
    caller — see `READ_*`/`PUBLISH_*` above for the two profiles.
    `application_name` shows up in the database's own `pg_stat_activity`,
    so a slow-query or connection-count investigation can immediately
    tell a read from a publish.
    """
    import psycopg2

    return psycopg2.connect(
        database_url,
        application_name=application_name,
        connect_timeout=connect_timeout_seconds,
        options=f"-c statement_timeout={statement_timeout_ms}",
    )


def ensure_schema(conn) -> None:
    """
    Idempotent — safe to call repeatedly, never destructive to existing
    rows. Called ONLY from the publish path (`publish_sector_median_snapshot`)
    — the read path (`fetch_latest_snapshot`) never calls this and never
    issues `CREATE TABLE`/`CREATE INDEX`; a read against a database whose
    table doesn't exist yet simply fails its SELECT and degrades to
    "snapshot unavailable" like any other read failure.
    """
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(CREATE_INDEX_SQL)
    conn.commit()


def _ensure_schema_once(conn) -> None:
    global _schema_ensured
    if not _schema_ensured:
        ensure_schema(conn)
        _schema_ensured = True


_ASSUMPTIONS_KEYS = frozenset({"revenue_growth_rate", "operating_margin", "terminal_growth_rate"})


def _is_valid_assumptions_shape(assumptions) -> bool:
    """
    The published `assumptions` container must have EXACTLY the three
    keys `_serialize_comparable_assumptions` (`src.api.sector_medians`)
    produces, each either `None` or a finite number — no extra/missing
    keys, no stray types (a string, a list, a bool masquerading as a rate).
    """
    if not isinstance(assumptions, dict) or set(assumptions) != _ASSUMPTIONS_KEYS:
        return False
    return all(value is None or _is_valid_finite_number(value) for value in assumptions.values())


def _passes_publish_thresholds(
    snapshot: dict,
    min_overall_coverage_fraction: float = MIN_OVERALL_COVERAGE_FRACTION,
    min_sector_sample_size: int = MIN_SECTOR_SAMPLE_SIZE,
) -> Tuple[bool, Optional[str]]:
    """
    Pure validation, no I/O: mirrors the same coverage/sample-size floors
    `evaluate_sector_median_cache` (`src.api.sector_medians`) enforces at
    READ time, applied here at WRITE/publish time so a systemically bad
    or malformed run (a provider outage, a broad parsing bug, tampered or
    corrupted data) never even reaches the table — the previous good
    snapshot stays "latest" untouched. Checks, in order: `generated_at`
    is a parseable, timezone-aware, non-future timestamp; `assumptions`
    has exactly the expected shape; `risk_free_rate` is finite;
    `universe_size`/`tickers_used` are genuine non-negative integers with
    `tickers_used <= universe_size`; overall coverage clears
    `min_overall_coverage_fraction`; `sector_medians` values are finite
    and positive; `sector_sample_counts` values are genuine non-negative
    integers whose sum equals `tickers_used` exactly (every valued ticker
    contributes to exactly one sector, so a genuine, untampered run
    always satisfies this); and at least one sector individually clears
    `min_sector_sample_size` — a run isn't refused just because ONE thin
    sector has too few samples (read-time validation already refuses
    that sector, specifically, on its own), only if NONE of them do.

    `assumptions.risk_free_rate` COMPATIBILITY (vs. a live caller) is
    intentionally NOT checked here: that comparison is only meaningful
    against a specific request's own live rate, not a fixed threshold at
    publish time.
    """
    generated_at = snapshot.get("generated_at")
    if not isinstance(generated_at, str):
        return False, "snapshot has no generated_at timestamp."
    try:
        generated_at_ts = datetime.datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        return False, "snapshot has an unparseable generated_at timestamp."
    if generated_at_ts.tzinfo is None:
        return False, "snapshot has a timezone-naive generated_at timestamp."
    now = datetime.datetime.now(datetime.timezone.utc)
    if generated_at_ts > now + MAX_FUTURE_SKEW:
        return False, "snapshot has a generated_at timestamp in the future."

    if not _is_valid_assumptions_shape(snapshot.get("assumptions")):
        return False, "snapshot has a malformed assumptions shape."

    risk_free_rate = snapshot.get("risk_free_rate")
    if not _is_valid_finite_number(risk_free_rate):
        return False, "snapshot has a missing or invalid risk_free_rate."

    universe_size = snapshot.get("universe_size")
    tickers_used = snapshot.get("tickers_used")
    if not _is_valid_nonneg_int(universe_size) or universe_size <= 0:
        return False, "snapshot has a missing or invalid universe_size."
    if not _is_valid_nonneg_int(tickers_used):
        return False, "snapshot has a missing or invalid tickers_used."
    if tickers_used > universe_size:
        return False, f"tickers_used ({tickers_used}) exceeds universe_size ({universe_size})."

    coverage = tickers_used / universe_size
    if coverage < min_overall_coverage_fraction:
        return False, (
            f"only {tickers_used}/{universe_size} ({coverage:.0%}) of the universe "
            f"was successfully valued this run (minimum {min_overall_coverage_fraction:.0%})."
        )

    sector_medians = snapshot.get("sector_medians")
    sector_sample_counts = snapshot.get("sector_sample_counts")
    if not isinstance(sector_medians, dict) or not sector_medians:
        return False, "snapshot has no sector medians."
    if not isinstance(sector_sample_counts, dict) or set(sector_sample_counts) != set(sector_medians):
        return False, "snapshot's sector_medians and sector_sample_counts keys don't match."

    for sector_name, median in sector_medians.items():
        if not _is_valid_finite_number(median) or median <= 0:
            return False, f"sector median for {sector_name!r} is not a finite positive number."

    for sector_name, count in sector_sample_counts.items():
        if not _is_valid_nonneg_int(count):
            return False, f"sector sample count for {sector_name!r} is not a genuine non-negative integer."

    total_sector_samples = sum(sector_sample_counts.values())
    if total_sector_samples != tickers_used:
        return False, (
            f"sector sample counts sum to {total_sector_samples}, which doesn't match "
            f"tickers_used ({tickers_used})."
        )

    qualifying_sectors = [
        sector_name for sector_name, count in sector_sample_counts.items() if count >= min_sector_sample_size
    ]
    if not qualifying_sectors:
        return False, f"no sector reached the minimum sample size of {min_sector_sample_size}."

    return True, None


def publish_sector_median_snapshot(
    snapshot: dict,
    database_url: Optional[str] = None,
    conn=None,
    min_overall_coverage_fraction: float = MIN_OVERALL_COVERAGE_FRACTION,
    min_sector_sample_size: int = MIN_SECTOR_SAMPLE_SIZE,
) -> Tuple[bool, Optional[str]]:
    """
    Validate `snapshot` (a `generate_sector_medians`-shaped dict) against
    the same publish-time thresholds `_passes_publish_thresholds`
    checks, and if it passes, append it as a new row — never overwriting
    or deleting any prior row, so a failed/rejected run always leaves
    the last good snapshot as "latest" untouched.

    Args:
        snapshot: A `src.api.sector_medians.generate_sector_medians`-shaped dict.
        database_url: Overrides `DATABASE_URL` (for testing). Ignored if `conn` is given.
        conn: An already-open DB-API connection (for testing with a fake
            connection double) — if given, this function does not open
            or close a connection of its own.

    Returns:
        (True, None) on a successful publish, or (False, reason) if the
        snapshot was rejected (nothing is written in that case) or the
        database itself could not be reached. Never raises.
    """
    ok, reason = _passes_publish_thresholds(
        snapshot,
        min_overall_coverage_fraction=min_overall_coverage_fraction,
        min_sector_sample_size=min_sector_sample_size,
    )
    if not ok:
        logger.warning("Refusing to publish sector median snapshot: %s", reason)
        return False, f"snapshot rejected: {reason}"

    owns_conn = conn is None
    if owns_conn:
        database_url = database_url if database_url is not None else _get_database_url()
        if not database_url:
            return False, "DATABASE_URL is not configured."
        try:
            conn = _connect(
                database_url,
                application_name=PUBLISH_APPLICATION_NAME,
                connect_timeout_seconds=PUBLISH_CONNECT_TIMEOUT_SECONDS,
                statement_timeout_ms=PUBLISH_STATEMENT_TIMEOUT_MS,
            )
        except Exception as exc:  # noqa: BLE001 - never leak connection details past this boundary
            logger.warning("Sector median publish: database connection failed (%s).", type(exc).__name__)
            return False, "could not connect to the sector median database."

    try:
        _ensure_schema_once(conn)
        with conn.cursor() as cur:
            cur.execute(
                INSERT_SQL,
                (
                    snapshot["generated_at"],
                    snapshot["universe_size"],
                    snapshot["tickers_used"],
                    snapshot["risk_free_rate"],
                    json.dumps(snapshot["assumptions"]),
                    json.dumps(snapshot["sector_medians"]),
                    json.dumps(snapshot["sector_sample_counts"]),
                ),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - never leak query/connection details past this boundary
        logger.warning("Sector median publish: database write failed (%s).", type(exc).__name__)
        return False, "database write failed while publishing sector median snapshot."
    finally:
        if owns_conn:
            conn.close()

    logger.info(
        "Published sector median snapshot generated at %s (%d/%d tickers).",
        snapshot["generated_at"],
        snapshot["tickers_used"],
        snapshot["universe_size"],
    )
    return True, None


def _row_to_snapshot(row) -> dict:
    (
        generated_at,
        universe_size,
        tickers_used,
        risk_free_rate,
        assumptions,
        sector_medians,
        sector_sample_counts,
    ) = row

    def _maybe_load(value):
        # psycopg2 decodes JSONB columns to native dicts automatically;
        # this only matters for a test double that hands back raw JSON text.
        if isinstance(value, (dict, list)) or value is None:
            return value
        return json.loads(value)

    return {
        # `evaluate_sector_median_cache` parses `generated_at` with
        # `datetime.fromisoformat`, which requires a `str` — a real
        # TIMESTAMPTZ column comes back from psycopg2 as an actual
        # timezone-aware `datetime`, so it's normalized back to an ISO
        # string here to keep exactly one cache-validation code path for
        # both the file-based and database-backed loaders.
        "generated_at": generated_at.isoformat() if hasattr(generated_at, "isoformat") else generated_at,
        "universe_size": universe_size,
        "tickers_used": tickers_used,
        "risk_free_rate": risk_free_rate,
        "assumptions": _maybe_load(assumptions),
        "sector_medians": _maybe_load(sector_medians),
        "sector_sample_counts": _maybe_load(sector_sample_counts),
    }


def fetch_latest_snapshot(database_url: Optional[str] = None, conn=None) -> Tuple[Optional[dict], Optional[str]]:
    """
    Fetch the most recently published sector-median snapshot (highest
    `generated_at`), shaped identically to `generate_sector_medians`'s
    return value, so `evaluate_sector_median_cache` can validate it with
    the exact same staleness/coverage/assumption/sample-size logic
    regardless of which backing store produced the dict.

    STRICTLY READ-ONLY: never runs `CREATE TABLE`/`CREATE INDEX` (schema
    creation is the publisher's job alone — see `ensure_schema`'s own
    docstring) and opens its connection with a short connect timeout and
    a short server-side statement timeout (`READ_CONNECT_TIMEOUT_SECONDS`/
    `READ_STATEMENT_TIMEOUT_MS`) so a database hiccup degrades in a few
    seconds rather than threatening Vercel's 40s `/api/evaluate` function
    ceiling. If the table doesn't exist yet (nothing has ever been
    published), the SELECT itself simply fails and is reported like any
    other read failure below — no special-casing needed.

    Returns (snapshot, None) if a row exists, or (None, reason) if
    `DATABASE_URL` is unset, the database is unreachable, the query timed
    out, the table is empty/absent (no snapshot has ever been
    published), or the returned row itself is malformed (wrong shape,
    invalid JSON in a JSONB column) — never raises.
    """
    owns_conn = conn is None
    if owns_conn:
        database_url = database_url if database_url is not None else _get_database_url()
        if not database_url:
            return None, "DATABASE_URL is not configured."
        try:
            conn = _connect(
                database_url,
                application_name=READ_APPLICATION_NAME,
                connect_timeout_seconds=READ_CONNECT_TIMEOUT_SECONDS,
                statement_timeout_ms=READ_STATEMENT_TIMEOUT_MS,
            )
        except Exception as exc:  # noqa: BLE001 - never leak connection details past this boundary
            logger.warning("Sector median fetch: database connection failed (%s).", type(exc).__name__)
            return None, "could not connect to the sector median database."

    try:
        with conn.cursor() as cur:
            cur.execute(SELECT_LATEST_SQL)
            row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001 - never leak query/connection details past this boundary
        logger.warning("Sector median fetch: database read failed (%s).", type(exc).__name__)
        return None, "database read failed while fetching sector median snapshot."
    finally:
        if owns_conn:
            conn.close()

    if row is None:
        return None, "No sector median snapshot has been published yet."

    try:
        return _row_to_snapshot(row), None
    except Exception as exc:  # noqa: BLE001 - never leak row/database contents past this boundary
        # Guards the tuple-unpack (a row with the wrong shape/column
        # count raises ValueError) and JSON decoding inside
        # `_row_to_snapshot` (a JSONB column holding invalid/unexpected
        # content raises `json.JSONDecodeError` or `TypeError`) — a
        # malformed row must degrade like any other read failure, never
        # raise past this function's own "never raises" contract.
        logger.warning("Sector median fetch: malformed row (%s).", type(exc).__name__)
        return None, "sector median snapshot row is malformed."


def get_cached_latest_snapshot(ttl_seconds: float = _CACHE_TTL_SECONDS) -> Tuple[Optional[dict], Optional[str]]:
    """
    `fetch_latest_snapshot` wrapped in a short in-process cache — see
    the module docstring's `_CACHE_TTL_SECONDS` note. `/api/evaluate`
    must not perform any additional database round trip beyond what a
    single burst of nearby requests actually needs.
    """
    global _cached_snapshot, _cached_reason, _cached_at_monotonic

    now = time.monotonic()
    if _cached_at_monotonic is not None and now - _cached_at_monotonic < ttl_seconds:
        return _cached_snapshot, _cached_reason

    snapshot, reason = fetch_latest_snapshot()
    _cached_snapshot, _cached_reason, _cached_at_monotonic = snapshot, reason, now
    return snapshot, reason


def _reset_cache_for_tests() -> None:
    """Test-only: clears the in-process cache and schema-ensured flag between tests."""
    global _cached_snapshot, _cached_reason, _cached_at_monotonic, _schema_ensured
    _cached_snapshot, _cached_reason, _cached_at_monotonic = None, None, None
    _schema_ensured = False
