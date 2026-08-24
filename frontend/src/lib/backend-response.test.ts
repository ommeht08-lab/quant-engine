// Focused tests for backend-response.ts's two pure normalization
// functions. No real network call is possible anywhere in this file —
// every input is either a plain Error object or an in-memory Response
// constructed directly (`new Response(...)`), never a real fetch.
//
// Run with:
//   node --test src/lib/backend-response.test.ts

import assert from "node:assert/strict";
import { test } from "node:test";

import { classifyFetchError, normalizeBackendResponse } from "./backend-response.ts";

function abortError(): Error {
  const error = new Error("This operation was aborted");
  error.name = "AbortError";
  return error;
}

test("classifyFetchError: an AbortError is classified as a controlled 504 timeout", () => {
  const result = classifyFetchError(abortError());

  assert.equal(result.status, 504);
  assert.deepEqual(result.body, {
    code: "VALUATION_BACKEND_TIMEOUT",
    error: "The live valuation service did not respond in time.",
  });
  assert.deepEqual(result.headers, {});
});

test("classifyFetchError: a non-timeout fetch failure is classified as a controlled 502", () => {
  const result = classifyFetchError(new Error("getaddrinfo ENOTFOUND backend.example.com"));

  assert.equal(result.status, 502);
  assert.deepEqual(result.body, {
    code: "VALUATION_BACKEND_UNREACHABLE",
    error: "The live valuation service did not respond. Portfolio data remains available below.",
  });
});

test("classifyFetchError: AbortError and a non-timeout error remain distinguishable", () => {
  const timeoutResult = classifyFetchError(abortError());
  const networkResult = classifyFetchError(new Error("connection reset"));

  assert.notEqual(timeoutResult.status, networkResult.status);
  assert.notEqual(
    (timeoutResult.body as { code: string }).code,
    (networkResult.body as { code: string }).code
  );
});

test("classifyFetchError: the raw error message never appears in the result", () => {
  const secretLookingMessage = "fetch failed: Authorization: Bearer super-secret-token-value";
  const result = classifyFetchError(new Error(secretLookingMessage));

  const serialized = JSON.stringify(result);
  assert.ok(!serialized.includes(secretLookingMessage));
  assert.ok(!serialized.includes("super-secret-token-value"));
});

test("classifyFetchError: a non-Error thrown value is still classified as non-timeout", () => {
  const result = classifyFetchError("a plain string, not an Error instance");

  assert.equal(result.status, 502);
  assert.equal((result.body as { code: string }).code, "VALUATION_BACKEND_UNREACHABLE");
});

test("normalizeBackendResponse: a valid JSON 200 success body is preserved as-is", async () => {
  const upstream = new Response(JSON.stringify({ ticker: "AAPL", wacc: 0.09 }), { status: 200 });

  const result = await normalizeBackendResponse(upstream);

  assert.equal(result.status, 200);
  assert.deepEqual(result.body, { ticker: "AAPL", wacc: 0.09 });
});

test("normalizeBackendResponse: a valid JSON 4xx body is preserved with its own status", async () => {
  const upstream = new Response(
    JSON.stringify({ detail: "revenue_growth_rate out of documented range" }),
    { status: 422 }
  );

  const result = await normalizeBackendResponse(upstream);

  assert.equal(result.status, 422);
  assert.deepEqual(result.body, { detail: "revenue_growth_rate out of documented range" });
});

test("normalizeBackendResponse: a valid JSON 5xx body is preserved with its own status", async () => {
  const upstream = new Response(JSON.stringify({ detail: "Unexpected error running valuation." }), {
    status: 500,
  });

  const result = await normalizeBackendResponse(upstream);

  assert.equal(result.status, 500);
  assert.deepEqual(result.body, { detail: "Unexpected error running valuation." });
});

test("normalizeBackendResponse: X-Request-ID is forwarded when present, on a JSON response", async () => {
  const upstream = new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "X-Request-ID": "req-abc-123" },
  });

  const result = await normalizeBackendResponse(upstream);

  assert.deepEqual(result.headers, { "X-Request-ID": "req-abc-123" });
});

test("normalizeBackendResponse: no X-Request-ID header is forwarded when the upstream didn't supply one", async () => {
  const upstream = new Response(JSON.stringify({ ok: true }), { status: 200 });

  const result = await normalizeBackendResponse(upstream);

  assert.deepEqual(result.headers, {});
});

test("normalizeBackendResponse: a non-JSON 504 is normalized to a controlled timeout response", async () => {
  const upstream = new Response("<html><body>Gateway Timeout</body></html>", {
    status: 504,
    headers: { "X-Request-ID": "req-timeout-1" },
  });

  const result = await normalizeBackendResponse(upstream);

  assert.equal(result.status, 504);
  assert.deepEqual(result.body, {
    code: "VALUATION_BACKEND_TIMEOUT",
    error: "The live valuation service did not respond in time.",
  });
  assert.deepEqual(result.headers, { "X-Request-ID": "req-timeout-1" });
});

