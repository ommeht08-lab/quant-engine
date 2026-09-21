import assert from "node:assert/strict";
import test from "node:test";

import {
  PUBLIC_EVALUATION_MAX_REQUESTS,
  PUBLIC_EVALUATION_WINDOW_SECONDS,
  decidePublicEvaluationRateLimit,
  publicEvaluationRateLimitKey,
  shouldFailOpenPublicEvaluationRateLimiter,
} from "./public-evaluation-rate-limit.ts";
import { firstForwardedClientIdentifier } from "./client-identifier.ts";

test("firstForwardedClientIdentifier uses the first trusted proxy address", () => {
  assert.equal(firstForwardedClientIdentifier("203.0.113.7, 10.0.0.2"), "203.0.113.7");
  assert.equal(firstForwardedClientIdentifier(" 2001:db8::1 "), "2001:db8::1");
  assert.equal(firstForwardedClientIdentifier(null), "unknown");
});

test("publicEvaluationRateLimitKey never stores the raw client address", () => {
  const key = publicEvaluationRateLimitKey("203.0.113.7", "separate-test-subkey");
  assert.match(key, /^public-evaluation:[a-f0-9]{64}$/);
  assert.ok(!key.includes("203.0.113.7"));
});

test("the public quota permits the final request inside the window", async () => {
  let capturedWindow = 0;
  const decision = await decidePublicEvaluationRateLimit(
    { key: "k", isProduction: true, failOpenOverride: undefined },
    async (_key, windowSeconds) => {
      capturedWindow = windowSeconds;
      return { ok: true, count: PUBLIC_EVALUATION_MAX_REQUESTS };
    },
  );
  assert.equal(decision, "allow");
  assert.equal(capturedWindow, PUBLIC_EVALUATION_WINDOW_SECONDS);
});

test("the public quota rejects requests over the fixed-window limit", async () => {
  const decision = await decidePublicEvaluationRateLimit(
    { key: "k", isProduction: true, failOpenOverride: undefined },
    async () => ({ ok: true, count: PUBLIC_EVALUATION_MAX_REQUESTS + 1 }),
  );
  assert.equal(decision, "limited");
});

test("an unavailable limiter fails open outside production", async () => {
  const decision = await decidePublicEvaluationRateLimit(
    { key: "k", isProduction: false, failOpenOverride: undefined },
    async () => ({ ok: false, reason: "unconfigured" }),
  );
  assert.equal(decision, "allow");
});

test("an unavailable limiter fails closed in production by default", async () => {
  const decision = await decidePublicEvaluationRateLimit(
    { key: "k", isProduction: true, failOpenOverride: undefined },
    async () => ({ ok: false, reason: "provider_error" }),
  );
  assert.equal(decision, "unavailable");
});

test("production fail-open requires the exact emergency override", () => {
  assert.equal(shouldFailOpenPublicEvaluationRateLimiter(true, "true"), true);
  assert.equal(shouldFailOpenPublicEvaluationRateLimiter(true, "TRUE"), false);
  assert.equal(shouldFailOpenPublicEvaluationRateLimiter(true, "1"), false);
  assert.equal(shouldFailOpenPublicEvaluationRateLimiter(true, undefined), false);
});
