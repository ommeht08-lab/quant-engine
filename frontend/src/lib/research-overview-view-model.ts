import type { ResearchCaseFixture } from "./research-fixture.ts";
import { qualityIssueCopy, scenarioDisplayLabel, type ValuationQuality } from "./valuation-quality.ts";
import { sectorMedianProvenanceCaption, sectorMedianUnavailableCopy, type SectorMedianUnavailableCode } from "./sector-median-copy.ts";
import { resolveMarginOfSafetyDisplay } from "./overview-response.ts";

/**
 * The typed view model behind the shared research-overview presentation
 * (`components/research/ResearchOverviewContent.tsx` +
 * `ResearchShell.tsx`). Two adapters build this from two genuinely
 * different sources — `fixtureToResearchOverviewViewModel` (below) from
 * the static `AAPL_RESEARCH_FIXTURE`, and `liveEvaluationToResearchOverviewViewModel`
 * (below) from a real `/api/evaluate/{ticker}` response — and the
 * presentation layer renders EXCLUSIVELY from this type. A component
 * reading it has no way to tell which adapter produced a given value
 * unless `mode` is itself what's being displayed (the "UI prototype ·
 * fixture data" vs. "Live valuation" notice, and the sidebar status
 * line, are the only places provenance is actually meaningful to the
 * reader) — every other field is just data.
 *
 * `PanelDataStatus` is the same four-way classification the task asked
 * for at every panel: real data actually returned, a value this app
 * computed FROM real data, a value the quality policy has deliberately
 * withheld, or a field the live API does not provide at all (revenue
 * history, SEC evidence — for every ticker, not just non-AAPL ones).
 */
export type PanelDataStatus = "available" | "calculated" | "withheld" | "unavailable";

export interface ValueCell {
  status: PanelDataStatus;
  /** Display string when status is "available"/"calculated" — null otherwise. */
  display: string | null;
  /** Shown for "withheld"/"unavailable" — a true, specific reason, never generic "coming soon" copy. */
  reason?: string;
  /** Shown alongside `display` for "available"/"calculated" cells — supplied
   * by the adapter, not inferred from `status` alone, because "available"
   * means something different per mode: "At knowledge cutoff" for the
   * static fixture vs. "Live" for a real API response. A shared caption
   * of "Live" under a FIXTURE value would misrepresent it as a live quote
   * — exactly the provenance confusion this view model exists to avoid. */
  caption?: string;
}

export interface ScenarioCell {
  key: "bear" | "base" | "bull";
  label: string;
  isBase: boolean;
  /** True when the scenario is computable; false shows `reason` (the model's own invalid_reason) instead of a value. */
  isValid: boolean;
  valueDisplay: string | null;
  reason: string | null;
  assumptionsNote: string | null;
}

export interface ResearchOverviewViewModel {
  mode: "fixture" | "live";
  ticker: string;
  /** Company name is only real for the fixture — the live API has no
   * such field, so this is null in live mode rather than a fabricated
   * or ticker-derived guess. */
  companyName: string | null;
  sector: string;
  /** "NASDAQ"-style exchange text — fixture-only (not a verified live field). */
  exchange: string | null;
  monogram: string;
  deck: string;
  asOfLabel: string;
  asOfValue: string;

  qualityLevel: "ordinary" | "caution" | "diagnostic_only" | null;
  qualityCodes: string[];
  qualitySummary: { heading: string; label: string; caption: string; tone: "positive" | "warning" };
  qualityDetails: string[];

  marketPrice: ValueCell;
  intrinsicValue: ValueCell;
  marginOfSafety: ValueCell & { positive: boolean };

  scenarios: ScenarioCell[];

  revenuePanel:
    | { status: "available"; chart: ResearchCaseFixture["chart"] }
    | { status: "unavailable"; reason: string };

  sectorRelativePanel:
    | { status: "available"; ticker: string; sector: string; priceToIntrinsicValue: number; sectorMedianPIV: number; snapshotCaption: string | null }
    | { status: "withheld" | "unavailable"; reason: string };

  evidencePanel: { status: "available" } | { status: "unavailable"; reason: string };

  /** Small "model checks" key/value lines under the valuation panel —
   * "Balance check: Passed" for the fixture, live-appropriate facts
   * (forecast policy, CapEx source) for live data. */
  modelChecks: Array<{ label: string; value: string }>;
}

const REVENUE_UNAVAILABLE_REASON =
  "Historical revenue by period is not yet part of the live valuation response — only the forward forecast years are.";
const EVIDENCE_UNAVAILABLE_REASON =
  "Point-in-time SEC filing evidence (accession numbers, concept-mapped facts) is not yet available through the live valuation response for any ticker, including AAPL.";

function formatCurrency(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
}

function formatPercent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

/**
 * Builds the view model from the static AAPL fixture — a faithful,
 * mechanical mapping (every fixture field the prior hand-authored JSX
 * used has an exact home here) so the fixture's own rendered output is
 * unchanged after this refactor.
 */
