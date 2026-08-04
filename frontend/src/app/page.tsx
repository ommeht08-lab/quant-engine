"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import PortfolioAllocation from "@/components/PortfolioAllocation";
import BacktestChart from "@/components/BacktestChart";

const API_BASE_URL = "http://localhost:8000";

interface FreeCashFlowYear {
  year: number;
  revenue: number;
  ebit: number;
  nopat: number;
  da: number;
  capex: number;
  change_in_nwc: number;
  fcf: number;
}

interface EvaluationResponse {
  ticker: string;
  current_price: number | null;
  wacc: number;
  enterprise_value: number;
  equity_value: number;
  intrinsic_value_per_share: number;
  projected_free_cash_flows: FreeCashFlowYear[];
  assumptions: {
    revenue_growth_rate: number;
    operating_margin: number;
    terminal_growth_rate: number;
    projection_years: number;
  };
  sector: string;
  price_to_intrinsic_value: number | null;
  sector_median_p_iv: number | null;
}

const compactCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});

const preciseCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatCompactCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return compactCurrencyFormatter.format(value);
}

function formatPreciseCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return preciseCurrencyFormatter.format(value);
}

function formatPercent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

interface SliderFieldProps {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}

function SliderField({ id, label, value, min, max, step, onChange }: SliderFieldProps) {
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-sm font-medium text-neutral-300">
          {label}
        </label>
        <span className="font-mono text-sm text-emerald-400">{formatPercent(value)}</span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-neutral-800 accent-emerald-500"
      />
    </div>
  );
}

interface MetricCardProps {
  label: string;
  value: string;
  emphasis?: boolean;
  sublabel?: string;
}

function MetricCard({ label, value, emphasis = false, sublabel }: MetricCardProps) {
  return (
    <div
      className={`rounded-2xl border p-6 ${
        emphasis
          ? "border-emerald-500/30 bg-emerald-500/[.06]"
          : "border-white/10 bg-white/[.03]"
      }`}
    >
      <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">{label}</p>
      <p
        className={`mt-2 font-mono tracking-tight ${
          emphasis ? "text-4xl text-emerald-400" : "text-2xl text-neutral-50"
        }`}
      >
        {value}
      </p>
      {sublabel && <p className="mt-1 text-xs text-neutral-500">{sublabel}</p>}
    </div>
  );
}

interface SectorBadgeProps {
  sector: string;
}

function SectorBadge({ sector }: SectorBadgeProps) {
  return (
    <span className="inline-flex items-center rounded-full border border-white/10 bg-white/[.05] px-3 py-1 text-xs font-semibold uppercase tracking-wide text-neutral-300">
      {sector}
    </span>
  );
}

interface SectorValuationComparisonProps {
  ticker: string;
  sector: string;
  priceToIntrinsicValue: number | null;
  sectorMedianPIV: number | null;
}

