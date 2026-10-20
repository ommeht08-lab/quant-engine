import assert from "node:assert/strict";
import test from "node:test";

import { scrollRegionLabel } from "./scroll-region-label.ts";

test("an explicit label wins", () => {
  assert.equal(scrollRegionLabel("Some caption.", "  Experiment comparison "), "Experiment comparison");
});

test("the caption's first sentence names the region", () => {
  assert.equal(scrollRegionLabel("Projected cash flows. All amounts in USD."), "Projected cash flows");
  assert.equal(scrollRegionLabel("Each row varies one input — not a variance attribution."), "Each row varies one input");
});

test("a long first sentence is shortened with an ellipsis", () => {
  const label = scrollRegionLabel("word ".repeat(40));
  assert.ok(label.length <= 80 && label.endsWith("…"));
});

test("a missing or blank caption falls back to a generic name", () => {
  assert.equal(scrollRegionLabel(null), "Scrollable table");
  assert.equal(scrollRegionLabel("   \n "), "Scrollable table");
});
