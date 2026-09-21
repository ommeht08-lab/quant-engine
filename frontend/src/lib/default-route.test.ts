import assert from "node:assert/strict";
import test from "node:test";

import { DEFAULT_APP_PATH, DEFAULT_OVERVIEW_TICKER } from "./default-route.ts";

test("DEFAULT_OVERVIEW_TICKER is MSFT", () => {
  assert.equal(DEFAULT_OVERVIEW_TICKER, "MSFT");
});

test("DEFAULT_APP_PATH opens the valuation workflow directly", () => {
  assert.equal(DEFAULT_APP_PATH, "/workspace");
  assert.notEqual(DEFAULT_APP_PATH, `/overview/${DEFAULT_OVERVIEW_TICKER}`);
});
