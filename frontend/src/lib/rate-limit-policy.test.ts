// Regression tests for the fail-open/fail-closed production policy
// (frontend/src/lib/rate-limit-policy.ts). Run with Node's built-in
// test runner:
//
//   node --test src/lib/rate-limit-policy.test.ts

import assert from "node:assert/strict";
import { test } from "node:test";

import { shouldFailOpenWhenRateLimiterUnavailable } from "./rate-limit-policy.ts";

test("development (non-production) fails open regardless of the override", () => {
  assert.equal(
    shouldFailOpenWhenRateLimiterUnavailable({ isProduction: false, overrideValue: undefined }),
    true
  );
});

test("test/CI environment (non-production) fails open", () => {
  assert.equal(
    shouldFailOpenWhenRateLimiterUnavailable({ isProduction: false, overrideValue: "false" }),
    true
  );
});

test("normal production (no override) fails closed", () => {
  assert.equal(
    shouldFailOpenWhenRateLimiterUnavailable({ isProduction: true, overrideValue: undefined }),
    false
  );
});

test("production with the explicit override set to exactly 'true' fails open", () => {
  assert.equal(
    shouldFailOpenWhenRateLimiterUnavailable({ isProduction: true, overrideValue: "true" }),
    true
  );
});

test("production with an empty override value still fails closed", () => {
  assert.equal(shouldFailOpenWhenRateLimiterUnavailable({ isProduction: true, overrideValue: "" }), false);
});

test("production requires the EXACT string 'true' — near-miss values still fail closed", () => {
  for (const value of ["1", "TRUE", "True", "yes", "on", " true", "true "]) {
    assert.equal(
      shouldFailOpenWhenRateLimiterUnavailable({ isProduction: true, overrideValue: value }),
      false,
      `override value ${JSON.stringify(value)} must not enable fail-open`
    );
  }
});
