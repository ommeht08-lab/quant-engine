"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { valuationErrorFromResponse, type ValuationRequestError } from "@/lib/valuation-errors";
import { classifySensitivityCell, sensitivityCellAccessibleLabel } from "@/lib/sensitivity-cell";
import AssumptionsPanel from "@/components/valuation/AssumptionsPanel";
import ValuationHeadline from "@/components/valuation/ValuationHeadline";
import ValuationSpectrum, {
  sensitivityCellToneAndGlyph,
  type DCFScenarioSet,
} from "@/components/valuation/ValuationSpectrum";
import ScrollHintTable from "@/components/valuation/ScrollHintTable";
import { formatCompactCurrency, formatPercent, formatPreciseCurrency } from "@/components/valuation/format";
import {
  sectorMedianProvenanceCaption,
  sectorMedianUnavailableCopy,
  type SectorMedianUnavailableCode,
} from "@/lib/sector-median-copy";

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

interface SensitivityAxis {
  label: string;
  values: number[];
  baseline_index: number;
}

interface DCFSensitivityMatrix {
  wacc_axis: SensitivityAxis;
  terminal_growth_axis: SensitivityAxis;
  // cells[i][j]: intrinsic value per share for wacc_axis.values[i] x
  // terminal_growth_axis.values[j], or null for a combination the model
  // refuses to value (e.g. WACC not strictly greater than terminal
  // growth) — never NaN/Infinity.
  cells: (number | null)[][];
  baseline_row: number;
  baseline_col: number;
  baseline_wacc: number;
  baseline_terminal_growth_rate: number;
  baseline_intrinsic_value_per_share: number | null;
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
  sector_median_unavailable_code: SectorMedianUnavailableCode | null;
  sector_median_unavailable_reason: string | null;
  sector_median_snapshot: {
    generated_at: string;
    universe_size: number;
    tickers_used: number;
    sector_sample_count: number;
  } | null;
  sensitivity: DCFSensitivityMatrix;
  scenarios: DCFScenarioSet;
}

interface SectorValuationComparisonProps {
  ticker: string;
  sector: string;
  priceToIntrinsicValue: number | null;
  sectorMedianPIV: number | null;
  sectorMedianUnavailableCode: SectorMedianUnavailableCode | null;
  sectorMedianSnapshot: {
    generated_at: string;
    universe_size: number;
    tickers_used: number;
    sector_sample_count: number;
  } | null;
}

function SectorValuationComparison({
  ticker,
  sector,
  priceToIntrinsicValue,
  sectorMedianPIV,
  sectorMedianUnavailableCode,
  sectorMedianSnapshot,
}: SectorValuationComparisonProps) {
  if (priceToIntrinsicValue === null || sectorMedianPIV === null) {
    return (
      <div className="panel p-5 sm:p-6">
        <p className="text-sm leading-6 text-[var(--paper-dim)]">
          {priceToIntrinsicValue === null
            ? "Price-to-intrinsic-value could not be computed for this ticker."
            : sectorMedianUnavailableCopy(sectorMedianUnavailableCode)}
        </p>
      </div>
    );
  }

  const passes = priceToIntrinsicValue <= sectorMedianPIV;
  const maxScale = Math.max(priceToIntrinsicValue, sectorMedianPIV) * 1.15;
  const stockBarPct = Math.min((priceToIntrinsicValue / maxScale) * 100, 100);
  const medianBarPct = Math.min((sectorMedianPIV / maxScale) * 100, 100);

  return (
    <div className="panel p-5 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-[var(--paper-dim)]">
          Passes the sector-relative Margin of Safety filter when {ticker}&rsquo;s P/IV is at or
          below the {sector} sector median.
        </span>
        <span
          className={`text-xs font-semibold uppercase tracking-wide ${
            passes ? "text-[var(--verdigris)]" : "text-[var(--signal)]"
          }`}
        >
          {passes ? "Below median" : "Above median"}
        </span>
      </div>

      <div className="space-y-4">
        <div>
          <div className="mb-1.5 flex items-baseline justify-between text-sm">
            <span className="text-[var(--paper-muted)]">{ticker} P/IV</span>
            <span
              className={`tabular-nums font-mono font-semibold ${passes ? "text-[var(--verdigris)]" : "text-[var(--signal)]"}`}
            >
              {priceToIntrinsicValue.toFixed(2)}x
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--ledger)]">
            <div
              className={`h-full ${passes ? "bg-[var(--verdigris)]" : "bg-[var(--signal)]"}`}
              style={{ width: `${stockBarPct}%` }}
            />
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-baseline justify-between text-sm">
            <span className="text-[var(--paper-muted)]">{sector} sector median</span>
            <span className="tabular-nums font-mono font-semibold text-[var(--paper-muted)]">
              {sectorMedianPIV.toFixed(2)}x
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--ledger)]">
            <div className="h-full bg-[var(--paper-dim)]" style={{ width: `${medianBarPct}%` }} />
          </div>
        </div>
      </div>

      {sectorMedianSnapshot && (
        <p className="mt-4 text-xs text-[var(--paper-dim)]">
          {sectorMedianProvenanceCaption(sectorMedianSnapshot)}
        </p>
      )}
    </div>
  );
}

