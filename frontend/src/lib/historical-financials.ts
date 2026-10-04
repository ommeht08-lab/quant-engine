export interface HistoricalFinancialPeriod {
  period_end: string;
  revenue: number | null;
  operating_cash_flow: number | null;
  capital_expenditures: number | null;
  free_cash_flow: number | null;
}
export interface HistoricalFinancials {
  currency: string | null;
  periods: HistoricalFinancialPeriod[];
}
export const historicalMetrics = [
  ["revenue", "Revenue"],
  ["operating_cash_flow", "Operating cash flow"],
  ["capital_expenditures", "Cash CapEx"],
  ["free_cash_flow", "Historical FCF"],
] as const;
export type HistoricalMetric = typeof historicalMetrics[number][0];
export function isHistoricalFinancials(value: unknown): value is HistoricalFinancials {
  const h = value as HistoricalFinancials | null;
  return Boolean(h && (h.currency === null || /^[A-Z]{3}$/.test(h.currency)) && Array.isArray(h.periods) && h.periods.length <= 5 && h.periods.every((row, index) =>
    /^\d{4}-\d{2}-\d{2}$/.test(row?.period_end) && (!index || row.period_end > h.periods[index - 1].period_end) && historicalMetrics.every(([key]) => row[key] === null || (typeof row[key] === "number" && Number.isFinite(row[key]))) &&
    (row.free_cash_flow === null || (row.operating_cash_flow !== null && row.capital_expenditures !== null && Math.abs(row.free_cash_flow - (row.operating_cash_flow - row.capital_expenditures)) <= Math.max(0.01, Math.abs(row.free_cash_flow) * 1e-8))),
  ));
}
