// Response types and a runtime validator for the Monte Carlo DCF API.
//
// The validator is a structural and internal-consistency gate: a response that
// is malformed, or whose counts do not add up (e.g. an above-market
// denominator that differs from the valid count, or histogram counts that do
// not sum to the valid count), is rejected instead of rendered.

export const EXPERIMENT_NAMES = ["growth", "margin", "wacc", "combined"] as const;
export type ExperimentName = (typeof EXPERIMENT_NAMES)[number];

export type ExperimentStatus = "complete" | "incomplete" | "refused";
export type OverallStatus = "complete" | "partial" | "incomplete";

export interface McStatistics {
  mean: number;
  p10: number;
  p50: number;
  p90: number;
  min: number;
  max: number;
}

export interface McAboveMarket {
  count: number;
  denominator: number;
  fraction: number;
}

export interface McHistogram {
  counts: number[];
  underflow: number;
  overflow: number;
}

export interface McExperiment {
  status: ExperimentStatus;
  target: number;
  sampled_inputs: string[];
  fixed_inputs_at_baseline: Record<string, number>;
  distributions: Record<string, Record<string, unknown>>;
  refusal_code: string | null;
  refusal_message: string | null;
  valid: number;
  attempted: number;
  failed_total: number;
  failed_by_code: Record<string, number>;
  rejected_proposals: number;
  negative_value_count: number;
  statistics: McStatistics | null;
  above_market: McAboveMarket | null;
  sampled_input_summary: Record<string, { min: number; mean: number; max: number }> | null;
  histogram: McHistogram | null;
}

export interface McHistory {
  status: "ok" | "refused";
  refusal_code?: string;
  refusal_message?: string;
  revenue_periods?: string[];
  yoy_growths?: number[];
  growth_center_cagr?: number;
  growth_sd?: number;
  margin_periods?: string[];
  margins?: number[];
  margin_center?: number;
  margin_sd?: number;
  n_growth?: number;
  n_margin?: number;
}

export interface McBaseline {
  intrinsic_value_per_share: number;
  wacc: number;
  wacc_pre_clamp: number;
  wacc_was_clamped: boolean;
  revenue_growth_rate: number;
  revenue_growth_rate_was_capped: boolean;
  operating_margin: number;
  [key: string]: unknown;
}

// Where each baseline assumption came from. Derived by the backend from the
// deterministic model's resolution path. Validated separately and tolerantly
// (see `readBaselineProvenance`): a missing or malformed block never hides the
// numerical results, it only removes the source details.
export interface McMarginPeriod {
  period: string;
  row_used: string;
  operating_profit: number | null;
  revenue: number | null;
  margin: number | null;
}

export interface McSpreadIllustration {
  n_observations: number;
  degrees_of_freedom: number;
  confidence: number;
  lower_multiplier: number;
  upper_multiplier: number;
  assumptions: string[];
  statement: string;
}

export interface McBaselineProvenance {
  schema: string;
  status: "ok";
  provenance_digest: string;
  currency: { reporting_currency: string | null; quote_currency_recorded: boolean; note: string };
  timing: {
    snapshot_captured_at: string | null;
    latest_statement_period_end: string | null;
    latest_statement_age_days_at_capture: number | null;
    statements: Record<string, { periods: string[]; latest_period: string | null }>;
    market_price: { observed_at: string | null; status: string; note: string };
    revenue_basis: string;
  };
  margin: {
    definition: string;
    rows_used: string[];
    mixed_rows: boolean;
    periods: McMarginPeriod[];
    periods_skipped: { period: string; reason: string }[];
    center: number | null;
    spread: number | null;
    n_observations: number;
    periods_reproduce_model_margin: boolean;
  };
  growth: {
    revenue_row: string | null;
    periods: string[] | null;
    cagr_uncapped: number | null;
    ceiling_applied_by_baseline: number;
    baseline_growth_rate: number | null;
    baseline_growth_was_capped: boolean;
    spread: number | null;
    n_observations: number;
  };
  tax_rate: {
    rate: number;
    source: string;
    is_fallback: boolean;
    fallback_reason: string | null;
    default_rate: number;
    basis: string;
    period: string | null;
  };
  cost_of_debt: {
    rate: number;
    source: string;
    is_fallback: boolean;
    fallback_reason: string | null;
    default_rate: number;
    basis: string;
    period: string | null;
    interest_expense_by_period: Record<string, number | null>;
  };
  beta: { value: number; source: string; is_fallback: boolean };
  risk_free_rate: { value: number; source: string; resolution_path_recorded: boolean; note: string };
  market_risk_premium: { value: number; fixed: boolean };
  terminal_growth_rate: { value: number; fixed: boolean };
  capex: {
    pct_of_revenue: number;
    source: string;
    fixed: boolean;
    basis: string;
    historical_ratio_for_context: { used_in_valuation: boolean; status: string; ratio: number | null; periods: string[] };
  };
  depreciation_and_amortization: { value: number; fixed: boolean; basis: string };
  working_capital: { value: number; fixed: boolean; basis: string };
  cash: {
    value: number | null;
    row_used: string | null;
    period: string | null;
    includes_short_term_investments: boolean;
    definition: string;
    excluded_short_term_investments: number | null;
    excluded_per_share: number | null;
  };
  debt: { value: number | null; row_used: string | null };
  wacc: {
    baseline_applied: number;
    baseline_pre_clamp: number;
    baseline_was_clamped: boolean;
    model_bounds: number[];
    sampled_interval: {
      lower: number;
      upper: number;
      requested_half_width: number;
      restricted_by_bounds: boolean;
      binding_constraints: string[];
    } | null;
    sampled_interval_refusal_code: string | null;
  };
  spread_illustration: { growth: McSpreadIllustration | null; margin: McSpreadIllustration | null };
}

