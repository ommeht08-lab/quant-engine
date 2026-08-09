"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

interface Position {
  symbol: string;
  qty: number;
  marketValue: number;
  currentPrice: number;
  avgEntryPrice: number;
  unrealizedPl: number;
  unrealizedPlPercent: number;
}

interface PositionsPayload {
  equity: number;
  positions: Position[];
}

interface AllocationRow extends Position {
  percentage: number;
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

export default function PortfolioAllocation() {
  const [data, setData] = useState<PositionsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadPositions() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await fetch("/api/positions");
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.error ?? `Request failed (HTTP ${response.status}).`);
        }
        const payload: PositionsPayload = await response.json();
        if (!cancelled) setData(payload);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Could not load live portfolio positions."
          );
          setData(null);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    loadPositions();
    return () => {
      cancelled = true;
    };
  }, []);

  const rows: AllocationRow[] =
    data && data.equity > 0
      ? data.positions
          .map((position) => ({
            ...position,
            percentage: (position.marketValue / data.equity) * 100,
          }))
          .sort((a, b) => b.percentage - a.percentage)
      : [];

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[.03] p-6 sm:p-8">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neutral-400">
          Live Portfolio Allocation
        </h2>
        {data && (
          <span className="font-mono text-xs text-neutral-500">
            Equity: {formatCurrency(data.equity)}
          </span>
        )}
      </div>

      {isLoading && (
        <p className="py-8 text-center text-sm text-neutral-500">Loading live positions…</p>
      )}

      {error && !isLoading && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {!isLoading && !error && rows.length === 0 && (
        <p className="py-8 text-center text-sm text-neutral-500">
          No open positions. Once the autonomous execution engine holds a position, its
          risk-adjusted (inverse-beta) weight will show up here.
        </p>
      )}

      {!isLoading && !error && rows.length > 0 && (
        <div className="space-y-4">
          {rows.map((row) => (
            <div key={row.symbol}>
              <div className="mb-1.5 flex items-baseline justify-between text-sm">
                <Link
                  href={`/ticker/${row.symbol}`}
                  className="font-mono font-semibold text-neutral-50 transition-colors hover:text-emerald-400"
                >
                  {row.symbol}
                </Link>
                <span className="font-mono text-neutral-300">
                  {row.percentage.toFixed(2)}% · {formatCurrency(row.marketValue)}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-800">
                <div
                  className="bg-emerald-500 h-2 rounded-full"
                  style={{ width: `${Math.min(row.percentage, 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
