/**
 * Pure geometry + framing for the valuation-spread rail: where the
 * market-price and intrinsic-value markers sit on a shared scale, and
 * how to describe the spread between them without ever implying a
 * percentage that isn't actually computable (a zero market price) or
 * calling a zero spread "upside".
 */

export type ValuationSpreadDirection = "upside" | "downside" | "equal";

export interface ValuationSpreadInput {
  /** Current market price, or `null` when unavailable. */
  marketPrice: number | null;
  /** Modeled intrinsic value per share. Can be zero or negative. */
  intrinsicValue: number;
}

export interface ValuationSpreadResult {
  direction: ValuationSpreadDirection;
  /** Signed percent spread ((intrinsic - market) / market * 100), or `null` when market price is 0 (undefined). */
  percent: number | null;
  /** 0–100 position of the market-price marker on the rail. */
  marketPct: number;
  /** 0–100 position of the intrinsic-value marker on the rail. */
  intrinsicPct: number;
  /** 0–100 left edge of the fill between the two markers. */
  fillStartPct: number;
  /** 0–100 width of the fill between the two markers. */
  fillWidthPct: number;
  domainMin: number;
  domainMax: number;
}

const PAD_FRACTION = 0.12;

/**
 * Compute rail geometry for a market price vs. intrinsic value pair.
 * Returns `null` only when there is no market price to plot against
 * (the caller should render its own "unavailable" state in that case).
 *
 * The domain always spans at least [0, market price, intrinsic value]
 * (plus padding) so zero is always a visible reference point on the
 * rail and negative intrinsic values stay on-scale instead of being
 * clamped away, and every returned *Pct value is clamped to [0, 100]
 * so markers can never render outside the rail.
 */
export function computeValuationSpread({
  marketPrice,
  intrinsicValue,
}: ValuationSpreadInput): ValuationSpreadResult | null {
  if (marketPrice === null) return null;

  const rawLow = Math.min(0, marketPrice, intrinsicValue);
  const rawHigh = Math.max(0, marketPrice, intrinsicValue);
  const range = rawHigh - rawLow;
  // When market price, intrinsic value, and 0 all coincide (e.g. both
  // are 0), fall back to a small fixed pad so the domain isn't a
  // zero-width point.
  const pad = range > 0 ? range * PAD_FRACTION : 1;
  const domainMin = rawLow - pad;
  const domainMax = rawHigh + pad;
  const span = domainMax - domainMin || 1;

  const toPct = (value: number) => {
    const pct = ((value - domainMin) / span) * 100;
    return Math.min(100, Math.max(0, pct));
  };

  const marketPct = toPct(marketPrice);
  const intrinsicPct = toPct(intrinsicValue);

  const direction: ValuationSpreadDirection =
    intrinsicValue === marketPrice ? "equal" : intrinsicValue > marketPrice ? "upside" : "downside";

  // A percent spread relative to a $0 market price is mathematically
  // undefined (division by zero) — never render a fabricated number for it.
  const percent = marketPrice !== 0 ? ((intrinsicValue - marketPrice) / marketPrice) * 100 : null;

  return {
    direction,
    percent,
    marketPct,
    intrinsicPct,
    fillStartPct: Math.min(marketPct, intrinsicPct),
    fillWidthPct: Math.abs(intrinsicPct - marketPct),
    domainMin,
    domainMax,
  };
}
