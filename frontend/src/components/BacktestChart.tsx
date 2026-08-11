"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";
import { calculateBacktestMetrics, type BacktestPoint } from "@/lib/metrics";

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const compactCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

function formatCurrency(value: number): string {
  return currencyFormatter.format(value);
}

function returnSince(start: number, current: number): string {
  const pct = ((current - start) / start) * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function formatSignedPercent(value: number, digits = 2): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function formatDrawdown(value: number): string {
  return value === 0 ? "0.00%" : `-${value.toFixed(2)}%`;
}

function MetricStatCard({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[.02] p-4">
      <p className="text-xs uppercase tracking-wider text-neutral-500">{label}</p>
      <p className={`mt-1 font-mono text-xl font-semibold ${valueClassName ?? "text-neutral-50"}`}>
        {value}
      </p>
    </div>
  );
}

function BacktestMetricsGrid({ data }: { data: BacktestPoint[] }) {
  const metrics = calculateBacktestMetrics(data);

  return (
    <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
      <MetricStatCard
        label="Alpha (Annualized)"
        value={formatSignedPercent(metrics.alpha)}
        valueClassName={metrics.alpha >= 0 ? "text-emerald-400" : "text-rose-400"}
      />
      <MetricStatCard
        label="Sharpe Ratio (Risk-Adjusted Return)"
        value={metrics.sharpeRatio.toFixed(2)}
      />
      <MetricStatCard
        label="Max Drawdown"
        value={formatDrawdown(metrics.maxDrawdown)}
        valueClassName="text-rose-400"
      />
      <MetricStatCard
        label="Annualized Volatility"
        value={`${metrics.annualizedVolatility.toFixed(2)}%`}
      />
    </div>
  );
}

function CustomTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload || payload.length === 0) return null;

  const strategyValue = payload.find((entry) => entry.dataKey === "strategy")?.value;
  const benchmarkValue = payload.find((entry) => entry.dataKey === "benchmark")?.value;
  const strategy = typeof strategyValue === "number" ? strategyValue : undefined;
  const benchmark = typeof benchmarkValue === "number" ? benchmarkValue : undefined;

  return (
    <div className="rounded-lg border border-white/10 bg-neutral-900 px-4 py-3 text-sm shadow-xl">
      <p className="mb-2 text-xs font-medium uppercase tracking-wider text-neutral-500">
        {label}
      </p>
      {strategy !== undefined && (
        <div className="flex items-center justify-between gap-6">
          <span className="flex items-center gap-1.5 text-neutral-300">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            Strategy
          </span>
          <span className="font-mono font-semibold text-emerald-400">
            {formatCurrency(strategy)}
          </span>
        </div>
      )}
      {benchmark !== undefined && (
        <div className="mt-1 flex items-center justify-between gap-6">
          <span className="flex items-center gap-1.5 text-neutral-300">
            <span className="h-2 w-2 rounded-full bg-neutral-500" />
            SPY
          </span>
          <span className="font-mono font-semibold text-neutral-400">
            {formatCurrency(benchmark)}
          </span>
        </div>
      )}
    </div>
  );
}

export default function BacktestChart() {
  const [data, setData] = useState<BacktestPoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadBacktest() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await fetch("/api/backtest");
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.error ?? `Request failed (HTTP ${response.status}).`);
        }
        const payload: BacktestPoint[] = await response.json();
        if (!cancelled) setData(payload);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load backtest results.");
          setData(null);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    loadBacktest();
    return () => {
      cancelled = true;
    };
  }, []);

  const first = data?.[0];
  const last = data?.[data.length - 1];

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[.03] p-6 sm:p-8">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neutral-400">
          Backtested Performance vs. S&amp;P 500
        </h2>
        {first && last && (
          <div className="flex items-center gap-4 font-mono text-xs">
            <span className="flex items-center gap-1.5 text-emerald-400">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              Strategy {returnSince(first.strategy, last.strategy)}
            </span>
            <span className="flex items-center gap-1.5 text-neutral-400">
              <span className="h-2 w-2 rounded-full bg-neutral-500" />
              SPY {returnSince(first.benchmark, last.benchmark)}
            </span>
          </div>
        )}
      </div>

      {isLoading && (
        <p className="py-16 text-center text-sm text-neutral-500">Loading backtest results…</p>
      )}

      {error && !isLoading && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {!isLoading && !error && data && data.length === 0 && (
        <p className="py-16 text-center text-sm text-neutral-500">
          No backtest results yet. Run the Python backtester
          (<code className="font-mono text-neutral-400">python -m src.backtesting.historical_tester</code>)
          to populate this chart.
        </p>
      )}

      {!isLoading && !error && data && data.length > 0 && <BacktestMetricsGrid data={data} />}

      {!isLoading && !error && data && data.length > 0 && (
        <div className="h-80 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" vertical={false} />
              <XAxis
                dataKey="date"
                stroke="rgba(255,255,255,0.3)"
                tick={{ fill: "#a3a3a3", fontSize: 12 }}
                tickLine={false}
                axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
              />
              <YAxis
                stroke="rgba(255,255,255,0.3)"
                tick={{ fill: "#a3a3a3", fontSize: 12 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(value: number) => compactCurrencyFormatter.format(value)}
                width={64}
                domain={["auto", "auto"]}
              />
              <Tooltip
                content={(props) => <CustomTooltip {...props} />}
                cursor={{ stroke: "rgba(255,255,255,0.15)" }}
              />
              <Line
                type="monotone"
                dataKey="benchmark"
                name="SPY"
                stroke="#737373"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
              />
              <Line
                type="monotone"
                dataKey="strategy"
                name="Strategy"
                stroke="#10b981"
                strokeWidth={2.5}
                dot={false}
                activeDot={{ r: 5 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {!isLoading && !error && data && data.length > 0 && (
        <p className="mt-5 text-xs text-neutral-500">
          Equal-weighted, buy-and-hold equity curve for the backtest&rsquo;s Top-N Conviction
          Score picks vs. an equal-notional SPY position, both from the backtest&rsquo;s entry
          date.
        </p>
      )}
    </div>
  );
}
