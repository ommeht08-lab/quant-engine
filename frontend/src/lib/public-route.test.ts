import assert from "node:assert/strict";
import test from "node:test";

import { isPublicRoute, publicRedirectPath } from "./public-route.ts";

test("the public root redirects to the flagship research case", () => {
  assert.equal(publicRedirectPath("/"), "/research/aapl");
  assert.equal(publicRedirectPath("/workspace"), null);
  assert.equal(publicRedirectPath("/research/aapl"), null);
});

test("curated research cases and methodology are public", () => {
  assert.equal(isPublicRoute("/research"), true);
  assert.equal(isPublicRoute("/research/aapl"), true);
  assert.equal(isPublicRoute("/research/aapl/statements"), true);
  assert.equal(isPublicRoute("/research/aapl/forecast"), true);
  assert.equal(isPublicRoute("/research/aapl/valuation"), true);
  assert.equal(isPublicRoute("/research/aapl/evidence"), true);
  assert.equal(isPublicRoute("/methodology"), true);
  assert.equal(isPublicRoute("/methodology/data"), true);
});

test("lookalike and private workspace paths remain private", () => {
  assert.equal(isPublicRoute("/researcher"), false);
  assert.equal(isPublicRoute("/workspace"), false);
  assert.equal(isPublicRoute("/portfolio"), false);
  assert.equal(isPublicRoute("/api/evaluate/AAPL"), false);
});
