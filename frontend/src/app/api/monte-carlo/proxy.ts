import { NextResponse } from "next/server";

import { deriveSubkey } from "@/lib/auth";
import { fetchWithTimeout } from "@/lib/backend-fetch";
import { classifyFetchError, normalizeBackendResponse, type NormalizedBackendResult } from "@/lib/backend-response";
import { firstForwardedClientIdentifier } from "@/lib/client-identifier";
import {
  MONTE_CARLO_BACKEND_REQUEST_TIMEOUT_MS,
  exportFilename,
  forwardedMonteCarloHeaders,
  isReplayBundleText,
  monteCarloBackendPath,
  parseSeedQuery,
  withMonteCarloWording,
  type MonteCarloRoute,
} from "@/lib/monte-carlo-proxy";
import {
  MONTE_CARLO_RATE_LIMIT_FAIL_OPEN_ENV_VAR,
  MONTE_CARLO_RATE_LIMIT_IDENTIFIER_LABEL,
  MONTE_CARLO_WINDOW_SECONDS,
  decideMonteCarloRateLimit,
  monteCarloRateLimitKey,
  type MonteCarloRateLimitKind,
} from "@/lib/monte-carlo-rate-limit";
import type { PublicEvaluationRateLimitDecision } from "@/lib/public-evaluation-rate-limit";
import { assertSecretMeetsRequirements, VALUATION_API_TOKEN_REQUIREMENT } from "@/lib/secret-validation";
import { assertSafeValuationApiUrl } from "@/lib/valuation-api-url";

// Shared by the three Monte Carlo route handlers. Same trust model as
// `app/api/evaluate/[ticker]/route.ts`: the browser only ever talks to this
// origin; the backend service token and origin are read server-side, validated,
// and never returned or logged. Timeouts follow the same layered contract
// (backend 40 s < upstream wait 45 s < route limit 60 s), enforced by
// tests/test_vercel_config.py.

const NO_STORE = { "Cache-Control": "private, no-store" };

function isProductionEnvironment(): boolean {
  return process.env.NODE_ENV === "production";
}

function jsonError(status: number, code: string, error: string, headers: Record<string, string> = {}) {
  return NextResponse.json({ code, error }, { status, headers: { ...NO_STORE, ...headers } });
}

async function applyRateLimit(request: Request, kind: MonteCarloRateLimitKind): Promise<NextResponse | null> {
  let decision: PublicEvaluationRateLimitDecision = "allow";
  try {
    const rawClientIdentifier = firstForwardedClientIdentifier(request.headers.get("x-forwarded-for"));
    const key = monteCarloRateLimitKey(kind, rawClientIdentifier, deriveSubkey(MONTE_CARLO_RATE_LIMIT_IDENTIFIER_LABEL));
    decision = await decideMonteCarloRateLimit({
      kind,
      key,
      isProduction: isProductionEnvironment(),
      failOpenOverride: process.env[MONTE_CARLO_RATE_LIMIT_FAIL_OPEN_ENV_VAR],
    });
  } catch (error) {
    console.error(
      "Monte Carlo rate limiter could not initialize or apply its quota:",
      error instanceof Error ? error.message : "unknown error",
    );
    if (isProductionEnvironment()) {
      return jsonError(503, "MONTE_CARLO_RATE_LIMIT_UNAVAILABLE", "The simulation is temporarily unavailable. Please try again shortly.");
    }
    // Local development and CI may omit SESSION_SECRET; match the limiter's non-production fail-open policy.
  }
  if (decision === "limited") {
    return jsonError(429, "MONTE_CARLO_RATE_LIMITED", "Too many simulation requests. Please try again in a few minutes.", {
      "Retry-After": String(MONTE_CARLO_WINDOW_SECONDS),
    });
  }
  if (decision === "unavailable") {
    console.error(
      `Monte Carlo rate limiter unavailable; failing closed in production. Set ${MONTE_CARLO_RATE_LIMIT_FAIL_OPEN_ENV_VAR}=true to override.`,
    );
    return jsonError(503, "MONTE_CARLO_RATE_LIMIT_UNAVAILABLE", "The simulation is temporarily unavailable. Please try again shortly.");
  }
  return null;
}

