// Presentation rules for the Monte Carlo panel, kept pure so they can be tested.
// Every count shown to a reader is the actual valid count from the response:
// no target is ever hard-coded as if it had been reached.

import type { ExperimentName, McBaselineProvenance, McExperiment, McSpreadIllustration } from "./monte-carlo-types.ts";

export const ABOVE_MARKET_LABEL = "Simulated valuations above market price under these assumptions";

/**
 * The v1 margin definition, named wherever a margin is shown. Yahoo's EBIT row is a provider-defined
 * figure; it is not a normalized or adjusted operating-earnings measure, and neither is the Operating
 * Income row used only as a separately labeled offline comparison.
 */
export const MARGIN_BASIS_LABEL = "Yahoo EBIT basis";

export const EXPERIMENT_LABELS: Record<ExperimentName, string> = {
  growth: "Growth only",
  margin: `Operating margin only (${MARGIN_BASIS_LABEL})`,
  wacc: "Discount rate (WACC) only",
  combined: "All three together",
};

const REFUSAL_COPY: Record<string, string> = {
  input_incomplete: "The company's statements were not complete enough to run the deterministic valuation.",
  insufficient_history: "Too few annual periods to estimate a spread from this company's own history.",
  irregular_fiscal_spacing: "The reported periods are not roughly one year apart, so year-over-year spread is not defined.",
  history_alignment_mismatch: "The growth and margin histories could not be aligned with the deterministic model.",
  nonpositive_revenue: "A historical revenue was not positive, so growth is undefined.",
  inadmissible_distribution: "Almost none of the historical distribution lies inside the allowed range, so it cannot be sampled reliably.",
  degenerate_outside_bounds: "The historical spread is zero and its center lies outside the allowed range.",
  wacc_interval_empty: "No usable discount-rate interval remains after applying the model's bounds.",
  kernel_baseline_mismatch: "The simulation engine did not reproduce the deterministic valuation, so no results are shown.",
};

export function refusalCopy(code: string | null): string {
  return (code && REFUSAL_COPY[code]) || "This experiment could not be run.";
}

const FAILURE_COPY: Record<string, string> = {
  growth_le_neg_100: "growth at or below −100%",
  nonfinite_projection: "projection not finite",
  wacc_not_above_terminal_growth: "WACC not above terminal growth",
  nonfinite_terminal_value: "terminal value not finite",
  nonfinite_discounting: "discounting not finite",
  nonfinite_equity: "equity value not finite",
};

export function failureSummary(experiment: Pick<McExperiment, "failed_by_code">): string[] {
  return Object.entries(experiment.failed_by_code)
    .filter(([, count]) => count > 0)
    .map(([code, count]) => `${FAILURE_COPY[code] ?? code}: ${count.toLocaleString("en-US")}`);
}

/** "9,450 valid valuations (target 10,000)" — the true count, always. */
export function validCountText(experiment: Pick<McExperiment, "valid" | "target" | "status">): string {
  const valid = experiment.valid.toLocaleString("en-US");
  const target = experiment.target.toLocaleString("en-US");
  if (experiment.status === "refused") return "Not run";
  return experiment.status === "incomplete"
    ? `Incomplete: ${valid} valid valuations (target ${target})`
    : `${valid} valid valuations`;
}

export interface AboveMarketDisplay {
  label: string;
  percent: string;
  detail: string;
  sentence: string;
}

/** The comparison sentence, with the actual valid denominator. `null` when nothing was simulated. */
export function describeAboveMarket(
  experiment: Pick<McExperiment, "above_market">,
): AboveMarketDisplay | null {
  const above = experiment.above_market;
  if (above === null || above.denominator === 0) return null;
  const percent = `${(above.fraction * 100).toFixed(1)}%`;
  const detail = `${above.count.toLocaleString("en-US")} of ${above.denominator.toLocaleString("en-US")}`;
  return { label: ABOVE_MARKET_LABEL, percent, detail, sentence: `${ABOVE_MARKET_LABEL}: ${percent} (${detail}).` };
}

export interface HistogramBar {
  x0: number;
  x1: number;
  count: number;
  /** Height as a fraction of the tallest bin, in [0, 1]. */
  height: number;
}

