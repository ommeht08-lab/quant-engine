import Link from "next/link";
import { Pool } from "pg";

// Always query live — a tear sheet is a point-in-time snapshot of the
// latest trade telemetry, never a cacheable resource.
export const dynamic = "force-dynamic";

// A pg.Pool is stashed on `globalThis` (not a plain module-level variable)
// so it survives Next.js dev-server HMR reloads instead of leaking a new
// pool — and its connections — on every hot reload.
declare global {
  var _tearSheetPgPool: Pool | undefined;
}

function getPool(): Pool {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error("DATABASE_URL is not set.");
  }
  if (!global._tearSheetPgPool) {
    global._tearSheetPgPool = new Pool({ connectionString });
  }
  return global._tearSheetPgPool;
}

interface LatestTrade {
  action: string;
  execution_price: number;
  quantity: number;
  wacc: number | null;
  beta: number | null;
  conviction_score: number | null;
  altman_z_score: number | null;
  timestamp: string;
}

/**
 * Fetch the most recent `trade_logs` row for `ticker` (written by the
 * Python execution engine's `src.utils.db.log_trade`). Returns null on
 * any failure — missing `DATABASE_URL`, the table not existing yet, or
 * genuinely no trade ever logged for this ticker — so the page can
 * render one clean "no data" state rather than distinguishing causes.
 */
async function getLatestTrade(ticker: string): Promise<LatestTrade | null> {
  let pool: Pool;
  try {
    pool = getPool();
  } catch (error) {
    console.error(error);
    return null;
  }

  try {
    const { rows } = await pool.query(
      `SELECT action, execution_price, quantity, wacc, beta, conviction_score, altman_z_score, timestamp
       FROM trade_logs
       WHERE ticker = $1
       ORDER BY timestamp DESC
       LIMIT 1`,
      [ticker]
    );
    if (rows.length === 0) return null;

    const row = rows[0];
    return {
      action: String(row.action),
      execution_price: Number(row.execution_price),
      quantity: Number(row.quantity),
      wacc: row.wacc === null ? null : Number(row.wacc),
      beta: row.beta === null ? null : Number(row.beta),
      conviction_score: row.conviction_score === null ? null : Number(row.conviction_score),
      altman_z_score: row.altman_z_score === null ? null : Number(row.altman_z_score),
      timestamp: row.timestamp instanceof Date ? row.timestamp.toISOString() : String(row.timestamp),
    };
  } catch (error) {
    console.error(`Failed to fetch tear sheet data for ${ticker}:`, error);
    return null;
  }
}

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatCurrency(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return currencyFormatter.format(value);
}

function formatPercent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "N/A";
  return `${(value * 100).toFixed(2)}%`;
}

function formatNumber(value: number | null, digits = 2): string {
  if (value === null || !Number.isFinite(value)) return "N/A";
  return value.toFixed(digits);
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function altmanZoneLabel(zScore: number | null): { label: string; className: string } {
  if (zScore === null || !Number.isFinite(zScore)) {
    return { label: "N/A", className: "text-neutral-500" };
  }
  if (zScore < 1.8) return { label: "Distress Zone", className: "text-rose-400" };
  if (zScore <= 2.99) return { label: "Grey Zone", className: "text-amber-400" };
  return { label: "Safe Zone", className: "text-emerald-400" };
}

function MetricCard({
  label,
  value,
  sublabel,
  sublabelClassName,
}: {
  label: string;
  value: string;
  sublabel?: string;
  sublabelClassName?: string;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[.03] p-6">
      <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">{label}</p>
      <p className="mt-2 font-mono text-2xl tracking-tight text-neutral-50">{value}</p>
      {sublabel && (
        <p className={`mt-1 text-xs ${sublabelClassName ?? "text-neutral-500"}`}>{sublabel}</p>
      )}
    </div>
  );
}

function ActionBadge({ action }: { action: string }) {
  const normalized = action.toUpperCase();
  const colorClasses =
    normalized === "BUY"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
      : normalized === "SELL"
        ? "border-rose-500/30 bg-rose-500/10 text-rose-400"
        : "border-white/10 bg-white/[.05] text-neutral-300";

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wide ${colorClasses}`}
    >
      {action}
    </span>
  );
}

export default async function TickerTearSheetPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  const ticker = symbol.toUpperCase();
  const trade = await getLatestTrade(ticker);
  const zZone = trade ? altmanZoneLabel(trade.altman_z_score) : null;

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-50">
      <div className="mx-auto max-w-5xl px-6 py-12">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-sm text-neutral-400 transition-colors hover:text-emerald-400"
        >
          ← Back to Dashboard
        </Link>

        <header className="mt-6 mb-10">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-500">
            Tear Sheet
          </p>
          <h1 className="mt-2 text-4xl font-semibold tracking-tight text-neutral-50 sm:text-5xl">
            {ticker}
          </h1>
        </header>

        {!trade ? (
          <div className="rounded-2xl border border-dashed border-white/10 px-6 py-16 text-center text-sm text-neutral-500">
            No trade telemetry found for {ticker}. Once the autonomous execution engine logs a
            trade for this ticker, its metrics will show up here.
          </div>
        ) : (
          <div className="space-y-8">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <MetricCard label="WACC" value={formatPercent(trade.wacc)} />
              <MetricCard label="Beta" value={formatNumber(trade.beta)} />
              <MetricCard label="Conviction Score" value={formatNumber(trade.conviction_score, 3)} />
              <MetricCard
                label="Altman Z-Score"
                value={formatNumber(trade.altman_z_score)}
                sublabel={zZone?.label}
                sublabelClassName={zZone?.className}
              />
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/[.03] p-6 sm:p-8">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-neutral-400">
                Last Trade Details
              </h2>
              <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
                <div>
                  <p className="text-xs uppercase tracking-wider text-neutral-500">Action</p>
                  <div className="mt-1.5">
                    <ActionBadge action={trade.action} />
                  </div>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wider text-neutral-500">Price</p>
                  <p className="mt-1.5 font-mono text-neutral-50">
                    {formatCurrency(trade.execution_price)}
                  </p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wider text-neutral-500">Quantity</p>
                  <p className="mt-1.5 font-mono text-neutral-50">{formatNumber(trade.quantity, 4)}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wider text-neutral-500">Timestamp</p>
                  <p className="mt-1.5 font-mono text-neutral-50">
                    {formatTimestamp(trade.timestamp)}
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
