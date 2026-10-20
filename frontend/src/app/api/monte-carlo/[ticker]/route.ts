import { NextResponse } from "next/server";

import { normalizeMonteCarloTicker } from "@/lib/monte-carlo-proxy";
import { proxyMonteCarlo } from "../proxy";

// Runs the four Monte Carlo experiments for one allowlisted ticker. The Python
// backend caches by snapshot, seed and configuration; this layer never caches.
//
// Layered hard-duration contract (enforced by tests/test_vercel_config.py):
//   backend platform hard stop (vercel.json maxDuration):  40 s
// < upstream wait (MONTE_CARLO_BACKEND_REQUEST_TIMEOUT_MS):  45 s
// < this route's own function limit (maxDuration below):     60 s
export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function GET(request: Request, { params }: { params: Promise<{ ticker: string }> }) {
  const { ticker: rawTicker } = await params;
  const ticker = normalizeMonteCarloTicker(rawTicker);
  if (ticker === null) {
    return NextResponse.json(
      { code: "MONTE_CARLO_INVALID_REQUEST", error: "Enter a ticker using 1–10 letters, numbers, dots or hyphens." },
      { status: 400, headers: { "Cache-Control": "private, no-store" } },
    );
  }
  return proxyMonteCarlo(request, { kind: "run", ticker }, "run");
}
