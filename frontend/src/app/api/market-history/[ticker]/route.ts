import { NextResponse } from "next/server";

import { fetchMarketHistory } from "@/lib/market-history";
import { parseTickerQueryParam } from "@/lib/ticker-query";

export const dynamic = "force-dynamic";
export const maxDuration = 15;

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker: rawTicker } = await params;
  const ticker = parseTickerQueryParam(rawTicker);
  if (!ticker) {
    return NextResponse.json({ error: "Enter a valid ticker symbol." }, { status: 400 });
  }

  try {
    const history = await fetchMarketHistory(ticker);
    return NextResponse.json(history, {
      headers: {
        "Cache-Control": "public, s-maxage=900, stale-while-revalidate=1800",
      },
    });
  } catch (error) {
    console.error(
      `Market history unavailable for ${ticker}:`,
      error instanceof Error ? error.message : "unknown error",
    );
    return NextResponse.json(
      { error: "Daily market history is temporarily unavailable." },
      { status: 502 },
    );
  }
}