interface SensitivityMatrixSectionProps {
  matrix: DCFSensitivityMatrix;
  marketPrice: number | null;
}

// Renders the already-computed grid — every cell is exactly what the API
// returned; no DCF math happens in this component. Kept separate from the
// Valuation Spectrum (a 5x5 grid doesn't belong on a single-scale rail),
// but visually related to it via the shared cobalt baseline accent.
function SensitivityMatrixSection({ matrix, marketPrice }: SensitivityMatrixSectionProps) {
  const hasMarketPrice = marketPrice !== null;

  return (
    <div>
      <h2 className="section-title">DCF sensitivity</h2>

      <div className="panel p-5 sm:p-6">
        <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="inline-block h-3 w-3 rounded-[2px] bg-[var(--cobalt-soft)] outline outline-2 -outline-offset-2 outline-[var(--cobalt)]"
            />
            <span className="text-[var(--paper-muted)]">Baseline case</span>
          </span>
          {hasMarketPrice ? (
            <>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className="text-[var(--verdigris)]">
                  ▲
                </span>
                <span className="text-[var(--paper-muted)]">Above market price</span>
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className="text-[var(--signal)]">
                  ▼
                </span>
                <span className="text-[var(--paper-muted)]">Below market price</span>
              </span>
            </>
          ) : (
            <span className="text-[var(--paper-muted)]">Market comparison unavailable.</span>
          )}
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden="true" className="text-[var(--paper-dim)]">
              —
            </span>
            <span className="text-[var(--paper-muted)]">Not a valid WACC / terminal growth pair</span>
          </span>
        </div>

        <ScrollHintTable>
          <table className="data-table w-full min-w-[560px] border-collapse text-sm">
            <caption className="sr-only">
              Intrinsic value per share at combinations of WACC (rows) and terminal growth rate
              (columns). The baseline case is marked.{" "}
              {hasMarketPrice
                ? "Cells are colored teal when above the current market price and red when below it."
                : "Market price is unavailable, so cells are not compared against it."}
            </caption>
            <thead>
              <tr className="border-b border-[var(--line)] text-left">
                <th scope="col" className="sticky left-0 z-10 bg-[var(--ink-raised)] py-2 pr-4 font-medium">
                  WACC \ Terminal g.
                </th>
                {matrix.terminal_growth_axis.values.map((terminalGrowth, colIndex) => (
                  <th
                    key={colIndex}
                    scope="col"
                    className={`tabular-nums font-mono px-3 py-2 text-right font-medium ${
                      colIndex === matrix.baseline_col ? "text-[var(--cobalt)]" : ""
                    }`}
                  >
                    {formatPercent(terminalGrowth, 1)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.wacc_axis.values.map((wacc, rowIndex) => (
                <tr key={rowIndex} className="border-b border-[var(--line)] last:border-0">
                  <th
                    scope="row"
                    className={`tabular-nums font-mono sticky left-0 z-10 bg-[var(--ink-raised)] py-2 pr-4 text-left font-medium ${
                      rowIndex === matrix.baseline_row ? "text-[var(--cobalt)]" : "text-[var(--paper-muted)]"
                    }`}
                  >
                    {formatPercent(wacc, 1)}
                  </th>
                  {matrix.terminal_growth_axis.values.map((terminalGrowth, colIndex) => {
                    const value = matrix.cells[rowIndex]?.[colIndex] ?? null;
                    const isBaseline = rowIndex === matrix.baseline_row && colIndex === matrix.baseline_col;
                    const status = classifySensitivityCell({ value, marketPrice });
                    const { toneClass, glyph } = sensitivityCellToneAndGlyph(status);
                    const accessibleLabel = sensitivityCellAccessibleLabel({
                      value,
                      marketPrice,
                      isBaseline,
                      formatCurrency: formatPreciseCurrency,
                    });

                    return (
                      <td
                        key={colIndex}
                        aria-label={accessibleLabel}
                        className={`tabular-nums font-mono px-3 py-2 text-right ${toneClass} ${
                          isBaseline
                            ? "bg-[var(--cobalt-soft)] outline outline-2 -outline-offset-2 outline-[var(--cobalt)]"
                            : ""
                        }`}
                      >
                        <span aria-hidden="true">
                          {value === null ? (
                            <span className="text-[var(--paper-dim)]">—</span>
                          ) : (
                            <>
                              {glyph && <span className="mr-0.5">{glyph}</span>}
                              {formatPreciseCurrency(value)}
                            </>
                          )}
                          {isBaseline && (
                            <span className="mt-0.5 block font-sans text-[9px] font-semibold uppercase tracking-wide text-[var(--cobalt)]">
                              Baseline
                            </span>
                          )}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollHintTable>

        <p className="mt-4 text-xs leading-5 text-[var(--paper-dim)]">
          Projected cash flows and the equity bridge remain fixed. Rows vary WACC; columns vary
          terminal growth.
        </p>
      </div>
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

  return (
    <div className="page-shell">
      <div className="shell-container pb-16">
        <header className="page-header">
          <div>
            <p className="eyebrow mb-2">Valuation workspace</p>
            <h1 className="display-title">Intrinsic value desk</h1>
          </div>
          <p className="page-deck">
            Build a DCF case from company history or your own operating assumptions, then read
            it against market price.
          </p>
        </header>

        <AssumptionsPanel
          ticker={ticker}
          onTickerChange={setTicker}
          onSubmit={runValuation}
          isLoading={isLoading}
          useCustomAssumptions={useCustomAssumptions}
          onModeChange={setUseCustomAssumptions}
          revenueGrowthRate={revenueGrowthRate}
          onRevenueGrowthRateChange={setRevenueGrowthRate}
          operatingMargin={operatingMargin}
          onOperatingMarginChange={setOperatingMargin}
          terminalGrowthRate={terminalGrowthRate}
          onTerminalGrowthRateChange={setTerminalGrowthRate}
        />

        {error && (
          <div
            className={`${error.kind === "unavailable" ? "status-warning" : "status-error"} mb-6`}
            role="alert"
          >
            <strong className="block text-[var(--paper)]">
              {error.kind === "unavailable" ? "Live valuation is not connected" : "Valuation could not run"}
            </strong>
            <span className="mt-1 block">{error.message}</span>
          </div>
        )}

        {!result && !isLoading && !error && (
          <div className="empty-state px-5 py-10 text-center">
            <strong className="block text-[var(--paper)]">No valuation on the desk yet.</strong>
            <span className="mt-1.5 block">
              Choose a ticker and run the model to compare market price with intrinsic value.
            </span>
          </div>
        )}

        {isLoading && !result && (
          <div className="panel px-5 py-10 text-center text-sm text-[var(--paper-dim)]">
            Fetching financial statements and running the model…
          </div>
        )}

        {result && (
          // Restrained dimming (25%, not 50%) and no pointer-events-none —
          // the previous result stays clearly marked as stale via aria-busy
          // and the "Updating…" status, but stays inspectable rather than
          // going unreadable or unclickable while the replacement loads.
          <div
            className={`space-y-8 transition-opacity duration-200 ${isLoading ? "opacity-75" : "opacity-100"}`}
            aria-busy={isLoading}
          >
            <ValuationHeadline
              ticker={result.ticker}
              sector={result.sector}
              intrinsicValue={result.intrinsic_value_per_share}
              marketPrice={result.current_price}
              wacc={result.wacc}
              isUpdating={isLoading}
            />

            <ValuationSpectrum scenarios={result.scenarios} marketPrice={result.current_price} />

            <SensitivityMatrixSection matrix={result.sensitivity} marketPrice={result.current_price} />

            <div>
              <h2 className="section-title">Assumptions used</h2>
              <dl className="divide-y divide-[var(--line)] border-y border-[var(--line)]">
                <div className="flex items-baseline justify-between gap-4 py-2.5">
                  <dt className="text-sm text-[var(--paper-muted)]">Enterprise value</dt>
                  <dd className="tabular-nums font-mono text-sm font-semibold text-[var(--paper)]">
                    {formatCompactCurrency(result.enterprise_value)}
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-4 py-2.5">
                  <dt className="text-sm text-[var(--paper-muted)]">Equity value</dt>
                  <dd className="tabular-nums font-mono text-sm font-semibold text-[var(--paper)]">
                    {formatCompactCurrency(result.equity_value)}
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-4 py-2.5">
                  <dt className="text-sm text-[var(--paper-muted)]">Revenue growth rate used</dt>
                  <dd className="flex items-baseline gap-2 text-right">
                    <span className="tabular-nums font-mono text-sm font-semibold text-[var(--paper)]">
                      {formatPercent(result.assumptions.revenue_growth_rate)}
                    </span>
                    <span className="text-xs text-[var(--paper-dim)]">
                      {result.revenue_growth_rate_source === "historical"
                        ? "Company historical"
                        : "Custom override"}
                    </span>
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-4 py-2.5">
                  <dt className="text-sm text-[var(--paper-muted)]">Operating margin used</dt>
                  <dd className="flex items-baseline gap-2 text-right">
                    <span className="tabular-nums font-mono text-sm font-semibold text-[var(--paper)]">
                      {formatPercent(result.assumptions.operating_margin)}
                    </span>
                    <span className="text-xs text-[var(--paper-dim)]">
                      {result.operating_margin_source === "historical"
                        ? "Company historical"
                        : "Custom override"}
                    </span>
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-4 py-2.5">
                  <dt className="text-sm text-[var(--paper-muted)]">Terminal growth rate</dt>
                  <dd className="tabular-nums font-mono text-sm font-semibold text-[var(--paper)]">
                    {formatPercent(result.assumptions.terminal_growth_rate)}
                  </dd>
                </div>
              </dl>
            </div>

            <div>
              <h2 className="section-title">Sector-relative valuation</h2>
              <SectorValuationComparison
                ticker={result.ticker}
                sector={result.sector}
                priceToIntrinsicValue={result.price_to_intrinsic_value}
                sectorMedianPIV={result.sector_median_p_iv}
                sectorMedianUnavailableCode={result.sector_median_unavailable_code}
                sectorMedianSnapshot={result.sector_median_snapshot}
              />
            </div>

            <div>
              <h2 className="section-title">Projected free cash flows</h2>
              <div className="panel p-5 sm:p-6">
                <ScrollHintTable>
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
                        <th className="py-2 pl-4 text-right font-medium">Free Cash Flow</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.projected_free_cash_flows.map((row) => (
                        <tr key={row.year} className="tabular-nums font-mono text-[var(--paper-muted)]">
                          <td className="py-3 pr-4 font-sans text-[var(--paper-dim)]">Year {row.year}</td>
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
                </ScrollHintTable>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
