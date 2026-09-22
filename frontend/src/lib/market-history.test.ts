import assert from "node:assert/strict";
import test from "node:test";

import { parseYahooMarketHistory } from "./market-history.ts";

test("parses aligned Yahoo timestamps and daily closes", () => {
  const result = parseYahooMarketHistory({
    chart: {
      result: [{
        meta: { currency: "USD", exchangeTimezoneName: "America/New_York" },
        timestamp: [1_700_000_000, 1_700_086_400, 1_700_172_800],
        indicators: { quote: [{ close: [190.1, 191.4, 189.8] }] },
      }],
      error: null,
    },
  }, "AAPL");

  assert.equal(result.ticker, "AAPL");
  assert.equal(result.source, "Yahoo Finance");
  assert.equal(result.currency, "USD");
  assert.deepEqual(result.points.map((point) => point.close), [190.1, 191.4, 189.8]);
  assert.equal(result.asOf, result.points[2].date);
});

test("drops null and invalid bars without inventing prices", () => {
  const result = parseYahooMarketHistory({
    chart: {
      result: [{
        meta: {},
        timestamp: [1_700_000_000, 1_700_086_400, 1_700_172_800, 1_700_259_200],
        indicators: { quote: [{ close: [190.1, null, -2, 192.3] }] },
      }],
    },
  }, "MSFT");

  assert.deepEqual(result.points.map((point) => point.close), [190.1, 192.3]);
  assert.equal(result.currency, "USD");
  assert.equal(result.exchangeTimezone, "America/New_York");
});

test("refuses a payload without enough real price observations", () => {
  assert.throws(
    () => parseYahooMarketHistory({
      chart: {
        result: [{ timestamp: [1_700_000_000], indicators: { quote: [{ close: [190.1] }] } }],
      },
    }, "CAT"),
    /insufficient daily price history/,
  );
});