export function histogramBars(edges: number[], counts: number[]): HistogramBar[] {
  if (edges.length !== counts.length + 1 || counts.length === 0) return [];
  const tallest = Math.max(...counts, 1);
  return counts.map((count, i) => ({ x0: edges[i], x1: edges[i + 1], count, height: count / tallest }));
}

export type MarkerPlacement =
  | { inRange: true; fraction: number }
  | { inRange: false; side: "below" | "above" };

/** Where a value falls along the shared histogram axis; out-of-range values are flagged, not clamped. */
export function markerPlacement(value: number, edges: number[]): MarkerPlacement | null {
  if (edges.length < 2 || !Number.isFinite(value)) return null;
  const low = edges[0];
  const high = edges[edges.length - 1];
  if (value < low) return { inRange: false, side: "below" };
  if (value > high) return { inRange: false, side: "above" };
  return { inRange: true, fraction: (value - low) / (high - low) };
}

export function interquantileWidth(experiment: Pick<McExperiment, "statistics">): number | null {
  const stats = experiment.statistics;
  return stats === null ? null : stats.p90 - stats.p10;
}

export const SAFE_SEED_PATTERN = /^(0|[1-9][0-9]{0,15})$/;

/** A seed typed by a user: a canonical non-negative integer up to 2^53 - 1, or null. */
export function parseSeedInput(raw: string): number | null {
  const trimmed = raw.trim();
  if (!SAFE_SEED_PATTERN.test(trimmed)) return null;
  const seed = Number(trimmed);
  return Number.isSafeInteger(seed) ? seed : null;
}

export interface MonteCarloRequestError {
  kind: "input" | "busy" | "limited" | "unavailable" | "request";
  message: string;
}

function detailOf(body: unknown): { code: string | null; message: string | null } {
  if (typeof body !== "object" || body === null) return { code: null, message: null };
  const record = body as Record<string, unknown>;
  const detail = record.detail;
  if (typeof detail === "object" && detail !== null) {
    const d = detail as Record<string, unknown>;
    return {
      code: typeof d.code === "string" ? d.code : null,
      message: typeof d.message === "string" ? d.message : null,
    };
  }
  return {
    code: typeof record.code === "string" ? record.code : null,
    message: typeof detail === "string" ? detail : typeof record.error === "string" ? record.error : null,
  };
}

/** Plain-language error for a failed simulation request (backend or proxy body). */
export function describeRequestError(status: number, body: unknown): MonteCarloRequestError {
  const { code, message } = detailOf(body);
  if (status === 409) {
    return { kind: "busy", message: "The same simulation is already being computed. Try again in a few seconds." };
  }
  if (status === 429) {
    return { kind: "limited", message: message ?? "Too many simulation requests. Please try again in a few minutes." };
  }
  if (status === 404 && code === "ticker_not_supported") {
    return { kind: "input", message: "That company is not enabled for this simulation." };
  }
  if (status === 400 || status === 422) {
    return { kind: "input", message: message ?? "The simulation could not be run for that input." };
  }
  if (status === 502 || status === 503 || status === 504) {
    return { kind: "unavailable", message: message ?? "The simulation service is unavailable. Please try again shortly." };
  }
  return { kind: "request", message: message ?? `Simulation request failed (HTTP ${status}).` };
}

export interface AxisDomain {
  low: number;
  high: number;
}

/**
 * The displayed x-axis: the shared histogram range, stretched (with a little
 * padding) to include reference values such as the market price when doing so
 * keeps the whole axis within `maxSpanFactor` times the histogram range.
 * Beyond that, the bars would shrink to a sliver, so the value is left off the
 * axis and reported in words instead (see `markerPlacement`).
 */
export function axisDomain(edges: number[], references: number[], maxSpanFactor = 3): AxisDomain {
  const first = edges[0];
  const last = edges[edges.length - 1];
  const span = last - first;
  let low = first;
  let high = last;
  for (const value of references) {
    if (!Number.isFinite(value)) continue;
    const nextLow = Math.min(low, value);
    const nextHigh = Math.max(high, value);
    if (nextHigh - nextLow <= span * maxSpanFactor) {
      low = nextLow;
      high = nextHigh;
    }
  }
  const padding = span * 0.03;
  return { low: low < first ? low - padding : first, high: high > last ? high + padding : last };
}

// ---------------------------------------------------- baseline sources ----

const pct = (value: number, digits = 2): string => `${(value * 100).toFixed(digits)}%`;

