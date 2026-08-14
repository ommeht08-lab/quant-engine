import { NextResponse } from "next/server";
import {
  getTickerSentimentAndMacro,
  SENTIMENT_CACHE_TTL_SECONDS,
  type TickerSentiment,
} from "@/lib/sentiment";
import { cacheAside } from "@/lib/redis";
import { requireSession } from "@/lib/auth";

// Keep this dynamic (not statically optimized at build time) so our own
// Redis cache-aside logic below runs fresh on every request — caching is
// handled explicitly via Upstash, not Next.js's build-time page cache.
export const dynamic = "force-dynamic";

/**
 * GET /api/sentiment/[symbol]
 *
 * Returns recent news headlines and macro risk context (10-Year Treasury
 * yield, VIX) for a ticker, fetched directly from Yahoo Finance and
 * cached (cache-aside) in Upstash Redis for `SENTIMENT_CACHE_TTL_SECONDS`
 * — this is exactly the kind of external, rate-limited call the cache
 * exists to protect. Shares its cache key/entries with the Tear Sheet
 * page (`frontend/src/app/ticker/[symbol]/page.tsx`), which calls
 * `getTickerSentimentAndMacro` the same cached way rather than through
 * this route. Always responds 200 with whatever could be fetched —
 * `getTickerSentimentAndMacro` degrades unavailable fields to `null` /
 * `[]` rather than throwing, so this route only 500s on a truly
 * unexpected failure.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ symbol: string }> }
) {
  if (!(await requireSession())) {
    return NextResponse.json({ error: "Not authenticated." }, { status: 401 });
  }

  const { symbol } = await params;
  const ticker = symbol.toUpperCase();

  try {
    const data = await cacheAside<TickerSentiment>(
      `sentiment:${ticker}`,
      SENTIMENT_CACHE_TTL_SECONDS,
      () => getTickerSentimentAndMacro(ticker)
    );
    return NextResponse.json(data);
  } catch (error) {
    console.error(`Failed to fetch sentiment/macro data for ${ticker}:`, error);
    return NextResponse.json(
      { error: "Failed to fetch sentiment and macro data." },
      { status: 500 }
    );
  }
}
