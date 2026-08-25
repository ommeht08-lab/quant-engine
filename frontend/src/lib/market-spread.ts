/**
 * Pure comparison of one modeled value (a scenario's intrinsic value per
 * share, a sensitivity cell, etc.) against the current market price: the
 * signed dollar and percent spread between them, and the visible/
 * accessible text describing it. Reuses `classifySensitivityCell` for
 * the above/below/equal/unavailable status rather than re-deriving that
 * comparison, so the two never drift apart.
 */

import { classifySensitivityCell, type SensitivityCellStatus } from "./sensitivity-cell.ts";

export interface MarketSpreadInput {
  /** The modeled value, or `null` when it isn't computable (e.g. an invalid scenario). */
  value: number | null;
  /** Current market price, or `null` when unavailable. */
  marketPrice: number | null;
}

export interface MarketSpreadResult {
  status: SensitivityCellStatus;
  /** value - marketPrice. `null` when there's no value or no market price to compare against. */
  dollarDelta: number | null;
  /** Percent spread relative to market price. `null` when not computable, OR when market price
   * is exactly 0 — division by zero is undefined, never fabricated as a number. */
  percentDelta: number | null;
}

/**
 * Compute the signed spread between a modeled value and the market price.
 * A `$0` market price still yields a `dollarDelta` (the absolute gap is
 * well-defined) but a `null` `percentDelta` (the relative gap is not).
 */
export function computeMarketSpread({ value, marketPrice }: MarketSpreadInput): MarketSpreadResult {
  const status = classifySensitivityCell({ value, marketPrice });
  if (status === "invalid" || status === "comparison-unavailable") {
    return { status, dollarDelta: null, percentDelta: null };
  }

  const dollarDelta = (value as number) - (marketPrice as number);
  const percentDelta = marketPrice !== 0 ? (dollarDelta / (marketPrice as number)) * 100 : null;

  return { status, dollarDelta, percentDelta };
}

export interface MarketSpreadDisplay {
  visible: string;
  accessible: string;
}

const PERCENTAGE_UNAVAILABLE = "Percentage unavailable";

const preciseCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatSignedCurrency(value: number): string {
  if (value === 0) return preciseCurrencyFormatter.format(0);
  const sign = value > 0 ? "+" : "-";
  return `${sign}${preciseCurrencyFormatter.format(Math.abs(value))}`;
}

function formatSignedPercent(value: number, digits = 1): string {
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toFixed(digits)}%`;
}

/**
 * Build the "vs. Market" cell's visible text and accessible label from
 * the SAME computed spread, so the two can never describe different
 * numbers — mirroring how tone/glyph and accessible text are derived
 * from one shared classification elsewhere in this codebase.
 */
export function formatMarketSpread(spread: MarketSpreadResult): MarketSpreadDisplay {
  if (spread.status === "invalid") {
    return { visible: "—", accessible: "Not computable" };
  }
  if (spread.status === "comparison-unavailable") {
    return { visible: "Unavailable", accessible: "Market price unavailable for comparison" };
  }

  const dollarText = formatSignedCurrency(spread.dollarDelta as number);
  const percentText = spread.percentDelta === null ? PERCENTAGE_UNAVAILABLE : formatSignedPercent(spread.percentDelta);
  const visible = `${dollarText} (${percentText})`;

  if (spread.status === "equal") {
    return { visible, accessible: `Equal to current market price, ${dollarText}, ${percentText}` };
  }

  const directionWord = spread.status === "above" ? "above" : "below";
  return { visible, accessible: `${dollarText} ${directionWord} current market price, ${percentText}` };
}
