import { formatPercent, formatPreciseCurrency } from "./format";
import { resolveThesisRailFields } from "@/lib/thesis-rail-fields";
import type { CaseKey, DCFScenarioSet } from "./ValuationSpectrum";
import { scenarioDisplayLabel, type ValuationQuality } from "@/lib/valuation-quality";

const CASE_ORDER: CaseKey[] = ["bear", "base", "bull"];

export interface ThesisRailProps {
  ticker: string;
  sector: string;
  marketPrice: number | null;
  scenarios: DCFScenarioSet;
  valuationQuality: ValuationQuality;
  revenueGrowthSource: "historical" | "custom";
  operatingMarginSource: "historical" | "custom";
  selectedScenario: CaseKey;
  onSelectScenario: (key: CaseKey) => void;
  isUpdating: boolean;
}

// The workspace's one persistent, signature surface — not a decorative
// sidebar. It always shows the analyst's current absolute-valuation
// position (selected scenario vs. market); it never shows the
// sector-relative comparison, which lives in its own section of the
// analysis column and is deliberately never conflated with this one.
//
// Every field below is resolved from the SAME selected scenario via
// `resolveThesisRailFields` — switching Bear/Base/Bull changes
// intrinsic value, revenue growth, operating margin, WACC, terminal
// growth, and the market delta together. Never mix in a different
// scenario's (e.g. the base case's) WACC or terminal growth.
export default function ThesisRail({
  ticker,
  sector,
  marketPrice,
  scenarios,
  valuationQuality,
  revenueGrowthSource,
  operatingMarginSource,
  selectedScenario,
  onSelectScenario,
  isUpdating,
}: ThesisRailProps) {
  const scenario = scenarios[selectedScenario];
  const fields = resolveThesisRailFields(
    scenario, valuationQuality.allows_market_comparison ? marketPrice : null
  );
  const deltaTone =
    fields.priceDelta === null
      ? "text-[var(--instrument-text)]"
      : fields.hasMarginOfSafety
        ? "text-[var(--verdigris)]"
        : "text-[var(--signal)]";

  return (
    <aside className="thesis-rail" aria-label="Valuation thesis" aria-busy={isUpdating}>
      <div className="thesis-rail-heading"><h2>Valuation thesis</h2><span>Absolute model</span></div>
      <div className="thesis-rail-byline">
        <div>
          <p className="thesis-rail-ticker">{ticker}</p>
          <p className="thesis-rail-sector">{sector}</p>
        </div>
        <div className="thesis-rail-source">
          {isUpdating && <span className="thesis-rail-updating">Updating</span>}
          <strong>{revenueGrowthSource === "historical" && operatingMarginSource === "historical" ? "Company history" : "Custom override"}</strong>
          <small>Recomputed this run</small>
        </div>
      </div>

      <div className="thesis-rail-primary">
        <p className="thesis-rail-row-label">{scenarioDisplayLabel(selectedScenario, valuationQuality)} model value / share</p>
        <p className="thesis-rail-value">
          {fields.intrinsicValue !== null ? formatPreciseCurrency(fields.intrinsicValue) : "Not computable"}
        </p>
        {fields.intrinsicValue !== null && (
          <>
            <p className={`thesis-rail-delta ${deltaTone}`}>
              {fields.priceDelta !== null
                ? `${fields.deltaIsPositive ? "+" : ""}${formatPreciseCurrency(fields.priceDelta)}`
                : "—"}
              {fields.priceDeltaPct !== null && (
                <span className="ml-1.5 text-[0.72rem] font-semibold opacity-80">
                  ({fields.deltaIsPositive ? "+" : ""}
                  {(fields.priceDeltaPct * 100).toFixed(1)}%)
                </span>
              )}
            </p>
            <p className="thesis-rail-delta-note">
              {fields.priceDelta !== null
                ? fields.deltaLabel
                : valuationQuality.allows_market_comparison
                  ? "No market price to compare"
                  : "Comparison withheld by valuation-quality cautions"}
            </p>
          </>
        )}
        {fields.intrinsicValue === null && (
          <p className="thesis-rail-delta-note">{scenario.invalid_reason}</p>
        )}
        {!valuationQuality.allows_market_comparison && (
          <p className="thesis-rail-delta-note">Market upside/downside is withheld by valuation-quality cautions.</p>
        )}
      </div>

      <div className="thesis-rail-metrics">
        <div className="thesis-rail-row thesis-rail-row--market">
          <span className="thesis-rail-row-label">Market price</span>
          <span className="thesis-rail-row-value">{formatPreciseCurrency(marketPrice)}</span>
        </div>
        <div className="thesis-rail-row">
          <span className="thesis-rail-row-label">Revenue growth</span>
          <span className="thesis-rail-row-value">{formatPercent(fields.revenueGrowthRate)}</span>
        </div>
        <div className="thesis-rail-row">
          <span className="thesis-rail-row-label">Operating margin</span>
          <span className="thesis-rail-row-value">{formatPercent(fields.operatingMargin)}</span>
        </div>
        <div className="thesis-rail-row">
          <span className="thesis-rail-row-label">WACC</span>
          <span className="thesis-rail-row-value">{formatPercent(fields.wacc, 2)}</span>
        </div>
        <div className="thesis-rail-row">
          <span className="thesis-rail-row-label">Terminal growth</span>
          <span className="thesis-rail-row-value">{formatPercent(fields.terminalGrowthRate)}</span>
        </div>
      </div>

      <div
        className="thesis-rail-selector"
        role="group"
        aria-label="Scenario (also controls the Valuation spectrum below)"
      >
        {CASE_ORDER.map((key) => (
          <button
            key={key}
            type="button"
            className="thesis-rail-selector-button"
            aria-pressed={selectedScenario === key}
            onClick={() => onSelectScenario(key)}
          >
            {scenarioDisplayLabel(key, valuationQuality)}
          </button>
        ))}
      </div>

      <p className="thesis-rail-footnote">
        Absolute valuation only — see Sector-relative valuation below for peer context.
      </p>
    </aside>
  );
}
