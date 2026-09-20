"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";

import AssumptionsBridge from "@/components/valuation/AssumptionsBridge";
import ProjectedCashFlows, { type FreeCashFlowYear } from "@/components/valuation/ProjectedCashFlows";
import SectorRelativeValuation, { type SectorMedianSnapshot } from "@/components/valuation/SectorRelativeValuation";
import SensitivityMatrix, { type DCFSensitivityMatrix } from "@/components/valuation/SensitivityMatrix";
import ThesisRail from "@/components/valuation/ThesisRail";
import ValuationSpectrum, { type CaseKey, type DCFScenarioSet } from "@/components/valuation/ValuationSpectrum";
import { formatPercent, formatPreciseCurrency } from "@/components/valuation/format";
import { defaultInteractiveEvaluationParams } from "@/lib/evaluation-request-policy";
import { overviewRouteForTicker, resolveMarginOfSafetyDisplay } from "@/lib/overview-response";
import type { SectorMedianUnavailableCode } from "@/lib/sector-median-copy";
import { valuationErrorFromResponse, type ValuationRequestError } from "@/lib/valuation-errors";
import { errorBannerHeadline, errorBannerTone, resolveWorkspaceResultState } from "@/lib/valuation-state-copy";
import { qualityIssueCopy, scenarioDisplayLabel, type ValuationQuality } from "@/lib/valuation-quality";
import styles from "./OverviewDashboard.module.css";

interface OverviewEvaluationResponse {
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
  revenue_growth_rate_source: "historical" | "custom";
  operating_margin_source: "historical" | "custom";
  capex_pct_revenue: number;
  capex_pct_revenue_source: "default" | "historical" | "fallback";
  sector: string;
  price_to_intrinsic_value: number | null;
  sector_median_p_iv: number | null;
  sector_median_unavailable_code: SectorMedianUnavailableCode | null;
  sector_median_snapshot: SectorMedianSnapshot | null;
  sensitivity: DCFSensitivityMatrix;
  scenarios: DCFScenarioSet;
}

interface OverviewClientProps {
  initialTicker: string;
}

const QUICK_TICKERS = [
  { ticker: "MSFT", company: "Microsoft" },
  { ticker: "AAPL", company: "Apple" },
  { ticker: "CAT", company: "Caterpillar" },
  { ticker: "INTC", company: "Intel" },
  { ticker: "VZ", company: "Verizon" },
] as const;

function TrendGlyph({ positive }: { positive: boolean }) {
  return <span aria-hidden="true">{positive ? "↗" : "↘"}</span>;
}

