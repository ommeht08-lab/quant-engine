/**
 * Presentation-only number formatting shared by the valuation workspace
 * components. No financial logic lives here — every value passed in is
 * already what the API (or a pure lib calculation) returned.
 */

const compactCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});

const preciseCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatCompactCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return compactCurrencyFormatter.format(value);
}

export function formatPreciseCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return preciseCurrencyFormatter.format(value);
}

export function formatPercent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}
