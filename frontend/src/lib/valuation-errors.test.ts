import assert from "node:assert/strict";
import test from "node:test";

import { valuationErrorFromResponse } from "./valuation-errors.ts";

test("uses the route error field instead of collapsing to a generic HTTP message", () => {
  assert.deepEqual(
    valuationErrorFromResponse(503, {
      code: "VALUATION_BACKEND_UNCONFIGURED",
      error: "Live valuation is not connected to this deployment yet.",
    }),
    {
      kind: "unavailable",
      message: "Live valuation is not connected to this deployment yet.",
    }
  );
});

test("preserves FastAPI detail messages for invalid valuation inputs", () => {
  assert.deepEqual(valuationErrorFromResponse(422, { detail: "Terminal growth is too high." }), {
    kind: "input",
    message: "Terminal growth is too high.",
  });
});

test("classifies an unreachable backend as unavailable", () => {
  assert.equal(
    valuationErrorFromResponse(502, { code: "VALUATION_BACKEND_UNREACHABLE" }).kind,
    "unavailable"
  );
});

test("retains an honest fallback for unexpected failures", () => {
  assert.deepEqual(valuationErrorFromResponse(500, null), {
    kind: "request",
    message: "Valuation request failed (HTTP 500).",
  });
});