function usdCompact(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  return `${sign}$${abs.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

const FALLBACK_REASON_COPY: Record<string, string> = {
  interest_expense_unavailable_or_zero: "interest expense is not reported (or is zero) for the latest annual period",
  total_debt_unavailable_or_zero: "total debt is not reported (or is zero)",
  pretax_income_unavailable_or_zero: "pretax income is not reported (or is zero) for the latest annual period",
  tax_provision_unavailable: "the tax provision is not reported for the latest annual period",
  effective_rate_outside_0_to_1: "the effective rate computed from the statements is outside 0%–100%",
};

export function fallbackReasonText(code: string | null): string {
  return (code && FALLBACK_REASON_COPY[code]) || "the statements did not provide a usable figure";
}

const WACC_CONSTRAINT_COPY: Record<string, string> = {
  model_lower_bound: "the model's lower WACC bound",
  model_upper_bound: "the model's upper WACC bound",
  terminal_growth_spread: "the minimum spread above terminal growth",
};

const SKIPPED_PERIOD_COPY: Record<string, string> = {
  revenue_missing_or_nonpositive: "revenue missing or not positive",
  operating_profit_row_missing: "no EBIT or Operating Income value",
};

/** A notice when the margin did not come purely from the Yahoo EBIT row (the v1 default). */
export function marginBasisNotice(rows: string[], mixed: boolean): string | null {
  if (rows.length === 1 && rows[0] === "EBIT" && !mixed) return null;
  if (rows.length === 0) return "No margin row was available for the statement periods.";
  return (
    `The margin did not come purely from Yahoo's EBIT row: ${marginRowsText(rows, mixed)}. ` +
    "The v1 default is the EBIT row, falling back to Operating Income only for a period with no EBIT value, so the margin center, spread and baseline mix definitions here."
  );
}

export function marginRowsText(rows: string[], mixed: boolean): string {
  if (rows.length === 0) return "none available";
  if (!mixed) return rows[0];
  return `${rows.join(" and ")} (rows differ by period)`;
}

/** The price-time sentence. Never substitutes the snapshot capture time for an absent quote time. */
export function describeQuoteTime(observedAt: string | null | undefined, formatDateTime: (iso: string) => string): string {
  if (observedAt === undefined) return "quote time unknown";
  return observedAt ? `quoted ${formatDateTime(observedAt)}` : "quote time not provided by the data source";
}

export function statementAgeText(days: number | null): string | null {
  if (days === null) return null;
  return `${days.toLocaleString("en-US")} days (${(days / 30.44).toFixed(1)} months) before capture`;
}

/** The spread-uncertainty illustration, stated under its assumptions and never as a guarantee. */
export function spreadIllustrationText(label: string, illustration: McSpreadIllustration | null): string | null {
  if (illustration === null) return null;
  const level = Math.round(illustration.confidence * 100);
  return (
    `Illustration only: if the ${illustration.n_observations} ${label} observations were independent draws from one normal ` +
    `distribution, a ${level}% interval for the true spread would run from ${illustration.lower_multiplier.toFixed(2)}x to ` +
    `${illustration.upper_multiplier.toFixed(2)}x the estimate. Real annual history can be autocorrelated, skewed or shifting, ` +
    `so this is not a distribution-free confidence statement.`
  );
}

export interface ProvenanceItem {
  label: string;
  value: string;
  /**
   * "fallback": a documented default replaced a missing figure. "fixed": a constant, not company data.
   * "caution": something a reader should weigh (an exclusion, a clamp, an unknown time).
   */
  flag?: "fallback" | "fixed" | "caution";
  detail?: string;
}

export interface ProvenanceTable {
  caption: string;
  columns: string[];
  rows: string[][];
}

export interface ProvenanceSection {
  id: "margin" | "financing" | "fixed" | "bridge" | "wacc" | "timing";
  title: string;
  items: ProvenanceItem[];
  table?: ProvenanceTable;
}

