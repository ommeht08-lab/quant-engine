"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

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
    <div className="min-h-screen bg-neutral-950 text-neutral-50">
      <div className="mx-auto max-w-6xl px-6 py-12">
        <header className="mb-10">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-500">
            Valuation Engine
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-neutral-50 sm:text-4xl">
            Trade Telemetry
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-neutral-400">
            Every order the autonomous execution engine has actually submitted, with the
            WACC, beta, and Conviction Score behind each decision.
          </p>
          <Link
            href="/"
            className="mt-4 inline-flex items-center gap-1 text-sm text-neutral-400 transition-colors hover:text-emerald-400"
          >
            ← Back to Valuation Dashboard
          </Link>
        </header>

        {isLoading && (
          <div className="rounded-2xl border border-white/10 bg-white/[.03] px-6 py-16 text-center text-sm text-neutral-500">
            Loading trade history…
          </div>
        )}

        {error && !isLoading && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        {!isLoading && !error && trades && trades.length === 0 && (
          <div className="rounded-2xl border border-dashed border-white/10 px-6 py-16 text-center text-sm text-neutral-500">
            No trades logged yet. Once the autonomous execution engine submits an order,
            it will show up here.
          </div>
        )}

        {!isLoading && !error && trades && trades.length > 0 && (
          <div className="rounded-2xl border border-white/10 bg-white/[.03] p-6 sm:p-8">
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-neutral-400">
              Showing {trades.length} of {total} Trade{total === 1 ? "" : "s"}
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-left text-xs uppercase tracking-wider text-neutral-500">
                    <th className="py-2 pr-4 font-medium">Timestamp</th>
                    <th className="py-2 pr-4 font-medium">Ticker</th>
                    <th className="py-2 pr-4 font-medium">Action</th>
                    <th className="py-2 pr-4 text-right font-medium">Quantity</th>
                    <th className="py-2 pr-4 text-right font-medium">Execution Price</th>
                    <th className="py-2 pr-4 text-right font-medium">WACC</th>
                    <th className="py-2 pr-4 text-right font-medium">Beta</th>
                    <th className="py-2 pl-4 text-right font-medium text-neutral-300">
                      Conviction Score
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((trade) => (
                    <tr
                      key={trade.id}
                      className="border-b border-white/5 font-mono text-neutral-200 last:border-0"
                    >
                      <td className="py-3 pr-4 font-sans text-neutral-400">
                        {formatTimestamp(trade.timestamp)}
                      </td>
                      <td className="py-3 pr-4 font-sans font-semibold text-neutral-50">
                        {trade.ticker}
                      </td>
                      <td className="py-3 pr-4">
                        <ActionBadge action={trade.action} />
                      </td>
                      <td className="py-3 pr-4 text-right">{formatNumber(trade.quantity, 4)}</td>
                      <td className="py-3 pr-4 text-right">
                        {formatCurrency(trade.execution_price)}
                      </td>
                      <td className="py-3 pr-4 text-right">{formatPercent(trade.wacc)}</td>
                      <td className="py-3 pr-4 text-right">{formatNumber(trade.beta)}</td>
                      <td className="py-3 pl-4 text-right font-semibold text-emerald-400">
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
                  className="rounded-lg border border-white/10 bg-white/[.03] px-4 py-2 text-sm font-medium text-neutral-300 transition-colors hover:border-emerald-500/30 hover:text-emerald-400 disabled:opacity-50"
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