function SectorValuationComparison({
  ticker,
  sector,
  priceToIntrinsicValue,
  sectorMedianPIV,
}: SectorValuationComparisonProps) {
  if (priceToIntrinsicValue === null || sectorMedianPIV === null) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/[.03] p-6 sm:p-8">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-neutral-400">
          Sector-Relative Valuation
        </h2>
        <p className="text-sm text-neutral-500">
          {priceToIntrinsicValue === null
            ? "Price-to-intrinsic-value could not be computed for this ticker."
            : `No sector median P/IV available yet for ${sector}.`}
        </p>
      </div>
    );
  }

  const passes = priceToIntrinsicValue <= sectorMedianPIV;
  const maxScale = Math.max(priceToIntrinsicValue, sectorMedianPIV) * 1.15;
  const stockBarPct = Math.min((priceToIntrinsicValue / maxScale) * 100, 100);
  const medianBarPct = Math.min((sectorMedianPIV / maxScale) * 100, 100);

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[.03] p-6 sm:p-8">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neutral-400">
          Sector-Relative Valuation
        </h2>
        <span
          className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wide ${
            passes
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
              : "border-rose-500/30 bg-rose-500/10 text-rose-400"
          }`}
        >
          {passes ? "✓ Below Sector Median" : "✗ Above Sector Median"}
        </span>
      </div>

      <div className="space-y-4">
        <div>
          <div className="mb-1.5 flex items-baseline justify-between text-sm">
            <span className="text-neutral-300">{ticker} P/IV</span>
            <span
              className={`font-mono font-semibold ${passes ? "text-emerald-400" : "text-rose-400"}`}
            >
              {priceToIntrinsicValue.toFixed(2)}x
            </span>
          </div>
          <div className="h-2.5 w-full overflow-hidden rounded-full bg-neutral-800">
            <div
              className={`h-full rounded-full ${passes ? "bg-emerald-500" : "bg-rose-500"}`}
              style={{ width: `${stockBarPct}%` }}
            />
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-baseline justify-between text-sm">
            <span className="text-neutral-300">{sector} Sector Median</span>
            <span className="font-mono font-semibold text-neutral-300">
              {sectorMedianPIV.toFixed(2)}x
            </span>
          </div>
          <div className="h-2.5 w-full overflow-hidden rounded-full bg-neutral-800">
            <div
              className="h-full rounded-full bg-neutral-500"
              style={{ width: `${medianBarPct}%` }}
            />
          </div>
        </div>
      </div>

      <p className="mt-5 text-xs text-neutral-500">
        Passes the sector-relative Margin of Safety filter when {ticker}&rsquo;s P/IV is at or
        below the {sector} sector median.
      </p>
    </div>
  );
}

export default function Home() {
  const [ticker, setTicker] = useState("AAPL");
  const [revenueGrowthRate, setRevenueGrowthRate] = useState(0.08);
  const [operatingMargin, setOperatingMargin] = useState(0.25);
  const [terminalGrowthRate, setTerminalGrowthRate] = useState(0.025);

  const [result, setResult] = useState<EvaluationResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runValuation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const trimmedTicker = ticker.trim().toUpperCase();
    if (!trimmedTicker) {
      setError("Enter a ticker symbol.");
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const params = new URLSearchParams({
        revenue_growth_rate: String(revenueGrowthRate),
        operating_margin: String(operatingMargin),
        terminal_growth_rate: String(terminalGrowthRate),
      });

      const response = await fetch(
        `${API_BASE_URL}/api/evaluate/${encodeURIComponent(trimmedTicker)}?${params.toString()}`
      );

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(
          body?.detail ?? `Valuation request failed (HTTP ${response.status}).`
        );
      }

      const data: EvaluationResponse = await response.json();
      setResult(data);
    } catch (err) {
      setResult(null);
      setError(
        err instanceof Error
          ? err.message
          : "Could not reach the valuation API. Is it running at http://localhost:8000?"
      );
    } finally {
      setIsLoading(false);
    }
  }

  const priceDelta =
    result && result.current_price !== null
      ? result.intrinsic_value_per_share - result.current_price
      : null;
  const priceDeltaPct =
    priceDelta !== null && result?.current_price ? priceDelta / result.current_price : null;

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-50">
      <div className="mx-auto max-w-6xl px-6 py-12">
        <header className="mb-10">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-500">
            Valuation Engine
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-neutral-50 sm:text-4xl">
            Discounted Cash Flow Dashboard
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-neutral-400">
            Run a live DCF valuation against real-time market and financial statement
            data, and see how intrinsic value responds to your assumptions.
          </p>
        </header>

        <div className="mb-8">
          <PortfolioAllocation />
        </div>

        <div className="mb-8">
          <BacktestChart />
        </div>

        <form
          onSubmit={runValuation}
          className="mb-8 rounded-2xl border border-white/10 bg-white/[.03] p-6 sm:p-8"
        >
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,220px)_1fr_auto] lg:items-end">
            <div>
              <label htmlFor="ticker" className="mb-2 block text-sm font-medium text-neutral-300">
                Ticker Symbol
              </label>
              <input
                id="ticker"
                type="text"
                value={ticker}
                onChange={(event) => setTicker(event.target.value.toUpperCase())}
                placeholder="AAPL"
                maxLength={10}
                autoComplete="off"
                spellCheck={false}
                className="w-full rounded-lg border border-white/10 bg-neutral-900 px-4 py-2.5 font-mono text-lg tracking-wide text-neutral-50 placeholder:text-neutral-600 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              />
            </div>

            <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
              <SliderField
                id="revenue-growth"
                label="Revenue Growth Rate"
                value={revenueGrowthRate}
                min={-0.1}
                max={0.4}
                step={0.005}
                onChange={setRevenueGrowthRate}
              />
              <SliderField
                id="operating-margin"
                label="Operating Margin"
                value={operatingMargin}
                min={0}
                max={0.6}
                step={0.005}
                onChange={setOperatingMargin}
              />
              <SliderField
                id="terminal-growth"
                label="Terminal Growth Rate"
                value={terminalGrowthRate}
                min={0}
                max={0.05}
                step={0.001}
                onChange={setTerminalGrowthRate}
              />
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="flex h-[46px] items-center justify-center gap-2 rounded-lg bg-emerald-500 px-6 font-semibold text-neutral-950 transition-colors hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-400"
            >
              {isLoading ? (
                <>
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-neutral-950/40 border-t-neutral-950" />
                  Running…
                </>
              ) : (
                "Run Valuation"
              )}
            </button>
          </div>
        </form>

        {error && (
          <div className="mb-8 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        {!result && !isLoading && !error && (
          <div className="rounded-2xl border border-dashed border-white/10 px-6 py-16 text-center text-sm text-neutral-500">
            Enter a ticker and click &ldquo;Run Valuation&rdquo; to generate a DCF estimate.
          </div>
        )}

        {isLoading && !result && (
          <div className="rounded-2xl border border-white/10 bg-white/[.03] px-6 py-16 text-center text-sm text-neutral-500">
            Fetching financial statements and running the model…
          </div>
        )}

        {result && (
          <div className="space-y-8">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold tracking-tight text-neutral-50">
                {result.ticker}
              </h2>
              <SectorBadge sector={result.sector} />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <MetricCard
                label={`Intrinsic Value / Share — ${result.ticker}`}
                value={formatPreciseCurrency(result.intrinsic_value_per_share)}
                emphasis
                sublabel={
                  priceDelta !== null && priceDeltaPct !== null
                    ? `${priceDelta >= 0 ? "+" : ""}${formatPreciseCurrency(priceDelta)} (${
                        priceDeltaPct >= 0 ? "+" : ""
                      }${(priceDeltaPct * 100).toFixed(1)}%) vs. market`
                    : undefined
                }
              />
              <MetricCard
                label="Current Market Price"
                value={formatPreciseCurrency(result.current_price)}
              />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <MetricCard label="WACC" value={formatPercent(result.wacc, 2)} />
              <MetricCard
                label="Enterprise Value"
                value={formatCompactCurrency(result.enterprise_value)}
              />
              <MetricCard
                label="Equity Value"
                value={formatCompactCurrency(result.equity_value)}
              />
            </div>

            <SectorValuationComparison
              ticker={result.ticker}
              sector={result.sector}
              priceToIntrinsicValue={result.price_to_intrinsic_value}
              sectorMedianPIV={result.sector_median_p_iv}
            />

            <div className="rounded-2xl border border-white/10 bg-white/[.03] p-6 sm:p-8">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-neutral-400">
                Projected Free Cash Flows
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-white/10 text-left text-xs uppercase tracking-wider text-neutral-500">
                      <th className="py-2 pr-4 font-medium">Year</th>
                      <th className="py-2 pr-4 font-medium">Revenue</th>
                      <th className="py-2 pr-4 font-medium">EBIT</th>
                      <th className="py-2 pr-4 font-medium">NOPAT</th>
                      <th className="py-2 pr-4 font-medium">D&amp;A</th>
                      <th className="py-2 pr-4 font-medium">CapEx</th>
                      <th className="py-2 pr-4 font-medium">Δ NWC</th>
                      <th className="py-2 pl-4 text-right font-medium text-neutral-300">
                        Free Cash Flow
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.projected_free_cash_flows.map((row) => (
                      <tr
                        key={row.year}
                        className="border-b border-white/5 font-mono text-neutral-200 last:border-0"
                      >
                        <td className="py-3 pr-4 font-sans text-neutral-400">Year {row.year}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.revenue)}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.ebit)}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.nopat)}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.da)}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.capex)}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.change_in_nwc)}</td>
                        <td className="py-3 pl-4 text-right font-semibold text-emerald-400">
                          {formatCompactCurrency(row.fcf)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
