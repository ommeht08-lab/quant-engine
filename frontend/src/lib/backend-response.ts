// Normalizes every possible outcome of the valuation-backend proxy
// fetch (frontend/src/app/api/evaluate/[ticker]/route.ts) into one
// small, framework-agnostic shape — kept separate from route.ts so it
// can be unit-tested with plain Response objects and a mocked fetch,
// without constructing a real authenticated Next.js request context
// (route.ts's own `requireSession()` needs one; this module never
// touches it).
//
// Every path here is deliberately conservative about what it exposes:
// never the raw upstream body when it isn't valid JSON, never a raw
// Error message, never a URL, never the Authorization header or
// VALUATION_API_TOKEN. The only things ever forwarded from the
// upstream response are its HTTP status (when the body is valid JSON),
// that already-safe JSON body, and its X-Request-ID header.

export interface NormalizedBackendResult {
  status: number;
  body: unknown;
  headers: Record<string, string>;
}

const REQUEST_ID_HEADER = "X-Request-ID";

function requestIdHeaders(response: Response): Record<string, string> {
  const requestId = response.headers.get(REQUEST_ID_HEADER);
  return requestId ? { [REQUEST_ID_HEADER]: requestId } : {};
}

/**
 * Classifies a REJECTED `fetchWithTimeout(...)` call — either the
 * AbortError its own internal timeout produces, or any other fetch/
 * network failure (DNS, connection refused, TLS, ...). Never includes
 * the error's own message, the target URL, or any credential in the
 * returned body.
 */
export function classifyFetchError(error: unknown): NormalizedBackendResult {
  const isTimeout = error instanceof Error && error.name === "AbortError";

  if (isTimeout) {
    return {
      status: 504,
      body: {
        code: "VALUATION_BACKEND_TIMEOUT",
        error: "The live valuation service did not respond in time.",
      },
      headers: {},
    };
  }

  return {
    status: 502,
    body: {
      code: "VALUATION_BACKEND_UNREACHABLE",
      error: "The live valuation service did not respond. Portfolio data remains available below.",
    },
    headers: {},
  };
}

/**
 * Shared shape for both "body wasn't valid JSON" and "body couldn't even
 * be read" — a 504 upstream status normalizes to a controlled 504
 * VALUATION_BACKEND_TIMEOUT (Vercel itself returns a 504, not
 * necessarily JSON, when the Python function exceeds its own
 * `maxDuration` — see vercel.json), every other status normalizes to a
 * controlled 502 VALUATION_BACKEND_INVALID_RESPONSE. Never includes the
 * raw body text or the read/parse failure's own message.
 */
function invalidBodyResult(upstreamStatus: number, headers: Record<string, string>): NormalizedBackendResult {
  if (upstreamStatus === 504) {
    return {
      status: 504,
      body: {
        code: "VALUATION_BACKEND_TIMEOUT",
        error: "The live valuation service did not respond in time.",
      },
      headers,
    };
  }
  return {
    status: 502,
    body: {
      code: "VALUATION_BACKEND_INVALID_RESPONSE",
      error: "The live valuation service returned an unexpected response.",
    },
    headers,
  };
}

/**
 * Normalizes a Response the backend fetch actually completed with (as
 * opposed to a rejected fetch — see `classifyFetchError` for that).
 * Reads the body once as text, then attempts to parse it as JSON:
 *
 * - Valid JSON: the upstream's own HTTP status and JSON body are
 *   preserved as-is — the Python backend is the source of truth for
 *   its own success/error shapes, this proxy doesn't reinterpret them.
 * - Invalid JSON (an HTML error page, an empty body, a platform-level
 *   error page instead of the backend's own JSON, ...): see
 *   `invalidBodyResult`.
 * - The body couldn't even be read (`response.text()` itself rejects —
 *   a connection reset, a truncated stream, ...): this function must
 *   stay total over that failure rather than let it escape as an
 *   uncontrolled rejection, so it's caught and normalized exactly like
 *   invalid JSON via the same `invalidBodyResult` — the read failure's
 *   own message is never inspected or exposed.
 *
 * In every branch, the upstream's X-Request-ID response header (if
 * present) is collected BEFORE the body read is attempted, so it's
 * still forwarded even when the read itself fails.
 */
export async function normalizeBackendResponse(response: Response): Promise<NormalizedBackendResult> {
  const headers = requestIdHeaders(response);

  let rawText: string;
  try {
    rawText = await response.text();
  } catch {
    return invalidBodyResult(response.status, headers);
  }

  try {
    const body = JSON.parse(rawText);
    return { status: response.status, body, headers };
  } catch {
    return invalidBodyResult(response.status, headers);
  }
}
