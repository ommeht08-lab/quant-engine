import { NextResponse } from "next/server";
import { getTickerSentimentAndMacro } from "@/lib/sentiment";

// Always fetch live — news and macro indicators are never a cacheable
// resource.
export const dynamic = "force-dynamic";

/**
 * GET /api/sentiment/[symbol]
 *
 * Returns recent news headlines and macro risk context (10-Year Treasury
 * yield, VIX) for a ticker, fetched directly from Yahoo Finance. Always
 * responds 200 with whatever could be fetched — `getTickerSentimentAndMacro`
 * degrades unavailable fields to `null` / `[]` rather than throwing, so
 * this route only 500s on a truly unexpected failure.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ symbol: string }> }
) {
  const { symbol } = await params;

  try {
    const data = await getTickerSentimentAndMacro(symbol);
    return NextResponse.json(data);
  } catch (error) {
    console.error(`Failed to fetch sentiment/macro data for ${symbol}:`, error);
    return NextResponse.json(
      { error: "Failed to fetch sentiment and macro data." },
      { status: 500 }
    );
  }
}
