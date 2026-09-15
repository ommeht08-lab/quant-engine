import assert from "node:assert/strict";
import test from "node:test";

import { DEFAULT_OVERVIEW_PATH, DEFAULT_OVERVIEW_TICKER } from "./default-route.ts";

test("DEFAULT_OVERVIEW_TICKER is MSFT", () => {
  assert.equal(DEFAULT_OVERVIEW_TICKER, "MSFT");
});

test("DEFAULT_OVERVIEW_PATH is the research home page, not a specific ticker", () => {
  assert.equal(DEFAULT_OVERVIEW_PATH, "/overview");
  // Deliberately NOT built from DEFAULT_OVERVIEW_TICKER — the home page is
  // not a company page. See default-route.ts's own docstring for why an
  // earlier revision of this constant was wrong to combine the two.
  assert.notEqual(DEFAULT_OVERVIEW_PATH, `/overview/${DEFAULT_OVERVIEW_TICKER}`);
});