export function fixtureToResearchOverviewViewModel(fixture: ResearchCaseFixture): ResearchOverviewViewModel {
  return {
    mode: "fixture",
    ticker: fixture.ticker,
    companyName: fixture.companyName,
    sector: fixture.sector,
    exchange: "NASDAQ",
    monogram: fixture.companyName.charAt(0),
    deck: "A five-year operating forecast and DCF built only from facts eligible by the selected knowledge cutoff.",
    asOfLabel: "Knowledge cutoff",
    asOfValue: fixture.knowledgeCutoff,
    qualityLevel: null,
    qualityCodes: [],
    qualitySummary: { heading: "Data quality", label: "Complete", caption: "0 unresolved required facts", tone: "positive" },
    qualityDetails: [],
    marketPrice: { status: "available", display: fixture.marketPrice, caption: "At knowledge cutoff" },
    intrinsicValue: { status: "available", display: fixture.intrinsicValue, caption: "Base case · per share" },
    marginOfSafety: { status: "available", display: fixture.marginOfSafety, positive: true, caption: "Intrinsic vs. market" },
    scenarios: fixture.scenarios.map((scenario) => ({
      key: scenario.name.toLowerCase() as "bear" | "base" | "bull",
      label: scenario.name,
      isBase: scenario.name === "Base",
      isValid: true,
      valueDisplay: scenario.value,
      reason: null,
      assumptionsNote: scenario.note,
    })),
    revenuePanel: { status: "available", chart: fixture.chart },
    sectorRelativePanel: { status: "unavailable", reason: "The prototype fixture does not model a sector-relative comparison." },
    evidencePanel: { status: "available" },
    modelChecks: [
      { label: "Balance check", value: "Passed" },
      { label: "Model", value: fixture.modelVersion },
      { label: "Dataset", value: "sec-gaap-v1.0" },
    ],
  };
}

// Mirrors the backend's ScenarioAssumptionsModel — kept local rather
// than importing a component-level type, since this adapter only needs
// the four numeric fields, not any rendering-specific shape.
interface LiveScenarioAssumptions {
  revenue_growth_rate: number;
  operating_margin: number;
  wacc: number;
  terminal_growth_rate: number;
}

interface LiveScenarioResult {
  name: string;
  assumptions: LiveScenarioAssumptions;
  intrinsic_value_per_share: number | null;
  is_valid: boolean;
  invalid_reason: string | null;
}

export interface LiveEvaluationResponse {
  ticker: string;
  current_price: number | null;
  intrinsic_value_per_share: number;
  sector: string;
  price_to_intrinsic_value: number | null;
  sector_median_p_iv: number | null;
  sector_median_unavailable_code: SectorMedianUnavailableCode | null;
  sector_median_snapshot: { generated_at: string; universe_size: number; tickers_used: number; sector_sample_count: number } | null;
  valuation_quality: ValuationQuality;
  forecast_method: "constant" | "maturation";
  capex_pct_revenue: number;
  capex_pct_revenue_source: "default" | "historical" | "fallback";
  scenarios: { bear: LiveScenarioResult; base: LiveScenarioResult; bull: LiveScenarioResult };
}

function liveScenarioCell(
  key: "bear" | "base" | "bull",
  scenario: LiveScenarioResult,
  quality: ValuationQuality
): ScenarioCell {
  return {
    key,
    label: scenarioDisplayLabel(key, quality),
    isBase: key === "base",
    isValid: scenario.is_valid,
    valueDisplay: scenario.is_valid && scenario.intrinsic_value_per_share !== null ? formatCurrency(scenario.intrinsic_value_per_share) : null,
    reason: scenario.is_valid ? null : scenario.invalid_reason,
    assumptionsNote: `Growth ${formatPercent(scenario.assumptions.revenue_growth_rate)} · Margin ${formatPercent(
      scenario.assumptions.operating_margin
    )} · WACC ${formatPercent(scenario.assumptions.wacc, 2)} · Terminal g. ${formatPercent(scenario.assumptions.terminal_growth_rate)}`,
  };
}

function liveCapexBasis(response: LiveEvaluationResponse): string {
  const ratio = formatPercent(response.capex_pct_revenue);
  if (response.capex_pct_revenue_source === "historical") {
    return `Historical annual average · ${ratio} of revenue`;
  }
  if (response.capex_pct_revenue_source === "fallback") {
    return `Flat default fallback · ${ratio} of revenue`;
  }
  return `Flat default · ${ratio} of revenue`;
}

/**
 * Builds the view model from a real `/api/evaluate/{ticker}` response.
 * Reads ONLY verified `EvaluationResponse` fields — no field here is
 * invented, and nothing from `AAPL_RESEARCH_FIXTURE` is ever consulted.
 * `fetchedAt` is this page's own clock (when the response arrived), not
 * a claim about the backend's own data vintage.
 */
