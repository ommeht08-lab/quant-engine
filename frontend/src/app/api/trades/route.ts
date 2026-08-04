import { NextResponse } from "next/server";
import { Pool } from "pg";

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

/**
 * GET /api/trades
 *
 * Returns every row from the `trade_logs` table (written by the Python
 * trading engine's `src.utils.db.log_trade`), most recent first.
 *
 * If `trade_logs` doesn't exist yet (no trade has ever been logged),
 * this returns an empty array rather than an error — that's a normal
 * empty state, not a failure.
 */
export async function GET() {
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

  try {
    const { rows } = await pool.query(
      "SELECT * FROM trade_logs ORDER BY timestamp DESC"
    );
    return NextResponse.json(rows);
  } catch (error) {
    if (isUndefinedTableError(error)) {
      return NextResponse.json([]);
    }
    console.error("Failed to fetch trade logs:", error);
    return NextResponse.json(
      { error: "Failed to fetch trade logs." },
      { status: 500 }
    );
  }
}
