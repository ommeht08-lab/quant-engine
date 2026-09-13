/** Interpretation of a computed DCF; never a replacement for its numbers. */
export type ValuationQualityCode =
  | "nonpositive_terminal_fcf"
  | "nonpositive_enterprise_value"
  | "reversed_scenario_values"
  | "extreme_observed_tax_rate"
  | "high_terminal_value_concentration";

export interface ValuationQuality {
  level: "ordinary" | "caution" | "diagnostic_only";
  codes: ValuationQualityCode[];
  allows_market_comparison: boolean;
  terminal_value_share_of_enterprise_value: number | null;
  observed_effective_tax_rate: number | null;
}

export function qualityIssueCopy(code: ValuationQualityCode): string {
  switch (code) {
    case "nonpositive_terminal_fcf":
      return "Final-year free cash flow is zero or negative; perpetuating it is not an actionable valuation.";
    case "nonpositive_enterprise_value":
      return "Modeled enterprise value is zero or negative; treat the share value as a distress diagnostic.";
    case "reversed_scenario_values":
      return "Case values do not run from Bear to Base to Bull. Bear/Bull describe inputs, not downside/upside outcomes.";
    case "extreme_observed_tax_rate":
      return "The observed effective tax rate is at least 60%; a single unusual year may distort all forecast cash flows.";
    case "high_terminal_value_concentration":
      return "At least 80% of enterprise value comes from the discounted terminal value; the result is highly assumption-sensitive.";
  }
}

export function scenarioDisplayLabel(
  key: "bear" | "base" | "bull", quality: ValuationQuality
): string {
  if (quality.codes.includes("reversed_scenario_values") && key !== "base") {
    return key === "bear" ? "Bear inputs" : "Bull inputs";
  }
  return key === "base" ? "Base" : key === "bear" ? "Bear" : "Bull";
}
