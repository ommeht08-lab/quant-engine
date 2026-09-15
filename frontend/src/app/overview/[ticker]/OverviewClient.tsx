"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";

import { valuationErrorFromResponse, type ValuationRequestError } from "@/lib/valuation-errors";
import { errorBannerHeadline, errorBannerTone, resolveWorkspaceResultState } from "@/lib/valuation-state-copy";
import { overviewRouteForTicker, resolveMarginOfSafetyDisplay } from "@/lib/overview-response";
import { defaultInteractiveEvaluationParams } from "@/lib/evaluation-request-policy";
import { qualityIssueCopy, type ValuationQuality } from "@/lib/valuation-quality";
import { formatPercent, formatPreciseCurrency } from "@/components/valuation/format";
import ValuationSpectrum, { type CaseKey, type DCFScenarioSet } from "@/components/valuation/ValuationSpectrum";
import SectorRelativeValuation, { type SectorMedianSnapshot } from "@/components/valuation/SectorRelativeValuation";
import type { SectorMedianUnavailableCode } from "@/lib/sector-median-copy";
import UnavailablePanel from "@/components/research/UnavailablePanel";

// Only the fields this page actually renders. Deliberately NOT a
// full mirror of the backend's EvaluationResponse (e.g. no capex/
// forecast-path/sensitivity fields) — see the implementation report for
// why an unused field is a claim of relevance this page doesn't make.
interface OverviewEvaluationResponse {
  ticker: string;
  current_price: number | null;
  intrinsic_value_per_share: number;
  sector: string;
  price_to_intrinsic_value: number | null;
  sector_median_p_iv: number | null;
  sector_median_unavailable_code: SectorMedianUnavailableCode | null;
  sector_median_snapshot: SectorMedianSnapshot | null;
  valuation_quality: ValuationQuality;
  scenarios: DCFScenarioSet;
}

interface OverviewClientProps {
  /** Already trimmed/uppercased by the server wrapper (`page.tsx`). */
  initialTicker: string;
}

