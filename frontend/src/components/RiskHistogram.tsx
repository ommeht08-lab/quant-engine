"use client";

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";

interface RiskMetrics {
  var95: number;
  cvar95: number;
  asOf: string;
}

interface DistributionPoint {
  x: number;
  density: number;
}

const percentFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

function formatPercent(value: number): string {
  return percentFormatter.format(value);
}

// The 5th-percentile z-score of a standard normal distribution — used to
// back out an implied standard deviation from the logged 95% VaR. The raw
// 10,000-path Monte Carlo simulation (`src.risk.monte_carlo`) isn't
// persisted, only its var_95/cvar_95 summary statistics, so this renders
// an illustrative normal-approximation return distribution calibrated so
// its own 5th percentile lands exactly on the logged VaR — not the
// literal simulated paths.
const Z_SCORE_5TH_PERCENTILE = 1.6448536269514722;
const BIN_COUNT = 61;
const DOMAIN_SIGMA_MULTIPLE = 4;

function normalPdf(x: number, sigma: number): number {
  if (sigma <= 0) return 0;
  return (1 / (sigma * Math.sqrt(2 * Math.PI))) * Math.exp(-(x * x) / (2 * sigma * sigma));
}

function buildDistribution(var95: number): DistributionPoint[] {
  const sigma = Math.abs(var95) / Z_SCORE_5TH_PERCENTILE;
  if (!Number.isFinite(sigma) || sigma <= 0) return [];

  const domainEdge = sigma * DOMAIN_SIGMA_MULTIPLE;
  const step = (2 * domainEdge) / (BIN_COUNT - 1);

  return Array.from({ length: BIN_COUNT }, (_, i) => {
    const x = -domainEdge + i * step;
    return { x, density: normalPdf(x, sigma) };
  });
}

function RiskMetricCard({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[.02] p-4">
      <p className="text-xs uppercase tracking-wider text-neutral-500">{label}</p>
      <p className="mt-1 font-mono text-xl font-semibold text-rose-400">{value}</p>
    </div>
  );
}

function CustomTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0]?.payload as DistributionPoint | undefined;
  if (!point) return null;

  return (
    <div className="rounded-lg border border-white/10 bg-neutral-900 px-4 py-3 text-sm shadow-xl">
      <p className="font-mono font-semibold text-neutral-200">{formatPercent(point.x)}</p>
    </div>
  );
}

export default function RiskHistogram() {
  const [risk, setRisk] = useState<RiskMetrics | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadRisk() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await fetch("/api/risk");
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.error ?? `Request failed (HTTP ${response.status}).`);
        }
        const payload: RiskMetrics | null = await response.json();
        if (!cancelled) setRisk(payload);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load risk metrics.");
          setRisk(null);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    loadRisk();
    return () => {
      cancelled = true;
    };
  }, []);

  const distribution = risk ? buildDistribution(risk.var95) : [];

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[.03] p-6 sm:p-8">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neutral-400">
          Portfolio Risk — Monte Carlo VaR / CVaR
        </h2>
        {risk && (
          <span className="font-mono text-xs text-neutral-500">
            as of {new Date(risk.asOf).toLocaleString()}
          </span>
        )}
      </div>

      {isLoading && (
        <p className="py-16 text-center text-sm text-neutral-500">Loading risk metrics…</p>
      )}

      {error && !isLoading && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {!isLoading && !error && !risk && (
        <p className="py-16 text-center text-sm text-neutral-500">
          No risk snapshot logged yet. Run the live trading engine
          (<code className="font-mono text-neutral-400">python -m src.trading.alpaca_execution</code>)
          to populate this chart.
        </p>
      )}

      {!isLoading && !error && risk && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-4">
            <RiskMetricCard label="1-Month 95% VaR" value={formatPercent(risk.var95)} />
            <RiskMetricCard
              label="95% CVaR (Expected Shortfall)"
              value={formatPercent(risk.cvar95)}
            />
          </div>

          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={distribution}
                barCategoryGap={0}
                margin={{ top: 24, right: 16, bottom: 0, left: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" vertical={false} />
                <XAxis
                  dataKey="x"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={(value: number) => formatPercent(value)}
                  stroke="rgba(255,255,255,0.3)"
                  tick={{ fill: "#a3a3a3", fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
                />
                <YAxis hide />
                <Tooltip
                  content={(props) => <CustomTooltip {...props} />}
                  cursor={{ fill: "rgba(255,255,255,0.05)" }}
                />
                <Bar dataKey="density" fill="#3f3f46" isAnimationActive={false} />
                <ReferenceLine
                  x={risk.var95}
                  stroke="#f43f5e"
                  strokeWidth={2}
                  strokeDasharray="4 4"
                  label={{ value: "VaR 95%", position: "top", fill: "#f43f5e", fontSize: 11 }}
                />
                <ReferenceLine
                  x={risk.cvar95}
                  stroke="#fb923c"
                  strokeWidth={2}
                  strokeDasharray="4 4"
                  label={{ value: "CVaR", position: "top", fill: "#fb923c", fontSize: 11 }}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <p className="mt-5 text-xs text-neutral-500">
            Illustrative return distribution (normal approximation calibrated to the logged 95%
            VaR) — the raw 10,000-path Monte Carlo simulation isn&rsquo;t persisted, only its
            VaR/CVaR summary statistics. Computed over a rolling 1-month (21 trading day) horizon
            from each holding&rsquo;s trailing 252-day return covariance.
          </p>
        </>
      )}
    </div>
  );
}
