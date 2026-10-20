import assert from "node:assert/strict";
import test from "node:test";

import {
  DESCRIPTION_CACHE_KEY,
  readCachedDescription,
  reconcileTicker,
  writeCachedDescription,
} from "./monte-carlo-description-cache.ts";
import type { MonteCarloDescription } from "./monte-carlo-types.ts";

const description = {
  model_version: "mc-dcf-v1", tickers: ["AAPL", "KO"], experiments: ["growth"], default_seed: 1,
  config: {}, config_hash: "x", disclosures: [{ id: "a", text: "b" }],
} as unknown as MonteCarloDescription;

function memory(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return { data, getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => { data[k] = v; } };
}

test("a written description reads back", () => {
  const store = memory();
  writeCachedDescription(store, description);
  assert.deepEqual(readCachedDescription(store), description);
});

test("missing, corrupt or invalid entries are ignored", () => {
  assert.equal(readCachedDescription(memory()), null);
  assert.equal(readCachedDescription(memory({ [DESCRIPTION_CACHE_KEY]: "not json" })), null);
  assert.equal(readCachedDescription(memory({ [DESCRIPTION_CACHE_KEY]: JSON.stringify({ ...description, tickers: [] }) })), null);
  assert.equal(readCachedDescription(memory({ [DESCRIPTION_CACHE_KEY]: JSON.stringify({ tickers: ["AAPL"] }) })), null);
  assert.equal(readCachedDescription(null), null);
  assert.equal(readCachedDescription(undefined), null);
});

test("a storage that throws never breaks the page", () => {
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("quota"); } };
  assert.equal(readCachedDescription(broken), null);
  assert.doesNotThrow(() => writeCachedDescription(broken, description));
});

test("the selected company is kept only while it is still allowed", () => {
  assert.equal(reconcileTicker("KO", ["AAPL", "KO"]), "KO");
  assert.equal(reconcileTicker("JNJ", ["AAPL", "KO"]), "AAPL");
  assert.equal(reconcileTicker("", ["AAPL", "KO"]), "AAPL");
  assert.equal(reconcileTicker("AAPL", []), "");
});
