"""Minimal, read-only production database heartbeat.

This module exists solely to verify that the configured Postgres instance is
awake and accepting connections.  It deliberately does not import any trading,
market-data, cache, or application-schema code, and its only database query is
``SELECT 1``.
"""

from __future__ import annotations

import os
import sys
from typing import Final

import psycopg2


CONNECT_TIMEOUT_SECONDS: Final = 10
STATEMENT_TIMEOUT_MILLISECONDS: Final = 5_000
APPLICATION_NAME: Final = "valuation-engine-readonly-heartbeat"


def run_heartbeat(database_url: str) -> None:
    """Run one bounded query in an explicitly read-only transaction.

    The connection is rolled back even after a successful query.  ``SELECT 1``
    cannot modify application data, while ``set_session(readonly=True)`` adds a
    server-enforced guard against future accidental query changes.
    """

    if not isinstance(database_url, str) or not database_url.strip():
        raise ValueError("DATABASE_URL must be a non-empty string")

    connection = psycopg2.connect(
        database_url,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        application_name=APPLICATION_NAME,
    )
    try:
        # This must happen before opening the cursor or executing any SQL.
        connection.set_session(readonly=True, autocommit=False)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MILLISECONDS}"
                )
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise RuntimeError("database heartbeat returned an unexpected result")
        finally:
            # A heartbeat never has anything to commit.  Rollback also closes
            # the read-only transaction before returning the pooled connection.
            connection.rollback()
    finally:
        connection.close()


def main() -> int:
    """CLI entry point that never prints the connection string or SQL details."""

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Database heartbeat failed: DATABASE_URL is not configured.", file=sys.stderr)
        return 2

    try:
        run_heartbeat(database_url)
    except Exception as error:  # GitHub should fail without leaking DSN details.
        print(
            f"Database heartbeat failed ({type(error).__name__}).",
            file=sys.stderr,
        )
        return 1

    print("Database heartbeat succeeded (read-only SELECT 1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
