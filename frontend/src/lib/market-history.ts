export interface MarketHistoryPoint {
  date: string;
  close: number;
}

export interface MarketHistoryResponse {
  ticker: string;
  currency: string;
  exchangeTimezone: string;
  source: "Yahoo Finance";
  asOf: string;
  points: MarketHistoryPoint[];
}

export function isMarketHistoryResponse(value: unknown): value is MarketHistoryResponse {
  const candidate = value as Partial<MarketHistoryResponse> | null;
  return Boolean(
    candidate
      && typeof candidate.ticker === "string"
      && typeof candidate.currency === "string"
      && candidate.source === "Yahoo Finance"
      && Array.isArray(candidate.points)
      && candidate.points.length >= 2
      && candidate.points.every((point) => (
        typeof point?.date === "string"
        && typeof point?.close === "number"
        && Number.isFinite(point.close)
        && point.close > 0
      )),
  );
}
