import assert from "node:assert/strict";
import test from "node:test";

import { qualityIssueCopy, scenarioDisplayLabel, type ValuationQuality } from "./valuation-quality.ts";

const ordinary: ValuationQuality = {
  level: "ordinary", codes: [], allows_market_comparison: true,
  terminal_value_share_of_enterprise_value: 0.72,
  observed_effective_tax_rate: 0.21,
};

test("ordinary cases keep conventional scenario names", () => {
  assert.equal(scenarioDisplayLabel("bear", ordinary), "Bear");
  assert.equal(scenarioDisplayLabel("bull", ordinary), "Bull");
});

test("reversed values are labeled as input cases, not intuitive outcomes", () => {
  const reversed: ValuationQuality = {
    ...ordinary, level: "diagnostic_only", codes: ["reversed_scenario_values"],
    allows_market_comparison: false,
  };
  assert.equal(scenarioDisplayLabel("bear", reversed), "Bear inputs");
  assert.equal(scenarioDisplayLabel("bull", reversed), "Bull inputs");
  assert.match(qualityIssueCopy("reversed_scenario_values"), /not downside\/upside outcomes/);
});

test("perpetual losses and terminal concentration have distinct cautions", () => {
  assert.match(qualityIssueCopy("nonpositive_terminal_fcf"), /not an actionable valuation/);
  assert.match(qualityIssueCopy("high_terminal_value_concentration"), /80%/);
});