export type BaselineProvenanceReading =
  | { kind: "ok"; value: McBaselineProvenance }
  | { kind: "unavailable" }
  | { kind: "absent" };

export interface McMeta {
  cache: "hit" | "miss";
  snapshot_cache: "hit" | "miss" | "waited";
  computed_at: string;
  served_at: string;
  /** Identity of the settings the service ran with; compared with the page's own (see monte-carlo-metadata). */
  metadata_id?: string;
  environment: Record<string, string | null>;
  timings_ms: Record<string, number | null>;
}

export interface MonteCarloResponse {
  schema: "mc-dcf-result-v1";
  model_version: string;
  status: OverallStatus;
  identity: {
    ticker: string;
    source: string;
    snapshot_id: string;
    snapshot_captured_at: string;
    statement_period_end: string | null;
    // Legacy field kept for digest compatibility: it holds the snapshot capture
    // time, NOT a quote time. Never display it as one; use baseline_provenance.timing.
    market_price_observed_at: string;
  };
  seed: number;
  config_hash: string;
  config: Record<string, unknown>;
  market_price: number;
  baseline: McBaseline;
  kernel_baseline_check: Record<string, unknown>;
  history: McHistory;
  experiments: Record<ExperimentName, McExperiment>;
  histogram_edges: number[] | null;
  histogram_edges_basis: "combined" | "pooled" | null;
  disclosures: { id: string; text: string }[];
  baseline_provenance?: unknown;
  result_digest: string;
  meta: McMeta;
}

export interface MonteCarloDescription {
  model_version: string;
  tickers: string[];
  experiments: string[];
  default_seed: number;
  config: Record<string, unknown>;
  config_hash: string;
  disclosures: { id: string; text: string }[];
  /** Hash of everything the controls show: allowlist, default seed, configuration and wording. */
  metadata_id: string;
}

type Obj = Record<string, unknown>;

