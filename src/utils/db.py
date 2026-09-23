"""
PostgreSQL telemetry for trades, backtests, and autonomous-run health.

Connects to the database identified by the `DATABASE_URL` environment
variable (e.g. postgresql://user:password@host:5432/dbname). Schema
creation/migration is a separate, explicit step (`ensure_schema`) from
logging a row: entry points call `ensure_schema()` exactly once per
process, and row writers then just insert — they no
longer run `CREATE TABLE`/`ALTER TABLE` on every single call, which
previously meant a 4-statement DDL round-trip before every individual
trade insert.

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

DB_CONNECT_TIMEOUT_SECONDS = 10
DB_STATEMENT_TIMEOUT_MS = 15_000


def _connect(database_url: str):
    """Open a bounded telemetry connection so a database stall cannot block a run indefinitely."""
    return psycopg2.connect(
        database_url,
        application_name="valuation-engine-telemetry",
        connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
        options=f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}",
    )


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

# Same idempotent-migration pattern as ALTER_TABLE_ADD_ALTMAN_Z_SQL, for the
# Monte Carlo portfolio risk metrics (`src.risk.monte_carlo.calculate_portfolio_var`).
# These are portfolio-level, not per-trade, so only the single synthetic
# "RISK_SNAPSHOT" row logged at the end of a live run populates them —
# every per-ticker trade row leaves them NULL.
ALTER_TABLE_ADD_VAR_CVAR_SQL = """
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS var_95 FLOAT;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS cvar_95 FLOAT;
"""

# Existing trade and risk rows remain unlabelled. Only new paper runs can
# attach account provenance; the risk API never treats legacy rows as current.
ALTER_TRADE_LOGS_ACCOUNT_PROVENANCE_SQL = """
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS account_epoch TEXT;
ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS account_fingerprint TEXT;
"""

CREATE_RISK_ACCOUNT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS trade_logs_risk_account_latest_idx
ON trade_logs (account_epoch, timestamp DESC, id DESC)
WHERE action = 'RISK_SNAPSHOT';
"""

INSERT_SQL = """
INSERT INTO trade_logs (
    ticker, action, quantity, execution_price, wacc, beta, conviction_score, altman_z_score,
    var_95, cvar_95, account_epoch, account_fingerprint
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
"""

CREATE_BACKTEST_CURVE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS backtest_curve (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    strategy_value DOUBLE PRECISION NOT NULL,
    spy_value DOUBLE PRECISION NOT NULL
);
"""

CREATE_REBALANCE_RUN_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS rebalance_run_events (
    id BIGSERIAL PRIMARY KEY,
    event_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('started', 'completed', 'failed')),
    completion_status TEXT CHECK (completion_status IN ('healthy', 'incomplete')),
    decision_outcome TEXT NOT NULL CHECK (
        decision_outcome IN ('candidates', 'no_candidates', 'not_reached')
    ),
    mode TEXT NOT NULL CHECK (mode IN ('dry-run', 'execute')),
    git_sha TEXT,
    trigger TEXT NOT NULL,
    universe_count INTEGER CHECK (universe_count >= 0),
    valued_count INTEGER CHECK (valued_count >= 0),
    eligible_count INTEGER CHECK (eligible_count >= 0),
    failure_stage TEXT,
    failure_code TEXT,
    UNIQUE (run_id, event_type),
    CHECK (
        (event_type = 'completed' AND completion_status IS NOT NULL)
        OR (event_type <> 'completed' AND completion_status IS NULL)
    ),
    CHECK (
        (event_type = 'failed' AND failure_code IS NOT NULL)
        OR event_type <> 'failed'
    ),
    CHECK (universe_count IS NULL OR valued_count IS NULL OR valued_count <= universe_count),
    CHECK (valued_count IS NULL OR eligible_count IS NULL OR eligible_count <= valued_count)
);
"""

# Additive, idempotent: scheduler-delay and account-epoch diagnostics.
ALTER_REBALANCE_RUN_EVENTS_DIAGNOSTICS_SQL = """
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS account_epoch TEXT;
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS account_fingerprint TEXT;
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS scheduled_for_estimate TIMESTAMPTZ;
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS schedule_estimate_ambiguous BOOLEAN;
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS queue_delay_estimate_seconds INTEGER
    CHECK (queue_delay_estimate_seconds IS NULL OR queue_delay_estimate_seconds >= 0);
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS market_open_at_start BOOLEAN;
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS orders_attempted INTEGER
    CHECK (orders_attempted IS NULL OR orders_attempted >= 0);
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS orders_filled INTEGER
    CHECK (orders_filled IS NULL OR orders_filled >= 0);
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS orders_partially_filled INTEGER
    CHECK (orders_partially_filled IS NULL OR orders_partially_filled >= 0);
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS orders_skipped_market_closed INTEGER
    CHECK (orders_skipped_market_closed IS NULL OR orders_skipped_market_closed >= 0);
ALTER TABLE rebalance_run_events ADD COLUMN IF NOT EXISTS run_outcome TEXT CHECK (
    run_outcome IS NULL OR run_outcome IN (
        'dry_run', 'market_closed', 'market_closed_after_order_attempts',
        'market_closed_after_partial_execution',
        'no_eligible_candidates', 'no_orders_needed', 'orders_filled', 'orders_incomplete'
    )
);
"""

