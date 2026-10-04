"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { parseTickerQueryParam } from "@/lib/ticker-query";
import CashFlowCharts from "@/components/valuation/CashFlowCharts";
import HistoricalFinancialsPanel from "@/components/valuation/HistoricalFinancialsPanel";
import { isWorkspaceResult, readSavedWorkspace, writeSavedWorkspace, clearSavedWorkspace } from "@/lib/workspace-storage";
import type { EvaluationResponse } from "@/lib/workspace-result";
import PriceComparison from "@/components/valuation/PriceComparison";
import { formatCompactCurrency, formatPercent, formatPreciseCurrency } from "@/components/valuation/format";
import type { FormEvent } from "react";
import { valuationErrorFromResponse, type ValuationRequestError } from "@/lib/valuation-errors";
import { errorBannerHeadline, errorBannerTone, resolveWorkspaceResultState } from "@/lib/valuation-state-copy";
import TickerCommandBar from "@/components/valuation/TickerCommandBar";
import AssumptionTray from "@/components/valuation/AssumptionTray";
import ValuationSpectrum, { type CaseKey } from "@/components/valuation/ValuationSpectrum";
import SensitivityMatrix from "@/components/valuation/SensitivityMatrix";
import SectorRelativeValuation from "@/components/valuation/SectorRelativeValuation";
import ProjectedCashFlows from "@/components/valuation/ProjectedCashFlows";
import MarketPriceChart from "@/components/valuation/MarketPriceChart";
import AssumptionsBridge from "@/components/valuation/AssumptionsBridge";
import { qualityIssueCopy } from "@/lib/valuation-quality";
import { DEFAULT_TERMINAL_GROWTH_RATE, STAGED_FORECAST_MODE } from "@/lib/evaluation-request-policy";
import { resolveMarginOfSafetyDisplay } from "@/lib/overview-response";
import { recordValuationRun } from "@/lib/valuation-history";
import { isMarketHistoryResponse, type MarketHistoryResponse } from "@/lib/market-history";


export interface WorkspaceClientProps {
  /** Fallback used when the current URL has no valid ticker query. */
  initialTicker: string;
}

