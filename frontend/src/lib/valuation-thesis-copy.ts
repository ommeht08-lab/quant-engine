/**
 * Copy for the Thesis Rail's absolute-valuation delta (intrinsic value
 * vs. market price) — kept separate from `sector-median-copy.ts`, which
 * owns the sector-RELATIVE comparison's own, deliberately different
 * language. The two must never blend: "Margin of Safety" is reserved
 * for this absolute comparison (market price at or below intrinsic
 * value) and must never describe a sector-relative P/IV read.
 */

/**
 * True only when a genuine margin of safety exists in the classic
 * value-investing sense: market price at or below intrinsic value (an
 * investor can buy for less than the estimated value). `null` (no
 * observed market price) is never a margin of safety — there is
 * nothing to compare against.
 */
export function hasMarginOfSafety(intrinsicValue: number, marketPrice: number | null): boolean {
  return marketPrice !== null && marketPrice <= intrinsicValue;
}

/**
 * Label for the Thesis Rail's delta figure: "Margin of safety" only
 * when one genuinely exists (see `hasMarginOfSafety`); "Downside" when
 * the market price is currently above intrinsic value (no margin of
 * safety exists); "Upside / downside" when there is no market price to
 * compare against at all.
 */
export function thesisDeltaLabel(intrinsicValue: number, marketPrice: number | null): string {
  if (marketPrice === null) return "Upside / downside";
  return hasMarginOfSafety(intrinsicValue, marketPrice) ? "Margin of safety" : "Downside";
}
