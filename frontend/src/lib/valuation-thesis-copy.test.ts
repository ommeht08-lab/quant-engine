import assert from "node:assert/strict";
import test from "node:test";

import { hasMarginOfSafety, thesisDeltaLabel } from "./valuation-thesis-copy.ts";

test("hasMarginOfSafety: true when market price is below intrinsic value", () => {
  assert.equal(hasMarginOfSafety(100, 80), true);
});

test("hasMarginOfSafety: true when market price exactly equals intrinsic value", () => {
  assert.equal(hasMarginOfSafety(100, 100), true);
});

test("hasMarginOfSafety: false when market price is above intrinsic value", () => {
  assert.equal(hasMarginOfSafety(100, 120), false);
});

test("hasMarginOfSafety: false when there is no market price to compare against", () => {
  assert.equal(hasMarginOfSafety(100, null), false);
});

test("thesisDeltaLabel: 'Margin of safety' only when one genuinely exists", () => {
  assert.equal(thesisDeltaLabel(100, 80), "Margin of safety");
});

test("thesisDeltaLabel: 'Downside' when market price exceeds intrinsic value", () => {
  assert.equal(thesisDeltaLabel(100, 120), "Downside");
});

test("thesisDeltaLabel: never labels a sector-relative read as 'Margin of safety'", () => {
  // Regression guard for the exact language-reservation rule this
  // redesign enforces: the phrase must never appear for a market price
  // ABOVE intrinsic value, however small the gap.
  assert.notEqual(thesisDeltaLabel(100, 100.01), "Margin of safety");
});

test("thesisDeltaLabel: falls back to a neutral label with no market price", () => {
  assert.equal(thesisDeltaLabel(100, null), "Upside / downside");
});
