import assert from "node:assert/strict";
import test from "node:test";

import { MONTE_CARLO_METADATA, settingsMismatch } from "./monte-carlo-metadata.ts";
import { makeResponse } from "./monte-carlo-fixtures.ts";
import { describeRequestError } from "./monte-carlo-display.ts";
import type { McMeta } from "./monte-carlo-types.ts";

const matching = (): { model_version: string; config_hash: string; meta: McMeta } => {
  const result = makeResponse();
  return {
    model_version: MONTE_CARLO_METADATA.model_version,
    config_hash: MONTE_CARLO_METADATA.config_hash,
    meta: { ...result.meta, metadata_id: MONTE_CARLO_METADATA.metadata_id },
  };
};

test("the bundled metadata is valid and lists the allowlist, seed and draw count", () => {
  assert.deepEqual(MONTE_CARLO_METADATA.tickers, ["AAPL", "KO", "JNJ"]);
  assert.equal(MONTE_CARLO_METADATA.config.target_valid, 10000);
  assert.match(MONTE_CARLO_METADATA.metadata_id, /^[0-9a-f]{64}$/);
});

test("settings that match the service are not reported", () => {
  assert.equal(settingsMismatch(MONTE_CARLO_METADATA, matching()), null);
});

test("each kind of mismatch is reported by name", () => {
  const cases: [string, (r: ReturnType<typeof matching>) => void][] = [
    ["model version", (r) => { r.model_version = "mc-dcf-v2"; }],
    ["simulation configuration", (r) => { r.config_hash = "f".repeat(64); }],
    ["company list or wording", (r) => { r.meta.metadata_id = "f".repeat(64); }],
    ["not reported", (r) => { delete r.meta.metadata_id; }],
  ];
  for (const [expected, change] of cases) {
    const result = matching();
    change(result);
    const mismatch = settingsMismatch(MONTE_CARLO_METADATA, result);
    assert.ok(mismatch, expected);
    assert.ok(mismatch.differences.some((d) => d.includes(expected)), expected);
    assert.match(mismatch.message, /Reload the page/);
  }
});

test("a company the service no longer allows points at a stale list", () => {
  const error = describeRequestError(404, { detail: { code: "ticker_not_supported", message: "x" } });
  assert.equal(error.kind, "input");
  assert.match(error.message, /may be out of date/);
});
