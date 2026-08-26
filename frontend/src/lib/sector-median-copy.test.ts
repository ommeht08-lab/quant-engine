import assert from "node:assert/strict";
import test from "node:test";

import { sectorMedianProvenanceCaption, sectorMedianUnavailableCopy } from "./sector-median-copy.ts";

const ALL_CODES = ["incompatible_assumptions", "insufficient_peers", "snapshot_unavailable"] as const;

for (const code of ALL_CODES) {
  test(`sectorMedianUnavailableCopy(${code}) returns a non-empty message with no backend internals`, () => {
    const message = sectorMedianUnavailableCopy(code);
    assert.equal(typeof message, "string");
    assert.ok(message.length > 0);
    // Guards against ever accidentally interpolating backend internals
    // (staleness windows, coverage percentages, raw exception text) into
    // this copy.
    assert.ok(!/stale|coverage|malformed|exception|error/i.test(message));
  });

  test(`sectorMedianUnavailableCopy(${code}) is stable / does not vary by call`, () => {
    assert.equal(sectorMedianUnavailableCopy(code), sectorMedianUnavailableCopy(code));
  });
}

test("each stable code produces a DIFFERENT, truthful message — no single one-size-fits-all copy", () => {
  const messages = new Set(ALL_CODES.map((code) => sectorMedianUnavailableCopy(code)));
  assert.equal(messages.size, ALL_CODES.length);
});

test("incompatible_assumptions copy mentions baseline assumptions, not a generic unavailability", () => {
  assert.match(sectorMedianUnavailableCopy("incompatible_assumptions"), /baseline/i);
});

test("insufficient_peers copy mentions comparable companies, not staleness or connectivity", () => {
  assert.match(sectorMedianUnavailableCopy("insufficient_peers"), /comparable companies/i);
});

test("an unrecognized or missing code falls back to the generic snapshot_unavailable copy", () => {
  assert.equal(sectorMedianUnavailableCopy(null), sectorMedianUnavailableCopy("snapshot_unavailable"));
  assert.equal(sectorMedianUnavailableCopy(undefined), sectorMedianUnavailableCopy("snapshot_unavailable"));
});

test("no unavailable-copy branch promises that a refresh will fix the condition", () => {
  for (const code of ALL_CODES) {
    assert.ok(!/next (scheduled )?refresh|will (update|resolve|fix)/i.test(sectorMedianUnavailableCopy(code)));
  }
});

test("sectorMedianProvenanceCaption reports universe coverage and sector sample count", () => {
  const caption = sectorMedianProvenanceCaption({
    generated_at: "2026-08-24T12:00:00+00:00",
    universe_size: 100,
    tickers_used: 87,
    sector_sample_count: 12,
  });
  assert.match(caption, /87\/100 tickers valued/);
  assert.match(caption, /12 sampled in this sector/);
  assert.match(caption, /Aug 24, 2026/);
});

test("sectorMedianProvenanceCaption degrades gracefully on an unparseable timestamp", () => {
  const caption = sectorMedianProvenanceCaption({
    generated_at: "not-a-real-timestamp",
    universe_size: 10,
    tickers_used: 8,
    sector_sample_count: 3,
  });
  assert.match(caption, /unknown date/);
  assert.match(caption, /8\/10 tickers valued/);
});
