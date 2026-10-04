import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

// `/overview` (ResearchHomeClient) and `/overview/[ticker]` (OverviewClient)
// are both public per public-route.ts, but /api/sentiment/[symbol] is
// session-gated and deliberately excluded from the public allowlist (see
// public-route.test.ts). This guards against either public page being
// wired back up to fetch that endpoint without the allowlist and the
// route's own auth check being revisited together.
const researchHomeSource = readFileSync(
  fileURLToPath(new URL("../app/overview/ResearchHomeClient.tsx", import.meta.url)),
  "utf8",
);
const overviewClientSource = readFileSync(
  fileURLToPath(new URL("../app/overview/[ticker]/OverviewClient.tsx", import.meta.url)),
  "utf8",
);

test("the public overview pages do not render or request the session-gated headlines widget", () => {
  for (const [name, source] of [
    ["ResearchHomeClient", researchHomeSource],
    ["OverviewClient", overviewClientSource],
  ] as const) {
    assert.ok(!source.includes("MarketBrief"), `${name} must not import or render MarketBrief`);
    assert.ok(!source.includes("/api/sentiment"), `${name} must not call the session-gated sentiment endpoint`);
  }
});
