import { NextResponse } from "next/server";
import { Pool } from "pg";
import { requireSession } from "@/lib/auth";
import { deriveRunHealth, type RunHealthEventRow } from "@/lib/run-health";

export const dynamic = "force-dynamic";

declare global {
  var _runHealthPgPool: Pool | undefined;
}

function getPool(): Pool {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) throw new Error("DATABASE_URL is not set.");
  if (!global._runHealthPgPool) global._runHealthPgPool = new Pool({ connectionString });
  return global._runHealthPgPool;
}

function isUndefinedTableError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    (error as { code?: string }).code === "42P01"
  );
}

/** Return the latest run's immutable lifecycle events; null means no run is recorded yet. */
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
    const { rows } = await getPool().query(
      `SELECT run_id, event_type, event_at, completion_status, decision_outcome,
              mode, git_sha, trigger, universe_count, valued_count, eligible_count,
              failure_stage, failure_code
       FROM rebalance_run_events
       WHERE run_id = (
         SELECT run_id FROM rebalance_run_events
         ORDER BY event_at DESC, id DESC
         LIMIT 1
       )
       ORDER BY event_at ASC, id ASC`
    );
    return NextResponse.json(deriveRunHealth(rows as RunHealthEventRow[]));
  } catch (error) {
    if (isUndefinedTableError(error)) return NextResponse.json(null);
    console.error("Failed to fetch trading run health:", error);
    return NextResponse.json(
      { error: "Failed to fetch trading run health." },
      { status: 500 }
    );
  }
}
