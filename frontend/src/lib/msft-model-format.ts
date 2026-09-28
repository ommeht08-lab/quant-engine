// Formatting helpers for the archived MSFT sensitivity-experiment preview
// screen. Every number this screen displays comes from an already-
// archived, reviewed report string (Decimal-precision, not a live
// computation) -- these functions only format for display; they never
// recompute a result.

/** Parses an archived Decimal-string value into a JS number for display
 * only. The full-precision string remains available via `title`/tooltip
 * attributes wherever this is used, so nothing is silently lost — this
 * is a rounding-for-display step, not a recalculation. */
function toNumber(value: string | number): number {
  return typeof value === "number" ? value : Number(value);
}

const usdMillionsFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
});

/** Formats a USD-millions Decimal-string as e.g. "62,119.0". Always
 * appends the unit at the call site (this returns the number only) so
 * every table can label its own column once rather than repeating "USD
 * millions" per cell. */
export function formatUsdMillions(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = toNumber(value);
  if (!Number.isFinite(n)) return "—";
  return usdMillionsFormatter.format(n);
}

const percentFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: "always",
});

/** `value` is a decimal fraction string (e.g. "0.10" for 10%, or a signed
 * ratio like "0.3775..." for +37.75%). */
export function formatPercent(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = toNumber(value);
  if (!Number.isFinite(n)) return "—";
  return percentFormatter.format(n);
}

/** Same as formatPercent but without a forced sign — for plain rate
 * inputs (tax rate, interest rate) rather than signed differences. */
const plainPercentFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
export function formatRate(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = toNumber(value);
  if (!Number.isFinite(n)) return "—";
  return plainPercentFormatter.format(n);
}

/** Full, unrounded Decimal string, for tooltips/title attributes where
 * exact archived precision should remain inspectable. */
export function exactString(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

export type YearStatus = "completed" | "refused" | "not_attempted" | "verification_failed";

export const YEAR_STATUS_LABEL: Record<YearStatus, string> = {
  completed: "Completed",
  refused: "Refused",
  not_attempted: "Not attempted",
  verification_failed: "Verification failed",
};

export const SCENARIO_STATUS_LABEL: Record<string, string> = {
  completed: "Completed — checks passed",
  expected_refusal_confirmed: "Expected refusal — confirmed",
  stopped_unexpected_refusal: "Stopped — unexpected refusal",
  incomplete: "Incomplete",
};
