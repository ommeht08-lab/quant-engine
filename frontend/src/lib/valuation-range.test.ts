import assert from "node:assert/strict";
import test from "node:test";

import { computeValuationRange } from "./valuation-range.ts";

function assertWithinRail(pct: number) {
  assert.ok(pct >= 0 && pct <= 100, `expected ${pct} to be within [0, 100]`);
}

test("returns null when every point is null (nothing to plot)", () => {
  const result = computeValuationRange([
    { key: "bear", label: "Bear", value: null },
    { key: "base", label: "Base", value: null },
  ]);
  assert.equal(result, null);
});

test("plots bear/base/bull and market price in a normal ordinary scenario", () => {
  const result = computeValuationRange([
    { key: "bear", label: "Bear", value: 80 },
    { key: "base", label: "Base", value: 120 },
    { key: "bull", label: "Bull", value: 160 },
    { key: "market", label: "Market price", value: 100 },
  ]);

  assert.ok(result);
  assert.equal(result.markers.length, 4);
  for (const marker of result.markers) {
    assertWithinRail(marker.pct);
  }
  // Ascending value -> ascending position on the rail.
  const byKey = Object.fromEntries(result.markers.map((m) => [m.key, m.pct]));
  assert.ok(byKey.bear < byKey.market);
  assert.ok(byKey.market < byKey.base);
  assert.ok(byKey.base < byKey.bull);
  // Domain includes zero.
  assert.ok(result.domainMin <= 0);
});

test("missing market price still plots bear/base/bull (3 points, not 4)", () => {
  const result = computeValuationRange([
    { key: "bear", label: "Bear", value: 80 },
    { key: "base", label: "Base", value: 120 },
    { key: "bull", label: "Bull", value: 160 },
    { key: "market", label: "Market price", value: null },
  ]);

  assert.ok(result);
  assert.equal(result.markers.length, 3);
  assert.ok(!result.markers.some((m) => m.key === "market"));
});

test("an unavailable (invalid) scenario is simply excluded from the markers", () => {
  const result = computeValuationRange([
    { key: "bear", label: "Bear", value: null }, // invalid scenario
    { key: "base", label: "Base", value: 120 },
    { key: "bull", label: "Bull", value: 160 },
  ]);

  assert.ok(result);
  assert.equal(result.markers.length, 2);
  assert.ok(!result.markers.some((m) => m.key === "bear"));
});

test("negative values stay on-scale and within the rail", () => {
  const result = computeValuationRange([
    { key: "bear", label: "Bear", value: -50 },
    { key: "base", label: "Base", value: -10 },
    { key: "bull", label: "Bull", value: 30 },
  ]);

  assert.ok(result);
  for (const marker of result.markers) {
    assertWithinRail(marker.pct);
  }
  assert.ok(result.domainMin < -50);
});

test("equal values produce equal positions with no division by zero", () => {
  const result = computeValuationRange([
    { key: "bear", label: "Bear", value: 100 },
    { key: "base", label: "Base", value: 100 },
    { key: "bull", label: "Bull", value: 100 },
  ]);

  assert.ok(result);
  assert.ok(Number.isFinite(result.domainMin));
  assert.ok(Number.isFinite(result.domainMax));
  assert.ok(result.domainMax > result.domainMin);
  const pcts = result.markers.map((m) => m.pct);
  assert.ok(pcts.every((pct) => pct === pcts[0]));
});

test("identical (clustered) markers get symmetric stack levels centered on 0 so they fan out both ways", () => {
  const result = computeValuationRange([
    { key: "bear", label: "Bear", value: 100 },
    { key: "base", label: "Base", value: 100 },
    { key: "bull", label: "Bull", value: 100 },
    { key: "market", label: "Market price", value: 100 },
  ]);

  assert.ok(result);
  const levels = result.markers.map((m) => m.stackLevel).sort((a, b) => a - b);
  assert.deepEqual(levels, [-1.5, -0.5, 0.5, 1.5]);
});

test("a chain of near-ties (each close to its neighbor, but first and last are not) still clusters as one group", () => {
  const result = computeValuationRange([
    { key: "bear", label: "Bear", value: 100 },
    { key: "base", label: "Base", value: 102 },
    { key: "bull", label: "Bull", value: 104 },
    { key: "market", label: "Market price", value: 106 },
  ]);

  assert.ok(result);
  const byKey = Object.fromEntries(result.markers.map((m) => [m.key, m.stackLevel]));
  // Sorted ascending by value (and thus by position): bear, base, bull, market.
  assert.deepEqual(
    [byKey.bear, byKey.base, byKey.bull, byKey.market],
    [-1.5, -0.5, 0.5, 1.5]
  );
});

test("markers far apart do not receive a stacking offset", () => {
  const result = computeValuationRange([
    { key: "bear", label: "Bear", value: 10 },
    { key: "base", label: "Base", value: 50 },
    { key: "bull", label: "Bull", value: 90 },
  ]);

  assert.ok(result);
  assert.ok(result.markers.every((m) => m.stackLevel === 0));
});

test("an extreme range keeps every marker within the rail and preserves ordering", () => {
  const result = computeValuationRange([
    { key: "bear", label: "Bear", value: 1 },
    { key: "base", label: "Base", value: 500 },
    { key: "bull", label: "Bull", value: 100000 },
  ]);

  assert.ok(result);
  for (const marker of result.markers) {
    assertWithinRail(marker.pct);
  }
  const byKey = Object.fromEntries(result.markers.map((m) => [m.key, m.pct]));
  assert.ok(byKey.bear < byKey.base);
  assert.ok(byKey.base < byKey.bull);
});

test("preserves the caller's original point order in the returned markers", () => {
  const result = computeValuationRange([
    { key: "market", label: "Market price", value: 100 },
    { key: "bear", label: "Bear", value: 80 },
    { key: "base", label: "Base", value: 120 },
    { key: "bull", label: "Bull", value: 160 },
  ]);

  assert.ok(result);
  assert.deepEqual(
    result.markers.map((m) => m.key),
    ["market", "bear", "base", "bull"]
  );
});
