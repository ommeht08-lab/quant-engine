"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import PortfolioAllocation from "@/components/PortfolioAllocation";
import BacktestChart from "@/components/BacktestChart";
import RiskHistogram from "@/components/RiskHistogram";
import { valuationErrorFromResponse, type ValuationRequestError } from "@/lib/valuation-errors";

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
  // "historical" = derived from the company's own financials (the
  // default); "custom" = an explicit slider value was sent and used
  // instead. Lets the UI say what was ACTUALLY used rather than
  // guessing from the numeric value alone.
  revenue_growth_rate_source: "historical" | "custom";
  operating_margin_source: "historical" | "custom";
  sector: string;
  price_to_intrinsic_value: number | null;
  sector_median_p_iv: number | null;
  sector_median_unavailable_reason: string | null;
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
        <label htmlFor={id} className="text-xs font-medium text-[var(--paper-muted)]">
          {label}
        </label>
        <span className="font-mono text-xs text-[var(--brass)]">{formatPercent(value)}</span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-[var(--ledger)] accent-[var(--verdigris)]"
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
      className={`metric-panel p-5 ${
        emphasis
          ? "border-[rgba(85,184,170,.4)] bg-[var(--verdigris-soft)]"
          : ""
      }`}
    >
      <p className="data-label text-[var(--paper-dim)]">{label}</p>
      <p
        className={`mt-3 font-mono tracking-tight ${
          emphasis ? "text-4xl text-[var(--verdigris)]" : "text-2xl text-[var(--paper)]"
        }`}
      >
        {value}
      </p>
      {sublabel && <p className="mt-2 text-xs leading-5 text-[var(--paper-dim)]">{sublabel}</p>}
    </div>
  );
}

interface SectorBadgeProps {
  sector: string;
}

function SectorBadge({ sector }: SectorBadgeProps) {
  return (
    <span className="inline-flex items-center border border-[var(--line)] bg-[rgba(236,232,220,.04)] px-3 py-1 font-mono text-[10px] font-semibold uppercase tracking-[.1em] text-[var(--paper-muted)]">
      {sector}
    </span>
  );
}

interface SectorValuationComparisonProps {
  ticker: string;
  sector: string;
  priceToIntrinsicValue: number | null;
  sectorMedianPIV: number | null;
  sectorMedianUnavailableReason: string | null;
}

