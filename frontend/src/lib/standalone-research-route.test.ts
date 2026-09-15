import assert from "node:assert/strict";
import test from "node:test";

import { usesStandaloneResearchShell } from "./standalone-research-route.ts";

test("live overview routes own the full research shell", () => {
  assert.equal(usesStandaloneResearchShell("/overview"), true);
  assert.equal(usesStandaloneResearchShell("/overview/MSFT"), true);
  assert.equal(usesStandaloneResearchShell("/overview/BRK.B"), true);
});

test("lookalike and unrelated private routes keep the standard app header", () => {
  assert.equal(usesStandaloneResearchShell("/overview-old"), false);
  assert.equal(usesStandaloneResearchShell("/workspace"), false);
  assert.equal(usesStandaloneResearchShell("/portfolio"), false);
});
