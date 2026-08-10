/**
 * Macro context and qualitative news sentiment for a ticker, fetched
 * directly from Yahoo Finance's public JSON endpoints.
 *
 * There is no Node equivalent of the Python `yfinance` library (see
 * `src/valuation/macro_sentiment.py` for the same capability there), so
 * this is the "standard external fetch fallback": plain `fetch` calls
 * against the same endpoints `yfinance` itself talks to under the hood.
 * Yahoo rate-limits or blocks these endpoints for some source IPs (data
 * center / cloud egress ranges in particular), so every fetch here
 * degrades to `null` / an empty array on any failure rather than
 * throwing — this is supplementary qualitative context, not a required
 * valuation input, and the UI is expected to render a clean fallback
 * when it's unavailable.
 */

const YAHOO_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
  Accept: "application/json",
};

const FETCH_TIMEOUT_MS = 8000;

export interface MacroIndicators {
  treasury10y: number | null;
  vix: number | null;
}

export interface Headline {
  title: string;
  publisher: string | null;
  link: string | null;
  publishedAt: string | null;
}

export interface TickerSentiment {
  ticker: string;
  macro: MacroIndicators;
  headlines: Headline[];
}

async function fetchYahooChartPrice(symbol: string): Promise<number | null> {
  try {
    const response = await fetch(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}`,
      { headers: YAHOO_HEADERS, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS), cache: "no-store" }
    );
    if (!response.ok) return null;

    const data = await response.json();
    const price = data?.chart?.result?.[0]?.meta?.regularMarketPrice;
    return typeof price === "number" ? price : null;
  } catch (error) {
    console.error(`Failed to fetch Yahoo chart price for ${symbol}:`, error);
    return null;
  }
}

async function fetchMacroIndicators(): Promise<MacroIndicators> {
  const [treasuryYield, vix] = await Promise.all([
    fetchYahooChartPrice("^TNX"),
    fetchYahooChartPrice("^VIX"),
  ]);
  return {
    // ^TNX is quoted as a raw yield-times-100 number (e.g. 4.70 for
    // 4.70%); normalize to a decimal fraction to match how the rest of
    // this app represents rates (e.g. WACC).
    treasury10y: treasuryYield === null ? null : treasuryYield / 100,
    vix,
  };
}

interface YahooNewsItem {
  title?: string;
  publisher?: string;
  link?: string;
  providerPublishTime?: number;
}

async function fetchRecentHeadlines(ticker: string, count = 5): Promise<Headline[]> {
  try {
    const url = `https://query1.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(
      ticker
    )}&newsCount=${count}&quotesCount=0`;
    const response = await fetch(url, {
      headers: YAHOO_HEADERS,
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      cache: "no-store",
    });
    if (!response.ok) return [];

    const data = await response.json();
    const items: YahooNewsItem[] = Array.isArray(data?.news) ? data.news : [];

    return items
      .slice(0, count)
      .filter((item) => Boolean(item.title))
      .map((item) => ({
        title: item.title as string,
        publisher: item.publisher ?? null,
        link: item.link ?? null,
        publishedAt:
          typeof item.providerPublishTime === "number"
            ? new Date(item.providerPublishTime * 1000).toISOString()
            : null,
      }));
  } catch (error) {
    console.error(`Failed to fetch news headlines for ${ticker}:`, error);
    return [];
  }
}

/**
 * Fetch recent news headlines and macro risk context (10-Year Treasury
 * yield, VIX) for a ticker. Never throws — every sub-fetch degrades
 * independently, so a macro-indicator outage doesn't also blank out
 * headlines (and vice versa).
 */
export async function getTickerSentimentAndMacro(ticker: string): Promise<TickerSentiment> {
  const symbol = ticker.toUpperCase();
  const [macro, headlines] = await Promise.all([
    fetchMacroIndicators(),
    fetchRecentHeadlines(symbol),
  ]);
  return { ticker: symbol, macro, headlines };
}
