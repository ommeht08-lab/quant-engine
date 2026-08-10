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
from typing import List, Optional

import psycopg2
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _to_native_float(value: Optional[float]) -> Optional[float]:
    """
    Coerce a numeric value to a native Python `float` (or None), guarding
    against numpy scalar types (e.g. `numpy.float64`) that pandas-derived
    calculations can silently leak — psycopg2 cannot adapt those and
    fails the whole INSERT with a `schema "np" does not exist` error.
    """
    return None if value is None else float(value)


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
    conviction_score DOUBLE PRECISION,
    altman_z_score DOUBLE PRECISION
);
"""

# Safe to run against a database that already has `trade_logs` from before
# `altman_z_score` existed — `ADD COLUMN IF NOT EXISTS` is a no-op (not an
# error) once the column is already there, and never touches existing rows.
ALTER_TABLE_ADD_ALTMAN_Z_SQL = """
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS altman_z_score FLOAT;
"""

INSERT_SQL = """
INSERT INTO trade_logs (ticker, action, quantity, execution_price, wacc, beta, conviction_score, altman_z_score)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
"""

CREATE_BACKTEST_CURVE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS backtest_curve (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    strategy_value DOUBLE PRECISION NOT NULL,
    spy_value DOUBLE PRECISION NOT NULL
);
"""

TRUNCATE_BACKTEST_CURVE_SQL = "TRUNCATE TABLE backtest_curve;"

INSERT_BACKTEST_CURVE_SQL = """
INSERT INTO backtest_curve (date, strategy_value, spy_value)
VALUES (%s, %s, %s);
"""


def log_trade(
    ticker: str,
    action: str,
    quantity: float,
    execution_price: float,
    wacc: Optional[float],
    beta: Optional[float],
    conviction_score: Optional[float],
    altman_z_score: Optional[float],
) -> None:
    """
    Record one executed trade to the `trade_logs` Postgres table.

    Connects using the `DATABASE_URL` environment variable, ensures the
    table exists (`CREATE TABLE IF NOT EXISTS`), migrates it forward if
    it already existed from before `altman_z_score` was added (`ALTER
    TABLE ... ADD COLUMN IF NOT EXISTS`, a no-op if already present and
    never destructive to existing rows), and inserts a single row. `id`
    and `timestamp` are populated by the database (auto-increment primary
    key and `NOW()` respectively) — not passed in.

    Args:
        ticker: Stock ticker symbol, e.g. "AAPL".
        action: Trade action, e.g. "BUY" or "SELL".
        quantity: Number of shares transacted.
        execution_price: Fill (or best-known reference) price per share.
        wacc: The ticker's WACC at the time of the trade decision, if known.
        beta: The ticker's beta at the time of the trade decision, if known.
        conviction_score: The Conviction Score that drove this trade, if known.
        altman_z_score: The ticker's Altman Z-Score credit health check at
            the time of the trade decision, if known.

    Raises:
        RuntimeError: If `DATABASE_URL` is not set.
        psycopg2.Error: If the connection or query fails. Callers should
            catch this (and RuntimeError) rather than let a telemetry
            failure abort trade execution.
    """
    load_dotenv()
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
            cur.execute(ALTER_TABLE_ADD_ALTMAN_Z_SQL)
            cur.execute(
                INSERT_SQL,
                (
                    ticker,
                    action,
                    _to_native_float(quantity),
                    _to_native_float(execution_price),
                    _to_native_float(wacc),
                    _to_native_float(beta),
                    _to_native_float(conviction_score),
                    _to_native_float(altman_z_score),
                ),
            )
        conn.commit()
        logger.info(
            "Logged trade: %s %s x%.4f @ $%.2f", action, ticker, quantity, execution_price
        )
    finally:
        if conn is not None:
            conn.close()


def log_backtest_curve(
    dates: List[str],
    strategy_values: List[float],
    spy_values: List[float],
) -> None:
    """
    Replace the `backtest_curve` Postgres table with a fresh equity-curve
    timeseries, read by the Next.js dashboard's backtest visualizer.

    Ensures the table exists, clears any previously logged curve
    (`TRUNCATE TABLE`), and inserts one row per date. A backtest run is
    a full replacement of "the" curve, not an append — there is only
    ever one current curve for the frontend to display.

    Args:
        dates: ISO date strings (e.g. "2024-08-01"), one per data point,
            ascending.
        strategy_values: Strategy portfolio equity value at each date,
            same length and order as `dates`.
        spy_values: SPY benchmark equity value at each date, same basis,
            same length and order as `dates`.

    Raises:
        RuntimeError: If `DATABASE_URL` is not set, or if `dates`,
            `strategy_values`, and `spy_values` aren't the same length.
        psycopg2.Error: If the connection or query fails. Callers should
            catch this (and RuntimeError) rather than let a telemetry
            failure abort the backtest run.
    """
    if not (len(dates) == len(strategy_values) == len(spy_values)):
        raise RuntimeError(
            "log_backtest_curve: dates, strategy_values, and spy_values must be the same length."
        )

    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to `.env` to enable backtest curve logging."
        )

    conn = None
    try:
        conn = psycopg2.connect(database_url)
        with conn.cursor() as cur:
            cur.execute(CREATE_BACKTEST_CURVE_TABLE_SQL)
            cur.execute(TRUNCATE_BACKTEST_CURVE_SQL)
            cur.executemany(
                INSERT_BACKTEST_CURVE_SQL,
                [
                    (date, _to_native_float(strategy_value), _to_native_float(spy_value))
                    for date, strategy_value, spy_value in zip(dates, strategy_values, spy_values)
                ],
            )
        conn.commit()
        logger.info("Logged %d backtest equity curve point(s).", len(dates))
    finally:
        if conn is not None:
            conn.close()
