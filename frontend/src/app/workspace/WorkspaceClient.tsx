"use client";

import { useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
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
import ForecastChart from "@/components/valuation/ForecastChart";
import AssumptionsBridge from "@/components/valuation/AssumptionsBridge";
import type { SectorMedianUnavailableCode } from "@/lib/sector-median-copy";
import { qualityIssueCopy, type ValuationQuality } from "@/lib/valuation-quality";
import { DEFAULT_TERMINAL_GROWTH_RATE, STAGED_FORECAST_MODE } from "@/lib/evaluation-request-policy";
import { resolveMarginOfSafetyDisplay } from "@/lib/overview-response";
import { recordValuationRun } from "@/lib/valuation-history";

interface EvaluationResponse {
  ticker: string;
  current_price: number | null;
  wacc: number;
  wacc_pre_clamp: number;
  wacc_was_clamped: boolean;
  enterprise_value: number;
  equity_value: number;
  intrinsic_value_per_share: number;
  implies_negative_equity_value: boolean;
  projected_free_cash_flows: FreeCashFlowYear[];
  forecast_method: "constant" | "maturation";
  forecast_path: {
    year: number;
    stage: "constant" | "near_term" | "maturation";
    revenue_growth_rate: number;
    operating_margin: number;
  }[];
  valuation_quality: ValuationQuality;
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

export interface WorkspaceClientProps {
  /**
   * Prefills the ticker field without running the model — set by
   * `workspace/page.tsx` from a validated `?ticker=` query parameter
   * (e.g. the research home page's "Value again" link), or "AAPL" when
   * absent/invalid. This component itself has no fallback of its own;
   * the page decides the default so that policy lives in exactly one
   * place.
   */
  initialTicker: string;
}

const DETAIL_TABS = [
  ["forecast", "Forecast"],
  ["scenarios", "Scenarios"],
  ["sensitivity", "Sensitivity"],
  ["comparison", "Peer context"],
  ["assumptions", "Assumptions"],
] as const;

type DetailTab = (typeof DETAIL_TABS)[number][0];

export default function WorkspaceClient({ initialTicker }: WorkspaceClientProps) {
  const [ticker, setTicker] = useState(initialTicker);
  // Default mode: use each company's own historical revenue growth and
  // operating margin — the growth/margin query params are OMITTED
  // entirely in this mode (never sent as 0 or as the slider's current
  // position). The dashboard explicitly requests the maturation forecast;
  // sector-relative comparison stays unavailable until a same-policy
  // peer snapshot exists.
  const [useCustomAssumptions, setUseCustomAssumptions] = useState(false);
  const [revenueGrowthRate, setRevenueGrowthRate] = useState(0.08);
  const [operatingMargin, setOperatingMargin] = useState(0.25);
  const [terminalGrowthRate, setTerminalGrowthRate] = useState(DEFAULT_TERMINAL_GROWTH_RATE);

  const [result, setResult] = useState<EvaluationResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<ValuationRequestError | null>(null);
  // Bear/Base/Bull selection is shared by the Thesis Rail and the
  // Valuation Spectrum instrument — one persisted choice, two places to
  // see and change it.
  const [selectedScenario, setSelectedScenario] = useState<CaseKey>("base");
  const [activeDetail, setActiveDetail] = useState<DetailTab>("forecast");

  function handleDetailTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, current: DetailTab) {
    const currentIndex = DETAIL_TABS.findIndex(([key]) => key === current);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % DETAIL_TABS.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + DETAIL_TABS.length) % DETAIL_TABS.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = DETAIL_TABS.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    const next = DETAIL_TABS[nextIndex][0];
    setActiveDetail(next);
    document.getElementById(`workspace-tab-${next}`)?.focus();
  }

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
        forecast_mode: STAGED_FORECAST_MODE,
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
      if (
        data.forecast_method !== "maturation" ||
        !Array.isArray(data.forecast_path) ||
        !Array.isArray(data.projected_free_cash_flows) ||
        data.forecast_path.length !== data.projected_free_cash_flows.length
        || !data.valuation_quality
        || !Array.isArray(data.valuation_quality.codes)
        || typeof data.valuation_quality.allows_market_comparison !== "boolean"
        || !["ordinary", "caution", "diagnostic_only"].includes(data.valuation_quality.level)
        || data.valuation_quality.allows_market_comparison !== (data.valuation_quality.codes.length === 0)
      ) {
        throw {
          kind: "unavailable",
          message: "The valuation service has not enabled the staged forecast yet. Please try again after it is updated.",
        } satisfies ValuationRequestError;
      }
      setResult(data);
      setSelectedScenario("base");
      setActiveDetail("forecast");

      // Record this explicit, successful run for the research home
      // page's "Recent valuations" module — never for an automatic page
      // load (that distinction is structural: this call only exists on
      // THIS explicit form-submit path, not in any effect-driven fetch).
      const baseCase = data.scenarios.base;
      const baseValue = baseCase.is_valid ? baseCase.intrinsic_value_per_share : null;
      const marginOfSafety = baseValue !== null
        ? resolveMarginOfSafetyDisplay({
            currentPrice: data.current_price,
            intrinsicValuePerShare: baseValue,
            allowsMarketComparison: data.valuation_quality.allows_market_comparison,
          })
        : null;
      recordValuationRun({
        ticker: trimmedTicker,
        baseIntrinsicValuePerShare: baseValue,
        marketPrice: data.current_price,
        marketGapPct: marginOfSafety?.deltaPct ?? null,
        qualityLevel: data.valuation_quality.level,
        assumptionMode: data.revenue_growth_rate_source,
        terminalGrowthRate: data.assumptions.terminal_growth_rate,
      });
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
            <h1 className="display-title">Valuation workspace</h1>
            <p className="mt-1 text-xs text-[var(--paper-dim)]">Intrinsic value, scenarios, and cash-flow evidence</p>
          </div>
          <p className="page-deck">
            Build a staged DCF case from company history or your own assumptions, then read it
            against market price. Peer comparisons appear only when their forecast policy matches.
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
          <div className="workspace-empty">
            <div className="workspace-empty-copy">
              <span className="workspace-empty-mark" aria-hidden="true">V</span>
              <div><strong>Ready to build the first case</strong><p>Choose a ticker, confirm the assumptions above, and run the staged model.</p></div>
            </div>
            <ol className="workspace-empty-steps">
              <li><span>1</span><div><strong>Resolve the operating case</strong><p>Use company history or explicit overrides.</p></div></li>
              <li><span>2</span><div><strong>Project five years of FCFF</strong><p>Read the near-term and maturation path.</p></div></li>
              <li><span>3</span><div><strong>Cross-check value</strong><p>Compare scenarios, market price, and peers.</p></div></li>
            </ol>
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

        {result && result.wacc_was_clamped && (
          <div className="status-warning mb-6" role="status">
            <strong className="block text-[var(--paper)]">Discount-rate bound applied</strong>
            <span className="mt-1 block">
              The model computed {(result.wacc_pre_clamp * 100).toFixed(2)}% WACC and used the
              permitted {(result.wacc * 100).toFixed(2)}% bound for this valuation.
            </span>
          </div>
        )}

        {result && result.implies_negative_equity_value && (
          <div className="status-warning mb-6" role="status">
            <strong className="block text-[var(--paper)]">Model implies negative equity value</strong>
            <span className="mt-1 block">
              This is an analytical distress signal produced by the projected cash flows and
              capital structure—not a literal forecast that a share can trade below zero.
            </span>
          </div>
        )}

        {result && result.valuation_quality.level !== "ordinary" && (
          <div className="status-warning mb-6" role="alert">
            <strong className="block text-[var(--paper)]">
              {result.valuation_quality.level === "diagnostic_only"
                ? "Diagnostic model output — not an actionable share valuation"
                : "Valuation interpretation caution"}
            </strong>
            <p className="mt-1">The calculations and assumptions below remain visible, but market and peer comparisons are withheld.</p>
            <ul className="mt-2 list-disc pl-5">
              {result.valuation_quality.codes.map((code) => <li key={code}>{qualityIssueCopy(code)}</li>)}
            </ul>
            {result.valuation_quality.terminal_value_share_of_enterprise_value !== null && (
              <p className="mt-2">
                Discounted terminal value supplies {(result.valuation_quality.terminal_value_share_of_enterprise_value * 100).toFixed(1)}% of enterprise value.
              </p>
            )}
          </div>
        )}

        {result && (
          <div
            className={`workspace-results ${workspaceState === "ready" ? "result-enter" : ""} ${
              isLoading || workspaceState === "previous-result" ? "result-stale" : ""
            }`}
            aria-busy={isLoading}
          >
            <div className="workspace-primary-grid">
              <ForecastChart rows={result.projected_free_cash_flows} forecastPath={result.forecast_path} />
              <ThesisRail
                ticker={result.ticker}
                sector={result.sector}
                marketPrice={result.current_price}
                scenarios={result.scenarios}
                valuationQuality={result.valuation_quality}
                revenueGrowthSource={result.revenue_growth_rate_source}
                operatingMarginSource={result.operating_margin_source}
                selectedScenario={selectedScenario}
                onSelectScenario={setSelectedScenario}
                isUpdating={isLoading}
              />
            </div>

            <div className="workspace-tabs" role="tablist" aria-label="Valuation detail">
              {DETAIL_TABS.map(([key, label]) => (
                <button
                  key={key}
                  id={`workspace-tab-${key}`}
                  type="button"
                  role="tab"
                  aria-controls="workspace-detail-panel"
                  aria-selected={activeDetail === key}
                  tabIndex={activeDetail === key ? 0 : -1}
                  className="workspace-tab"
                  onClick={() => setActiveDetail(key)}
                  onKeyDown={(event) => handleDetailTabKeyDown(event, key)}
                >
                  {label}
                </button>
              ))}
            </div>

            <div
              id="workspace-detail-panel"
              className="workspace-detail"
              role="tabpanel"
              aria-labelledby={`workspace-tab-${activeDetail}`}
            >
              {activeDetail === "forecast" && (
                <ProjectedCashFlows rows={result.projected_free_cash_flows} forecastPath={result.forecast_path} />
              )}

              {activeDetail === "scenarios" && (
              <ValuationSpectrum
                scenarios={result.scenarios}
                valuationQuality={result.valuation_quality}
                marketPrice={result.current_price}
                selectedScenario={selectedScenario}
                onSelectScenario={setSelectedScenario}
              />
              )}

              {activeDetail === "sensitivity" && (
              <SensitivityMatrix
                matrix={result.sensitivity}
                marketPrice={result.valuation_quality.allows_market_comparison ? result.current_price : null}
                comparisonWithheld={!result.valuation_quality.allows_market_comparison}
              />
              )}

              {activeDetail === "comparison" && (
              <SectorRelativeValuation
                ticker={result.ticker}
                sector={result.sector}
                priceToIntrinsicValue={result.price_to_intrinsic_value}
                sectorMedianPIV={result.sector_median_p_iv}
                sectorMedianUnavailableCode={result.sector_median_unavailable_code}
                sectorMedianSnapshot={result.sector_median_snapshot}
              />
              )}

              {activeDetail === "assumptions" && <AssumptionsBridge result={result} />}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
