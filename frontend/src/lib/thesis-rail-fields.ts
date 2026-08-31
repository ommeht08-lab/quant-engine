import { hasMarginOfSafety, thesisDeltaLabel } from "./valuation-thesis-copy.ts";

export interface ScenarioAssumptionsLike {
  revenue_growth_rate: number;
  operating_margin: number;
  wacc: number;
  terminal_growth_rate: number;
}

export interface ScenarioLike {
  assumptions: ScenarioAssumptionsLike;
  intrinsic_value_per_share: number | null;
  is_valid: boolean;
  invalid_reason: string | null;
}

export interface ThesisRailFields {
  revenueGrowthRate: number;
  operatingMargin: number;
  wacc: number;
  terminalGrowthRate: number;
  intrinsicValue: number | null;
  priceDelta: number | null;
  priceDeltaPct: number | null;
  deltaIsPositive: boolean;
  deltaLabel: string;
  hasMarginOfSafety: boolean;
}

/**
 * Resolves every scenario-dependent Thesis Rail field from ONE
 * scenario, so switching Bear/Base/Bull always changes intrinsic
 * value, revenue growth, operating margin, WACC, terminal growth, and
 * the market delta together — never a mix of the selected scenario's
 * valuation next to a different (e.g. base-case) WACC or terminal
 * growth. `assumptions` is always present on a scenario even when it
 * is not computable (`is_valid: false`), so the four assumption fields
 * are always resolved from it; only the valuation-derived fields
 * (intrinsic value, delta) fall back when the scenario is invalid.
 */
export function resolveThesisRailFields(scenario: ScenarioLike, marketPrice: number | null): ThesisRailFields {
  const intrinsicValue = scenario.is_valid ? scenario.intrinsic_value_per_share : null;
  const priceDelta = intrinsicValue !== null && marketPrice !== null ? intrinsicValue - marketPrice : null;
  const priceDeltaPct = priceDelta !== null && marketPrice ? priceDelta / marketPrice : null;
  const deltaIsPositive = priceDelta !== null && priceDelta >= 0;
  const deltaLabel = intrinsicValue !== null ? thesisDeltaLabel(intrinsicValue, marketPrice) : "Upside / downside";

  return {
    revenueGrowthRate: scenario.assumptions.revenue_growth_rate,
    operatingMargin: scenario.assumptions.operating_margin,
    wacc: scenario.assumptions.wacc,
    terminalGrowthRate: scenario.assumptions.terminal_growth_rate,
    intrinsicValue,
    priceDelta,
    priceDeltaPct,
    deltaIsPositive,
    deltaLabel,
    hasMarginOfSafety: intrinsicValue !== null && hasMarginOfSafety(intrinsicValue, marketPrice),
  };
}
