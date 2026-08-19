import Link from "next/link";
import { redirect } from "next/navigation";
import { Pool } from "pg";
import {
  getTickerSentimentAndMacro,
  SENTIMENT_CACHE_TTL_SECONDS,
  type Headline,
  type TickerSentiment,
} from "@/lib/sentiment";
import { cacheAside } from "@/lib/redis";
import { requireSession } from "@/lib/auth";
import { safeHeadlineHref } from "@/lib/headline-links";

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
    return { label: "N/A", className: "text-[var(--paper-dim)]" };
  }
  if (zScore < 1.8) return { label: "Distress Zone", className: "text-[var(--signal)]" };
  if (zScore <= 2.99) return { label: "Grey Zone", className: "text-[var(--brass)]" };
  return { label: "Safe Zone", className: "text-[var(--verdigris)]" };
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
    <div className="metric-panel p-5">
      <p className="data-label text-[var(--paper-dim)]">{label}</p>
      <p className="mt-3 font-mono text-2xl tracking-tight text-[var(--paper)]">{value}</p>
      {sublabel && (
        <p className={`mt-2 text-xs ${sublabelClassName ?? "text-[var(--paper-dim)]"}`}>{sublabel}</p>
      )}
    </div>
  );
}

function HeadlineList({ headlines }: { headlines: Headline[] }) {
  if (headlines.length === 0) {
    return <p className="text-sm text-[var(--paper-dim)]">No recent headlines found for this ticker.</p>;
  }

  return (
    <ul className="space-y-3">
      {headlines.map((headline, index) => {
        const href = safeHeadlineHref(headline.link);
        return (
        <li
          key={`${headline.link ?? headline.title}-${index}`}
          className="border-b border-[var(--line)] pb-3 last:border-0 last:pb-0"
        >
          {href ? (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium text-[var(--paper)] transition-colors hover:text-[var(--verdigris)]"
            >
              {headline.title}
            </a>
          ) : (
            <span className="text-sm font-medium text-[var(--paper)]">{headline.title}</span>
          )}
          <div className="mt-1 flex items-center gap-2 text-xs text-[var(--paper-dim)]">
            {headline.publisher && <span>{headline.publisher}</span>}
            {headline.publisher && headline.publishedAt && <span>·</span>}
            {headline.publishedAt && <span>{formatTimestamp(headline.publishedAt)}</span>}
          </div>
        </li>
        );
      })}
    </ul>
  );
}

function MarketContextSection({ sentiment }: { sentiment: TickerSentiment }) {
  return (
    <div className="panel p-6 sm:p-8">
      <h2 className="panel-title mb-5">
        Market Context &amp; Qualitative Drivers
      </h2>

      <div className="mb-6 grid grid-cols-2 gap-4 sm:max-w-xs">
        <div className="metric-panel p-4">
          <p className="data-label text-[var(--paper-dim)]">10-Yr Treasury Yield</p>
          <p className="mt-2 font-mono text-lg text-[var(--paper)]">
            {formatPercent(sentiment.macro.treasury10y)}
          </p>
        </div>
        <div className="metric-panel p-4">
          <p className="data-label text-[var(--paper-dim)]">VIX</p>
          <p className="mt-2 font-mono text-lg text-[var(--paper)]">
            {formatNumber(sentiment.macro.vix)}
          </p>
        </div>
      </div>

      <h3 className="data-label mb-3 text-[var(--paper-dim)]">
        Recent Headlines
      </h3>
      <HeadlineList headlines={sentiment.headlines} />
    </div>
  );
}

function ActionBadge({ action }: { action: string }) {
  const normalized = action.toUpperCase();
  const colorClasses =
    normalized === "BUY"
      ? "border-[rgba(85,184,170,.35)] bg-[var(--verdigris-soft)] text-[var(--verdigris)]"
      : normalized === "SELL"
        ? "border-[rgba(228,113,104,.35)] bg-[rgba(228,113,104,.09)] text-[var(--signal)]"
        : "border-[var(--line)] bg-[rgba(236,232,220,.04)] text-[var(--paper-muted)]";

  return (
    <span
      className={`inline-flex items-center border px-2.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-[.08em] ${colorClasses}`}
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
  // Defense in depth: this page queries Postgres/Redis directly (see
  // `getLatestTrade`/`cacheAside` below) rather than going through an
  // already-protected API route, so it must not rely solely on
  // `src/proxy.ts` for authentication — a matcher change or refactor
  // there must not silently expose trade telemetry. Same session check
  // as every private API route (`src/lib/auth.ts#requireSession`).
  if (!(await requireSession())) {
    redirect("/login");
  }

  const { symbol } = await params;
  const ticker = symbol.toUpperCase();
  const [trade, sentiment] = await Promise.all([
    getLatestTrade(ticker),
    cacheAside<TickerSentiment>(`sentiment:${ticker}`, SENTIMENT_CACHE_TTL_SECONDS, () =>
      getTickerSentimentAndMacro(ticker)
    ),
  ]);
  const zZone = trade ? altmanZoneLabel(trade.altman_z_score) : null;

  return (
    <div className="page-shell">
      <div className="shell-container max-w-5xl pb-20">
        <Link
          href="/"
          className="inline-flex items-center gap-1 pt-10 text-sm text-[var(--paper-muted)] transition-colors hover:text-[var(--verdigris)]"
        >
          ← Back to Dashboard
        </Link>

        <header className="page-header mt-0">
          <div>
          <p className="eyebrow mb-5">Security tear sheet</p>
          <h1 className="display-title">
            {ticker}
          </h1>
          </div>
          <p className="page-deck">The latest execution evidence, quality signals, and market context recorded for this security.</p>
        </header>

        {!trade ? (
          <div className="empty-state px-6 py-16 text-center">
            <strong className="block text-[var(--paper-muted)]">No execution evidence is recorded for {ticker}.</strong>
            <span className="mt-1 block">A tear sheet appears after a completed paper execution records this security.</span>
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

            <MarketContextSection sentiment={sentiment} />

            <div className="panel p-6 sm:p-8">
              <h2 className="panel-title mb-4">
                Last Trade Details
              </h2>
              <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
                <div>
                  <p className="data-label text-[var(--paper-dim)]">Action</p>
                  <div className="mt-1.5">
                    <ActionBadge action={trade.action} />
                  </div>
                </div>
                <div>
                  <p className="data-label text-[var(--paper-dim)]">Price</p>
                  <p className="mt-1.5 font-mono text-[var(--paper)]">
                    {formatCurrency(trade.execution_price)}
                  </p>
                </div>
                <div>
                  <p className="data-label text-[var(--paper-dim)]">Quantity</p>
                  <p className="mt-1.5 font-mono text-[var(--paper)]">{formatNumber(trade.quantity, 4)}</p>
                </div>
                <div>
                  <p className="data-label text-[var(--paper-dim)]">Timestamp</p>
                  <p className="mt-1.5 font-mono text-[var(--paper)]">
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
