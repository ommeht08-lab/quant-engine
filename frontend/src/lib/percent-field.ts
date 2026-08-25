/**
 * Pure normalization for a "percent-displayed, decimal-valued" numeric
 * field (e.g. a revenue growth rate stored as 0.08, typed/shown as
 * "8.0"). Shared by every numeric+slider assumption control so the
 * displayed text, the slider position, and the decimal value sent to the
 * API can never disagree.
 */

export interface PercentFieldRange {
  /** Decimal min (e.g. -0.1 for -10%). */
  min: number;
  /** Decimal max (e.g. 0.4 for 40%). */
  max: number;
  /** Decimal step (e.g. 0.005 for 0.5 percentage points). */
  step: number;
}

/**
 * Clamp `value` (decimal) to [min, max], then snap it to the nearest
 * step increment from `min`, re-clamping to absorb any floating-point
 * drift introduced by the snap.
 */
export function clampAndQuantize(value: number, { min, max, step }: PercentFieldRange): number {
  const clamped = Math.min(max, Math.max(min, value));
  if (!(step > 0)) return clamped;

  const steps = Math.round((clamped - min) / step);
  const snapped = min + steps * step;
  // Round off floating-point noise from repeated division/multiplication
  // (e.g. 0.015000000000000001) before the final re-clamp.
  const rounded = Math.round(snapped * 1e8) / 1e8;
  return Math.min(max, Math.max(min, rounded));
}

/**
 * Parse a raw percent-space string (what the user typed, e.g. "8.5")
 * into a clamped, step-quantized DECIMAL value (e.g. 0.085).
 *
 * Returns `null` for anything that isn't a usable number — blank/
 * whitespace-only input, or text that doesn't parse as a finite number
 * — so the caller can revert to the last valid value instead of
 * silently coercing an empty/invalid field to 0.
 */
export function parsePercentInput(raw: string, range: PercentFieldRange): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;

  const parsedPercent = Number(trimmed);
  if (!Number.isFinite(parsedPercent)) return null;

  return clampAndQuantize(parsedPercent / 100, range);
}

/** Format a decimal value (e.g. 0.085) as the percent-space display text (e.g. "8.5"). */
export function formatPercentInputValue(value: number, precision: number): string {
  return (value * 100).toFixed(precision);
}
