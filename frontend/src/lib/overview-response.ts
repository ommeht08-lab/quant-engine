import { hasMarginOfSafety, thesisDeltaLabel } from "./valuation-thesis-copy.ts";

/**
 * Pure logic for the `/overview/[ticker]` research page — kept separate
 * from the page/client component so ticker normalization and the
 * margin-of-safety withholding rule are unit-testable without rendering
 * anything (matches this repo's existing convention — see
 * `valuation-state-copy.ts`/`thesis-rail-fields.ts` for the same split).
 */

/**
 * Trims and uppercases a raw ticker input; `null` for an empty/
 * whitespace-only value, never an empty string, so callers can use
 * truthiness instead of a length check.
 */
export function normalizeOverviewTicker(input: string): string | null {
  const trimmed = input.trim().toUpperCase();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * The `/overview/{TICKER}` path to navigate to for a raw ticker input,
 * or `null` if the input normalizes to nothing — the caller should not
 * navigate at all in that case (mirrors `SearchBar`'s own empty-input
 * guard for the tear sheet's `/ticker/{symbol}` navigation).
 */
export function overviewRouteForTicker(input: string): string | null {
  const ticker = normalizeOverviewTicker(input);
  return ticker ? `/overview/${encodeURIComponent(ticker)}` : null;
}

export interface MarginOfSafetyDisplay {
  /** True when `valuation_quality.allows_market_comparison` is false —
   * every other field is then meaningless and must not be read. */
  withheld: boolean;
  hasObservedPrice: boolean;
  /** Null when withheld or there is no observed price to compare against. */
  hasMarginOfSafety: boolean | null;
  /** (intrinsic - price) / price; null under the same conditions as `hasMarginOfSafety`. */
  deltaPct: number | null;
  /** "Margin of safety" | "Downside" | "Upside / downside" | "Withheld". */
  label: string;
}

/**
 * Resolves the overview's margin-of-safety display from the TOP-LEVEL
 * `current_price`/`intrinsic_value_per_share` fields, honoring the
 * quality-withholding policy exactly the way the backend withholds
 * `price_to_intrinsic_value`/`sector_median_p_iv`: `current_price` and
 * `intrinsic_value_per_share` are both still present and real even when
 * `allows_market_comparison` is false (the backend never nulls them),
 * so this function — not the backend — is what must refuse to compute
 * or display a market-comparison ratio from them in that case. Reuses
 * `hasMarginOfSafety`/`thesisDeltaLabel` (the Thesis Rail's own
 * absolute-comparison definitions) rather than a second, possibly
 * inconsistent definition of "margin of safety".
 */
export function resolveMarginOfSafetyDisplay(params: {
  currentPrice: number | null;
  intrinsicValuePerShare: number;
  allowsMarketComparison: boolean;
}): MarginOfSafetyDisplay {
  const { currentPrice, intrinsicValuePerShare, allowsMarketComparison } = params;

  if (!allowsMarketComparison) {
    return {
      withheld: true,
      hasObservedPrice: currentPrice !== null,
      hasMarginOfSafety: null,
      deltaPct: null,
      label: "Withheld",
    };
  }

  const deltaPct =
    currentPrice !== null && currentPrice !== 0
      ? (intrinsicValuePerShare - currentPrice) / currentPrice
      : null;

  return {
    withheld: false,
    hasObservedPrice: currentPrice !== null,
    hasMarginOfSafety: currentPrice !== null ? hasMarginOfSafety(intrinsicValuePerShare, currentPrice) : null,
    deltaPct,
    label: thesisDeltaLabel(intrinsicValuePerShare, currentPrice),
  };
}
