import { NextResponse } from "next/server";
import { Pool } from "pg";
import { cacheAside } from "@/lib/redis";
import { requireSession } from "@/lib/auth";
import { ACCOUNT_RISK_QUERY, configuredRiskEpoch, riskCacheKey } from "@/lib/risk-account";

export const dynamic = "force-dynamic";

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

/** No risk row for this account yet, or the additive schema has not run. */
const MISSING_RISK_SCHEMA = new Set(["42P01", "42703"]);

function isMissingRiskSchemaError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    MISSING_RISK_SCHEMA.has((error as { code?: string }).code ?? "")
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

// 5 minutes: long enough to meaningfully cut repeated-load DB traffic,
// short enough that a fresh live-trading run's risk snapshot becomes
// visible again without needing manual invalidation.
const CACHE_TTL_SECONDS = 300;

/**
 * Fetch the most recently logged portfolio-level risk snapshot — the
 * synthetic "RISK_SNAPSHOT" row appended by the Python trading engine's
 * `src.trading.alpaca_execution` as the final `log_trade` call of every
 * live (non-dry-run) invocation for the configured account, whether or not
 * VaR was computable that run (see `src.risk.monte_carlo.VaRResult`).
 * `var_95`/`cvar_95` are
 * NULL on the row when VaR was unavailable — that's surfaced as
 * `status: "unavailable"`, distinct from `null` here, which means no
 * live run has logged a snapshot for this account. Legacy unlabelled rows
 * are excluded. A not-yet-migrated `trade_logs` table also yields no snapshot.
 */
async function fetchLatestRisk(epoch: string): Promise<RiskMetrics | null> {
  const pool = getPool();
  try {
    const { rows } = await pool.query(ACCOUNT_RISK_QUERY, [epoch]);
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
    if (isMissingRiskSchemaError(error)) {
      console.warn("Account-scoped risk schema is not available yet.");
      return null;
    }
    throw error;
  }
}

/**
 * GET /api/risk
 *
 * Returns this configured paper account's latest risk snapshot as a `RiskMetrics`
 * (see above for the "ok" / "unavailable" discriminated union), cached
 * (cache-aside) in Upstash Redis for CACHE_TTL_SECONDS. Responds with
 * `null` (200 OK) when no tagged snapshot exists for this account. It never
 * falls back to an unlabelled or other-account row.
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

  const epoch = configuredRiskEpoch(process.env.ALPACA_ACCOUNT_EPOCH);
  if (!epoch) {
    return NextResponse.json(
      { error: "Account-specific risk is not configured for this deployment." },
      { status: 503 }
    );
  }

  try {
    const data = await cacheAside(riskCacheKey(epoch), CACHE_TTL_SECONDS, () => fetchLatestRisk(epoch));
    return NextResponse.json(data);
  } catch (error) {
    console.error("Failed to fetch latest risk metrics:", error);
    return NextResponse.json(
      { error: "Failed to fetch latest risk metrics." },
      { status: 500 }
    );
  }
}
