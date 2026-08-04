"""
PostgreSQL telemetry for logging executed algorithmic trades.

Connects to the database identified by the `DATABASE_URL` environment
variable (e.g. postgresql://user:password@host:5432/dbname), ensures the
`trade_logs` table exists, and inserts one row per executed trade.

This is telemetry, not a source of truth for portfolio state or order
execution — Alpaca's own account/position endpoints remain authoritative.
Callers should catch failures here and log a warning rather than let a
telemetry problem block actual trade execution.
"""

import logging
import os
from typing import Optional

import psycopg2

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trade_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ticker TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    execution_price DOUBLE PRECISION NOT NULL,
    wacc DOUBLE PRECISION,
    beta DOUBLE PRECISION,
    conviction_score DOUBLE PRECISION
);
"""

INSERT_SQL = """
INSERT INTO trade_logs (ticker, action, quantity, execution_price, wacc, beta, conviction_score)
VALUES (%s, %s, %s, %s, %s, %s, %s);
"""


def log_trade(
    ticker: str,
    action: str,
    quantity: float,
    execution_price: float,
    wacc: Optional[float],
    beta: Optional[float],
    conviction_score: Optional[float],
) -> None:
    """
    Record one executed trade to the `trade_logs` Postgres table.

    Connects using the `DATABASE_URL` environment variable, ensures the
    table exists (`CREATE TABLE IF NOT EXISTS`), and inserts a single
    row. `id` and `timestamp` are populated by the database (auto-
    increment primary key and `NOW()` respectively) — not passed in.

    Args:
        ticker: Stock ticker symbol, e.g. "AAPL".
        action: Trade action, e.g. "BUY" or "SELL".
        quantity: Number of shares transacted.
        execution_price: Fill (or best-known reference) price per share.
        wacc: The ticker's WACC at the time of the trade decision, if known.
        beta: The ticker's beta at the time of the trade decision, if known.
        conviction_score: The Conviction Score that drove this trade, if known.

    Raises:
        RuntimeError: If `DATABASE_URL` is not set.
        psycopg2.Error: If the connection or query fails. Callers should
            catch this (and RuntimeError) rather than let a telemetry
            failure abort trade execution.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to `.env` to enable trade telemetry logging."
        )

    conn = None
    try:
        conn = psycopg2.connect(database_url)
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            cur.execute(
                INSERT_SQL,
                (ticker, action, quantity, execution_price, wacc, beta, conviction_score),
            )
        conn.commit()
        logger.info(
            "Logged trade: %s %s x%.4f @ $%.2f", action, ticker, quantity, execution_price
        )
    finally:
        if conn is not None:
            conn.close()
