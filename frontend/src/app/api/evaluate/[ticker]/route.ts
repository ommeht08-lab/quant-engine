import { NextResponse } from "next/server";

import { requireSession } from "@/lib/auth";
import { fetchWithTimeout } from "@/lib/backend-fetch";
import { classifyFetchError, normalizeBackendResponse, type NormalizedBackendResult } from "@/lib/backend-response";
import { assertSecretMeetsRequirements, VALUATION_API_TOKEN_REQUIREMENT } from "@/lib/secret-validation";
import { assertSafeValuationApiUrl } from "@/lib/valuation-api-url";

// Live DCF valuation — always fresh, never cached at this layer (the
// Python backend/`yfinance` layer does its own caching).
export const dynamic = "force-dynamic";

// The three-layer hard-duration contract (see
// tests/test_vercel_config.py's TestLayeredTimeoutOrdering for the
// enforced cross-file proof of all three values together):
//
//   backend platform hard stop (vercel.json maxDuration):      40s
// < frontend upstream wait (VALUATION_BACKEND_REQUEST_TIMEOUT_MS below): 45s
// < this route's own function limit (maxDuration below):       60s
//
// Vercel terminates the Python backend invocation once it exceeds ITS
// 40s maxDuration and returns a platform 504 to whoever's waiting. This
// ordering (40s < 45s) is DESIGNED so that platform 504 normally arrives
// while this route is still within its own 45s wait, so it observes and
// normalizes it itself (see `@/lib/backend-response`) instead of having
// already given up — but the configured inequality only guarantees the
// declared limits' ordering, not real-world timing: runtime scheduling,
// process teardown, and network propagation can all consume part of the
// 5s margin. If propagation ever does exceed it, this route's own fetch
// simply hits its AbortError path first and returns the same controlled
// 504 VALUATION_BACKEND_TIMEOUT — so the observable outcome is identical
// either way. 60s here keeps 15s of headroom after the 45s wait gives up,
// for this function to build and return a controlled response before
// Vercel would kill THIS function too.
export const maxDuration = 60;

const VALUATION_BACKEND_REQUEST_TIMEOUT_MS = 45_000;

function isProductionEnvironment(): boolean {
  return process.env.NODE_ENV === "production";
}

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
 * service actually runs per environment. `VALUATION_API_URL` is
 * validated by `assertSafeValuationApiUrl` before any fetch — see that
 * function's docstring for exactly what's enforced and the residual
 * "an operator must configure this explicitly in production" caveat it
 * cannot resolve on its own.
 *
 * The backend now requires a `VALUATION_API_TOKEN` bearer token
 * (`src.api.main.require_service_token`) — read here from the
 * server-only `VALUATION_API_TOKEN` env var (validated against the same
 * requirements the backend itself enforces — see
 * `src/lib/secret-validation.ts`) and forwarded in the `Authorization`
 * header. It is never sent to, or readable by, the browser: this Route
 * Handler only ever returns the backend's JSON response body, never the
 * request it made to get it, and no error path below includes the
 * token value or the raw backend response body in a log line.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ ticker: string }> }
) {
  if (!(await requireSession())) {
    return NextResponse.json({ error: "Not authenticated." }, { status: 401 });
  }

  let serviceToken: string;
  try {
    const rawToken = process.env.VALUATION_API_TOKEN;
    assertSecretMeetsRequirements(rawToken, VALUATION_API_TOKEN_REQUIREMENT);
    serviceToken = rawToken;
  } catch (error) {
    console.error(
      "VALUATION_API_TOKEN misconfigured:",
      error instanceof Error ? error.message : "unknown error"
    );
    return NextResponse.json(
      {
        code: "VALUATION_BACKEND_UNCONFIGURED",
        error: "Live valuation is not connected to this deployment yet.",
      },
      { status: 503 }
    );
  }

  let backendOrigin: string;
  try {
    const isProduction = isProductionEnvironment();
    // The `http://localhost:8000` development convenience default only
    // applies OUTSIDE production — see assertSafeValuationApiUrl's
    // docstring: production must configure this explicitly, with no
    // safe fallback.
    const rawUrl = process.env.VALUATION_API_URL ?? (isProduction ? "" : "http://localhost:8000");
    backendOrigin = assertSafeValuationApiUrl(rawUrl, { isProduction });
  } catch (error) {
    console.error(
      "VALUATION_API_URL misconfigured:",
      error instanceof Error ? error.message : "unknown error"
    );
    return NextResponse.json(
      {
        code: "VALUATION_BACKEND_UNCONFIGURED",
        error: "Live valuation is not connected to this deployment yet.",
      },
      { status: 503 }
    );
  }

  const { ticker } = await params;
  const { search } = new URL(request.url);

  // Built from the VALIDATED origin via the URL constructor, never by
  // concatenating strings — avoids the class of bug where a missing/
  // extra slash or an unescaped character silently changes which host
  // or path the request actually goes to.
  const targetUrl = new URL(`/api/evaluate/${encodeURIComponent(ticker)}`, backendOrigin);
  targetUrl.search = search;

  let response: Response;
  try {
    response = await fetchWithTimeout(
      targetUrl,
      { headers: { Authorization: `Bearer ${serviceToken}` } },
      VALUATION_BACKEND_REQUEST_TIMEOUT_MS
    );
  } catch (error) {
    const isTimeout = error instanceof Error && error.name === "AbortError";
    // Fixed classification labels only — never the raw Error, its
    // message, the target URL, or the raw ticker route parameter. Any
    // of those could carry attacker-controlled input or leak into logs
    // in a way backend-response.ts's own sanitization can't protect
    // against, since this log line is separate from its returned body.
    console.error(
      isTimeout ? "valuation backend request timed out" : "valuation backend request failed"
    );
    return toNextResponse(classifyFetchError(error));
  }

  return toNextResponse(await normalizeBackendResponse(response));
}

function toNextResponse(result: NormalizedBackendResult): NextResponse {
  const nextResponse = NextResponse.json(result.body, { status: result.status });
  for (const [key, value] of Object.entries(result.headers)) {
    nextResponse.headers.set(key, value);
  }
  return nextResponse;
}