function toNextResponse(result: NormalizedBackendResult, extraHeaders: Record<string, string> = {}): NextResponse {
  const response = NextResponse.json(withMonteCarloWording(result.body), { status: result.status });
  for (const [key, value] of Object.entries({ ...NO_STORE, ...result.headers, ...extraHeaders })) {
    response.headers.set(key, value);
  }
  return response;
}

export async function proxyMonteCarlo(
  request: Request,
  route: MonteCarloRoute,
  kind: MonteCarloRateLimitKind,
): Promise<NextResponse> {
  // Reject malformed requests before spending quota or contacting the backend.
  const { search } = new URL(request.url);
  const seed = route.kind === "describe" ? { ok: true as const, seed: null } : parseSeedQuery(search);
  if (route.kind === "describe" && search !== "") {
    return jsonError(400, "MONTE_CARLO_INVALID_REQUEST", "This endpoint takes no query parameters.");
  }
  if (!seed.ok) {
    return jsonError(400, "MONTE_CARLO_INVALID_REQUEST", "The seed must be a whole number from 0 to 9007199254740991.");
  }

  const limited = await applyRateLimit(request, kind);
  if (limited) return limited;

  let serviceToken: string;
  try {
    const rawToken = process.env.VALUATION_API_TOKEN;
    assertSecretMeetsRequirements(rawToken, VALUATION_API_TOKEN_REQUIREMENT);
    serviceToken = rawToken;
  } catch (error) {
    console.error("VALUATION_API_TOKEN misconfigured:", error instanceof Error ? error.message : "unknown error");
    return jsonError(503, "VALUATION_BACKEND_UNCONFIGURED", "Live valuation is not connected to this deployment yet.");
  }

  let backendOrigin: string;
  try {
    const isProduction = isProductionEnvironment();
    const rawUrl = process.env.VALUATION_API_URL ?? (isProduction ? "" : "http://localhost:8000");
    backendOrigin = assertSafeValuationApiUrl(rawUrl, { isProduction });
  } catch (error) {
    console.error("VALUATION_API_URL misconfigured:", error instanceof Error ? error.message : "unknown error");
    return jsonError(503, "VALUATION_BACKEND_UNCONFIGURED", "Live valuation is not connected to this deployment yet.");
  }

  // Built from the validated origin and validated path/seed only.
  const targetUrl = new URL(monteCarloBackendPath(route), backendOrigin);
  if (seed.seed !== null) targetUrl.searchParams.set("seed", seed.seed);

  let response: Response;
  try {
    response = await fetchWithTimeout(
      targetUrl,
      { headers: { Authorization: `Bearer ${serviceToken}` } },
      MONTE_CARLO_BACKEND_REQUEST_TIMEOUT_MS,
    );
  } catch (error) {
    console.error(
      error instanceof Error && error.name === "AbortError"
        ? "monte carlo backend request timed out"
        : "monte carlo backend request failed",
    );
    return toNextResponse(classifyFetchError(error));
  }

  if (route.kind === "export" && response.ok) {
    // Byte-faithful: the bundle's hashes depend on number formatting that a JS parse/serialize would change.
    const fallback = response.clone();
    const text = await response.text();
    if (isReplayBundleText(text)) {
      return new NextResponse(text, {
        status: response.status,
        headers: {
          ...NO_STORE,
          "Content-Type": "application/json",
          ...forwardedMonteCarloHeaders(response.headers),
          "Content-Disposition": `attachment; filename="${exportFilename(route.snapshotId, seed.seed)}"`,
        },
      });
    }
    return toNextResponse(await normalizeBackendResponse(fallback));
  }

  const result = await normalizeBackendResponse(response);
  const extra: Record<string, string> = forwardedMonteCarloHeaders(response.headers);
  if (route.kind === "export" && result.status === 200) {
    extra["Content-Disposition"] = `attachment; filename="${exportFilename(route.snapshotId, seed.seed)}"`;
  }
  return toNextResponse(result, extra);
}
