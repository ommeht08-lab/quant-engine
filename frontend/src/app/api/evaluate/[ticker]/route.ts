import { NextResponse } from "next/server";

import { requireSession } from "@/lib/auth";
import { assertSecretMeetsRequirements, VALUATION_API_TOKEN_REQUIREMENT } from "@/lib/secret-validation";
import { assertSafeValuationApiUrl } from "@/lib/valuation-api-url";

// Live DCF valuation — always fresh, never cached at this layer (the
// Python backend/`yfinance` layer does its own caching).
export const dynamic = "force-dynamic";

// Bounded so a hung/slow backend can't tie up this route's serverless
// invocation indefinitely — aborted via `AbortController`, not just a
// client-side "give up and hope the server request stops too" timeout.
const VALUATION_BACKEND_REQUEST_TIMEOUT_MS = 10_000;

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

  const timeoutController = new AbortController();
  const timeoutHandle = setTimeout(() => timeoutController.abort(), VALUATION_BACKEND_REQUEST_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(targetUrl, {
      headers: { Authorization: `Bearer ${serviceToken}` },
      signal: timeoutController.signal,
    });
  } catch (error) {
    const isTimeout = error instanceof Error && error.name === "AbortError";
    console.error(
      `Failed to reach the valuation backend for ${ticker}:`,
      isTimeout ? "request timed out" : error instanceof Error ? error.message : "unknown error"
    );
    return NextResponse.json(
      {
        code: "VALUATION_BACKEND_UNREACHABLE",
        error: "The live valuation service did not respond. Portfolio data remains available below.",
      },
      { status: 502 }
    );
  } finally {
    clearTimeout(timeoutHandle);
  }

  const body = await response.json().catch(() => null);
  return NextResponse.json(body, { status: response.status });
}
