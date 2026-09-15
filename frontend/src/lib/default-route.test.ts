import assert from "node:assert/strict";
import test from "node:test";

import { DEFAULT_OVERVIEW_PATH, DEFAULT_OVERVIEW_TICKER } from "./default-route.ts";

test("DEFAULT_OVERVIEW_TICKER is MSFT", () => {
  assert.equal(DEFAULT_OVERVIEW_TICKER, "MSFT");
});

test("DEFAULT_OVERVIEW_PATH is built from the shared ticker constant, not a second literal", () => {
  assert.equal(DEFAULT_OVERVIEW_PATH, "/overview/MSFT");
  assert.equal(DEFAULT_OVERVIEW_PATH, `/overview/${DEFAULT_OVERVIEW_TICKER}`);
});
