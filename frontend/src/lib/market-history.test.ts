import assert from "node:assert/strict";
import test from "node:test";

import { isMarketHistoryResponse } from "./market-history.ts";

const validHistory = {
  ticker: "AAPL",
  currency: "USD",
  exchangeTimezone: "America/New_York",
  source: "Yahoo Finance",
  asOf: "2026-09-21T20:00:00-04:00",
  points: [
    { date: "2026-09-18T20:00:00-04:00", close: 185.5 },
    { date: "2026-09-21T20:00:00-04:00", close: 187.25 },
  ],
};

test("accepts a complete backend market-history response", () => {
  assert.equal(isMarketHistoryResponse(validHistory), true);
});

test("rejects nonpositive or nonfinite observations", () => {
  assert.equal(isMarketHistoryResponse({
    ...validHistory,
    points: [{ date: "x", close: 1 }, { date: "y", close: 0 }],
  }), false);
});

test("rejects a response with fewer than two observations", () => {
  assert.equal(isMarketHistoryResponse({ ...validHistory, points: validHistory.points.slice(0, 1) }), false);
});
