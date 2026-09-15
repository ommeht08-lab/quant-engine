import assert from "node:assert/strict";
import test from "node:test";

import { parseTickerQueryParam } from "./ticker-query.ts";

test("parseTickerQueryParam: trims and uppercases a plain ticker", () => {
  assert.equal(parseTickerQueryParam("  aapl  "), "AAPL");
  assert.equal(parseTickerQueryParam("msft"), "MSFT");
});

test("parseTickerQueryParam: accepts a real share-class ticker with a dot", () => {
  assert.equal(parseTickerQueryParam("brk.b"), "BRK.B");
});

test("parseTickerQueryParam: accepts a hyphenated ticker", () => {
  assert.equal(parseTickerQueryParam("bf-b"), "BF-B");
});

test("parseTickerQueryParam: absent, repeated, or non-string input is null", () => {
  assert.equal(parseTickerQueryParam(undefined), null);
  assert.equal(parseTickerQueryParam(null), null);
  assert.equal(parseTickerQueryParam(["AAPL", "MSFT"]), null);
});

test("parseTickerQueryParam: empty/whitespace-only input is null", () => {
  assert.equal(parseTickerQueryParam(""), null);
  assert.equal(parseTickerQueryParam("   "), null);
});

test("parseTickerQueryParam: overlong input is null, not silently truncated", () => {
  assert.equal(parseTickerQueryParam("AAPLAAPLAAPL"), null);
});

test("parseTickerQueryParam: rejects a path-separator or other unsafe character", () => {
  assert.equal(parseTickerQueryParam("AAPL/../etc"), null);
  assert.equal(parseTickerQueryParam("<script>"), null);
  assert.equal(parseTickerQueryParam("AAPL;DROP"), null);
  assert.equal(parseTickerQueryParam("AAPL MSFT"), null);
});
