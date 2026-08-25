import assert from "node:assert/strict";
import test from "node:test";

import { computeValuationSpread } from "./valuation-spread.ts";

function assertClose(actual: number, expected: number, epsilon = 1e-6, message?: string) {
  assert.ok(
    Math.abs(actual - expected) < epsilon,
    message ?? `expected ${actual} to be within ${epsilon} of ${expected}`
  );
}

function assertWithinRail(pct: number) {
  assert.ok(pct >= 0 && pct <= 100, `expected ${pct} to be within [0, 100]`);
}

test("returns null when market price is unavailable", () => {
  assert.equal(computeValuationSpread({ marketPrice: null, intrinsicValue: 100 }), null);
});

test("upside: intrinsic value above market price", () => {
  const result = computeValuationSpread({ marketPrice: 100, intrinsicValue: 120 });
  assert.ok(result);
  assert.equal(result.direction, "upside");
  assertClose(result.percent!, 20);
  assertWithinRail(result.marketPct);
  assertWithinRail(result.intrinsicPct);
  assert.ok(result.intrinsicPct > result.marketPct);
});

test("downside: intrinsic value below market price", () => {
  const result = computeValuationSpread({ marketPrice: 120, intrinsicValue: 100 });
  assert.ok(result);
  assert.equal(result.direction, "downside");
  assertClose(result.percent!, (100 - 120) / 120 * 100);
  assertWithinRail(result.marketPct);
  assertWithinRail(result.intrinsicPct);
  assert.ok(result.intrinsicPct < result.marketPct);
});

test("equal values: never labeled upside, spread is exactly zero", () => {
  const result = computeValuationSpread({ marketPrice: 100, intrinsicValue: 100 });
  assert.ok(result);
  assert.equal(result.direction, "equal");
  assert.notEqual(result.direction, "upside");
  assert.equal(result.percent, 0);
  assert.equal(result.marketPct, result.intrinsicPct);
  assert.equal(result.fillWidthPct, 0);
});

test("negative intrinsic value stays on-scale rather than clamping to the market price", () => {
  const result = computeValuationSpread({ marketPrice: 50, intrinsicValue: -20 });
  assert.ok(result);
  assert.equal(result.direction, "downside");
  // Domain must include 0, so it extends below the negative intrinsic value too.
  assert.ok(result.domainMin < -20);
  assert.ok(result.domainMax > 50);
  // Both markers remain strictly inside the rail (not clipped to an edge).
  assert.ok(result.intrinsicPct > 0 && result.intrinsicPct < 100);
  assert.ok(result.marketPct > 0 && result.marketPct < 100);
  assert.ok(result.intrinsicPct < result.marketPct);
});

test("zero market price: percent spread is undefined, not fabricated as 0", () => {
  const result = computeValuationSpread({ marketPrice: 0, intrinsicValue: 10 });
  assert.ok(result);
  assert.equal(result.percent, null);
  assert.equal(result.direction, "upside");
  assertWithinRail(result.marketPct);
  assertWithinRail(result.intrinsicPct);
});

test("market price and intrinsic value both zero: domain does not collapse to zero width", () => {
  const result = computeValuationSpread({ marketPrice: 0, intrinsicValue: 0 });
  assert.ok(result);
  assert.equal(result.direction, "equal");
  assert.equal(result.percent, null);
  assert.ok(result.domainMax > result.domainMin);
  assert.equal(result.marketPct, 50);
  assert.equal(result.intrinsicPct, 50);
});

test("extreme upside spread keeps both markers within the rail", () => {
  const result = computeValuationSpread({ marketPrice: 1, intrinsicValue: 100000 });
  assert.ok(result);
  assert.equal(result.direction, "upside");
  assertWithinRail(result.marketPct);
  assertWithinRail(result.intrinsicPct);
  assert.ok(result.marketPct > 0 && result.marketPct < 100);
  assert.ok(result.intrinsicPct > 0 && result.intrinsicPct < 100);
  assert.ok(result.percent! > 0);
});

test("extreme downside spread keeps both markers within the rail", () => {
  const result = computeValuationSpread({ marketPrice: 100000, intrinsicValue: 1 });
  assert.ok(result);
  assert.equal(result.direction, "downside");
  assertWithinRail(result.marketPct);
  assertWithinRail(result.intrinsicPct);
  assert.ok(result.marketPct > 0 && result.marketPct < 100);
  assert.ok(result.intrinsicPct > 0 && result.intrinsicPct < 100);
  assert.ok(result.percent! < -99);
});

test("fillStartPct/fillWidthPct always describe the segment between the two markers", () => {
  const result = computeValuationSpread({ marketPrice: 120, intrinsicValue: 100 });
  assert.ok(result);
  assert.equal(result.fillStartPct, Math.min(result.marketPct, result.intrinsicPct));
  assert.equal(result.fillWidthPct, Math.abs(result.intrinsicPct - result.marketPct));
});
