import { NextResponse } from "next/server";
import { Pool } from "pg";
import { cacheAside } from "@/lib/redis";

// A pg.Pool is stashed on `globalThis` (not a plain module-level variable)
// so it survives Next.js dev-server HMR reloads instead of leaking a new
// pool — and its connections — on every hot reload.
declare global {
  var _backtestPgPool: Pool | undefined;
}

function getPool(): Pool {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error("DATABASE_URL is not set.");
  }
  if (!global._backtestPgPool) {
    global._backtestPgPool = new Pool({ connectionString });
  }
  return global._backtestPgPool;
}

/** Postgres error code for "relation does not exist". */
const UNDEFINED_TABLE = "42P01";

function isUndefinedTableError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    (error as { code?: string }).code === UNDEFINED_TABLE
  );
}

export interface BacktestPoint {
  date: string;
  strategy: number;
  benchmark: number;
}

const CACHE_KEY = "backtest:curve";
// 5 minutes: long enough to meaningfully cut repeated-load DB traffic,
// short enough that a fresh backtest run (which TRUNCATEs + rewrites the
// whole table) becomes visible again without needing manual invalidation.
const CACHE_TTL_SECONDS = 300;

async function fetchBacktestCurve(): Promise<BacktestPoint[]> {
  const pool = getPool();
  try {
    const { rows } = await pool.query(
      "SELECT date, strategy_value AS strategy, spy_value AS benchmark FROM backtest_curve ORDER BY date ASC"
    );
    return rows.map((row) => ({
      date: row.date instanceof Date ? row.date.toISOString().slice(0, 10) : String(row.date),
      strategy: Number(row.strategy),
      benchmark: Number(row.benchmark),
    }));
  } catch (error) {
    if (isUndefinedTableError(error)) {
      // No backtest has ever been run against a configured database —
      // a normal empty state, not a failure.
      return [];
    }
    throw error;
  }
}

/**
 * GET /api/backtest
 *
 * Returns the strategy-vs-SPY equity curve written by the Python
 * backtester's `src.utils.db.log_backtest_curve` (called at the end of
 * `run_backtest` in `src.backtesting.historical_tester`), cached
 * (cache-aside) in Upstash Redis for `CACHE_TTL_SECONDS`.
 */
export async function GET() {
  if (!process.env.DATABASE_URL) {
    return NextResponse.json(
      { error: "DATABASE_URL is not configured for this deployment." },
      { status: 500 }
    );
  }

  try {
    const data = await cacheAside(CACHE_KEY, CACHE_TTL_SECONDS, fetchBacktestCurve);
    return NextResponse.json(data);
  } catch (error) {
    console.error("Failed to fetch backtest curve:", error);
    return NextResponse.json(
      { error: "Failed to fetch backtest curve." },
      { status: 500 }
    );
  }
}