function isObj(value: unknown): value is Obj {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function isNum(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}
function isStr(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}
function isStrOrNull(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function validDisclosures(value: unknown): boolean {
  return Array.isArray(value) && value.every((d) => isObj(d) && isStr(d.id) && isStr(d.text));
}

function validExperiment(value: unknown, binCount: number | null): value is McExperiment {
  if (!isObj(value)) return false;
  if (!["complete", "incomplete", "refused"].includes(value.status as string)) return false;
  if (![value.target, value.valid, value.attempted, value.failed_total, value.rejected_proposals, value.negative_value_count].every(isCount)) {
    return false;
  }
  const valid = value.valid as number;
  if (!Array.isArray(value.sampled_inputs) || !isObj(value.fixed_inputs_at_baseline) || !isObj(value.distributions)) return false;
  if (!isObj(value.failed_by_code) || !Object.values(value.failed_by_code).every(isCount)) return false;
  if (!isStrOrNull(value.refusal_code) || !isStrOrNull(value.refusal_message)) return false;

  if (value.status === "refused") {
    return (
      isStr(value.refusal_code) && valid === 0 && value.statistics === null &&
      value.above_market === null && value.histogram === null
    );
  }
  if (value.status === "complete" && valid !== value.target) return false;
  if (valid > (value.attempted as number)) return false;
  if (valid + (value.failed_total as number) !== value.attempted) return false;

  if (valid === 0) {
    return value.statistics === null && value.above_market === null && value.histogram === null;
  }
  const stats = value.statistics;
  if (!isObj(stats) || !["mean", "p10", "p50", "p90", "min", "max"].every((k) => isNum(stats[k]))) return false;
  const s = stats as unknown as McStatistics;
  if (!(s.min <= s.p10 && s.p10 <= s.p50 && s.p50 <= s.p90 && s.p90 <= s.max)) return false;

  const above = value.above_market;
  if (!isObj(above) || !isCount(above.count) || !isCount(above.denominator) || !isNum(above.fraction)) return false;
  if (above.denominator !== valid || above.count > valid) return false;
  if (Math.abs(above.fraction - above.count / valid) > 1e-12) return false;

  const histogram = value.histogram;
  if (histogram === null) return binCount === null;
  if (binCount === null) return false;
  if (!isObj(histogram) || !Array.isArray(histogram.counts) || histogram.counts.length !== binCount) return false;
  if (!histogram.counts.every(isCount) || !isCount(histogram.underflow) || !isCount(histogram.overflow)) return false;
  const total = (histogram.counts as number[]).reduce((a, b) => a + b, 0) + histogram.underflow + histogram.overflow;
  return total === valid;
}

export function isMonteCarloResponse(value: unknown): value is MonteCarloResponse {
  if (!isObj(value) || value.schema !== "mc-dcf-result-v1") return false;
  if (!["complete", "partial", "incomplete"].includes(value.status as string)) return false;
  if (!isStr(value.model_version) || !isCount(value.seed) || !isStr(value.config_hash) || !isStr(value.result_digest)) return false;
  if (!isNum(value.market_price) || !isObj(value.config) || !validDisclosures(value.disclosures)) return false;

  const identity = value.identity;
  if (!isObj(identity) || !isStr(identity.ticker) || !isStr(identity.snapshot_id) || !isStr(identity.snapshot_captured_at)) return false;
  if (!isStr(identity.market_price_observed_at) || !isStrOrNull(identity.statement_period_end ?? null)) return false;

  const baseline = value.baseline;
  if (!isObj(baseline) || !["intrinsic_value_per_share", "wacc", "revenue_growth_rate", "operating_margin"].every((k) => isNum(baseline[k]))) return false;
  if (typeof baseline.wacc_was_clamped !== "boolean" || typeof baseline.revenue_growth_rate_was_capped !== "boolean") return false;

  const history = value.history;
  if (!isObj(history) || (history.status !== "ok" && history.status !== "refused")) return false;

  const meta = value.meta;
  if (!isObj(meta) || (meta.cache !== "hit" && meta.cache !== "miss") || !isStr(meta.computed_at)) return false;
  if (meta.metadata_id !== undefined && !isStr(meta.metadata_id)) return false;

  const edges = value.histogram_edges;
  let binCount: number | null = null;
  if (edges !== null) {
    if (!Array.isArray(edges) || edges.length < 2 || !edges.every(isNum)) return false;
    if (!edges.every((edge, i) => i === 0 || edge > (edges[i - 1] as number))) return false;
    binCount = edges.length - 1;
  }
  const basis = value.histogram_edges_basis;
  if (edges === null ? basis !== null : basis !== "combined" && basis !== "pooled") return false;

  const experiments = value.experiments;
  if (!isObj(experiments)) return false;
  if (Object.keys(experiments).length !== EXPERIMENT_NAMES.length) return false;
  return EXPERIMENT_NAMES.every((name) => validExperiment(experiments[name], binCount));
}

export function isMonteCarloDescription(value: unknown): value is MonteCarloDescription {
  return (
    isObj(value) &&
    isStr(value.model_version) &&
    Array.isArray(value.tickers) && value.tickers.length > 0 && value.tickers.every(isStr) &&
    isCount(value.default_seed) &&
    isObj(value.config) &&
    isStr(value.config_hash) &&
    isStr(value.metadata_id) &&
    validDisclosures(value.disclosures)
  );
}

function isNumOrNull(value: unknown): value is number | null {
  return value === null || isNum(value);
}

function isBool(value: unknown): value is boolean {
  return typeof value === "boolean";
}

function isStrArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((v) => typeof v === "string");
}

function validIllustration(value: unknown): boolean {
  return (
    value === null ||
    (isObj(value) && isCount(value.n_observations) && isCount(value.degrees_of_freedom) && isNum(value.confidence) &&
      isNum(value.lower_multiplier) && isNum(value.upper_multiplier) && isStrArray(value.assumptions) && isStr(value.statement))
  );
}

/** Structural check of the fields the interface reads. Anything else in the block is ignored. */
export function isBaselineProvenance(value: unknown): value is McBaselineProvenance {
  if (!isObj(value) || value.status !== "ok" || !isStr(value.schema) || !isStr(value.provenance_digest)) return false;
  const { timing, margin, growth, tax_rate: tax, cost_of_debt: debt, beta, risk_free_rate: rf, capex, cash, wacc } = value as Obj;

  if (!isObj(value.currency) || !isBool(value.currency.quote_currency_recorded)) return false;
  if (!isObj(timing) || !isStrOrNull(timing.snapshot_captured_at) || !isStrOrNull(timing.latest_statement_period_end)) return false;
  if (!isNumOrNull(timing.latest_statement_age_days_at_capture) || !isObj(timing.statements) || !isStr(timing.revenue_basis)) return false;
  const price = timing.market_price;
  if (!isObj(price) || !isStrOrNull(price.observed_at) || !isStr(price.status) || !isStr(price.note)) return false;

  if (!isObj(margin) || !isStrArray(margin.rows_used) || !isBool(margin.mixed_rows) || !isBool(margin.periods_reproduce_model_margin)) return false;
  if (!Array.isArray(margin.periods) || !margin.periods.every((p) => isObj(p) && isStr(p.period) && isStr(p.row_used) && isNumOrNull(p.margin))) return false;
  if (!Array.isArray(margin.periods_skipped) || !margin.periods_skipped.every((p) => isObj(p) && isStr(p.period) && isStr(p.reason))) return false;
  if (!isNumOrNull(margin.center) || !isNumOrNull(margin.spread) || !isCount(margin.n_observations)) return false;

  if (!isObj(growth) || !isNumOrNull(growth.cagr_uncapped) || !isNumOrNull(growth.spread) || !isBool(growth.baseline_growth_was_capped)) return false;
  if (!isNum(growth.ceiling_applied_by_baseline) || !isCount(growth.n_observations)) return false;

  if (!isObj(tax) || !isNum(tax.rate) || !isStr(tax.source) || !isBool(tax.is_fallback) || !isStrOrNull(tax.fallback_reason) || !isNum(tax.default_rate)) return false;
  if (!isObj(debt) || !isNum(debt.rate) || !isStr(debt.source) || !isBool(debt.is_fallback) || !isStrOrNull(debt.fallback_reason) || !isNum(debt.default_rate)) return false;
  if (!isObj(debt.interest_expense_by_period)) return false;
  if (!isObj(beta) || !isNum(beta.value) || !isBool(beta.is_fallback)) return false;
  if (!isObj(rf) || !isNum(rf.value) || !isBool(rf.resolution_path_recorded)) return false;
  for (const key of ["market_risk_premium", "terminal_growth_rate", "depreciation_and_amortization", "working_capital"]) {
    const item = value[key];
    if (!isObj(item) || !isNum(item.value) || !isBool(item.fixed)) return false;
  }

  if (!isObj(capex) || !isNum(capex.pct_of_revenue) || !isBool(capex.fixed) || !isStr(capex.source)) return false;
  const context = capex.historical_ratio_for_context;
  if (!isObj(context) || !isBool(context.used_in_valuation) || !isNumOrNull(context.ratio) || !isStrArray(context.periods)) return false;

  if (!isObj(cash) || !isNumOrNull(cash.value) || !isStrOrNull(cash.row_used) || !isBool(cash.includes_short_term_investments)) return false;
  if (!isStr(cash.definition) || !isNumOrNull(cash.excluded_short_term_investments) || !isNumOrNull(cash.excluded_per_share)) return false;
  if (!isObj(value.debt) || !isNumOrNull(value.debt.value)) return false;

  if (!isObj(wacc) || !isNum(wacc.baseline_applied) || !isNum(wacc.baseline_pre_clamp) || !isBool(wacc.baseline_was_clamped)) return false;
  if (!Array.isArray(wacc.model_bounds) || !wacc.model_bounds.every(isNum)) return false;
  const interval = wacc.sampled_interval;
  if (interval !== null) {
    if (!isObj(interval) || !isNum(interval.lower) || !isNum(interval.upper) || !isBool(interval.restricted_by_bounds) || !isStrArray(interval.binding_constraints)) return false;
  }

  const illustration = value.spread_illustration;
  return isObj(illustration) && validIllustration(illustration.growth) && validIllustration(illustration.margin);
}

/**
 * How to treat a response's baseline provenance. A block that is absent (older
 * cached result), explicitly unavailable, or malformed never blocks the
 * numerical results: the interface just says the source details are missing.
 */
export function readBaselineProvenance(response: Pick<MonteCarloResponse, "baseline_provenance">): BaselineProvenanceReading {
  const block = response.baseline_provenance;
  if (block === undefined || block === null) return { kind: "absent" };
  return isBaselineProvenance(block) ? { kind: "ok", value: block } : { kind: "unavailable" };
}
