import assert from "node:assert/strict";
import test from "node:test";

import { resolveThesisRailFields, type ScenarioLike } from "./thesis-rail-fields.ts";

function scenario(overrides: Partial<ScenarioLike> = {}): ScenarioLike {
  return {
    assumptions: {
      revenue_growth_rate: 0.08,
      operating_margin: 0.25,
      wacc: 0.09,
      terminal_growth_rate: 0.025,
    },
    intrinsic_value_per_share: 150,
    is_valid: true,
    invalid_reason: null,
    ...overrides,
  };
}

test("bear scenario: every field reflects the bear case's own assumptions", () => {
  const bear = scenario({
    assumptions: { revenue_growth_rate: 0.02, operating_margin: 0.18, wacc: 0.11, terminal_growth_rate: 0.015 },
    intrinsic_value_per_share: 90,
  });
  const fields = resolveThesisRailFields(bear, 100);

  assert.equal(fields.revenueGrowthRate, 0.02);
  assert.equal(fields.operatingMargin, 0.18);
  assert.equal(fields.wacc, 0.11);
  assert.equal(fields.terminalGrowthRate, 0.015);
  assert.equal(fields.intrinsicValue, 90);
  assert.equal(fields.priceDelta, -10);
  assert.equal(fields.deltaIsPositive, false);
  assert.equal(fields.hasMarginOfSafety, false);
  assert.equal(fields.deltaLabel, "Downside");
});

test("base scenario: every field reflects the base case's own assumptions", () => {
  const base = scenario({
    assumptions: { revenue_growth_rate: 0.08, operating_margin: 0.25, wacc: 0.09, terminal_growth_rate: 0.025 },
    intrinsic_value_per_share: 150,
  });
  const fields = resolveThesisRailFields(base, 100);

  assert.equal(fields.revenueGrowthRate, 0.08);
  assert.equal(fields.operatingMargin, 0.25);
  assert.equal(fields.wacc, 0.09);
  assert.equal(fields.terminalGrowthRate, 0.025);
  assert.equal(fields.intrinsicValue, 150);
  assert.equal(fields.priceDelta, 50);
  assert.equal(fields.deltaIsPositive, true);
  assert.equal(fields.hasMarginOfSafety, true);
  assert.equal(fields.deltaLabel, "Margin of safety");
});

test("bull scenario: every field reflects the bull case's own assumptions, never the base case's", () => {
  const bull = scenario({
    assumptions: { revenue_growth_rate: 0.14, operating_margin: 0.32, wacc: 0.075, terminal_growth_rate: 0.032 },
    intrinsic_value_per_share: 220,
  });
  const fields = resolveThesisRailFields(bull, 100);

  assert.equal(fields.revenueGrowthRate, 0.14);
  assert.equal(fields.operatingMargin, 0.32);
  assert.equal(fields.wacc, 0.075);
  assert.equal(fields.terminalGrowthRate, 0.032);
  // Regression guard: none of these may equal a different (e.g. base-case)
  // WACC/terminal growth that isn't this scenario's own.
  assert.notEqual(fields.wacc, 0.09);
  assert.notEqual(fields.terminalGrowthRate, 0.025);
  assert.equal(fields.intrinsicValue, 220);
});

test("invalid scenario: assumptions still resolve, but valuation fields fall back safely", () => {
  const invalid = scenario({
    assumptions: { revenue_growth_rate: 0.4, operating_margin: 0.6, wacc: 0.05, terminal_growth_rate: 0.049 },
    intrinsic_value_per_share: null,
    is_valid: false,
    invalid_reason: "WACC must exceed the terminal growth rate.",
  });
  const fields = resolveThesisRailFields(invalid, 100);

  // The four assumption fields are always resolvable from the scenario's
  // own (clamped) assumptions, even when the model refused to value it.
  assert.equal(fields.revenueGrowthRate, 0.4);
  assert.equal(fields.operatingMargin, 0.6);
  assert.equal(fields.wacc, 0.05);
  assert.equal(fields.terminalGrowthRate, 0.049);

  assert.equal(fields.intrinsicValue, null);
  assert.equal(fields.priceDelta, null);
  assert.equal(fields.priceDeltaPct, null);
  assert.equal(fields.deltaIsPositive, false);
  assert.equal(fields.hasMarginOfSafety, false);
  assert.equal(fields.deltaLabel, "Upside / downside");
});

test("no market price: a valid scenario still resolves its own assumptions, with no delta to compare", () => {
  const base = scenario({ intrinsic_value_per_share: 150 });
  const fields = resolveThesisRailFields(base, null);

  assert.equal(fields.wacc, 0.09);
  assert.equal(fields.terminalGrowthRate, 0.025);
  assert.equal(fields.intrinsicValue, 150);
  assert.equal(fields.priceDelta, null);
  assert.equal(fields.priceDeltaPct, null);
  assert.equal(fields.hasMarginOfSafety, false);
  assert.equal(fields.deltaLabel, "Upside / downside");
});
