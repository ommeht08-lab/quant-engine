// Focused timeout/cancellation behavior tests for `fetchWithTimeout`
// (frontend/src/lib/backend-fetch.ts), extracted from the valuation-
// backend proxy route specifically so this mechanism could be tested
// here rather than only through the full Route Handler (which needs a
// live Next.js request scope for its real session/auth check that a
// bare test doesn't have). No real network request is made anywhere in
// this file — `globalThis.fetch` is replaced for the duration of each
// test and restored afterward.
//
// Run with:
//   node --test src/lib/backend-fetch.test.ts

import assert from "node:assert/strict";
import { test } from "node:test";

import { fetchWithTimeout } from "./backend-fetch.ts";

function abortableHangingFetch(): typeof fetch {
  return ((_url: string | URL, init?: RequestInit) => {
    return new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        const abortError = new Error("This operation was aborted");
        abortError.name = "AbortError";
        reject(abortError);
      });
    });
  }) as typeof fetch;
}

test("aborts the underlying fetch once the timeout elapses", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = abortableHangingFetch();

  try {
    const pending = fetchWithTimeout("https://backend.example.com/x", {}, 45_000);
    t.mock.timers.tick(45_000);
    await assert.rejects(pending, (error: Error) => error.name === "AbortError");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("does not abort before the timeout elapses", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const originalFetch = globalThis.fetch;

  // The mocked fetch captures the signal but does NOT resolve on its
  // own — resolution is controlled explicitly below, after the abort
  // state has already been checked, so this test can distinguish
  // "still pending, not aborted" from "already finished" instead of
  // racing a fetch that resolves before the clock is ever advanced.
  let capturedSignal: AbortSignal | undefined;
  let resolveFetch: ((response: Response) => void) | undefined;
  const pendingFetch = new Promise<Response>((resolve) => {
    resolveFetch = resolve;
  });
  globalThis.fetch = ((_url: string | URL, init?: RequestInit) => {
    capturedSignal = init?.signal ?? undefined;
    return pendingFetch;
  }) as typeof fetch;

  try {
    // fetchWithTimeout schedules its setTimeout(45_000) as part of
    // calling `fetch` synchronously (the mock above already ran and
    // captured the signal by the time this line finishes) — only THEN
    // does advancing the clock mean anything.
    const pending = fetchWithTimeout("https://backend.example.com/x", {}, 45_000);

    t.mock.timers.tick(44_999);
    assert.equal(capturedSignal?.aborted, false);

    resolveFetch?.(new Response(null, { status: 200 }));
    const response = await pending;
    assert.equal(response.status, 200);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("resolves with the backend's response when it answers before the timeout", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ ok: true }), { status: 200 })) as typeof fetch;

  try {
    const response = await fetchWithTimeout("https://backend.example.com/x", {}, 45_000);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("propagates a non-timeout fetch failure unchanged", async () => {
  const originalFetch = globalThis.fetch;
  const networkError = new Error("getaddrinfo ENOTFOUND backend.example.com");
  globalThis.fetch = (async () => {
    throw networkError;
  }) as typeof fetch;

  try {
    await assert.rejects(
      fetchWithTimeout("https://backend.example.com/x", {}, 45_000),
      (error: Error) => error === networkError
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("forwards the caller's init options (headers) to the underlying fetch", async () => {
  const originalFetch = globalThis.fetch;
  let capturedHeaders: HeadersInit | undefined;
  globalThis.fetch = ((_url: string | URL, init?: RequestInit) => {
    capturedHeaders = init?.headers;
    return Promise.resolve(new Response(null, { status: 200 }));
  }) as typeof fetch;

  try {
    await fetchWithTimeout(
      "https://backend.example.com/x",
      { headers: { Authorization: "Bearer test-token-not-a-real-secret" } },
      45_000
    );
    assert.deepEqual(capturedHeaders, { Authorization: "Bearer test-token-not-a-real-secret" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