function marginSection(p: McBaselineProvenance): ProvenanceSection {
  const m = p.margin;
  const items: ProvenanceItem[] = [
    {
      label: "Statement row",
      value: marginRowsText(m.rows_used, m.mixed_rows),
      detail: `${m.definition} v1 default: Yahoo's EBIT row. It is not a normalized or adjusted operating-earnings figure, and neither is the Operating Income row.`,
    },
    {
      label: "Center and spread",
      value: `${m.center === null ? "n/a" : pct(m.center)} mean · ${m.spread === null ? "no spread (history refused)" : `${pct(m.spread)} sample SD`} · ${m.n_observations} periods`,
    },
  ];
  if (m.rows_used.includes("EBIT") || m.mixed_rows) {
    items.push({
      label: "About the EBIT row",
      value: "Can include non-operating and special items, so it can differ from the Operating Income row. The margin center, spread and the ordering of single-input widths can change materially with the row used.",
    });
  }
  if (!m.periods_reproduce_model_margin) {
    items.push({ label: "Check", value: "These periods do not reproduce the deterministic model's margin; treat the listing as unreliable.", flag: "caution" });
  }
  if (m.periods_skipped.length) {
    items.push({
      label: "Periods not used",
      value: m.periods_skipped.map((s) => `${s.period} (${SKIPPED_PERIOD_COPY[s.reason] ?? s.reason})`).join("; "),
    });
  }
  return {
    id: "margin",
    title: `Operating margin (${MARGIN_BASIS_LABEL})`,
    items,
    table: {
      caption: "Annual margins used for the center and spread",
      columns: ["Fiscal period", "Row used", "Margin"],
      rows: m.periods.map((row) => [row.period, row.row_used, row.margin === null ? "n/a" : pct(row.margin)]),
    },
  };
}

function financingSection(p: McBaselineProvenance): ProvenanceSection {
  const tax = p.tax_rate;
  const debt = p.cost_of_debt;
  const items: ProvenanceItem[] = [
    {
      label: "Tax rate",
      value: pct(tax.rate),
      flag: tax.is_fallback ? "fallback" : undefined,
      detail: tax.is_fallback
        ? `Default ${pct(tax.default_rate, 0)} applied because ${fallbackReasonText(tax.fallback_reason)}.`
        : `${tax.basis}${tax.period ? ` Period ${tax.period}.` : ""}`,
    },
    {
      label: "Cost of debt",
      value: pct(debt.rate),
      flag: debt.is_fallback ? "fallback" : undefined,
      detail: debt.is_fallback
        ? `Default ${pct(debt.default_rate, 0)} applied because ${fallbackReasonText(debt.fallback_reason)}. It affects the discount rate only.`
        : `${debt.basis}${debt.period ? ` Period ${debt.period}.` : ""}`,
    },
    {
      label: "Beta",
      value: p.beta.value.toFixed(2),
      flag: p.beta.is_fallback ? "fallback" : undefined,
      detail: p.beta.is_fallback ? "Provider beta unavailable; the model default applied." : "As reported by the data provider.",
    },
    {
      label: "Risk-free rate",
      value: pct(p.risk_free_rate.value),
      detail: p.risk_free_rate.resolution_path_recorded
        ? p.risk_free_rate.source
        : `Lookup path unknown. ${p.risk_free_rate.note}`,
    },
  ];
  return { id: "financing", title: "Tax, cost of debt and discount-rate inputs", items };
}

function fixedSection(p: McBaselineProvenance): ProvenanceSection {
  const capexContext = p.capex.historical_ratio_for_context;
  return {
    id: "fixed",
    title: "Fixed assumptions (not company data, not varied)",
    items: [
      {
        label: "Capital expenditure",
        value: `${pct(p.capex.pct_of_revenue, 1)} of revenue`,
        flag: "fixed",
        detail:
          capexContext.status === "derived" && capexContext.ratio !== null
            ? `For context only: this company's own average over ${capexContext.periods.length} annual periods is ${pct(capexContext.ratio, 1)}. It is not used.`
            : p.capex.basis,
      },
      { label: "Depreciation and amortization", value: `${pct(p.depreciation_and_amortization.value, 1)} of revenue`, flag: "fixed", detail: p.depreciation_and_amortization.basis },
      { label: "Working capital", value: `${pct(p.working_capital.value, 1)} of the year's revenue change`, flag: "fixed", detail: p.working_capital.basis },
      { label: "Terminal growth", value: pct(p.terminal_growth_rate.value), flag: "fixed" },
      { label: "Equity risk premium", value: pct(p.market_risk_premium.value), flag: "fixed" },
    ],
  };
}