// Keyed by `initialTicker` from `page.tsx` (`<OverviewClient key={ticker} .../>`)
// so navigating to a DIFFERENT ticker fully remounts this component —
// every piece of local state (the input, the previous result, the
// selected scenario) starts fresh rather than being reset by an effect.
// This is what guarantees the header and the rendered numbers can never
// momentarily disagree about which company they describe: there is no
// window where a new ticker's name is on screen next to a different
// ticker's stale scenario values.
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
      try {
        const response = await fetch(
          `/api/evaluate/${encodeURIComponent(initialTicker)}?${defaultInteractiveEvaluationParams().toString()}`
        );
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw valuationErrorFromResponse(response.status, body);
        }
        const data: OverviewEvaluationResponse = await response.json();
        if (!cancelled) {
          setResult(data);
          setSelectedScenario("base");
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err && typeof err === "object" && "kind" in err && "message" in err
              ? (err as ValuationRequestError)
              : { kind: "unavailable", message: "The valuation service did not respond." }
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [initialTicker]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = overviewRouteForTicker(tickerInput);
    if (!target) return;
    router.push(target);
  }

  const resultState = resolveWorkspaceResultState({
    hasResult: result !== null,
    isLoading,
    hasError: error !== null,
  });

  const marginOfSafety = result
    ? resolveMarginOfSafetyDisplay({
        currentPrice: result.current_price,
        intrinsicValuePerShare: result.intrinsic_value_per_share,
        allowsMarketComparison: result.valuation_quality.allows_market_comparison,
      })
    : null;

  return (
    <div className="page-shell">
      <div className="shell-container max-w-5xl pb-20">
        <header className="page-header">
          <div>
            <p className="eyebrow mb-1.5">Company research overview</p>
            <h1 className="display-title">{initialTicker || "Research overview"}</h1>
          </div>
          <p className="page-deck">
            A live intrinsic-value case built from this company&rsquo;s own historical growth and margin, under
            the model&rsquo;s current default assumptions. Not a forecast, recommendation, or price target.
          </p>
        </header>

        <form onSubmit={handleSubmit} className="mb-8 flex flex-wrap items-end gap-3">
          <div className="w-full max-w-xs">
            <label htmlFor="overview-ticker" className="data-label mb-1.5 block">
              Ticker
            </label>
            <input
              id="overview-ticker"
              type="text"
              value={tickerInput}
              onChange={(event) => setTickerInput(event.target.value.toUpperCase())}
              placeholder="AAPL"
              maxLength={10}
              autoComplete="off"
              spellCheck={false}
              className="input-field px-4 py-3 font-mono text-base tracking-[.06em]"
            />
          </div>
          <button type="submit" disabled={isLoading} className="button-primary gap-2">
            {isLoading ? (
              <>
                <span className="spinner" aria-hidden="true" />
                Loading…
              </>
            ) : (
              "View"
            )}
          </button>
        </form>

        {error && (
          <div className={`${errorBannerTone(error.kind) === "warning" ? "status-warning" : "status-error"} mb-6`} role="alert">
            <strong className="block text-[var(--paper)]">{errorBannerHeadline(error.kind)}</strong>
            <span className="mt-1 block">{error.message}</span>
          </div>
        )}

        {resultState === "first-loading" && (
          <div className="space-y-4" aria-hidden="true">
            <div className="skeleton-block h-32 rounded-xl" />
            <div className="skeleton-block h-72 rounded-xl" />
          </div>
        )}
        {resultState === "first-loading" && (
          <p className="sr-only" role="status">
            Fetching financial statements and running the model…
          </p>
        )}

        {result && (
          <div className={resultState === "ready" ? "result-enter" : ""} aria-busy={isLoading}>
            {result.valuation_quality.level !== "ordinary" && (
              <div className="status-warning mb-6" role="alert">
                <strong className="block text-[var(--paper)]">
                  {result.valuation_quality.level === "diagnostic_only"
                    ? "Diagnostic model output — not an actionable share valuation"
                    : "Valuation interpretation caution"}
                </strong>
                <p className="mt-1">
                  The calculations and assumptions below remain visible, but market and peer comparisons are
                  withheld.
                </p>
                <ul className="mt-2 list-disc pl-5">
                  {result.valuation_quality.codes.map((code) => (
                    <li key={code}>{qualityIssueCopy(code)}</li>
                  ))}
                </ul>
                {result.valuation_quality.terminal_value_share_of_enterprise_value !== null && (
                  <p className="mt-2">
                    Discounted terminal value supplies{" "}
                    {(result.valuation_quality.terminal_value_share_of_enterprise_value * 100).toFixed(1)}% of
                    enterprise value.
                  </p>
                )}
              </div>
            )}

            <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div className="metric-panel p-5">
                <p className="data-label text-[var(--paper-dim)]">Market price</p>
                <p className="mt-3 font-mono text-2xl tracking-tight text-[var(--paper)]">
                  {result.current_price !== null ? formatPreciseCurrency(result.current_price) : "No observed price"}
                </p>
              </div>
              <div className="metric-panel p-5">
                <p className="data-label text-[var(--paper-dim)]">Intrinsic value / share</p>
                <p className="mt-3 font-mono text-2xl tracking-tight text-[var(--paper)]">
                  {formatPreciseCurrency(result.intrinsic_value_per_share)}
                </p>
              </div>
              <div className="metric-panel p-5">
                <p className="data-label text-[var(--paper-dim)]">Margin of safety</p>
                {marginOfSafety?.withheld ? (
                  <p className="mt-3 font-mono text-2xl tracking-tight text-[var(--paper-dim)]">Withheld</p>
                ) : (
                  <p
                    className={`mt-3 font-mono text-2xl tracking-tight ${
                      marginOfSafety?.hasMarginOfSafety ? "text-[var(--verdigris)]" : "text-[var(--paper)]"
                    }`}
                  >
                    {marginOfSafety?.deltaPct !== null && marginOfSafety?.deltaPct !== undefined
                      ? formatPercent(marginOfSafety.deltaPct)
                      : "—"}
                  </p>
                )}
                <p className="mt-2 text-xs text-[var(--paper-dim)]">{marginOfSafety?.label}</p>
              </div>
            </div>

            <div className="space-y-8">
              <ValuationSpectrum
                scenarios={result.scenarios}
                valuationQuality={result.valuation_quality}
                marketPrice={result.current_price}
                selectedScenario={selectedScenario}
                onSelectScenario={setSelectedScenario}
              />

              <SectorRelativeValuation
                ticker={result.ticker}
                sector={result.sector}
                priceToIntrinsicValue={result.price_to_intrinsic_value}
                sectorMedianPIV={result.sector_median_p_iv}
                sectorMedianUnavailableCode={result.sector_median_unavailable_code}
                sectorMedianSnapshot={result.sector_median_snapshot}
              />

              <UnavailablePanel
                title="Revenue history"
                reason="Historical revenue by period is not yet part of the live valuation response — only the forward forecast years are. This section will populate once that field exists."
              />

              <UnavailablePanel
                title="Filing evidence"
                reason="Point-in-time SEC filing evidence (accession numbers, concept-mapped facts) is not yet available through the live valuation response for any ticker, including AAPL."
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
