import assert from "node:assert/strict";
import test from "node:test";

import { makeExperiment, makeProvenance, makeResponse } from "./monte-carlo-fixtures.ts";
import {
  isBaselineProvenance,
  isMonteCarloDescription,
  isMonteCarloResponse,
  readBaselineProvenance,
} from "./monte-carlo-types.ts";

const clone = <T,>(value: T): T => structuredClone(value);

test("a consistent response is accepted, including after a JSON round trip", () => {
  assert.equal(isMonteCarloResponse(makeResponse()), true);
  assert.equal(isMonteCarloResponse(JSON.parse(JSON.stringify(makeResponse()))), true);
});

test("non-objects and unknown schemas are rejected", () => {
  for (const value of [null, undefined, 1, "x", [], {}]) assert.equal(isMonteCarloResponse(value), false);
  assert.equal(isMonteCarloResponse({ ...makeResponse(), schema: "other" }), false);
});

test("an incomplete experiment is valid when its counts add up", () => {
  const response = makeResponse();
  response.status = "partial";
  response.experiments.growth = makeExperiment({
    status: "incomplete",
    valid: 94,
    attempted: 100,
    failed_total: 6,
    failed_by_code: { nonfinite_projection: 6 },
    above_market: { count: 47, denominator: 94, fraction: 0.5 },
    histogram: { counts: [9, 55, 25], underflow: 3, overflow: 2 },
  });
  assert.equal(isMonteCarloResponse(response), true);
});

test("a refused experiment must carry a code and no results", () => {
  const response = makeResponse();
  response.status = "partial";
  response.experiments.growth = makeExperiment({
    status: "refused", refusal_code: "insufficient_history", refusal_message: "x", valid: 0, attempted: 0,
    failed_total: 0, failed_by_code: {}, statistics: null, above_market: null, histogram: null,
    sampled_input_summary: null,
  });
  assert.equal(isMonteCarloResponse(response), true);
  response.experiments.growth.refusal_code = null;
  assert.equal(isMonteCarloResponse(response), false);
});

test("inconsistent counts are rejected", () => {
  const cases: Array<[string, (r: ReturnType<typeof makeResponse>) => void]> = [
    ["denominator is not the valid count", (r) => { r.experiments.combined.above_market = { count: 60, denominator: 10000, fraction: 0.006 }; }],
    ["fraction does not match count/denominator", (r) => { r.experiments.combined.above_market!.fraction = 0.9; }],
    ["histogram does not sum to valid", (r) => { r.experiments.combined.histogram!.counts = [10, 60, 24]; }],
    ["histogram has the wrong number of bins", (r) => { r.experiments.combined.histogram!.counts = [50, 50]; }],
    ["valid + failed != attempted", (r) => { r.experiments.combined.attempted = 120; }],
    ["complete but short of target", (r) => { r.experiments.combined.target = 200; }],
    ["percentiles out of order", (r) => { r.experiments.combined.statistics!.p10 = 200; }],
    ["a non-finite statistic", (r) => { r.experiments.combined.statistics!.mean = Number.NaN; }],
    ["edges not increasing", (r) => { r.histogram_edges = [70, 70, 110, 130]; }],
    ["edges missing but histograms present", (r) => { r.histogram_edges = null; r.histogram_edges_basis = null; }],
    ["a missing experiment", (r) => { delete (r.experiments as Record<string, unknown>).wacc; }],
    ["a bad overall status", (r) => { (r as unknown as Record<string, unknown>).status = "great"; }],
    ["a missing digest", (r) => { r.result_digest = ""; }],
    ["a non-boolean clamp flag", (r) => { (r.baseline as unknown as Record<string, unknown>).wacc_was_clamped = "no"; }],
    ["no identity", (r) => { (r as unknown as Record<string, unknown>).identity = null; }],
  ];
  for (const [name, mutate] of cases) {
    const response = clone(makeResponse());
    mutate(response);
    assert.equal(isMonteCarloResponse(response), false, name);
  }
});

test("a valid result with no histogram is accepted only when there are no edges", () => {
  const response = makeResponse({ histogram_edges: null, histogram_edges_basis: null });
  for (const name of ["growth", "margin", "wacc", "combined"] as const) response.experiments[name].histogram = null;
  assert.equal(isMonteCarloResponse(response), true);
});

test("description validation", () => {
  const description = {
    model_version: "mc-dcf-v1", tickers: ["AAPL", "KO"], experiments: ["growth"], default_seed: 1,
    config: {}, config_hash: "x", disclosures: [{ id: "a", text: "b" }],
  };
  assert.equal(isMonteCarloDescription(description), true);
  assert.equal(isMonteCarloDescription({ ...description, tickers: [] }), false);
  assert.equal(isMonteCarloDescription({ ...description, tickers: [1] }), false);
  assert.equal(isMonteCarloDescription(null), false);
});

test("a response without a provenance block (an older result) is still accepted and reads as absent", () => {
  const response = makeResponse();
  assert.equal(isMonteCarloResponse(response), true);
  assert.deepEqual(readBaselineProvenance(response), { kind: "absent" });
});

test("a well-formed provenance block is read as ok, including after a JSON round trip", () => {
  const response = makeResponse({ baseline_provenance: makeProvenance() });
  assert.equal(isMonteCarloResponse(response), true);
  const reading = readBaselineProvenance(JSON.parse(JSON.stringify(response)));
  assert.equal(reading.kind, "ok");
  assert.equal(isBaselineProvenance(makeProvenance()), true);
});

test("a malformed or unavailable provenance block never hides the numerical results", () => {
  const broken = clone(makeProvenance()) as unknown as Record<string, unknown>;
  delete broken.margin;
  const malformed = makeResponse({ baseline_provenance: broken });
  assert.equal(isMonteCarloResponse(malformed), true);
  assert.deepEqual(readBaselineProvenance(malformed), { kind: "unavailable" });

  const unavailable = makeResponse({ baseline_provenance: { schema: "mc-dcf-baseline-provenance-v1", status: "unavailable", error_type: "KeyError", provenance_digest: "x" } });
  assert.equal(isMonteCarloResponse(unavailable), true);
  assert.deepEqual(readBaselineProvenance(unavailable), { kind: "unavailable" });
});

test("provenance fields with the wrong type are rejected one by one", () => {
  type Loose = Record<string, Record<string, unknown> & { growth?: Record<string, unknown> }>;
  const mutations: [string, (p: Loose) => void][] = [
    ["quote time not a string", (p) => { (p.timing.market_price as Record<string, unknown>).observed_at = 5; }],
    ["fallback flag missing", (p) => { delete p.cost_of_debt.is_fallback; }],
    ["tax rate not a number", (p) => { p.tax_rate.rate = "17%"; }],
    ["row list not strings", (p) => { p.margin.rows_used = [1]; }],
    ["fixed flag missing", (p) => { delete p.capex.fixed; }],
    ["wacc clamp flag missing", (p) => { delete p.wacc.baseline_was_clamped; }],
    ["illustration multipliers missing", (p) => { delete (p.spread_illustration.growth as Record<string, unknown>).upper_multiplier; }],
    ["cash definition missing", (p) => { delete p.cash.definition; }],
  ];
  for (const [name, mutate] of mutations) {
    const block = clone(makeProvenance()) as unknown as Loose;
    mutate(block);
    assert.equal(isBaselineProvenance(block), false, name);
  }
});
