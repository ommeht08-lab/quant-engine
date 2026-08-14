import { NextResponse } from "next/server";

import { requireSession } from "@/lib/auth";

// Live DCF valuation — always fresh, never cached at this layer (the
// Python backend/`yfinance` layer does its own caching).
export const dynamic = "force-dynamic";

/**
 * Server-side proxy to the Python FastAPI backend's
 * `GET /api/evaluate/{ticker}` (`src.api.main`).
 *
 * The frontend used to call the Python backend directly from the
 * browser via a hardcoded `http://localhost:8000` — broken outside
 * local dev (nothing at that address in a deployed environment) and it
 * meant the backend had to accept CORS from any origin. Routing through
 * this Route Handler instead means the browser only ever talks to this
 * Next.js app's own origin, and `VALUATION_API_URL` (server-side only —
 * deliberately not `NEXT_PUBLIC_*`) can point at wherever the Python
 * service actually runs per environment.
 */
function getValuationApiBaseUrl(): string {
  return process.env.VALUATION_API_URL ?? "http://localhost:8000";
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ ticker: string }> }
) {
  if (!(await requireSession())) {
    return NextResponse.json({ error: "Not authenticated." }, { status: 401 });
  }

  const { ticker } = await params;
  const { search } = new URL(request.url);

  let response: Response;
  try {
    response = await fetch(
      `${getValuationApiBaseUrl()}/api/evaluate/${encodeURIComponent(ticker)}${search}`
    );
  } catch (error) {
    console.error(`Failed to reach the valuation backend for ${ticker}:`, error);
    return NextResponse.json(
      { error: "Could not reach the valuation backend." },
      { status: 502 }
    );
  }

  const body = await response.json().catch(() => null);
  return NextResponse.json(body, { status: response.status });
}
