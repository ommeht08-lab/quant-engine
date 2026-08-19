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
    <section className="panel h-full p-6 sm:p-8">
      <div className="panel-header">
        <h2 className="panel-title">
          Paper portfolio allocation
        </h2>
        {data && (
          <span className="panel-kicker">
            Equity: {formatCurrency(data.equity)}
          </span>
        )}
      </div>

      {isLoading && (
        <p className="py-8 text-center text-sm text-[var(--paper-dim)]">Loading paper positions…</p>
      )}

      {error && !isLoading && (
        <div className="status-error">
          {error}
        </div>
      )}

      {!isLoading && !error && rows.length === 0 && (
        <div className="empty-state py-8 text-center">
          <strong className="block text-[var(--paper-muted)]">No paper positions are open.</strong>
          <span className="mt-1 block">Allocation appears after a completed paper execution records holdings.</span>
        </div>
      )}

      {!isLoading && !error && rows.length > 0 && (
        <div className="space-y-4">
          {rows.map((row) => (
            <div key={row.symbol}>
              <div className="mb-1.5 flex items-baseline justify-between text-sm">
                <Link
                  href={`/ticker/${row.symbol}`}
                  className="font-mono font-semibold text-[var(--paper)] transition-colors hover:text-[var(--verdigris)]"
                >
                  {row.symbol}
                </Link>
                <span className="font-mono text-xs text-[var(--paper-muted)]">
                  {row.percentage.toFixed(2)}% · {formatCurrency(row.marketValue)}
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden bg-[var(--ledger)]">
                <div
                  className="h-1.5 bg-[var(--verdigris)]"
                  style={{ width: `${Math.min(row.percentage, 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
