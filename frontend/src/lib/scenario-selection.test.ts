import assert from "node:assert/strict";
import test from "node:test";

import {
  resolveDisplayedKey,
  resolveMarketSelectedForNewResult,
} from "./scenario-selection.ts";

test("resolveDisplayedKey: an active hover always wins over everything else", () => {
  const displayed = resolveDisplayedKey({
    hoveredKey: "bull",
    isMarketSelected: true,
    selectedScenario: "bear",
  });
  assert.equal(displayed, "bull");
});

test("resolveDisplayedKey: market selection wins when nothing is hovered", () => {
  const displayed = resolveDisplayedKey({
    hoveredKey: null,
    isMarketSelected: true,
    selectedScenario: "base",
  });
  assert.equal(displayed, "market");
});

test("resolveDisplayedKey: falls back to the persisted scenario selection", () => {
  const displayed = resolveDisplayedKey({
    hoveredKey: null,
    isMarketSelected: false,
    selectedScenario: "bull",
  });
  assert.equal(displayed, "bull");
});

test("resolveMarketSelectedForNewResult: stays selected when a market price still exists", () => {
  assert.equal(
    resolveMarketSelectedForNewResult({ wasMarketSelected: true, marketPrice: 150 }),
    true
  );
});

test("resolveMarketSelectedForNewResult: falls back to false when the new result has no market price", () => {
  assert.equal(
    resolveMarketSelectedForNewResult({ wasMarketSelected: true, marketPrice: null }),
    false
  );
});

test("resolveMarketSelectedForNewResult: stays false when it was never selected", () => {
  assert.equal(
    resolveMarketSelectedForNewResult({ wasMarketSelected: false, marketPrice: 150 }),
    false
  );
});
