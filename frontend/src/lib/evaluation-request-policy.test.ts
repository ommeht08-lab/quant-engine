import assert from "node:assert/strict";
import test from "node:test";

import {
  defaultInteractiveEvaluationParams,
  DEFAULT_TERMINAL_GROWTH_RATE,
  STAGED_FORECAST_MODE,
} from "./evaluation-request-policy.ts";

test("STAGED_FORECAST_MODE is the staged five-year forecast, not the trader's flat default", () => {
  assert.equal(STAGED_FORECAST_MODE, "maturation");
});

test("DEFAULT_TERMINAL_GROWTH_RATE matches the dashboard's long-standing default", () => {
  assert.equal(DEFAULT_TERMINAL_GROWTH_RATE, 0.025);
});

test("defaultInteractiveEvaluationParams: requests the staged forecast explicitly rather than relying on the backend default", () => {
  const params = defaultInteractiveEvaluationParams();
  assert.equal(params.get("forecast_mode"), "maturation");
  assert.equal(params.get("terminal_growth_rate"), "0.025");
});

test("defaultInteractiveEvaluationParams: omits growth/margin overrides — the deliberate historical-mode signal, not an oversight", () => {
  const params = defaultInteractiveEvaluationParams();
  assert.equal(params.has("revenue_growth_rate"), false);
  assert.equal(params.has("operating_margin"), false);
});

test("defaultInteractiveEvaluationParams: does not set capex_mode — no drift to fix there", () => {
  const params = defaultInteractiveEvaluationParams();
  assert.equal(params.has("capex_mode"), false);
});

test("defaultInteractiveEvaluationParams: contains exactly the two intended keys, nothing extra", () => {
  const params = defaultInteractiveEvaluationParams();
  assert.deepEqual(Array.from(params.keys()).sort(), ["forecast_mode", "terminal_growth_rate"]);
});