export default function WorkspaceClient({ initialTicker }: WorkspaceClientProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryTicker = parseTickerQueryParam(searchParams.getAll("ticker").length === 1 ? searchParams.get("ticker") ?? undefined : undefined);
  const view = pathname.split("/")[2] ?? "overview";
  const requestSequence = useRef(0);
  const [ticker, setTicker] = useState(queryTicker ?? initialTicker);
  const [previousQuery, setPreviousQuery] = useState(queryTicker);
  if (queryTicker !== previousQuery) { setPreviousQuery(queryTicker); if (queryTicker) setTicker(queryTicker); }
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
  const [marketHistory, setMarketHistory] = useState<MarketHistoryResponse | null>(null);
  const [marketHistoryStatus, setMarketHistoryStatus] = useState<"loading" | "ready" | "unavailable">("loading");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<ValuationRequestError | null>(null);
  // Bear/Base/Bull selection is shared by the Thesis Rail and the
  // Valuation Spectrum instrument — one persisted choice, two places to
  // see and change it.
  const [selectedScenario, setSelectedScenario] = useState<CaseKey>("base");
  const [runAt, setRunAt] = useState<string | null>(null);
  const [restored, setRestored] = useState(false);
  const [storageAvailable, setStorageAvailable] = useState(true);
  useEffect(() => {
    function restore() {
      const saved = readSavedWorkspace();
      if (!saved) return;
      setResult(saved.result);
      if (!queryTicker) setTicker(saved.result.ticker);
      setUseCustomAssumptions(saved.result.revenue_growth_rate_source === "custom");
      setRevenueGrowthRate(saved.result.assumptions.revenue_growth_rate);
      setOperatingMargin(saved.result.assumptions.operating_margin);
      setTerminalGrowthRate(saved.result.assumptions.terminal_growth_rate);
      setMarketHistory(saved.marketHistory);
      setMarketHistoryStatus(saved.marketHistory ? "ready" : "unavailable");
      setSelectedScenario(saved.selectedScenario);
      setRunAt(saved.savedAt);
      setRestored(true);
    }
    // Restore external browser storage only; this effect never fetches or runs a model.
    restore();
    // Hydrate once. Later ticker-query changes are handled independently above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  function selectScenario(scenario: CaseKey) {
    setSelectedScenario(scenario);
    if (result && runAt) setStorageAvailable(writeSavedWorkspace({ result, marketHistory, selectedScenario: scenario, savedAt: runAt }));
  }
  function clearResult() {
    requestSequence.current += 1;
    setIsLoading(false);
    clearSavedWorkspace();
    setResult(null); setMarketHistory(null); setRunAt(null); setRestored(false); setError(null);
    router.push("/workspace");
  }
  async function runValuation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const trimmedTicker = ticker.trim().toUpperCase();
    if (!parseTickerQueryParam(trimmedTicker)) {
      setError({ kind: "input", message: "Enter a ticker using 1–10 letters, numbers, dots or hyphens." });
      return;
    }

    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;

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

      const historyPromise = fetch(`/api/market-history/${encodeURIComponent(trimmedTicker)}`)
        .then(async (historyResponse) => {
          if (!historyResponse.ok) return null;
          return await historyResponse.json() as MarketHistoryResponse;
        })
        .catch(() => null);

      const response = await fetch(
        `/api/evaluate/${encodeURIComponent(trimmedTicker)}?${params.toString()}`
      );

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw valuationErrorFromResponse(response.status, body);
      }

      const data: EvaluationResponse = await response.json();
      if (requestSequence.current !== requestId) return;
      if (!isWorkspaceResult(data)) {
        throw { kind: "unavailable", message: "The valuation service returned incomplete data. Please try again." } satisfies ValuationRequestError;
      }
      setResult(data);
      const completedAt = new Date().toISOString();
      setRunAt(completedAt);
      setRestored(false);
      setStorageAvailable(writeSavedWorkspace({ result: data, marketHistory: null, selectedScenario: "base", savedAt: completedAt }));
      setMarketHistory(null);
      setMarketHistoryStatus("loading");
      void historyPromise.then((history) => {
        if (requestSequence.current !== requestId) return;
        if (isMarketHistoryResponse(history) && history.ticker === data.ticker) {
          setMarketHistory(history);
          setMarketHistoryStatus("ready");
          setStorageAvailable(writeSavedWorkspace({ result: data, marketHistory: history, selectedScenario: readSavedWorkspace()?.savedAt === completedAt ? readSavedWorkspace()!.selectedScenario : "base", savedAt: completedAt }));
        } else {
          setMarketHistoryStatus("unavailable");
        }
      });
      setSelectedScenario("base");
      router.push("/workspace");

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
      if (requestSequence.current !== requestId) return;
      // A failed rerun must never clear a previous result that's still
      // useful on screen — `result` is left exactly as it was; only the
      // error banner and the "previous result" state change.
      setError(
        err && typeof err === "object" && "kind" in err && "message" in err
          ? (err as ValuationRequestError)
          : { kind: "unavailable", message: "The valuation service did not respond." }
      );
    } finally {
      if (requestSequence.current === requestId) setIsLoading(false);
    }
  }

  const workspaceState = resolveWorkspaceResultState({
    hasResult: result !== null,
    isLoading,
    hasError: error !== null,
  });

  const pageTitle: Record<string,string> = { overview: "Valuation workspace", valuation: "Valuation", "cash-flows": "Cash flow forecast", projections: "Projection detail", evidence: "Evidence & sources", assumptions: "Model assumptions" };
  const selected = result?.scenarios[selectedScenario];
  return (
    <main className="page-shell dark-workspace">
      <div className="shell-container pb-16">
        <header className="page-header">
          <div><h1 className="display-title">{pageTitle[view] ?? "Valuation workspace"}</h1>
          <p className="page-deck">{view === "overview" ? "Choose a company, review its assumptions, then inspect its reported financial history and model estimates." : result ? `${result.ticker} · ${restored ? "Saved valuation" : "Completed valuation"}` : "Run a valuation to inspect this part of the model."}</p></div>
        </header>
        {(view === "overview" || view === "assumptions") && (
          <section className="model-start">
            <div>
              <TickerCommandBar ticker={ticker} onTickerChange={setTicker} onSubmit={runValuation} isLoading={isLoading} useCustomAssumptions={useCustomAssumptions} onModeChange={setUseCustomAssumptions} />
              <AssumptionTray useCustomAssumptions={useCustomAssumptions} revenueGrowthRate={revenueGrowthRate} onRevenueGrowthRateChange={setRevenueGrowthRate} operatingMargin={operatingMargin} onOperatingMarginChange={setOperatingMargin} terminalGrowthRate={terminalGrowthRate} onTerminalGrowthRateChange={setTerminalGrowthRate} />
            </div>
            <div className="model-start-context"><h2>Begin with company history</h2><p>Historical growth and margin are the model default. Custom mode sends your explicit overrides. Terminal growth applies to every run.</p><p>The model runs only when you select Run Valuation. Page navigation does not request a new valuation.</p><Link href="/methodology">Read the methodology</Link></div>
          </section>
        )}
        {error && (
          <div className={`${errorBannerTone(error.kind) === "warning" ? "status-warning" : "status-error"} mb-6`} role="alert">
            <strong className="block text-[var(--paper)]">{errorBannerHeadline(error.kind)}</strong>
            <span className="mt-1 block">{error.message}</span>
          </div>
        )}

        {workspaceState === "first-loading" && (
          <div className="workspace-grid" aria-hidden="true">
            <div className="workspace-rail-slot">
              <div className="skeleton-block skeleton-panel h-72" />
            </div>
            <div className="workspace-analysis-slot space-y-4">
              <div className="skeleton-block skeleton-panel h-56" />
              <div className="skeleton-block skeleton-panel h-40" />
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


        {!result && !isLoading && view !== "overview" && view !== "assumptions" && <section className="panel model-empty"><h2>No completed valuation yet</h2><p>Start with a company and run the model. Completed results are saved in this browser and remain available across workspace pages and refreshes.</p><Link className="button-primary" href="/workspace">Choose a company</Link></section>}
        {!result && !isLoading && view === "overview" && <section className="model-next"><h2>Review the case</h2><div className="model-step-links"><Link href="/workspace/valuation"><strong>Valuation</strong><span>Price comparison, scenarios and sensitivity</span></Link><Link href="/workspace/cash-flows"><strong>Cash flows</strong><span>Annual forecast and cash flow reconciliation</span></Link><Link href="/workspace/evidence"><strong>Evidence</strong><span>Input sources and reporting cutoffs</span></Link></div></section>}
        {result && (
          <div className={`workspace-results ${isLoading || workspaceState === "previous-result" ? "result-stale" : ""}`} aria-busy={isLoading}>
            <div className="model-result-context"><strong>{result.ticker}</strong><span>Statement period {result.valuation_input_provenance.statement_period_end}</span><span>{result.revenue_growth_rate_source === "historical" && result.operating_margin_source === "historical" ? "Company history" : "Custom inputs"}</span><Link href="/workspace/assumptions">Review or rerun assumptions</Link></div>
            <div className="model-saved-status"><p>{restored ? "Saved result" : "Completed run"} · {runAt ? new Date(runAt).toLocaleString() : "Run time unavailable"}. {restored ? "Prices and statements have not been refreshed. Run again to update." : storageAvailable ? "Saved in this browser; reloads will not run the model." : "Browser storage is unavailable; this result will be lost after a refresh."}</p><button className="button-secondary" onClick={clearResult}>Clear saved result</button></div>
            {(view === "overview" || view === "cash-flows") && <HistoricalFinancialsPanel history={result.historical_financials ?? null} source={result.valuation_input_provenance.source} />}
            {(view === "overview" || view === "valuation") && <>
              <section className="panel model-summary"><div className="model-panel-heading"><h2>{result.ticker} valuation</h2><div className="model-scenarios" role="group" aria-label="Valuation scenario">{(["base","bear","bull"] as CaseKey[]).map(key=><button key={key} aria-pressed={selectedScenario===key} onClick={()=>selectScenario(key)}>{key === "base" ? "Base" : key === "bear" ? "Bear" : "Bull"}</button>)}</div></div>
                <dl className="model-metrics"><div><dt>{result.valuation_quality.allows_market_comparison ? "Intrinsic value / share" : "Model output / share"}</dt><dd>{selected?.is_valid ? formatPreciseCurrency(selected.intrinsic_value_per_share) : "Not computable"}</dd></div><div><dt>Market price at run</dt><dd>{formatPreciseCurrency(result.current_price)}</dd></div><div><dt>Base enterprise value</dt><dd>{formatCompactCurrency(result.enterprise_value)}</dd></div><div><dt>Selected case WACC</dt><dd>{selected ? formatPercent(selected.assumptions.wacc,2) : "—"}</dd></div></dl>
              </section>
              {view === "overview" ? null : <>
                <PriceComparison marketPrice={result.current_price} scenario={selected!} quality={result.valuation_quality} />
                <section className="panel model-analysis"><ValuationSpectrum scenarios={result.scenarios} valuationQuality={result.valuation_quality} marketPrice={result.current_price} selectedScenario={selectedScenario} onSelectScenario={selectScenario} /></section>
                <section className="panel model-analysis"><SensitivityMatrix matrix={result.sensitivity} marketPrice={result.valuation_quality.allows_market_comparison ? result.current_price : null} comparisonWithheld={!result.valuation_quality.allows_market_comparison} /></section>
                <section className="panel model-analysis"><SectorRelativeValuation ticker={result.ticker} sector={result.sector} priceToIntrinsicValue={result.price_to_intrinsic_value} sectorMedianPIV={result.sector_median_p_iv} sectorMedianUnavailableCode={result.sector_median_unavailable_code} sectorMedianSnapshot={result.sector_median_snapshot} /></section>
                <MarketPriceChart ticker={result.ticker} history={marketHistory} status={marketHistoryStatus} />
              </>}
            </>}
            {view === "overview" && <section className="model-next"><h2>Inspect the model estimates</h2><div className="model-step-links"><Link href="/workspace/valuation"><strong>Valuation</strong><span>Scenario values, sensitivity and market prices</span></Link><Link href="/workspace/cash-flows"><strong>Cash flow forecast</strong><span>Projected Base FCF and the cash-flow bridge</span></Link><Link href="/workspace/projections"><strong>Projection detail</strong><span>Annual model assumptions and components</span></Link></div></section>}
            {view === "cash-flows" && <CashFlowCharts rows={result.projected_free_cash_flows} showBridge />}
            {(view === "cash-flows" || view === "projections") && <ProjectedCashFlows rows={result.projected_free_cash_flows} forecastPath={result.forecast_path} />}
            {view === "assumptions" && <section className="panel model-analysis"><AssumptionsBridge result={result} /></section>}
            {view === "evidence" && <section className="panel model-analysis"><h2>Valuation input provenance</h2><dl className="model-evidence"><div><dt>Source</dt><dd>{result.valuation_input_provenance.source === "sec" ? "SEC filings" : "Yahoo statements"}</dd></div><div><dt>Selection reason</dt><dd>{result.valuation_input_provenance.source_selection_reason}</dd></div><div><dt>Statement period end</dt><dd>{result.valuation_input_provenance.statement_period_end}</dd></div><div><dt>Knowledge cutoff</dt><dd>{result.valuation_input_provenance.knowledge_cutoff}</dd></div><div><dt>Policy version</dt><dd>{result.valuation_input_provenance.policy_version}</dd></div><div><dt>Ingestion batches</dt><dd>{result.valuation_input_provenance.ingestion_batch_ids.length ? result.valuation_input_provenance.ingestion_batch_ids.join(", ") : "None reported by the service"}</dd></div></dl><p>Source selection and cutoffs are reported by the valuation service. They do not establish that every issuer line has been independently reconciled.</p></section>}
          </div>
        )}
        <footer className="model-footer"><span>Valuation Engine</span></footer>
      </div>
    </main>
  );
}
