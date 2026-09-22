import { NextResponse } from "next/server";

import { fetchWithTimeout } from "@/lib/backend-fetch";
import { assertSecretMeetsRequirements, VALUATION_API_TOKEN_REQUIREMENT } from "@/lib/secret-validation";
import { parseTickerQueryParam } from "@/lib/ticker-query";
import { assertSafeValuationApiUrl } from "@/lib/valuation-api-url";

export const dynamic = "force-dynamic";
export const maxDuration = 15;
const MARKET_HISTORY_TIMEOUT_MS = 10_000;

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
    const rawToken = process.env.VALUATION_API_TOKEN;
    assertSecretMeetsRequirements(rawToken, VALUATION_API_TOKEN_REQUIREMENT);
    const isProduction = process.env.NODE_ENV === "production";
    const rawUrl = process.env.VALUATION_API_URL ?? (isProduction ? "" : "http://localhost:8000");
    const backendOrigin = assertSafeValuationApiUrl(rawUrl, { isProduction });
    const targetUrl = new URL(`/api/market-history/${encodeURIComponent(ticker)}`, backendOrigin);
    const response = await fetchWithTimeout(
      targetUrl,
      { headers: { Authorization: `Bearer ${rawToken}` } },
      MARKET_HISTORY_TIMEOUT_MS,
    );
    if (!response.ok) throw new Error("market history backend unavailable");
    const history: unknown = await response.json();
    return NextResponse.json(history, {
      headers: {
        "Cache-Control": "public, s-maxage=900, stale-while-revalidate=1800",
      },
    });
  } catch (error) {
    console.error(
      "Market history backend request failed:",
      error instanceof Error && error.name === "AbortError" ? "timeout" : "unavailable",
    );
    return NextResponse.json(
      { error: "Daily market history is temporarily unavailable." },
      { status: 502 },
    );
  }
}
