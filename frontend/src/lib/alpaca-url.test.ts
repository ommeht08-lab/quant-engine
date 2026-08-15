// Security regression tests for `assertSafeAlpacaBaseUrl` (frontend/src/lib/alpaca-url.ts),
// which gates every request that carries Alpaca credentials
// (frontend/src/app/api/positions/route.ts). Run with Node's built-in
// test runner — no test framework dependency needed:
//
//   node --test src/lib/alpaca-url.test.ts
//
// (Node 22.6+/23+ strips TypeScript types natively; no ts-node/tsx required.)

import assert from "node:assert/strict";
import { test } from "node:test";

import { assertSafeAlpacaBaseUrl, PAPER_ALPACA_HOSTNAME } from "./alpaca-url.ts";

test("accepts exactly the paper trading HTTPS host", () => {
  const result = assertSafeAlpacaBaseUrl("https://paper-api.alpaca.markets");
  assert.equal(result, `https://${PAPER_ALPACA_HOSTNAME}`);
});

test("rejects the live-trading hostname", () => {
  assert.throws(() => assertSafeAlpacaBaseUrl("https://api.alpaca.markets"));
});

test("rejects http (non-HTTPS)", () => {
  assert.throws(() => assertSafeAlpacaBaseUrl("http://paper-api.alpaca.markets"));
});

test("rejects a lookalike suffix host", () => {
  assert.throws(() => assertSafeAlpacaBaseUrl("https://paper-api.alpaca.markets.evil.com"));
});

test("rejects a lookalike prefix/subdomain host", () => {
  assert.throws(() => assertSafeAlpacaBaseUrl("https://paper-api.alpaca.markets.attacker.io"));
  assert.throws(() => assertSafeAlpacaBaseUrl("https://evil.paper-api.alpaca.markets"));
});

test("rejects embedded userinfo even with the correct host", () => {
  assert.throws(() => assertSafeAlpacaBaseUrl("https://user:pass@paper-api.alpaca.markets"));
});

test("rejects a non-default port", () => {
  assert.throws(() => assertSafeAlpacaBaseUrl("https://paper-api.alpaca.markets:8443"));
});

test("rejects a malformed URL", () => {
  assert.throws(() => assertSafeAlpacaBaseUrl("not a url"));
  assert.throws(() => assertSafeAlpacaBaseUrl(""));
});

test("rejects a completely different scheme", () => {
  assert.throws(() => assertSafeAlpacaBaseUrl("ftp://paper-api.alpaca.markets"));
  assert.throws(() => assertSafeAlpacaBaseUrl("javascript:alert(1)"));
});

test("strips any path/query smuggled into the env value", () => {
  const result = assertSafeAlpacaBaseUrl("https://paper-api.alpaca.markets/../../evil?x=1");
  assert.equal(result, `https://${PAPER_ALPACA_HOSTNAME}`);
});
