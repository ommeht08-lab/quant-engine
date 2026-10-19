// Pure helpers for the Monte Carlo proxy routes under `app/api/monte-carlo/`.
// Kept free of Next.js imports so the request-shaping rules can be unit-tested
// with plain values. Nothing here ever sees, builds or logs a credential.

export const MONTE_CARLO_BACKEND_REQUEST_TIMEOUT_MS = 45_000;

const TICKER_PATTERN = /^[A-Z][A-Z0-9.-]{0,9}$/;
const SNAPSHOT_ID_PATTERN = /^[a-f0-9]{64}$/;
// The backend, the proxy and the browser all handle seeds as JSON numbers, so
// the largest exactly representable integer is the shared upper bound.
const MAX_SEED = Number.MAX_SAFE_INTEGER;

export type MonteCarloRoute =
  | { kind: "describe" }
  | { kind: "run"; ticker: string }
  | { kind: "export"; snapshotId: string };

export function normalizeMonteCarloTicker(raw: string): string | null {
  const ticker = raw.trim().toUpperCase();
  return TICKER_PATTERN.test(ticker) ? ticker : null;
}

export function isValidSnapshotId(raw: string): boolean {
  return SNAPSHOT_ID_PATTERN.test(raw);
}

export type SeedParse = { ok: true; seed: string | null } | { ok: false };

/**
 * The only query parameter forwarded to the backend is a canonical `seed`.
 * Anything else (unknown keys, repeated keys, signs, decimals, exponents,
 * leading zeros, values above 2^53 - 1) is rejected here
 * rather than passed through.
 */
export function parseSeedQuery(search: string): SeedParse {
  const params = new URLSearchParams(search);
  const keys = Array.from(params.keys());
  if (keys.length === 0) return { ok: true, seed: null };
  if (keys.length !== 1 || keys[0] !== "seed" || params.getAll("seed").length !== 1) return { ok: false };
  const value = params.get("seed") ?? "";
  if (!/^(0|[1-9][0-9]{0,15})$/.test(value)) return { ok: false };
  if (Number(value) > MAX_SEED) return { ok: false };
  return { ok: true, seed: value };
}

/** Backend path for a validated route (never built from unvalidated input). */
export function monteCarloBackendPath(route: MonteCarloRoute): string {
  switch (route.kind) {
    case "describe":
      return "/api/monte-carlo";
    case "run":
      return `/api/monte-carlo/${encodeURIComponent(route.ticker)}`;
    case "export":
      return `/api/monte-carlo/snapshots/${encodeURIComponent(route.snapshotId)}`;
  }
}

/**
 * Headers worth forwarding from the backend: the retry hint on a 409, as a
 * whole number of seconds only.
 */
export function forwardedMonteCarloHeaders(upstream: { get(name: string): string | null }): Record<string, string> {
  const retryAfter = upstream.get("Retry-After");
  return retryAfter !== null && /^[0-9]{1,4}$/.test(retryAfter) ? { "Retry-After": retryAfter } : {};
}

export const REPLAY_BUNDLE_SCHEMA = "mc-dcf-replay-bundle-v1";

/**
 * A replay bundle is verified by hashing its numbers, and the hash distinguishes `1.0` from `1`.
 * Parsing and re-serializing it in JavaScript rewrites such numbers, so the export route forwards
 * the backend's bytes untouched. This only checks that the text is a replay bundle worth forwarding.
 */
export function isReplayBundleText(text: string): boolean {
  try {
    const body: unknown = JSON.parse(text);
    return typeof body === "object" && body !== null && !Array.isArray(body) &&
      (body as Record<string, unknown>).schema === REPLAY_BUNDLE_SCHEMA;
  } catch {
    return false;
  }
}

export function exportFilename(snapshotId: string, seed: string | null): string {
  const suffix = seed === null ? "" : `-seed-${seed}`;
  return `mc-dcf-${snapshotId.slice(0, 12)}${suffix}.json`;
}

const BACKEND_FAILURE_COPY: Record<string, string> = {
  VALUATION_BACKEND_TIMEOUT: "The simulation service did not respond in time. Please try again.",
  VALUATION_BACKEND_UNREACHABLE: "The simulation service did not respond. Please try again shortly.",
  VALUATION_BACKEND_INVALID_RESPONSE: "The simulation service returned an unexpected response.",
};

/**
 * The shared backend-failure classifier (used by the valuation proxy) words its
 * messages for the valuation workspace ("Portfolio data remains available
 * below"), which is wrong here. Keep its stable `code`, replace the text.
 */
export function withMonteCarloWording(body: unknown): unknown {
  if (typeof body !== "object" || body === null) return body;
  const record = body as Record<string, unknown>;
  const code = typeof record.code === "string" ? record.code : null;
  return code !== null && code in BACKEND_FAILURE_COPY ? { ...record, error: BACKEND_FAILURE_COPY[code] } : body;
}
