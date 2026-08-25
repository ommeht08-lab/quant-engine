"use client";

import { useEffect, useState } from "react";

interface TradeLog {
  id: number;
  timestamp: string;
  ticker: string;
  action: string;
  quantity: number;
  execution_price: number;
  wacc: number | null;
  beta: number | null;
  conviction_score: number | null;
}

interface TradesPage {
  trades: TradeLog[];
  total: number;
  limit: number;
  offset: number;
}

const PAGE_SIZE = 50;

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatCurrency(value: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return currencyFormatter.format(value);
}

function formatPercent(value: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function formatNumber(value: number | null, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
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

function ActionBadge({ action }: { action: string }) {
  const normalized = action.toUpperCase();
  const colorClasses =
    normalized === "BUY"
      ? "border-[var(--verdigris)] bg-[var(--verdigris-soft)] text-[var(--verdigris)]"
      : normalized === "SELL"
        ? "border-[var(--signal)] bg-[var(--signal-soft)] text-[var(--signal)]"
        : "border-[var(--line-strong)] bg-[var(--ledger)] text-[var(--paper-muted)]";

  return (
    <span
      className={`inline-flex items-center rounded-[4px] border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-[.06em] ${colorClasses}`}
    >
      {action}
    </span>
  );
}

export default function TradesPage() {
  const [trades, setTrades] = useState<TradeLog[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadFirstPage() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await fetch(`/api/trades?limit=${PAGE_SIZE}&offset=0`);
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.error ?? `Request failed (HTTP ${response.status}).`);
        }
        const data: TradesPage = await response.json();
        if (!cancelled) {
          setTrades(data.trades);
          setTotal(data.total);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load trade history.");
          setTrades(null);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    loadFirstPage();
    return () => {
      cancelled = true;
    };
  }, []);

  async function loadMore() {
    if (!trades) return;
    setIsLoadingMore(true);
    try {
      const response = await fetch(`/api/trades?limit=${PAGE_SIZE}&offset=${trades.length}`);
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.error ?? `Request failed (HTTP ${response.status}).`);
      }
      const data: TradesPage = await response.json();
      setTrades((prev) => [...(prev ?? []), ...data.trades]);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load more trades.");
    } finally {
      setIsLoadingMore(false);
    }
  }

  return (
    <div className="page-shell">
      <div className="shell-container pb-16">
        <header className="page-header">
          <div>
            <p className="eyebrow mb-2">Execution record</p>
            <h1 className="display-title">Trade log</h1>
          </div>
          <p className="page-deck">
            Every order the autonomous execution engine has submitted, with the WACC, beta, and
            Conviction Score behind each decision.
          </p>
        </header>

        {isLoading && (
          <div className="panel px-5 py-10 text-center text-sm text-[var(--paper-dim)]">
            Loading trade history…
          </div>
        )}

        {error && !isLoading && <div className="status-error">{error}</div>}

        {!isLoading && !error && trades && trades.length === 0 && (
          <div className="empty-state px-5 py-10 text-center">
            <strong className="block text-[var(--paper)]">No submitted orders are recorded.</strong>
            <span className="mt-1.5 block">
              Completed paper executions will appear here with the model inputs used at
              submission.
            </span>
          </div>
        )}

        {!isLoading && !error && trades && trades.length > 0 && (
          <div className="panel p-5 sm:p-6">
            <div className="panel-header">
              <h2 className="panel-title">
                Showing {trades.length} of {total} trade{total === 1 ? "" : "s"}
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table className="data-table w-full min-w-[820px] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-[var(--line)] text-left">
                    <th className="py-2 pr-4 font-medium">Timestamp</th>
                    <th className="py-2 pr-4 font-medium">Ticker</th>
                    <th className="py-2 pr-4 font-medium">Action</th>
                    <th className="py-2 pr-4 text-right font-medium">Quantity</th>
                    <th className="py-2 pr-4 text-right font-medium">Execution Price</th>
                    <th className="py-2 pr-4 text-right font-medium">WACC</th>
                    <th className="py-2 pr-4 text-right font-medium">Beta</th>
                    <th className="py-2 pl-4 text-right font-medium">Conviction Score</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((trade) => (
                    <tr key={trade.id} className="tabular-nums font-mono text-[var(--paper-muted)]">
                      <td className="py-3 pr-4 font-sans text-[var(--paper-dim)]">
                        {formatTimestamp(trade.timestamp)}
                      </td>
                      <td className="py-3 pr-4 font-sans font-semibold text-[var(--paper)]">
                        {trade.ticker}
                      </td>
                      <td className="py-3 pr-4">
                        <ActionBadge action={trade.action} />
                      </td>
                      <td className="py-3 pr-4 text-right">{formatNumber(trade.quantity, 4)}</td>
                      <td className="py-3 pr-4 text-right">{formatCurrency(trade.execution_price)}</td>
                      <td className="py-3 pr-4 text-right">{formatPercent(trade.wacc)}</td>
                      <td className="py-3 pr-4 text-right">{formatNumber(trade.beta)}</td>
                      <td className="py-3 pl-4 text-right font-semibold text-[var(--verdigris)]">
                        {formatNumber(trade.conviction_score, 3)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {trades.length < total && (
              <div className="mt-6 flex justify-center">
                <button
                  type="button"
                  onClick={loadMore}
                  disabled={isLoadingMore}
                  className="button-secondary disabled:opacity-50"
                >
                  {isLoadingMore ? "Loading…" : `Load ${Math.min(PAGE_SIZE, total - trades.length)} more`}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
