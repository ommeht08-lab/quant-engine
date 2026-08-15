// Regression tests for the HMAC'd client-identifier hashing used by the
// login rate limiter (frontend/src/lib/client-identifier.ts) — proves a
// raw IP address never becomes the literal Redis key. Run with Node's
// built-in test runner:
//
//   node --test src/lib/client-identifier.test.ts

import assert from "node:assert/strict";
import { test } from "node:test";

import { hashIdentifierWithSubkey } from "./client-identifier.ts";

const SUBKEY = "test-subkey-not-a-real-secret";

test("produces a hex-encoded SHA-256-length digest", () => {
  const digest = hashIdentifierWithSubkey("203.0.113.42", SUBKEY);
  assert.match(digest, /^[0-9a-f]{64}$/);
});

test("the raw identifier never appears in the output digest", () => {
  const rawIp = "203.0.113.42";
  const digest = hashIdentifierWithSubkey(rawIp, SUBKEY);
  assert.ok(!digest.includes(rawIp));
});

test("is deterministic for the same identifier and subkey", () => {
  const a = hashIdentifierWithSubkey("203.0.113.42", SUBKEY);
  const b = hashIdentifierWithSubkey("203.0.113.42", SUBKEY);
  assert.equal(a, b);
});

test("different identifiers produce different digests", () => {
  const a = hashIdentifierWithSubkey("203.0.113.42", SUBKEY);
  const b = hashIdentifierWithSubkey("203.0.113.43", SUBKEY);
  assert.notEqual(a, b);
});

test("different subkeys produce different digests for the same identifier", () => {
  const a = hashIdentifierWithSubkey("203.0.113.42", "subkey-one");
  const b = hashIdentifierWithSubkey("203.0.113.42", "subkey-two");
  assert.notEqual(a, b);
});

test("normalizes case and surrounding whitespace before hashing", () => {
  const canonical = hashIdentifierWithSubkey("2001:db8::1", SUBKEY);
  const withWhitespace = hashIdentifierWithSubkey("  2001:db8::1  ", SUBKEY);
  const differentCase = hashIdentifierWithSubkey("2001:DB8::1", SUBKEY);
  assert.equal(withWhitespace, canonical);
  assert.equal(differentCase, canonical);
});
