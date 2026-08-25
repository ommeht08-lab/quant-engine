import assert from "node:assert/strict";
import test from "node:test";

import { clampAndQuantize, formatPercentInputValue, parsePercentInput } from "./percent-field.ts";

const REVENUE_GROWTH_RANGE = { min: -0.1, max: 0.4, step: 0.005 };
const TERMINAL_GROWTH_RANGE = { min: 0, max: 0.05, step: 0.001 };

test("parsePercentInput returns null for blank input instead of coercing to 0", () => {
  assert.equal(parsePercentInput("", REVENUE_GROWTH_RANGE), null);
});

test("parsePercentInput returns null for whitespace-only input", () => {
  assert.equal(parsePercentInput("   ", REVENUE_GROWTH_RANGE), null);
});

test("parsePercentInput returns null for non-numeric input", () => {
  assert.equal(parsePercentInput("abc", REVENUE_GROWTH_RANGE), null);
});

test("parsePercentInput returns null for a value that never becomes finite (Infinity/NaN text)", () => {
  assert.equal(parsePercentInput("Infinity", REVENUE_GROWTH_RANGE), null);
  assert.equal(parsePercentInput("NaN", REVENUE_GROWTH_RANGE), null);
});

test("parsePercentInput converts a plain percent string to its decimal value", () => {
  assert.equal(parsePercentInput("8", REVENUE_GROWTH_RANGE), 0.08);
});

test("parsePercentInput clamps a value above max down to max", () => {
  assert.equal(parsePercentInput("999", REVENUE_GROWTH_RANGE), 0.4);
});

test("parsePercentInput clamps a value below min up to min", () => {
  assert.equal(parsePercentInput("-500", REVENUE_GROWTH_RANGE), -0.1);
});

test("parsePercentInput quantizes an off-step value to the nearest step", () => {
  // 8.12% -> 0.0812, step is 0.005 (0.5 pts) from min -0.1, nearest step is 0.08.
  assert.equal(parsePercentInput("8.12", REVENUE_GROWTH_RANGE), 0.08);
});

test("parsePercentInput rounds half-way ties to the nearer step consistently", () => {
  // 8.25% -> 0.0825 is exactly between the 0.08 and 0.085 steps; Math.round ties up.
  assert.equal(parsePercentInput("8.25", REVENUE_GROWTH_RANGE), 0.085);
});

test("clampAndQuantize snaps to a step grid anchored at a non-zero min", () => {
  // min=-0.1, step=0.005 -> valid grid points are -0.1, -0.095, -0.09, ...
  assert.equal(clampAndQuantize(0.0823, REVENUE_GROWTH_RANGE), 0.08);
});

test("clampAndQuantize handles a fine step without floating-point drift", () => {
  assert.equal(clampAndQuantize(0.0234, TERMINAL_GROWTH_RANGE), 0.023);
});

test("clampAndQuantize is a no-op for a value already exactly on the grid", () => {
  assert.equal(clampAndQuantize(0.025, TERMINAL_GROWTH_RANGE), 0.025);
});

test("clampAndQuantize passes through unchanged when step is 0 (clamp only)", () => {
  assert.equal(clampAndQuantize(0.033, { min: 0, max: 0.05, step: 0 }), 0.033);
  assert.equal(clampAndQuantize(0.9, { min: 0, max: 0.05, step: 0 }), 0.05);
});

test("formatPercentInputValue and parsePercentInput round-trip a quantized value", () => {
  const decimal = parsePercentInput("12.3", REVENUE_GROWTH_RANGE);
  assert.ok(decimal !== null);
  assert.equal(formatPercentInputValue(decimal, 1), "12.5");
  // 12.3% quantizes to the nearest 0.5pt step (12.5), and re-parsing that
  // displayed text must reproduce the exact same decimal (idempotent).
  assert.equal(parsePercentInput(formatPercentInputValue(decimal, 1), REVENUE_GROWTH_RANGE), decimal);
});

test("formatPercentInputValue formats a decimal at the requested precision", () => {
  assert.equal(formatPercentInputValue(0.025, 1), "2.5");
  assert.equal(formatPercentInputValue(0, 1), "0.0");
  assert.equal(formatPercentInputValue(-0.1, 1), "-10.0");
});