function SectorValuationComparison({
  ticker,
  sector,
  priceToIntrinsicValue,
  sectorMedianPIV,
  sectorMedianUnavailableReason,
}: SectorValuationComparisonProps) {
  if (priceToIntrinsicValue === null || sectorMedianPIV === null) {
    return (
      <div className="panel p-6 sm:p-8">
        <h2 className="panel-title mb-2">
          Sector-Relative Valuation
        </h2>
        <p className="text-sm leading-6 text-[var(--paper-dim)]">
          {priceToIntrinsicValue === null
            ? "Price-to-intrinsic-value could not be computed for this ticker."
            : sectorMedianUnavailableReason ?? `No sector median P/IV available yet for ${sector}.`}
        </p>
      </div>
    );
  }

  const passes = priceToIntrinsicValue <= sectorMedianPIV;
  const maxScale = Math.max(priceToIntrinsicValue, sectorMedianPIV) * 1.15;
  const stockBarPct = Math.min((priceToIntrinsicValue / maxScale) * 100, 100);
  const medianBarPct = Math.min((sectorMedianPIV / maxScale) * 100, 100);

  return (
    <div className="panel p-6 sm:p-8">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h2 className="panel-title">
          Sector-Relative Valuation
        </h2>
        <span
          className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wide ${
            passes
              ? "border-[rgba(85,184,170,.35)] bg-[var(--verdigris-soft)] text-[var(--verdigris)]"
              : "border-[rgba(228,113,104,.35)] bg-[rgba(228,113,104,.09)] text-[var(--signal)]"
          }`}
        >
          {passes ? "✓ Below Sector Median" : "✗ Above Sector Median"}
        </span>
      </div>

      <div className="space-y-4">
        <div>
          <div className="mb-1.5 flex items-baseline justify-between text-sm">
            <span className="text-[var(--paper-muted)]">{ticker} P/IV</span>
            <span
              className={`font-mono font-semibold ${passes ? "text-[var(--verdigris)]" : "text-[var(--signal)]"}`}
            >
              {priceToIntrinsicValue.toFixed(2)}x
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden bg-[var(--ledger)]">
            <div
              className={`h-full ${passes ? "bg-[var(--verdigris)]" : "bg-[var(--signal)]"}`}
              style={{ width: `${stockBarPct}%` }}
            />
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-baseline justify-between text-sm">
            <span className="text-[var(--paper-muted)]">{sector} Sector Median</span>
            <span className="font-mono font-semibold text-[var(--paper-muted)]">
              {sectorMedianPIV.toFixed(2)}x
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden bg-[var(--ledger)]">
            <div
              className="h-full bg-[var(--paper-dim)]"
              style={{ width: `${medianBarPct}%` }}
            />
          </div>
        </div>
      </div>

      <p className="mt-5 text-xs leading-5 text-[var(--paper-dim)]">
        Passes the sector-relative Margin of Safety filter when {ticker}&rsquo;s P/IV is at or
        below the {sector} sector median.
      </p>
    </div>
  );
}

export default function Home() {
  const [ticker, setTicker] = useState("AAPL");
  // Default mode: use each company's own historical revenue growth and
  // operating margin — the growth/margin query params are OMITTED
  // entirely in this mode (never sent as 0 or as the slider's current
  // position), which is also the exact default the sector-median cache
  // (src.api.sector_medians / the trading engine) is generated with, so
  // the default request stays comparable against the default cache.
  const [useCustomAssumptions, setUseCustomAssumptions] = useState(false);
  const [revenueGrowthRate, setRevenueGrowthRate] = useState(0.08);
  const [operatingMargin, setOperatingMargin] = useState(0.25);
  const [terminalGrowthRate, setTerminalGrowthRate] = useState(0.025);

  const [result, setResult] = useState<EvaluationResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<ValuationRequestError | null>(null);

  async function runValuation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const trimmedTicker = ticker.trim().toUpperCase();
    if (!trimmedTicker) {
      setError({ kind: "input", message: "Enter a ticker symbol." });
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const params = new URLSearchParams({
        terminal_growth_rate: String(terminalGrowthRate),
      });
      // Only send explicit growth/margin overrides in custom mode — in
      // historical mode these params are omitted entirely so the backend
      // derives them from the company's own financials, never silently
      // sending the slider's last position while claiming "historical".
      if (useCustomAssumptions) {
        params.set("revenue_growth_rate", String(revenueGrowthRate));
        params.set("operating_margin", String(operatingMargin));
      }

      const response = await fetch(
        `/api/evaluate/${encodeURIComponent(trimmedTicker)}?${params.toString()}`
      );

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw valuationErrorFromResponse(response.status, body);
      }

      const data: EvaluationResponse = await response.json();
      setResult(data);
    } catch (err) {
      setResult(null);
      setError(
        err && typeof err === "object" && "kind" in err && "message" in err
          ? (err as ValuationRequestError)
          : { kind: "unavailable", message: "The valuation service did not respond." }
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
    <div className="page-shell">
      <div className="shell-container pb-20">
        <header className="page-header">
          <div>
            <p className="eyebrow mb-5">Intrinsic value desk</p>
            <h1 className="display-title">Turn market price into a research question.</h1>
          </div>
          <p className="page-deck">
            Build a discounted cash flow case from company history or your own operating
            assumptions, then read it against portfolio exposure, risk, and evidence from the
            paper account.
          </p>
        </header>

        <div className="section-intro">
          <div>
            <p className="eyebrow mb-2">Model workbench</p>
            <h2>Set the case, then inspect the spread.</h2>
          </div>
          <p>
            Historical mode keeps company-derived growth and margins. Custom mode makes every
            override explicit.
          </p>
        </div>

        <form onSubmit={runValuation} className="panel mb-5 p-6 sm:p-8">
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,220px)_1fr_auto] lg:items-end">
            <div>
              <label htmlFor="ticker" className="data-label mb-2 block text-[var(--paper-dim)]">
                Ticker
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
                className="input-field px-4 py-2.5 font-mono text-lg tracking-[.08em]"
              />
            </div>

            <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
              <div
                className={useCustomAssumptions ? undefined : "pointer-events-none opacity-40"}
                aria-disabled={!useCustomAssumptions}
              >
                <SliderField
                  id="revenue-growth"
                  label="Revenue Growth Rate"
                  value={revenueGrowthRate}
                  min={-0.1}
                  max={0.4}
                  step={0.005}
                  onChange={setRevenueGrowthRate}
                />
              </div>
              <div
                className={useCustomAssumptions ? undefined : "pointer-events-none opacity-40"}
                aria-disabled={!useCustomAssumptions}
              >
                <SliderField
                  id="operating-margin"
                  label="Operating Margin"
                  value={operatingMargin}
                  min={0}
                  max={0.6}
                  step={0.005}
                  onChange={setOperatingMargin}
                />
              </div>
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

            <button type="submit" disabled={isLoading} className="button-primary gap-2">
              {isLoading ? (
                <>
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-[rgba(4,16,15,.35)] border-t-[#04100f]" />
                  Running…
                </>
              ) : (
                "Run Valuation"
              )}
            </button>

            <div className="lg:col-span-3">
              <div className="flex items-center gap-3 border-t border-[var(--line)] py-4">
                <button
                  type="button"
                  role="switch"
                  aria-checked={useCustomAssumptions}
                  id="assumption-mode-toggle"
                  onClick={() => setUseCustomAssumptions((prev) => !prev)}
                  className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
                    useCustomAssumptions ? "bg-[var(--verdigris)]" : "bg-[var(--ledger)]"
                  }`}
                >
                  <span
                    className={`absolute top-0.5 h-5 w-5 rounded-full bg-[var(--paper)] transition-transform ${
                      useCustomAssumptions ? "translate-x-[22px]" : "translate-x-0.5"
                    }`}
                  />
                </button>
                <label htmlFor="assumption-mode-toggle" className="cursor-pointer text-sm">
                  <span className="font-medium text-[var(--paper)]">
                    {useCustomAssumptions ? "Custom Assumptions" : "Company Historical Assumptions"}
                  </span>
                  <span className="ml-2 text-[var(--paper-dim)]">
                    {useCustomAssumptions
                      ? "Growth and margin sliders above are sent as explicit overrides."
                      : "Revenue growth & operating margin are derived from each company's own historical financials."}
                  </span>
                </label>
              </div>
            </div>
          </div>
        </form>

        {error && (
          <div
            className={`${error.kind === "unavailable" ? "status-warning" : "status-error"} mb-5`}
            role="alert"
          >
            <strong className="block text-[var(--paper)]">
              {error.kind === "unavailable" ? "Live valuation is not connected" : "Valuation could not run"}
            </strong>
            <span className="mt-1 block">{error.message}</span>
          </div>
        )}

        {!result && !isLoading && !error && (
          <div className="empty-state px-6 py-12 text-center">
            <strong className="block font-display text-xl font-normal text-[var(--paper-muted)]">
              No valuation on the desk yet.
            </strong>
            <span className="mt-2 block">
              Choose a ticker and run the model to compare market price with intrinsic value.
            </span>
          </div>
        )}

        {isLoading && !result && (
          <div className="panel px-6 py-12 text-center text-sm text-[var(--paper-dim)]">
            Fetching financial statements and running the model…
          </div>
        )}

        {result && (
          <div className="space-y-8">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="font-display text-3xl font-normal tracking-tight text-[var(--paper)]">
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

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <MetricCard
                label="Revenue Growth Rate Used"
                value={formatPercent(result.assumptions.revenue_growth_rate)}
                sublabel={
                  result.revenue_growth_rate_source === "historical"
                    ? "Company historical (derived)"
                    : "Custom (slider override)"
                }
              />
              <MetricCard
                label="Operating Margin Used"
                value={formatPercent(result.assumptions.operating_margin)}
                sublabel={
                  result.operating_margin_source === "historical"
                    ? "Company historical (derived)"
                    : "Custom (slider override)"
                }
              />
              <MetricCard
                label="Terminal Growth Rate"
                value={formatPercent(result.assumptions.terminal_growth_rate)}
              />
            </div>

            <SectorValuationComparison
              ticker={result.ticker}
              sector={result.sector}
              priceToIntrinsicValue={result.price_to_intrinsic_value}
              sectorMedianPIV={result.sector_median_p_iv}
              sectorMedianUnavailableReason={result.sector_median_unavailable_reason}
            />

            <div className="panel p-6 sm:p-8">
              <div className="panel-header">
                <h2 className="panel-title">Projected Free Cash Flows</h2>
                <span className="panel-kicker">Forecast detail</span>
              </div>
              <div className="overflow-x-auto">
                <table className="data-table w-full min-w-[640px] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-[var(--line)] text-left">
                      <th className="py-2 pr-4 font-medium">Year</th>
                      <th className="py-2 pr-4 font-medium">Revenue</th>
                      <th className="py-2 pr-4 font-medium">EBIT</th>
                      <th className="py-2 pr-4 font-medium">NOPAT</th>
                      <th className="py-2 pr-4 font-medium">D&amp;A</th>
                      <th className="py-2 pr-4 font-medium">CapEx</th>
                      <th className="py-2 pr-4 font-medium">Δ NWC</th>
                      <th className="py-2 pl-4 text-right font-medium">
                        Free Cash Flow
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.projected_free_cash_flows.map((row) => (
                      <tr
                        key={row.year}
                        className="font-mono text-[var(--paper-muted)]"
                      >
                        <td className="py-3 pr-4 font-sans text-[var(--paper-dim)]">
                          Year {row.year}
                        </td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.revenue)}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.ebit)}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.nopat)}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.da)}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.capex)}</td>
                        <td className="py-3 pr-4">{formatCompactCurrency(row.change_in_nwc)}</td>
                        <td className="py-3 pl-4 text-right font-semibold text-[var(--verdigris)]">
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

        <div className="section-intro">
          <div>
            <p className="eyebrow mb-2">Portfolio evidence</p>
            <h2>What the paper account is carrying.</h2>
          </div>
          <p>
            Exposure, historical performance, and risk remain separate from the valuation case
            so unavailable data is never mistaken for zero.
          </p>
        </div>

        <div className="grid gap-5 xl:grid-cols-2">
          <PortfolioAllocation />
          <RiskHistogram />
        </div>

        <div className="mt-5">
          <BacktestChart />
        </div>
      </div>
    </div>
  );
}
