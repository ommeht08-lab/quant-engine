import assert from "node:assert/strict";
import test from "node:test";

import { isPublicRoute, publicRedirectPath } from "./public-route.ts";
import { DEFAULT_APP_PATH } from "./default-route.ts";

test("the root path redirects directly to the valuation workspace", () => {
  assert.equal(publicRedirectPath("/"), DEFAULT_APP_PATH);
  assert.equal(publicRedirectPath("/"), "/workspace");
  assert.equal(publicRedirectPath("/workspace"), null);
  assert.equal(publicRedirectPath("/overview"), null);
  assert.equal(publicRedirectPath("/overview/MSFT"), null);
  // The curated case is still a fixed direct URL, not a redirect target.
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

test("workspace and company overview routes stay behind the session gate", () => {
  assert.equal(isPublicRoute("/overview"), false);
  assert.equal(isPublicRoute("/overview/MSFT"), false);
  assert.equal(isPublicRoute(DEFAULT_APP_PATH), false);
});

/**
 * Simulates proxy.ts's own decision tree using its real dependencies —
 * not a re-implementation of proxy.ts, just a trace through the two
 * pure functions it actually calls — to prove an unauthenticated
 * visitor's redirect chain among the root, workspace, login, and
 * company overview always terminates rather than cycling. Mirrors
 * proxy.ts's exact order: a public-redirect hop first, then the
 * "/login or already-public, stop" check, otherwise (private,
 * unauthenticated) hop to "/login".
 */
function nextHopForUnauthenticatedVisitor(pathname: string): string | null {
  const publicRedirect = publicRedirectPath(pathname);
  if (publicRedirect) return publicRedirect;
  if (pathname === "/login" || isPublicRoute(pathname)) return null;
  return "/login";
}

test("no redirect loop among root, workspace, login, and company overview", () => {
  for (const start of ["/", "/workspace", "/overview", "/overview/MSFT", "/login"]) {
    const visited = new Set<string>();
    let pathname = start;
    let hops = 0;
    const MAX_HOPS = 5;
    while (hops < MAX_HOPS) {
      assert.ok(!visited.has(pathname), `revisited "${pathname}" starting from "${start}" — redirect loop`);
      visited.add(pathname);
      const next = nextHopForUnauthenticatedVisitor(pathname);
      if (next === null) break;
      pathname = next;
      hops++;
    }
    assert.ok(hops < MAX_HOPS, `redirect chain from "${start}" did not terminate within ${MAX_HOPS} hops`);
  }
});