CREATE_REBALANCE_RUN_EVENTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS rebalance_run_events_latest_idx
ON rebalance_run_events (event_at DESC, id DESC);
"""

CREATE_REBALANCE_RUN_EVENTS_APPEND_ONLY_SQL = """
CREATE OR REPLACE FUNCTION reject_rebalance_run_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'rebalance_run_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS rebalance_run_events_reject_mutation ON rebalance_run_events;
CREATE TRIGGER rebalance_run_events_reject_mutation
BEFORE UPDATE OR DELETE ON rebalance_run_events
FOR EACH ROW EXECUTE FUNCTION reject_rebalance_run_event_mutation();
"""

TRUNCATE_BACKTEST_CURVE_SQL = "TRUNCATE TABLE backtest_curve;"

INSERT_BACKTEST_CURVE_SQL = """
INSERT INTO backtest_curve (date, strategy_value, spy_value)
VALUES (%s, %s, %s);
"""


def _get_database_url() -> str:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to `.env` to enable database telemetry."
        )
    return database_url


def ensure_schema() -> None:
    """
    Create/migrate every table this module writes to (`trade_logs`,
    `backtest_curve`, `rebalance_run_events`). Idempotent — every table or
    column creation is guarded, safe to call repeatedly, and never
    destructive to existing rows. The run-event trigger enforces that
    operational receipts remain append-only after insertion.

    Entry points (`src.trading.alpaca_execution.main`,
    `src.backtesting.historical_tester.run_backtest`) call this once at
    process start; row writers assume the schema already exists and do not
    run any DDL themselves.

    Raises:
        RuntimeError: If `DATABASE_URL` is not set.
        psycopg2.Error: If the connection or a DDL statement fails.
    """
    database_url = _get_database_url()
    conn = None
    try:
        conn = _connect(database_url)
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            cur.execute(ALTER_TABLE_ADD_ALTMAN_Z_SQL)
            cur.execute(ALTER_TABLE_ADD_VAR_CVAR_SQL)
            cur.execute(ALTER_TRADE_LOGS_ACCOUNT_PROVENANCE_SQL)
            cur.execute(CREATE_RISK_ACCOUNT_INDEX_SQL)
            cur.execute(CREATE_BACKTEST_CURVE_TABLE_SQL)
            cur.execute(CREATE_REBALANCE_RUN_EVENTS_TABLE_SQL)
            cur.execute(ALTER_REBALANCE_RUN_EVENTS_DIAGNOSTICS_SQL)
            cur.execute(CREATE_REBALANCE_RUN_EVENTS_INDEX_SQL)
            cur.execute(CREATE_REBALANCE_RUN_EVENTS_APPEND_ONLY_SQL)
        conn.commit()
    finally:
        if conn is not None:
            conn.close()


def log_trade(
    ticker: str,
    action: str,
    quantity: float,
    execution_price: float,
    wacc: Optional[float],
    beta: Optional[float],
    conviction_score: Optional[float],
    altman_z_score: Optional[float],
    var_95: Optional[float] = None,
    cvar_95: Optional[float] = None,
    account_epoch: Optional[str] = None,
    account_fingerprint: Optional[str] = None,
) -> None:
    """
    Record one executed trade (or, for `var_95`/`cvar_95`, a portfolio-
    level risk snapshot) to the `trade_logs` Postgres table.

    Connects using the `DATABASE_URL` environment variable and inserts a
    single row. Assumes the schema already exists — call `ensure_schema()`
    once at process start before any `log_trade` calls; this function no
    longer runs any `CREATE TABLE`/`ALTER TABLE` itself. `id` and
    `timestamp` are populated by the database (auto-increment primary key
    and `NOW()` respectively) — not passed in.

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
        var_95: Portfolio-level 95% Monte Carlo VaR at the time of this
            row, if known. Only meaningful on the end-of-run "RISK_SNAPSHOT"
            row (`src.trading.alpaca_execution`) — per-trade rows leave
            this None.
        cvar_95: Portfolio-level 95% Monte Carlo CVaR (Expected Shortfall),
            same caveat as `var_95`.
        account_epoch: Non-secret paper-account generation label on a risk
            snapshot. Legacy rows and ordinary trades leave it NULL.
        account_fingerprint: One-way account-ID fingerprint on a risk snapshot.

    Raises:
        RuntimeError: If `DATABASE_URL` is not set.
        psycopg2.Error: If the connection or query fails. Callers should
            catch this (and RuntimeError) rather than let a telemetry
            failure abort trade execution.
    """
    database_url = _get_database_url()

    conn = None
    try:
        conn = _connect(database_url)
        with conn.cursor() as cur:
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
                    _to_native_float(var_95),
                    _to_native_float(cvar_95),
                    account_epoch,
                    account_fingerprint,
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

    Clears any previously logged curve (`TRUNCATE TABLE`) and inserts one
    row per date. A backtest run is a full replacement of "the" curve,
    not an append — there is only ever one current curve for the frontend
    to display. Assumes the schema already exists — call `ensure_schema()`
    once at process start; this function no longer runs `CREATE TABLE` itself.

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

    database_url = _get_database_url()

    conn = None
    try:
        conn = _connect(database_url)
        with conn.cursor() as cur:
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