export function liveEvaluationToResearchOverviewViewModel(
  response: LiveEvaluationResponse,
  fetchedAt: Date
): ResearchOverviewViewModel {
  const quality = response.valuation_quality;
  const allowsComparison = quality.allows_market_comparison;

  const marginOfSafety = resolveMarginOfSafetyDisplay({
    currentPrice: response.current_price,
    intrinsicValuePerShare: response.intrinsic_value_per_share,
    allowsMarketComparison: allowsComparison,
  });

  const marketPrice: ValueCell = response.current_price !== null
    ? { status: "available", display: formatCurrency(response.current_price), caption: "Returned by valuation API" }
    : { status: "unavailable", display: null, reason: "No observed market price was returned for this ticker." };

  const intrinsicValue: ValueCell = { status: "available", display: formatCurrency(response.intrinsic_value_per_share), caption: "Base model · per share" };

  const marginOfSafetyCell: ValueCell & { positive: boolean } = marginOfSafety.withheld
    ? {
        status: "withheld",
        display: null,
        reason: "Withheld — this valuation has quality-interpretation cautions (see below).",
        positive: false,
      }
    : marginOfSafety.deltaPct !== null
      ? {
          status: "calculated",
          display: formatPercent(marginOfSafety.deltaPct),
          positive: marginOfSafety.hasMarginOfSafety === true,
          caption: "Calculated from returned values",
        }
      : { status: "unavailable", display: null, reason: "No observed market price to compare against.", positive: false };

  const sectorRelativePanel: ResearchOverviewViewModel["sectorRelativePanel"] = !allowsComparison
    ? {
        status: "withheld",
        reason: sectorMedianUnavailableCopy("valuation_quality"),
      }
    : response.price_to_intrinsic_value !== null && response.sector_median_p_iv !== null
      ? {
          status: "available",
          ticker: response.ticker,
          sector: response.sector,
          priceToIntrinsicValue: response.price_to_intrinsic_value,
          sectorMedianPIV: response.sector_median_p_iv,
          snapshotCaption: response.sector_median_snapshot ? sectorMedianProvenanceCaption(response.sector_median_snapshot) : null,
        }
      : {
          status: response.sector_median_unavailable_code === "valuation_quality" ? "withheld" : "unavailable",
          reason: sectorMedianUnavailableCopy(response.sector_median_unavailable_code),
        };

  const qualityDetails: string[] = [];
  if (
    quality.codes.includes("high_terminal_value_concentration") &&
    quality.terminal_value_share_of_enterprise_value !== null
  ) {
    qualityDetails.push(
      `Discounted terminal value supplies ${formatPercent(quality.terminal_value_share_of_enterprise_value)} of enterprise value.`
    );
  }
  if (quality.codes.includes("extreme_observed_tax_rate") && quality.observed_effective_tax_rate !== null) {
    qualityDetails.push(`Observed effective tax rate is ${formatPercent(quality.observed_effective_tax_rate)}.`);
  }

  const qualitySummary = quality.level === "ordinary"
    ? { heading: "Valuation quality", label: "Ordinary", caption: "No valuation cautions", tone: "positive" as const }
    : {
        heading: "Valuation quality",
        label: quality.level === "caution" ? "Caution" : "Diagnostic only",
        caption: `${quality.codes.length} interpretation note${quality.codes.length === 1 ? "" : "s"}`,
        tone: "warning" as const,
      };

  return {
    mode: "live",
    ticker: response.ticker,
    companyName: null,
    sector: response.sector,
    exchange: null,
    monogram: response.ticker.charAt(0),
    deck: "A staged intrinsic-value case built from this company's historical growth and margin under the model's current assumptions. A transparent policy case, not a recommendation or price target.",
    asOfLabel: "Response received",
    asOfValue: fetchedAt.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }),
    qualityLevel: quality.level,
    qualityCodes: quality.codes.map((code) => qualityIssueCopy(code)),
    qualitySummary,
    qualityDetails,
    marketPrice,
    intrinsicValue,
    marginOfSafety: marginOfSafetyCell,
    scenarios: [
      liveScenarioCell("bear", response.scenarios.bear, quality),
      liveScenarioCell("base", response.scenarios.base, quality),
      liveScenarioCell("bull", response.scenarios.bull, quality),
    ],
    revenuePanel: { status: "unavailable", reason: REVENUE_UNAVAILABLE_REASON },
    sectorRelativePanel,
    evidencePanel: { status: "unavailable", reason: EVIDENCE_UNAVAILABLE_REASON },
    modelChecks: [
      {
        label: "Forecast policy",
        value: response.forecast_method === "maturation" ? "Staged five-year maturation" : "Constant forecast response",
      },
      { label: "CapEx basis", value: liveCapexBasis(response) },
    ],
  };
}
