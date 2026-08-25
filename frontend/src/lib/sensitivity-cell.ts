/**
 * Pure classification for one cell of the DCF sensitivity grid, and the
 * accessible text describing it. Used consistently for the cell's visual
 * tone, its directional glyph, and its accessible label/description —
 * all three are derived from the SAME classification, never computed
 * independently of each other.
 */

export type SensitivityCellStatus = "above" | "below" | "equal" | "invalid" | "comparison-unavailable";

export interface SensitivityCellInput {
  /** The cell's intrinsic value per share, or `null` for an invalid WACC/terminal-growth combination. */
  value: number | null;
  /** Current market price, or `null` when it's unavailable. */
  marketPrice: number | null;
}

/**
 * Classify a cell relative to the current market price:
 *   - `"invalid"` — the cell itself has no value (an unmodelable
 *     WACC/terminal-growth combination) — checked BEFORE market price,
 *     since a missing value is never a market-comparison question.
 *   - `"comparison-unavailable"` — the cell has a value, but there's no
 *     market price to compare it against.
 *   - `"above"` / `"below"` / `"equal"` — the cell's value compared to
 *     market price.
 */
export function classifySensitivityCell({ value, marketPrice }: SensitivityCellInput): SensitivityCellStatus {
  if (value === null) return "invalid";
  if (marketPrice === null) return "comparison-unavailable";
  if (value > marketPrice) return "above";
  if (value < marketPrice) return "below";
  return "equal";
}

export interface SensitivityCellAccessibleLabelInput extends SensitivityCellInput {
  /** Whether this cell is the baseline (offset 0, 0) case. */
  isBaseline: boolean;
  /** Formats a finite number as a currency string, e.g. "$261.14". Injected rather than
   * hardcoded so this module stays free of Intl/locale formatting concerns. */
  formatCurrency: (value: number) => string;
}

/**
 * Build the full accessible label/text for one cell: the value (or why
 * it's unavailable), its above/below/equal-market status when knowable,
 * and whether it's the baseline case — every piece of information a
 * sighted reader gets from color/position alone, spelled out in text so
 * nothing depends on color or on an `aria-hidden` glyph being announced.
 */
export function sensitivityCellAccessibleLabel({
  value,
  marketPrice,
  isBaseline,
  formatCurrency,
}: SensitivityCellAccessibleLabelInput): string {
  const status = classifySensitivityCell({ value, marketPrice });
  const parts: string[] = [];

  if (status === "invalid") {
    parts.push("Not a valid WACC and terminal growth combination");
  } else {
    parts.push(formatCurrency(value as number));
    if (status === "above") parts.push("above current market price");
    else if (status === "below") parts.push("below current market price");
    else if (status === "equal") parts.push("equal to current market price");
    else parts.push("market price unavailable for comparison");
  }

  if (isBaseline) parts.push("baseline case");

  return parts.join(", ");
}
