import { NextResponse } from "next/server";
import { Pool } from "pg";
import { cacheAside } from "@/lib/redis";

// A pg.Pool is stashed on `globalThis` (not a plain module-level variable)
// so it survives Next.js dev-server HMR reloads instead of leaking a new
// pool — and its connections — on every hot reload.
declare global {
  var _riskPgPool: Pool | undefined;
}

function getPool(): Pool {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error("DATABASE_URL is not set.");
  }
  if (!global._riskPgPool) {
    global._riskPgPool = new Pool({ connectionString });
  }
  return global._riskPgPool;
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

export interface RiskMetrics {
  var95: number;
  cvar95: number;
  asOf: string;
}

const CACHE_KEY = "risk:latest";
// 5 minutes: long enough to meaningfully cut repeated-load DB traffic,
// short enough that a fresh live-trading run's risk snapshot becomes
// visible again without needing manual invalidation.
const CACHE_TTL_SECONDS = 300;

/**
 * Fetch the most recently logged portfolio-level Monte Carlo VaR/CVaR
 * snapshot — the synthetic "RISK_SNAPSHOT" row appended by the Python
 * trading engine's `src.trading.alpaca_execution` as the final
 * `log_trade` call of each live run (see `src.risk.monte_carlo`).
 * Returns null if no snapshot has ever been logged (a normal empty
 * state, not a failure) or `trade_logs` doesn't exist yet.
 */
async function fetchLatestRisk(): Promise<RiskMetrics | null> {
  const pool = getPool();
  try {
    const { rows } = await pool.query(
      `SELECT timestamp, var_95, cvar_95 FROM trade_logs
       WHERE action = 'RISK_SNAPSHOT' AND var_95 IS NOT NULL AND cvar_95 IS NOT NULL
       ORDER BY timestamp DESC
       LIMIT 1`
    );
    if (rows.length === 0) return null;

    const row = rows[0];
    return {
      var95: Number(row.var_95),
      cvar95: Number(row.cvar_95),
      asOf: row.timestamp instanceof Date ? row.timestamp.toISOString() : String(row.timestamp),
    };
  } catch (error) {
    if (isUndefinedTableError(error)) {
      return null;
    }
    throw error;
  }
}

/**
 * GET /api/risk
 *
 * Returns the latest recorded Monte Carlo 95% VaR / CVaR (Expected
 * Shortfall) portfolio risk metrics, cached (cache-aside) in Upstash
 * Redis for CACHE_TTL_SECONDS. Responds with `null` (200 OK) — not an
 * error — if no live trading run has logged a risk snapshot yet.
 */
export async function GET() {
  if (!process.env.DATABASE_URL) {
    return NextResponse.json(
      { error: "DATABASE_URL is not configured for this deployment." },
      { status: 500 }
    );
  }

  try {
    const data = await cacheAside(CACHE_KEY, CACHE_TTL_SECONDS, fetchLatestRisk);
    return NextResponse.json(data);
  } catch (error) {
    console.error("Failed to fetch latest risk metrics:", error);
    return NextResponse.json(
      { error: "Failed to fetch latest risk metrics." },
      { status: 500 }
    );
  }
}
