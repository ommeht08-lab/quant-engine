const YAHOO_CHART_ORIGIN = "https://query1.finance.yahoo.com";
const MARKET_HISTORY_TIMEOUT_MS = 8_000;

const YAHOO_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
  Accept: "application/json",
};

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

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function parseYahooMarketHistory(payload: unknown, ticker: string): MarketHistoryResponse {
  const chart = (payload as {
    chart?: {
      result?: Array<{
        meta?: { currency?: unknown; exchangeTimezoneName?: unknown };
        timestamp?: unknown;
        indicators?: { quote?: Array<{ close?: unknown }> };
      }>;
      error?: unknown;
    };
  })?.chart;
  const result = chart?.result?.[0];
  const timestamps = Array.isArray(result?.timestamp) ? result.timestamp : [];
  const closes = Array.isArray(result?.indicators?.quote?.[0]?.close)
    ? result.indicators.quote[0].close
    : [];

  const points: MarketHistoryPoint[] = [];
  const count = Math.min(timestamps.length, closes.length);
  for (let index = 0; index < count; index += 1) {
    const timestamp = finiteNumber(timestamps[index]);
    const close = finiteNumber(closes[index]);
    if (timestamp === null || close === null || close <= 0) continue;

    const date = new Date(timestamp * 1000);
    if (Number.isNaN(date.getTime())) continue;
    points.push({ date: date.toISOString(), close });
  }

  if (points.length < 2) {
    throw new Error("Yahoo Finance returned insufficient daily price history.");
  }

  const currency = typeof result?.meta?.currency === "string" ? result.meta.currency : "USD";
  const exchangeTimezone = typeof result?.meta?.exchangeTimezoneName === "string"
    ? result.meta.exchangeTimezoneName
    : "America/New_York";

  return {
    ticker,
    currency,
    exchangeTimezone,
    source: "Yahoo Finance",
    asOf: points.at(-1)!.date,
    points,
  };
}

export async function fetchMarketHistory(ticker: string): Promise<MarketHistoryResponse> {
  const url = new URL(`/v8/finance/chart/${encodeURIComponent(ticker)}`, YAHOO_CHART_ORIGIN);
  url.searchParams.set("range", "1y");
  url.searchParams.set("interval", "1d");
  url.searchParams.set("includePrePost", "false");
  url.searchParams.set("events", "div,splits");

  const response = await fetch(url, {
    headers: YAHOO_HEADERS,
    signal: AbortSignal.timeout(MARKET_HISTORY_TIMEOUT_MS),
    next: { revalidate: 900 },
  });
  if (!response.ok) {
    throw new Error(`Yahoo Finance history request failed with status ${response.status}.`);
  }

  return parseYahooMarketHistory(await response.json(), ticker);
}
