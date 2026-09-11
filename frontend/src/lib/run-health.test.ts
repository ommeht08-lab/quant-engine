import assert from "node:assert/strict";
import test from "node:test";

import { deriveRunHealth, RUN_STALE_AFTER_MS, type RunHealthEventRow } from "./run-health.ts";

const started: RunHealthEventRow = {
  run_id: "github:123:1",
  event_type: "started",
  event_at: "2026-09-10T20:00:00.000Z",
  completion_status: null,
  decision_outcome: "not_reached",
  mode: "dry-run",
  git_sha: "abc123",
  trigger: "workflow_dispatch",
  universe_count: null,
  valued_count: null,
  eligible_count: null,
  failure_stage: null,
  failure_code: null,
};

test("a recent start is running and a stale start is incomplete", () => {
  const startedMs = new Date(started.event_at).getTime();
  assert.equal(deriveRunHealth([started], startedMs + 1_000)?.status, "running");
  assert.equal(
    deriveRunHealth([started], startedMs + RUN_STALE_AFTER_MS + 1)?.status,
    "incomplete"
  );
});

test("a healthy no-candidate completion remains operationally healthy", () => {
  const completed: RunHealthEventRow = {
    ...started,
    event_type: "completed",
    event_at: "2026-09-10T20:05:00.000Z",
    completion_status: "healthy",
    decision_outcome: "no_candidates",
    universe_count: 100,
    valued_count: 92,
    eligible_count: 0,
  };

  const result = deriveRunHealth([completed, started]);
  assert.equal(result?.status, "healthy");
  assert.equal(result?.decision, "no_candidates");
  assert.equal(result?.valuedCount, 92);
});

test("explicit incomplete and failed terminal events remain distinct", () => {
  const incomplete: RunHealthEventRow = {
    ...started,
    event_type: "completed",
    completion_status: "incomplete",
    decision_outcome: "candidates",
    event_at: "2026-09-10T20:05:00.000Z",
  };
  const failed: RunHealthEventRow = {
    ...started,
    event_type: "failed",
    event_at: "2026-09-10T20:04:00.000Z",
    failure_stage: "pipeline",
    failure_code: "RuntimeError",
  };

  assert.equal(deriveRunHealth([started, incomplete])?.status, "incomplete");
  assert.equal(deriveRunHealth([started, failed])?.status, "failed");
});
