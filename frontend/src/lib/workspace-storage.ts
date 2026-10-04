import type { EvaluationResponse } from "./workspace-result";
import { isHistoricalFinancials } from "./historical-financials.ts";
import { isMarketHistoryResponse, type MarketHistoryResponse } from "./market-history.ts";

export const SAVED_WORKSPACE_KEY = "valuation-workspace-result-v1";
export interface SavedWorkspace {
  result: EvaluationResponse;
  marketHistory: MarketHistoryResponse | null;
  selectedScenario: "base" | "bear" | "bull";
  savedAt: string;
}
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const nullableNumber = (v: unknown) => v === null || finite(v);
const nullableString = (v: unknown) => v === null || typeof v === "string";
const indexIn = (v: unknown, length: number) => finite(v) && Number.isInteger(v) && v >= 0 && v < length;
export function isWorkspaceResult(value: unknown): value is EvaluationResponse {
  const r = value as EvaluationResponse | null;
  return Boolean(r && typeof r.ticker === "string" && /^[A-Z0-9][A-Z0-9.-]{0,9}$/.test(r.ticker)
    && r.forecast_method === "maturation" && Array.isArray(r.projected_free_cash_flows) && r.projected_free_cash_flows.length > 0
    && Array.isArray(r.forecast_path) && r.forecast_path.length === r.projected_free_cash_flows.length
    && r.projected_free_cash_flows.every(row => [row?.year,row?.revenue,row?.ebit,row?.nopat,row?.da,row?.capex,row?.change_in_nwc,row?.fcf].every(finite))
    && r.forecast_path.every(row => [row?.year,row?.revenue_growth_rate,row?.operating_margin].every(finite) && ["near_term","maturation"].includes(row.stage))
    && [r.wacc,r.wacc_pre_clamp,r.enterprise_value,r.equity_value,r.intrinsic_value_per_share].every(finite)
    && typeof r.wacc_was_clamped === "boolean" && typeof r.implies_negative_equity_value === "boolean"
    && (r.current_price === null || finite(r.current_price))
    && r.assumptions && [r.assumptions.revenue_growth_rate,r.assumptions.operating_margin,r.assumptions.terminal_growth_rate,r.assumptions.projection_years].every(finite)
    && ["historical","custom"].includes(r.revenue_growth_rate_source) && ["historical","custom"].includes(r.operating_margin_source)
    && r.valuation_quality && ["ordinary","caution","diagnostic_only"].includes(r.valuation_quality.level) && Array.isArray(r.valuation_quality.codes)
    && r.valuation_quality.codes.every(code => ["nonpositive_terminal_fcf","nonpositive_enterprise_value","reversed_scenario_values","extreme_observed_tax_rate","high_terminal_value_concentration"].includes(code))
    && nullableNumber(r.valuation_quality.terminal_value_share_of_enterprise_value) && nullableNumber(r.valuation_quality.observed_effective_tax_rate)
    && r.valuation_quality.allows_market_comparison === (r.valuation_quality.codes.length === 0)
    && r.valuation_input_provenance && ["sec","yahoo"].includes(r.valuation_input_provenance.source) && typeof r.valuation_input_provenance.statement_period_end === "string"
    && [r.valuation_input_provenance.source_selection_reason,r.valuation_input_provenance.knowledge_cutoff,r.valuation_input_provenance.policy_version].every(v=>typeof v === "string")
    && Array.isArray(r.valuation_input_provenance.ingestion_batch_ids) && r.valuation_input_provenance.ingestion_batch_ids.every(v=>typeof v === "string")
    && typeof r.sector === "string" && nullableNumber(r.price_to_intrinsic_value) && nullableNumber(r.sector_median_p_iv) && nullableString(r.sector_median_unavailable_reason)
    && (r.sector_median_unavailable_code === null || ["incompatible_assumptions","insufficient_peers","snapshot_unavailable","valuation_quality"].includes(r.sector_median_unavailable_code))
    && (r.sector_median_snapshot === null || (r.sector_median_snapshot && typeof r.sector_median_snapshot.generated_at === "string" && [r.sector_median_snapshot.universe_size,r.sector_median_snapshot.tickers_used,r.sector_median_snapshot.sector_sample_count].every(finite)))
    && r.sensitivity && [r.sensitivity.wacc_axis,r.sensitivity.terminal_growth_axis].every(axis=>axis && typeof axis.label === "string" && Array.isArray(axis.values) && axis.values.length>0 && axis.values.every(finite) && indexIn(axis.baseline_index,axis.values.length))
    && Array.isArray(r.sensitivity.cells) && r.sensitivity.cells.length === r.sensitivity.wacc_axis.values.length && r.sensitivity.cells.every(row=>Array.isArray(row) && row.length===r.sensitivity.terminal_growth_axis.values.length && row.every(nullableNumber))
    && indexIn(r.sensitivity.baseline_row,r.sensitivity.wacc_axis.values.length) && indexIn(r.sensitivity.baseline_col,r.sensitivity.terminal_growth_axis.values.length)
    && finite(r.sensitivity.baseline_wacc) && finite(r.sensitivity.baseline_terminal_growth_rate) && nullableNumber(r.sensitivity.baseline_intrinsic_value_per_share)
    && r.scenarios && (["base","bear","bull"] as const).every(key=>{const s=r.scenarios[key];return s && s.name===key && typeof s.is_valid==="boolean" && typeof s.implies_negative_equity_value==="boolean" && nullableString(s.invalid_reason) && nullableNumber(s.intrinsic_value_per_share) && (!s.is_valid || finite(s.intrinsic_value_per_share)) && s.assumptions && [s.assumptions.wacc,s.assumptions.revenue_growth_rate,s.assumptions.operating_margin,s.assumptions.terminal_growth_rate].every(finite)})
    && (r.historical_financials == null || isHistoricalFinancials(r.historical_financials)));
}
function browserStorage(): StorageLike | undefined {
  try { return typeof window === "undefined" ? undefined : window.localStorage; } catch { return undefined; }
}
export function readSavedWorkspace(storage = browserStorage()): SavedWorkspace | null {
  try {
    const raw = storage?.getItem(SAVED_WORKSPACE_KEY);
    if (!raw || raw.length > 500_000) return null;
    const s = JSON.parse(raw);
    if (s.version !== 1 || !isWorkspaceResult(s.result) || !["base","bear","bull"].includes(s.selectedScenario) || typeof s.savedAt !== "string" || !Number.isFinite(Date.parse(s.savedAt))) return null;
    return { result:s.result, selectedScenario:s.selectedScenario, savedAt:s.savedAt,
      marketHistory:isMarketHistoryResponse(s.marketHistory) && s.marketHistory.ticker === s.result.ticker ? s.marketHistory : null };
  } catch { return null; }
}
export function writeSavedWorkspace(saved: SavedWorkspace, storage = browserStorage()): boolean {
  try { if (!storage) return false; storage.setItem(SAVED_WORKSPACE_KEY, JSON.stringify({ version:1, ...saved })); return true; } catch { return false; }
}
export function clearSavedWorkspace(storage = browserStorage()): void {
  try { storage?.removeItem(SAVED_WORKSPACE_KEY); } catch { /* Storage can be disabled. */ }
}
