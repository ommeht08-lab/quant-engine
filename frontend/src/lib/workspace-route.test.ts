import assert from "node:assert/strict";
import test from "node:test";

import { isUncertaintyPath, shouldMountWorkspace } from "./workspace-route.ts";

test("only the uncertainty route is recognised", () => {
  assert.equal(isUncertaintyPath("/workspace/uncertainty"), true);
  assert.equal(isUncertaintyPath("/workspace/uncertainty/"), true);
  assert.equal(isUncertaintyPath("/workspace/uncertainty/preview"), true);
  for (const other of ["/workspace", "/workspace/valuation", "/workspace/uncertaintyx", "/uncertainty", "", null, undefined]) {
    assert.equal(isUncertaintyPath(other), false, String(other));
  }
});

test("the workspace is skipped on a first visit to the uncertainty page", () => {
  assert.equal(shouldMountWorkspace("/workspace/uncertainty", false), false);
});

test("every other workspace page mounts it", () => {
  for (const path of ["/workspace", "/workspace/valuation", "/workspace/cash-flows", "/workspace/projections", "/workspace/evidence", "/workspace/assumptions"]) {
    assert.equal(shouldMountWorkspace(path, false), true, path);
  }
});

test("once mounted it stays mounted, so state survives a visit to the uncertainty page", () => {
  assert.equal(shouldMountWorkspace("/workspace/uncertainty", true), true);
});