export default function OverviewClient({ initialTicker }: OverviewClientProps) {
  const router = useRouter();
  const [tickerInput, setTickerInput] = useState(initialTicker);
  const [result, setResult] = useState<OverviewEvaluationResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ValuationRequestError | null>(null);
  const [selectedScenario, setSelectedScenario] = useState<CaseKey>("base");

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await fetch(
          `/api/evaluate/${encodeURIComponent(initialTicker)}?${defaultInteractiveEvaluationParams().toString()}`,
        );
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw valuationErrorFromResponse(response.status, body);
        }
        const data: OverviewEvaluationResponse = await response.json();
        if (
          data.forecast_method !== "maturation" ||
          !Array.isArray(data.forecast_path) ||
          !Array.isArray(data.projected_free_cash_flows) ||
          data.forecast_path.length !== data.projected_free_cash_flows.length ||
          !data.sensitivity ||
          !data.scenarios ||
          !data.valuation_quality
        ) {
          throw {
            kind: "unavailable",
            message: "The valuation service returned an incomplete research result.",
          } satisfies ValuationRequestError;
        }
        if (!cancelled) {
          setResult(data);
          setSelectedScenario("base");
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err && typeof err === "object" && "kind" in err && "message" in err
              ? (err as ValuationRequestError)
              : { kind: "unavailable", message: "The valuation service did not respond." },
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    run();
    return () => { cancelled = true; };
  }, [initialTicker]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = overviewRouteForTicker(tickerInput);
    if (target) router.push(target);
  }

  const resultState = resolveWorkspaceResultState({
    hasResult: result !== null,
    isLoading,
    hasError: error !== null,
  });
  const selectedCase = result?.scenarios[selectedScenario] ?? null;
  const selectedValue = selectedCase?.is_valid ? selectedCase.intrinsic_value_per_share : null;
  const marginOfSafety = result && selectedValue !== null
    ? resolveMarginOfSafetyDisplay({
        currentPrice: result.current_price,
        intrinsicValuePerShare: selectedValue,
        allowsMarketComparison: result.valuation_quality.allows_market_comparison,
      })
    : null;

  return (
    <main className={styles.dashboard}>
      <div className={styles.content}>
        <header className={styles.hero}>
          <div>
            <h1>{result?.ticker ?? initialTicker} <span>valuation dashboard</span></h1>
            <p>{result ? `${result.sector} · Five-year staged DCF` : "Loading the latest company financials and valuation case."}</p>
          </div>
          <form onSubmit={handleSubmit} className={styles.tickerForm}>
            <label htmlFor="overview-ticker">Change company</label>
            <div>
              <input
                id="overview-ticker"
                value={tickerInput}
                onChange={(event) => setTickerInput(event.target.value.toUpperCase())}
                placeholder="AAPL"
                maxLength={10}
                autoComplete="off"
                spellCheck={false}
              />
              <button type="submit" disabled={isLoading}>
                {isLoading ? <><span className="spinner" aria-hidden="true" />Updating</> : "Open"}
              </button>
            </div>
          </form>
        </header>

        <nav className={styles.tickerStrip} aria-label="Quick company selection">
          <span>Coverage</span>
          {QUICK_TICKERS.map(({ ticker, company }) => (
            <button
              key={ticker}
              type="button"
              aria-current={initialTicker === ticker ? "page" : undefined}
              onClick={() => router.push(`/overview/${ticker}`)}
            >
              <strong>{ticker}</strong>
              <small>{company}</small>
            </button>
          ))}
        </nav>

        {error && (
          <div className={errorBannerTone(error.kind) === "warning" ? "status-warning" : "status-error"} role="alert">
            <strong>{errorBannerHeadline(error.kind)}</strong>
            <span>{error.message}</span>
          </div>
        )}

        {resultState === "first-loading" && (
          <div className={styles.loadingGrid} aria-hidden="true">
            {[0, 1, 2, 3].map((key) => <div key={key} className="skeleton-block" />)}
            <div className="skeleton-block" />
            <div className="skeleton-block" />
          </div>
        )}
        {resultState === "first-loading" && <p className="sr-only" role="status">Fetching financial statements and running the model…</p>}

        {result && (
          <div className={resultState === "ready" ? "result-enter" : ""} aria-busy={isLoading}>
            <section className={styles.metrics} aria-label="Valuation summary">
              <article className={styles.metricCard}>
                <div className={styles.metricTop}><span>Market price</span><i className={styles.blue}>Live</i></div>
                <strong>{result.current_price === null ? "Unavailable" : formatPreciseCurrency(result.current_price)}</strong>
                <small>Observed equity price</small>
              </article>
              <article className={styles.metricCard}>
                <div className={styles.metricTop}><span>{scenarioDisplayLabel(selectedScenario, result.valuation_quality)} value</span><i className={styles.violet}>DCF</i></div>
                <strong>{selectedValue === null ? "Not computable" : formatPreciseCurrency(selectedValue)}</strong>
                <small>Intrinsic value per share</small>
              </article>
              <article className={styles.metricCard}>
                <div className={styles.metricTop}><span>Market gap</span><i className={marginOfSafety?.hasMarginOfSafety ? styles.green : styles.red}>Spread</i></div>
                <strong className={marginOfSafety?.hasMarginOfSafety ? styles.positive : styles.negative}>
                  {marginOfSafety?.deltaPct == null ? "Withheld" : <><TrendGlyph positive={marginOfSafety.deltaPct >= 0} /> {formatPercent(marginOfSafety.deltaPct)}</>}
                </strong>
                <small>{marginOfSafety?.label ?? "Comparison unavailable"}</small>
              </article>
              <article className={styles.metricCard}>
                <div className={styles.metricTop}><span>Discount rate</span><i className={styles.cyan}>WACC</i></div>
                <strong>{formatPercent(result.wacc, 2)}</strong>
                <small>{result.wacc_was_clamped ? `Bound from ${formatPercent(result.wacc_pre_clamp, 2)}` : "Model-derived capital cost"}</small>
              </article>
            </section>

            {(result.wacc_was_clamped || result.implies_negative_equity_value || result.valuation_quality.level !== "ordinary") && (
              <section className={styles.alerts} aria-label="Model cautions">
                {result.wacc_was_clamped && <p><strong>Discount-rate bound applied.</strong> The model uses {formatPercent(result.wacc, 2)} instead of {formatPercent(result.wacc_pre_clamp, 2)}.</p>}
                {result.implies_negative_equity_value && <p><strong>Negative modeled equity.</strong> Treat this result as a distress diagnostic rather than a tradable share price.</p>}
                {result.valuation_quality.level !== "ordinary" && (
                  <div>
                    <strong>{result.valuation_quality.level === "diagnostic_only" ? "Diagnostic model output." : "Interpretation caution."}</strong>
                    <span> Market and peer comparisons are withheld.</span>
                    <ul>{result.valuation_quality.codes.map((code) => <li key={code}>{qualityIssueCopy(code)}</li>)}</ul>
                  </div>
                )}
              </section>
            )}

            <section className={styles.primaryGrid}>
              <div className={styles.panel}>
                <div className={styles.panelHeading}>
                  <h2>Valuation spectrum</h2>
                  <span>Bear · Base · Bull · Market</span>
                </div>
                <ValuationSpectrum
                  scenarios={result.scenarios}
                  valuationQuality={result.valuation_quality}
                  marketPrice={result.current_price}
                  selectedScenario={selectedScenario}
                  onSelectScenario={setSelectedScenario}
                />
              </div>
              <div className={styles.thesisPanel}>
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
            </section>

            <section className={styles.secondaryGrid}>
              <div className={styles.panel}>
                <div className={styles.panelHeading}><h2>DCF sensitivity</h2><span>Range of outcomes · WACC × terminal growth</span></div>
                <SensitivityMatrix
                  matrix={result.sensitivity}
                  marketPrice={result.valuation_quality.allows_market_comparison ? result.current_price : null}
                  comparisonWithheld={!result.valuation_quality.allows_market_comparison}
                />
              </div>
              <div className={styles.panel}>
                <div className={styles.panelHeading}><h2>Sector comparison</h2></div>
                <SectorRelativeValuation
                  ticker={result.ticker}
                  sector={result.sector}
                  priceToIntrinsicValue={result.price_to_intrinsic_value}
                  sectorMedianPIV={result.sector_median_p_iv}
                  sectorMedianUnavailableCode={result.sector_median_unavailable_code}
                  sectorMedianSnapshot={result.sector_median_snapshot}
                />
              </div>
            </section>

            <section className={styles.panel}>
              <div className={styles.panelHeading}>
                <h2>Projected free cash flow</h2>
                <span>Five-year operating path · CapEx {formatPercent(result.capex_pct_revenue)} of revenue · {result.capex_pct_revenue_source}</span>
              </div>
              <ProjectedCashFlows rows={result.projected_free_cash_flows} forecastPath={result.forecast_path} />
            </section>

            <section className={styles.panel}>
              <div className={styles.panelHeading}><h2>Assumptions and equity bridge</h2><span>Audit trail · company history + policy inputs</span></div>
              <AssumptionsBridge result={result} />
            </section>
          </div>
        )}
      </div>
    </main>
  );
}
