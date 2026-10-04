import type { FreeCashFlowYear } from "@/components/valuation/ProjectedCashFlows";
import type { DCFScenarioSet } from "@/components/valuation/ValuationSpectrum";
import type { DCFSensitivityMatrix } from "@/components/valuation/SensitivityMatrix";
import type { SectorMedianSnapshot } from "@/components/valuation/SectorRelativeValuation";
import type { SectorMedianUnavailableCode } from "./sector-median-copy";
import type { ValuationQuality } from "./valuation-quality";
import type { HistoricalFinancials } from "./historical-financials";

export interface EvaluationResponse {
  historical_financials?: HistoricalFinancials | null;
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
  valuation_input_provenance: {
    source: "sec" | "yahoo";
    source_selection_reason: string;
    knowledge_cutoff: string;
    statement_period_end: string;
    policy_version: string;
    ingestion_batch_ids: string[];
  };
}
