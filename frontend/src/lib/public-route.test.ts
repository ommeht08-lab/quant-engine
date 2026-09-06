import assert from "node:assert/strict";
import test from "node:test";

import { isPublicRoute } from "./public-route.ts";

test("curated research cases and methodology are public", () => {
  assert.equal(isPublicRoute("/research"), true);
  assert.equal(isPublicRoute("/research/aapl"), true);
  assert.equal(isPublicRoute("/methodology"), true);
  assert.equal(isPublicRoute("/methodology/data"), true);
});

test("lookalike and private workspace paths remain private", () => {
  assert.equal(isPublicRoute("/researcher"), false);
  assert.equal(isPublicRoute("/workspace"), false);
  assert.equal(isPublicRoute("/portfolio"), false);
  assert.equal(isPublicRoute("/api/evaluate/AAPL"), false);
});
