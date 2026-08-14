import { NextResponse } from "next/server";
import { Pool } from "pg";
import { requireSession } from "@/lib/auth";

// A pg.Pool is stashed on `globalThis` (not a plain module-level variable)
// so it survives Next.js dev-server HMR reloads instead of leaking a new
// pool — and its connections — on every hot reload.
declare global {
  var _tradesPgPool: Pool | undefined;
}

function getPool(): Pool {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error("DATABASE_URL is not set.");
  }
  if (!global._tradesPgPool) {
    global._tradesPgPool = new Pool({ connectionString });
  }
  return global._tradesPgPool;
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

const DEFAULT_PAGE_LIMIT = 50;
const MAX_PAGE_LIMIT = 200;

function parsePagination(url: string): { limit: number; offset: number } {
  const { searchParams } = new URL(url);

  const rawLimit = Number(searchParams.get("limit"));
  const limit =
    Number.isInteger(rawLimit) && rawLimit > 0 ? Math.min(rawLimit, MAX_PAGE_LIMIT) : DEFAULT_PAGE_LIMIT;

  const rawOffset = Number(searchParams.get("offset"));
  const offset = Number.isInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;

  return { limit, offset };
}

/**
 * GET /api/trades?limit=50&offset=0
 *
 * Returns a page of `trade_logs` rows (written by the Python trading
 * engine's `src.utils.db.log_trade`), most recent first, along with the
 * total matching row count so the client can page through the full
 * history instead of fetching every row on every load — this table has
 * no upper bound on how many rows a long-running live deployment
 * accumulates.
 *
 * The synthetic end-of-run `action = 'RISK_SNAPSHOT'` row (see
 * `src.trading.alpaca_execution.main` and `src.risk.monte_carlo`) is
 * excluded — it carries no per-trade data (ticker/quantity/price are
 * placeholders) and would otherwise show up as a nonsensical "trade" in
 * this listing; it's surfaced separately via `/api/risk`.
 *
 * If `trade_logs` doesn't exist yet (no trade has ever been logged),
 * this returns an empty page rather than an error — that's a normal
 * empty state, not a failure.
 */
export async function GET(request: Request) {
  if (!(await requireSession())) {
    return NextResponse.json({ error: "Not authenticated." }, { status: 401 });
  }

  let pool: Pool;
  try {
    pool = getPool();
  } catch (error) {
    console.error(error);
    return NextResponse.json(
      { error: "DATABASE_URL is not configured for this deployment." },
      { status: 500 }
    );
  }

  const { limit, offset } = parsePagination(request.url);

  try {
    const { rows } = await pool.query(
      `SELECT *, COUNT(*) OVER() AS total_count
       FROM trade_logs
       WHERE action != 'RISK_SNAPSHOT'
       ORDER BY timestamp DESC
       LIMIT $1 OFFSET $2`,
      [limit, offset]
    );

    const total = rows.length > 0 ? Number(rows[0].total_count) : 0;
    const trades = rows.map((row) => {
      const trade = { ...row };
      delete trade.total_count;
      return trade;
    });

    return NextResponse.json({ trades, total, limit, offset });
  } catch (error) {
    if (isUndefinedTableError(error)) {
      return NextResponse.json({ trades: [], total: 0, limit, offset });
    }
    console.error("Failed to fetch trade logs:", error);
    return NextResponse.json(
      { error: "Failed to fetch trade logs." },
      { status: 500 }
    );
  }
}
