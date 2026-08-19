// Fetch wrapped with a bounded timeout via `AbortController` — extracted
// out of the valuation-backend proxy route (`app/api/evaluate/[ticker]/
// route.ts`) so the timeout/cancellation mechanism itself can be
// unit-tested in isolation (with mocked timers, see
// `backend-fetch.test.ts`) without needing a live Next.js request scope,
// which that route's real session/auth check (`requireSession()`, via
// `next/headers`'s `cookies()`) requires and a bare test doesn't have.
//
// Behavior is unchanged from the inline version this replaces: on
// timeout, the underlying `fetch` rejects with an `AbortError` (the
// standard behavior of an aborted `fetch`) — this function does not
// catch or reinterpret that; the caller's existing error handling
// (distinguishing `error.name === "AbortError"` for logging, but
// treating every fetch failure the same way in the response) is
// unaffected.

export async function fetchWithTimeout(
  url: string | URL,
  init: RequestInit,
  timeoutMs: number
): Promise<Response> {
  const controller = new AbortController();
  const timeoutHandle = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timeoutHandle);
  }
}
