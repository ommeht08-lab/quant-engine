import { NextResponse } from "next/server";
import { Pool } from "pg";
import { cacheAside } from "@/lib/redis";
import { requireSession } from "@/lib/auth";

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

export interface RiskMetricsOk {
  status: "ok";
  var95: number;
  cvar95: number;
  asOf: string;
}

export interface RiskMetricsUnavailable {
  status: "unavailable";
  asOf: string;
}

export type RiskMetrics = RiskMetricsOk | RiskMetricsUnavailable;

const CACHE_KEY = "risk:latest";
// 5 minutes: long enough to meaningfully cut repeated-load DB traffic,
// short enough that a fresh live-trading run's risk snapshot becomes
// visible again without needing manual invalidation.
const CACHE_TTL_SECONDS = 300;

/**
 * Fetch the most recently logged portfolio-level risk snapshot — the
 * synthetic "RISK_SNAPSHOT" row appended by the Python trading engine's
 * `src.trading.alpaca_execution` as the final `log_trade` call of every
 * live (non-dry-run) invocation, whether or not VaR was computable that
 * run (see `src.risk.monte_carlo.VaRResult`). `var_95`/`cvar_95` are
 * NULL on the row when VaR was unavailable — that's surfaced as
 * `status: "unavailable"`, distinct from `null` here, which means no
 * live run has EVER logged a snapshot (a normal, different empty state).
 * `trade_logs` not existing yet degrades the same way.
 */
async function fetchLatestRisk(): Promise<RiskMetrics | null> {
  const pool = getPool();
  try {
    const { rows } = await pool.query(
      `SELECT timestamp, var_95, cvar_95 FROM trade_logs
       WHERE action = 'RISK_SNAPSHOT'
       ORDER BY timestamp DESC
       LIMIT 1`
    );
    if (rows.length === 0) return null;

    const row = rows[0];
    const asOf = row.timestamp instanceof Date ? row.timestamp.toISOString() : String(row.timestamp);

    if (row.var_95 === null || row.cvar_95 === null) {
      return { status: "unavailable", asOf };
    }

    return {
      status: "ok",
      var95: Number(row.var_95),
      cvar95: Number(row.cvar_95),
      asOf,
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
 * Returns the latest recorded portfolio risk snapshot as a `RiskMetrics`
 * (see above for the "ok" / "unavailable" discriminated union), cached
 * (cache-aside) in Upstash Redis for CACHE_TTL_SECONDS. Responds with
 * `null` (200 OK) — not an error — only if no live trading run has ever
 * logged a snapshot.
 */
export async function GET() {
  if (!(await requireSession())) {
    return NextResponse.json({ error: "Not authenticated." }, { status: 401 });
  }

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