test("normalizeBackendResponse: a non-JSON non-504 response is normalized to a controlled 502", async () => {
  const upstream = new Response("<html><body>Bad Gateway</body></html>", { status: 502 });

  const result = await normalizeBackendResponse(upstream);

  assert.equal(result.status, 502);
  assert.deepEqual(result.body, {
    code: "VALUATION_BACKEND_INVALID_RESPONSE",
    error: "The live valuation service returned an unexpected response.",
  });
});

test("normalizeBackendResponse: a non-JSON 200 response is also normalized to a controlled 502", async () => {
  // Not just error statuses -- an unexpected non-JSON 200 (e.g. an
  // intermediary returning an HTML page with a 200) must not be passed
  // through as if it were a real, successful valuation body.
  const upstream = new Response("not json at all", { status: 200 });

  const result = await normalizeBackendResponse(upstream);

  assert.equal(result.status, 502);
  assert.equal((result.body as { code: string }).code, "VALUATION_BACKEND_INVALID_RESPONSE");
});

test("normalizeBackendResponse: an empty body is treated as non-JSON, not a crash", async () => {
  const upstream = new Response("", { status: 502 });

  const result = await normalizeBackendResponse(upstream);

  assert.equal(result.status, 502);
  assert.equal((result.body as { code: string }).code, "VALUATION_BACKEND_INVALID_RESPONSE");
});

test("normalizeBackendResponse: the raw non-JSON body text never appears anywhere in the result", async () => {
  const secretLookingBody =
    "<html>Upstream failure — Authorization: Bearer super-secret-value leaked-in-a-stack-trace</html>";
  const upstream = new Response(secretLookingBody, { status: 502 });

  const result = await normalizeBackendResponse(upstream);

  const serialized = JSON.stringify(result);
  assert.ok(!serialized.includes(secretLookingBody));
  assert.ok(!serialized.includes("super-secret-value"));
  assert.ok(!serialized.includes("<html>"));
});

function responseWithFailingBody(status: number, headers?: Record<string, string>): Response {
  // A ReadableStream whose pull() rejects -- simulates a connection
  // reset / truncated stream, structurally, with no real network
  // involved. Response.text() surfaces this as a rejection.
  const stream = new ReadableStream({
    pull() {
      return Promise.reject(new Error("simulated body-read failure: Authorization: Bearer super-secret-token"));
    },
  });
  return new Response(stream, { status, headers });
}

test("normalizeBackendResponse: a body-read failure on a 502 upstream status returns the controlled 502", async () => {
  const upstream = responseWithFailingBody(502);

  const result = await normalizeBackendResponse(upstream);

  assert.equal(result.status, 502);
  assert.deepEqual(result.body, {
    code: "VALUATION_BACKEND_INVALID_RESPONSE",
    error: "The live valuation service returned an unexpected response.",
  });
});

test("normalizeBackendResponse: a body-read failure on a 504 upstream status returns the controlled 504", async () => {
  const upstream = responseWithFailingBody(504);

  const result = await normalizeBackendResponse(upstream);

  assert.equal(result.status, 504);
  assert.deepEqual(result.body, {
    code: "VALUATION_BACKEND_TIMEOUT",
    error: "The live valuation service did not respond in time.",
  });
});

test("normalizeBackendResponse: a secret-looking body-read exception message never appears in the result", async () => {
  const upstream = responseWithFailingBody(502);

  const result = await normalizeBackendResponse(upstream);

  const serialized = JSON.stringify(result);
  assert.ok(!serialized.includes("simulated body-read failure"));
  assert.ok(!serialized.includes("super-secret-token"));
});

test("normalizeBackendResponse: X-Request-ID remains forwarded when the body read then fails", async () => {
  const upstream = responseWithFailingBody(502, { "X-Request-ID": "req-body-fail-1" });

  const result = await normalizeBackendResponse(upstream);

  assert.deepEqual(result.headers, { "X-Request-ID": "req-body-fail-1" });
});

test("normalizeBackendResponse: does not reject for a body-read failure -- it stays total", async () => {
  const upstream = responseWithFailingBody(500);

  await assert.doesNotReject(normalizeBackendResponse(upstream));
});

test("normalizeBackendResponse: no real network call occurs -- purely in-memory Response objects", async () => {
  // Structural proof, not an assertion on behavior: this whole file
  // never calls `fetch`, only constructs `Response` objects directly.
  const originalFetch = globalThis.fetch;
  let fetchWasCalled = false;
  globalThis.fetch = (async () => {
    fetchWasCalled = true;
    throw new Error("normalizeBackendResponse must never call fetch itself");
  }) as typeof fetch;

  try {
    const upstream = new Response(JSON.stringify({ ok: true }), { status: 200 });
    await normalizeBackendResponse(upstream);
    assert.equal(fetchWasCalled, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
