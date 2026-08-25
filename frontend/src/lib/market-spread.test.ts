import assert from "node:assert/strict";
import test from "node:test";

import { computeMarketSpread, formatMarketSpread } from "./market-spread.ts";

test("value above market price yields a positive dollar and percent delta", () => {
  const result = computeMarketSpread({ value: 118.42, marketPrice: 100 });

  assert.equal(result.status, "above");
  assert.ok(Math.abs((result.dollarDelta as number) - 18.42) < 1e-9);
  assert.ok(Math.abs((result.percentDelta as number) - 18.42) < 1e-9);
});

test("value below market price yields a negative dollar and percent delta", () => {
  const result = computeMarketSpread({ value: 92.9, marketPrice: 100 });

  assert.equal(result.status, "below");
  assert.ok(Math.abs((result.dollarDelta as number) - -7.1) < 1e-9);
  assert.ok(Math.abs((result.percentDelta as number) - -7.1) < 1e-9);
});

test("value equal to market price yields a zero delta, not treated as above or below", () => {
  const result = computeMarketSpread({ value: 100, marketPrice: 100 });

  assert.equal(result.status, "equal");
  assert.equal(result.dollarDelta, 0);
  assert.equal(result.percentDelta, 0);
});

test("missing market price makes the comparison unavailable, not zero", () => {
  const result = computeMarketSpread({ value: 100, marketPrice: null });

  assert.equal(result.status, "comparison-unavailable");
  assert.equal(result.dollarDelta, null);
  assert.equal(result.percentDelta, null);
});

test("a zero market price still yields a dollar delta but never a fabricated percent", () => {
  const above = computeMarketSpread({ value: 25, marketPrice: 0 });
  assert.equal(above.status, "above");
  assert.equal(above.dollarDelta, 25);
  assert.equal(above.percentDelta, null);

  const below = computeMarketSpread({ value: -10, marketPrice: 0 });
  assert.equal(below.status, "below");
  assert.equal(below.dollarDelta, -10);
  assert.equal(below.percentDelta, null);
});

test("a negative intrinsic value still produces a well-defined signed spread", () => {
  const result = computeMarketSpread({ value: -20, marketPrice: 50 });

  assert.equal(result.status, "below");
  assert.equal(result.dollarDelta, -70);
  assert.ok(Math.abs((result.percentDelta as number) - -140) < 1e-9);
});

test("an invalid (non-computable) scenario never yields a spread", () => {
  const result = computeMarketSpread({ value: null, marketPrice: 100 });

  assert.equal(result.status, "invalid");
  assert.equal(result.dollarDelta, null);
  assert.equal(result.percentDelta, null);
});

test("formatMarketSpread: above market — exact visible and accessible text", () => {
  const spread = computeMarketSpread({ value: 112.6, marketPrice: 100 });
  const display = formatMarketSpread(spread);

  assert.deepEqual(display, {
    visible: "+$12.60 (+12.6%)",
    accessible: "+$12.60 above current market price, +12.6%",
  });
});

test("formatMarketSpread: below market — exact visible and accessible text", () => {
  const spread = computeMarketSpread({ value: 92.9, marketPrice: 100 });
  const display = formatMarketSpread(spread);

  assert.deepEqual(display, {
    visible: "-$7.10 (-7.1%)",
    accessible: "-$7.10 below current market price, -7.1%",
  });
});

test("formatMarketSpread: equal — exact visible and accessible text", () => {
  const spread = computeMarketSpread({ value: 100, marketPrice: 100 });
  const display = formatMarketSpread(spread);

  assert.deepEqual(display, {
    visible: "$0.00 (0.0%)",
    accessible: "Equal to current market price, $0.00, 0.0%",
  });
});

test("formatMarketSpread: missing market price — exact visible and accessible text", () => {
  const spread = computeMarketSpread({ value: 100, marketPrice: null });
  const display = formatMarketSpread(spread);

  assert.deepEqual(display, {
    visible: "Unavailable",
    accessible: "Market price unavailable for comparison",
  });
});

test("formatMarketSpread: zero market price — dollar spread shown, percentage marked unavailable", () => {
  const spread = computeMarketSpread({ value: 25, marketPrice: 0 });
  const display = formatMarketSpread(spread);

  assert.deepEqual(display, {
    visible: "+$25.00 (Percentage unavailable)",
    accessible: "+$25.00 above current market price, Percentage unavailable",
  });
});

test("formatMarketSpread: invalid scenario — exact visible and accessible text", () => {
  const spread = computeMarketSpread({ value: null, marketPrice: 100 });
  const display = formatMarketSpread(spread);

  assert.deepEqual(display, {
    visible: "—",
    accessible: "Not computable",
  });
});
