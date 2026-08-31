import assert from "node:assert/strict";
import test from "node:test";

import {
  errorBannerHeadline,
  errorBannerTone,
  resolveWorkspaceResultState,
} from "./valuation-state-copy.ts";

test("errorBannerHeadline: unavailable reads as a connectivity problem, not a bad request", () => {
  assert.equal(errorBannerHeadline("unavailable"), "Live valuation is not connected");
});

test("errorBannerHeadline: input errors point at the ticker/assumptions", () => {
  assert.equal(errorBannerHeadline("input"), "Check the ticker or assumptions");
});

test("errorBannerHeadline: request errors read as a generic run failure", () => {
  assert.equal(errorBannerHeadline("request"), "Valuation could not run");
});

test("errorBannerTone: only 'unavailable' is a warning, everything else is an error", () => {
  assert.equal(errorBannerTone("unavailable"), "warning");
  assert.equal(errorBannerTone("input"), "error");
  assert.equal(errorBannerTone("request"), "error");
});

test("resolveWorkspaceResultState: no result yet and not loading -> empty", () => {
  assert.equal(resolveWorkspaceResultState({ hasResult: false, isLoading: false, hasError: false }), "empty");
});

test("resolveWorkspaceResultState: no result yet and loading -> first-loading", () => {
  assert.equal(resolveWorkspaceResultState({ hasResult: false, isLoading: true, hasError: false }), "first-loading");
});

test("resolveWorkspaceResultState: no result yet, loading, even with a stale error flag -> first-loading (loading wins)", () => {
  assert.equal(resolveWorkspaceResultState({ hasResult: false, isLoading: true, hasError: true }), "first-loading");
});

test("resolveWorkspaceResultState: a result exists and a re-run is in flight -> stale-updating", () => {
  assert.equal(resolveWorkspaceResultState({ hasResult: true, isLoading: true, hasError: false }), "stale-updating");
});

test("resolveWorkspaceResultState: a result exists, a re-run is in flight, even with a stale error flag -> stale-updating (loading wins)", () => {
  assert.equal(resolveWorkspaceResultState({ hasResult: true, isLoading: true, hasError: true }), "stale-updating");
});

test("resolveWorkspaceResultState: a result exists and nothing is loading -> ready", () => {
  assert.equal(resolveWorkspaceResultState({ hasResult: true, isLoading: false, hasError: false }), "ready");
});

test("resolveWorkspaceResultState: a rerun failed while a previous result exists -> previous-result, never cleared", () => {
  assert.equal(resolveWorkspaceResultState({ hasResult: true, isLoading: false, hasError: true }), "previous-result");
});
