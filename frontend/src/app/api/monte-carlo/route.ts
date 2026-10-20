import { NextResponse } from "next/server";

import { MONTE_CARLO_METADATA } from "@/lib/monte-carlo-metadata";

import { applyRateLimit } from "./proxy";

// Allowlist, configuration and disclosures, served from the metadata bundled with this deployment so the valuation
// backend is never started for it. The backend remains authoritative: it enforces the allowlist and reports the
// identity of its own settings with each result. Same light rate limit as before.
export const dynamic = "force-dynamic";
export const maxDuration = 60; // the layered timeout contract (tests/test_vercel_config.py) covers every Monte Carlo route

export async function GET(request: Request) {
  if (new URL(request.url).search !== "") {
    return NextResponse.json(
      { code: "MONTE_CARLO_INVALID_REQUEST", error: "This endpoint takes no query parameters." },
      { status: 400, headers: { "Cache-Control": "private, no-store" } },
    );
  }
  const limited = await applyRateLimit(request, "light");
  if (limited) return limited;
  return NextResponse.json(MONTE_CARLO_METADATA, { headers: { "Cache-Control": "private, no-store" } });
}
