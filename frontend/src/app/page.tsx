"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { valuationErrorFromResponse, type ValuationRequestError } from "@/lib/valuation-errors";
import { errorBannerHeadline, errorBannerTone, resolveWorkspaceResultState } from "@/lib/valuation-state-copy";
import TickerCommandBar from "@/components/valuation/TickerCommandBar";
import AssumptionTray from "@/components/valuation/AssumptionTray";
import ThesisRail from "@/components/valuation/ThesisRail";
import ValuationSpectrum, { type CaseKey, type DCFScenarioSet } from "@/components/valuation/ValuationSpectrum";
import SensitivityMatrix, { type DCFSensitivityMatrix } from "@/components/valuation/SensitivityMatrix";
import SectorRelativeValuation, {
  type SectorMedianSnapshot,
} from "@/components/valuation/SectorRelativeValuation";
import ProjectedCashFlows, { type FreeCashFlowYear } from "@/components/valuation/ProjectedCashFlows";
import AssumptionsBridge from "@/components/valuation/AssumptionsBridge";
import type { SectorMedianUnavailableCode } from "@/lib/sector-median-copy";

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
  sector_median_snapshot: SectorMedianSnapshot | null;
  sensitivity: DCFSensitivityMatrix;
  scenarios: DCFScenarioSet;
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
  // Bear/Base/Bull selection is shared by the Thesis Rail and the
  // Valuation Spectrum instrument — one persisted choice, two places to
  // see and change it.
  const [selectedScenario, setSelectedScenario] = useState<CaseKey>("base");

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
      setSelectedScenario("base");
    } catch (err) {
      // A failed rerun must never clear a previous result that's still
      // useful on screen — `result` is left exactly as it was; only the
      // error banner and the "previous result" state change.
      setError(
        err && typeof err === "object" && "kind" in err && "message" in err
          ? (err as ValuationRequestError)
          : { kind: "unavailable", message: "The valuation service did not respond." }
      );
    } finally {
      setIsLoading(false);
    }
  }

  const workspaceState = resolveWorkspaceResultState({
    hasResult: result !== null,
    isLoading,
    hasError: error !== null,
  });

  return (
    <div className="page-shell">
      <div className="shell-container pb-16">
        <header className="page-header">
          <div>
            <p className="eyebrow mb-1.5">Valuation workspace</p>
            <h1 className="display-title">Intrinsic value desk</h1>
          </div>
          <p className="page-deck">
            Build a DCF case from company history or your own assumptions, then read it against
            market price and sector peers.
          </p>
        </header>

        <TickerCommandBar
          ticker={ticker}
          onTickerChange={setTicker}
          onSubmit={runValuation}
          isLoading={isLoading}
          useCustomAssumptions={useCustomAssumptions}
          onModeChange={setUseCustomAssumptions}
        />

        <AssumptionTray
          useCustomAssumptions={useCustomAssumptions}
          revenueGrowthRate={revenueGrowthRate}
          onRevenueGrowthRateChange={setRevenueGrowthRate}
          operatingMargin={operatingMargin}
          onOperatingMarginChange={setOperatingMargin}
          terminalGrowthRate={terminalGrowthRate}
          onTerminalGrowthRateChange={setTerminalGrowthRate}
        />

        {error && (
          <div className={`${errorBannerTone(error.kind) === "warning" ? "status-warning" : "status-error"} mb-6`} role="alert">
            <strong className="block text-[var(--paper)]">{errorBannerHeadline(error.kind)}</strong>
            <span className="mt-1 block">{error.message}</span>
          </div>
        )}

        {workspaceState === "empty" && (
          <div className="empty-state px-5 py-10 text-center">
            <strong className="block text-[var(--paper)]">No valuation on the desk yet.</strong>
            <span className="mt-1.5 block">
              Choose a ticker and run the model to compare market price with intrinsic value.
            </span>
          </div>
        )}

        {workspaceState === "first-loading" && (
          <div className="workspace-grid" aria-hidden="true">
            <div className="workspace-rail-slot">
              <div className="skeleton-block h-72 rounded-xl" />
            </div>
            <div className="workspace-analysis-slot space-y-4">
              <div className="skeleton-block h-56 rounded-xl" />
              <div className="skeleton-block h-40 rounded-xl" />
            </div>
          </div>
        )}
        {workspaceState === "first-loading" && (
          <p className="sr-only" role="status">
            Fetching financial statements and running the model…
          </p>
        )}

        {result && workspaceState === "previous-result" && (
          <p className="previous-result-notice" role="status">
            Showing the previous result — the last refresh above did not complete.
          </p>
        )}

        {result && (
          <div
            className={`workspace-grid ${workspaceState === "ready" ? "result-enter" : ""} ${
              isLoading || workspaceState === "previous-result" ? "result-stale" : ""
            }`}
            aria-busy={isLoading}
          >
            <div className="workspace-rail-slot">
              <ThesisRail
                ticker={result.ticker}
                sector={result.sector}
                marketPrice={result.current_price}
                scenarios={result.scenarios}
                selectedScenario={selectedScenario}
                onSelectScenario={setSelectedScenario}
                isUpdating={isLoading}
              />
            </div>

            <div className="workspace-analysis-slot">
              <ValuationSpectrum
                scenarios={result.scenarios}
                marketPrice={result.current_price}
                selectedScenario={selectedScenario}
                onSelectScenario={setSelectedScenario}
              />

              <SensitivityMatrix matrix={result.sensitivity} marketPrice={result.current_price} />

              <SectorRelativeValuation
                ticker={result.ticker}
                sector={result.sector}
                priceToIntrinsicValue={result.price_to_intrinsic_value}
                sectorMedianPIV={result.sector_median_p_iv}
                sectorMedianUnavailableCode={result.sector_median_unavailable_code}
                sectorMedianSnapshot={result.sector_median_snapshot}
              />

              <ProjectedCashFlows rows={result.projected_free_cash_flows} />

              <AssumptionsBridge result={result} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