function bridgeSection(p: McBaselineProvenance): ProvenanceSection {
  const cash = p.cash;
  const items: ProvenanceItem[] = [
    {
      label: "Cash",
      value: cash.value === null ? "not available" : usdCompact(cash.value),
      detail: `${cash.row_used ? `Row: ${cash.row_used}. ` : ""}${cash.definition}`,
    },
  ];
  if (cash.excluded_short_term_investments !== null) {
    items.push({
      label: "Short-term investments not counted",
      value: `${usdCompact(cash.excluded_short_term_investments)}${cash.excluded_per_share !== null ? ` (about $${cash.excluded_per_share.toFixed(2)} per share)` : ""}`,
      flag: "caution",
      detail: "Excluded from the equity bridge, so every simulated value per share is lower by this amount than if they were counted.",
    });
  } else if (!cash.includes_short_term_investments) {
    items.push({ label: "Short-term investments", value: "Not reported separately in the statements, so any exclusion is unknown." });
  }
  items.push({ label: "Total debt", value: p.debt.value === null ? "not available" : usdCompact(p.debt.value), detail: p.debt.row_used ? `Row: ${p.debt.row_used}.` : undefined });
  return { id: "bridge", title: "Equity bridge", items };
}

function waccSection(p: McBaselineProvenance): ProvenanceSection {
  const w = p.wacc;
  const [lowBound, highBound] = w.model_bounds;
  const interval = w.sampled_interval;
  const items: ProvenanceItem[] = [
    {
      label: "Baseline WACC",
      value: w.baseline_was_clamped ? `${pct(w.baseline_applied)} (clamped from ${pct(w.baseline_pre_clamp)})` : pct(w.baseline_applied),
      flag: w.baseline_was_clamped ? "caution" : undefined,
      detail: w.baseline_was_clamped
        ? `The CAPM rate fell outside the model's ${pct(lowBound, 0)}–${pct(highBound, 0)} bounds and was clamped.`
        : `Within the model's ${pct(lowBound, 0)}–${pct(highBound, 0)} bounds; not clamped.`,
    },
  ];
  if (interval === null) {
    items.push({ label: "Sampled interval", value: "none (WACC experiment refused)" });
  } else {
    items.push({
      label: "Sampled interval",
      value: `${pct(interval.lower)} – ${pct(interval.upper)}`,
      flag: interval.restricted_by_bounds ? "caution" : undefined,
      detail: interval.restricted_by_bounds
        ? `Narrower than the requested ±${pct(interval.requested_half_width)} around the baseline because of ${interval.binding_constraints.map((c) => WACC_CONSTRAINT_COPY[c] ?? c).join(" and ")}.`
        : `The requested ±${pct(interval.requested_half_width)} around the baseline, unrestricted.`,
    });
  }
  return { id: "wacc", title: "Discount rate: clamping versus the sampled range", items };
}

function timingSection(p: McBaselineProvenance, formatDateTime: (iso: string) => string): ProvenanceSection {
  const t = p.timing;
  const age = statementAgeText(t.latest_statement_age_days_at_capture);
  const periods = t.statements["income_statement"]?.periods ?? [];
  return {
    id: "timing",
    title: "Dates",
    items: [
      { label: "Latest annual statements", value: t.latest_statement_period_end ?? "unknown", detail: age ?? undefined },
      { label: "Annual periods in the income statement", value: periods.length ? periods.join(", ") : "none" },
      { label: "Snapshot captured", value: t.snapshot_captured_at ? formatDateTime(t.snapshot_captured_at) : "not recorded" },
      {
        label: "Market price observed",
        value: t.market_price.observed_at ? formatDateTime(t.market_price.observed_at) : "Not provided by the data source",
        flag: t.market_price.observed_at ? undefined : "caution",
        detail: t.market_price.observed_at ? undefined : t.market_price.note,
      },
      { label: "Revenue basis", value: t.revenue_basis },
      {
        label: "Currency",
        value: p.currency.reporting_currency ?? "unknown",
        detail: p.currency.quote_currency_recorded ? undefined : p.currency.note,
      },
    ],
  };
}

/** The "where the baseline comes from" content, as plain data the panel renders one-to-one. */
export function buildProvenanceSections(
  p: McBaselineProvenance,
  formatDateTime: (iso: string) => string,
): ProvenanceSection[] {
  return [marginSection(p), financingSection(p), fixedSection(p), bridgeSection(p), waccSection(p), timingSection(p, formatDateTime)];
}
