import assert from "node:assert/strict";
import test from "node:test";

/**
 * Node's built-in test runner has no `localStorage` global (that's a
 * browser/Next.js client-component API) — `valuation-history.ts` guards
 * on `typeof localStorage`, not `typeof window`, specifically so a small
 * in-memory stand-in like this one is enough to exercise it for real,
 * without needing a DOM/jsdom dependency this repo doesn't otherwise use.
 */
class FakeLocalStorage {
  private store = new Map<string, string>();

  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  clear(): void {
    this.store.clear();
  }

  // Set directly by a test that wants to simulate hand-edited/legacy
  // storage content without going through `setItem`'s own JSON.stringify.
  setRaw(key: string, rawValue: string): void {
    this.store.set(key, rawValue);
  }
}

const STORAGE_KEY = "valuation-engine:valuation-history:v1";
let fakeStorage: FakeLocalStorage;

// A fresh fake store before each test — no shared state leaking between
// cases (each test would otherwise see every earlier test's writes).
test.beforeEach(() => {
  fakeStorage = new FakeLocalStorage();
  (globalThis as unknown as { localStorage: unknown }).localStorage = fakeStorage;
});

test.after(() => {
  delete (globalThis as unknown as { localStorage?: unknown }).localStorage;
});

// Imported AFTER the localStorage shim is installed above would be safer
// in a module system that re-evaluated per test, but ESM caches the
// module after first import — the functions below read `localStorage` as
// a bare global lookup on EVERY call (not captured once at import time),
// so installing the shim in `beforeEach` before each test body runs is
// sufficient regardless of import order.
const { readValuationHistory, recordValuationRun } = await import("./valuation-history.ts");

function baseSnapshot(overrides: Partial<Parameters<typeof recordValuationRun>[0]> = {}) {
  return {
    ticker: "AAPL",
    baseIntrinsicValuePerShare: 187.42,
    marketPrice: 175.3,
    marketGapPct: 0.0692,
    qualityLevel: "ordinary" as const,
    assumptionMode: "historical" as const,
    terminalGrowthRate: 0.025,
    ...overrides,
  };
}

test("readValuationHistory: returns an empty array when nothing is stored", () => {
  assert.deepEqual(readValuationHistory(), []);
});

test("recordValuationRun: round-trips a full snapshot with an assigned timestamp", () => {
  const result = recordValuationRun(baseSnapshot(), 1_000);
  assert.equal(result.length, 1);
  assert.equal(result[0].ticker, "AAPL");
  assert.equal(result[0].timestamp, 1_000);
  assert.equal(result[0].baseIntrinsicValuePerShare, 187.42);
  assert.equal(result[0].marketPrice, 175.3);
  assert.equal(result[0].marketGapPct, 0.0692);
  assert.equal(result[0].qualityLevel, "ordinary");
  assert.equal(result[0].assumptionMode, "historical");
  assert.equal(result[0].terminalGrowthRate, 0.025);

  // Genuinely persisted, not just returned — a fresh read sees it too.
  assert.deepEqual(readValuationHistory(), result);
});

test("recordValuationRun: most recent run is first", () => {
  recordValuationRun(baseSnapshot({ ticker: "AAPL" }), 1_000);
  recordValuationRun(baseSnapshot({ ticker: "MSFT" }), 10_000);
  const history = readValuationHistory();
  assert.equal(history.length, 2);
  assert.equal(history[0].ticker, "MSFT");
  assert.equal(history[1].ticker, "AAPL");
});

test("recordValuationRun: null fields (withheld comparison, uncomputable base case) round-trip as null", () => {
  const result = recordValuationRun(
    baseSnapshot({
      baseIntrinsicValuePerShare: null,
      marketPrice: null,
      marketGapPct: null,
      qualityLevel: "diagnostic_only",
    }),
    1_000
  );
  assert.equal(result[0].baseIntrinsicValuePerShare, null);
  assert.equal(result[0].marketPrice, null);
  assert.equal(result[0].marketGapPct, null);
  assert.equal(result[0].qualityLevel, "diagnostic_only");
});

test("history cap: keeps only the most recent 12 entries", () => {
  for (let i = 0; i < 15; i++) {
    recordValuationRun(baseSnapshot({ ticker: `T${i}` }), 1_000 + i * 10_000);
  }
  const history = readValuationHistory();
  assert.equal(history.length, 12);
  // The 3 oldest (T0, T1, T2) were evicted; the most recent (T14) is first.
  assert.equal(history[0].ticker, "T14");
  assert.equal(history[11].ticker, "T3");
});

test("duplicate-write protection: a second run of the SAME ticker with an IDENTICAL outcome, inside the window, replaces the entry instead of appending", () => {
  recordValuationRun(baseSnapshot({ ticker: "AAPL", baseIntrinsicValuePerShare: 100 }), 1_000);
  const result = recordValuationRun(
    baseSnapshot({ ticker: "AAPL", baseIntrinsicValuePerShare: 100 }), // same outcome, fired again
    1_800 // 800ms later — inside the 1500ms dedupe window
  );
  assert.equal(result.length, 1, "expected the exact-repeat to overwrite, not append");
  assert.equal(result[0].baseIntrinsicValuePerShare, 100);
  assert.equal(result[0].timestamp, 1_000, "expected the ORIGINAL timestamp to be preserved for the collapsed entry");
});

test("duplicate protection does NOT suppress two legitimate consecutive valuations of the same ticker with a DIFFERENT result", () => {
  // A user runs AAPL under historical assumptions, immediately switches to
  // custom assumptions, and reruns AAPL a second time — two real, distinct
  // valuations of the same company, seconds apart. Matching on ticker
  // alone would wrongly collapse these into one; the rule must require
  // the full outcome to match, not just the ticker.
  recordValuationRun(
    baseSnapshot({ ticker: "AAPL", assumptionMode: "historical", baseIntrinsicValuePerShare: 187.42 }),
    1_000
  );
  const result = recordValuationRun(
    baseSnapshot({ ticker: "AAPL", assumptionMode: "custom", baseIntrinsicValuePerShare: 240.1 }),
    1_800 // 800ms later — inside the dedupe window, but a genuinely different run
  );
  assert.equal(result.length, 2, "two distinct valuations of the same ticker must both be preserved");
  assert.equal(result[0].assumptionMode, "custom");
  assert.equal(result[0].baseIntrinsicValuePerShare, 240.1);
  assert.equal(result[0].timestamp, 1_800, "the second, genuinely different run gets its own timestamp");
  assert.equal(result[1].assumptionMode, "historical");
  assert.equal(result[1].baseIntrinsicValuePerShare, 187.42);
});

test("duplicate protection does NOT suppress two identical-looking runs of the same ticker at a different terminal growth rate", () => {
  recordValuationRun(baseSnapshot({ ticker: "AAPL", terminalGrowthRate: 0.025 }), 1_000);
  const result = recordValuationRun(baseSnapshot({ ticker: "AAPL", terminalGrowthRate: 0.03 }), 1_200);
  assert.equal(result.length, 2, "a changed terminal growth rate makes this a different run, not a duplicate");
});

test("duplicate-write protection: a second run of the SAME ticker OUTSIDE the window appends a new entry", () => {
  recordValuationRun(baseSnapshot({ ticker: "AAPL" }), 1_000);
  const result = recordValuationRun(baseSnapshot({ ticker: "AAPL" }), 1_000 + 5_000);
  assert.equal(result.length, 2, "expected two distinct runs, not a collapse");
});

test("duplicate-write protection: a run of a DIFFERENT ticker inside the window still appends", () => {
  recordValuationRun(baseSnapshot({ ticker: "AAPL" }), 1_000);
  const result = recordValuationRun(baseSnapshot({ ticker: "MSFT" }), 1_100);
  assert.equal(result.length, 2, "a different ticker is never treated as a duplicate of the last run");
});

test("malformed payload: non-JSON stored content is treated as empty history, never thrown", () => {
  fakeStorage.setRaw(STORAGE_KEY, "{not valid json");
  assert.deepEqual(readValuationHistory(), []);
});

test("malformed payload: a JSON value that isn't an array is treated as empty history", () => {
  fakeStorage.setRaw(STORAGE_KEY, JSON.stringify({ ticker: "AAPL" }));
  assert.deepEqual(readValuationHistory(), []);
});

test("malformed payload: invalid entries are dropped; valid entries in the same array survive", () => {
  const valid = {
    ticker: "AAPL",
    timestamp: 1_000,
    baseIntrinsicValuePerShare: 187.42,
    marketPrice: 175.3,
    marketGapPct: 0.0692,
    qualityLevel: "ordinary",
    assumptionMode: "historical",
    terminalGrowthRate: 0.025,
  };
  fakeStorage.setRaw(
    STORAGE_KEY,
    JSON.stringify([
      valid,
      { ticker: "MSFT" }, // missing every other required field
      { ...valid, ticker: "" }, // empty ticker
      { ...valid, ticker: "TOOLONGTICKER" }, // over 10 characters
      { ...valid, qualityLevel: "not-a-real-level" }, // invalid enum value
      { ...valid, assumptionMode: "guessed" }, // invalid enum value
      "just a string, not an object",
      null,
    ])
  );
  const history = readValuationHistory();
  assert.equal(history.length, 1);
  assert.equal(history[0].ticker, "AAPL");
});

test("nonfinite values: a stored numeric field that overflows to Infinity via JSON is rejected", () => {
  // JSON.parse does not reject numeric overflow — `JSON.parse("1e400")`
  // legitimately evaluates to the JS value `Infinity`. Building this as a
  // JS object first and calling JSON.stringify on it would NOT reproduce
  // this case (a JS `Infinity` value stringifies to the JSON token
  // `null`, losing the overflow entirely) — the raw JSON text has to be
  // constructed directly, the way a hand-edited or corrupted storage
  // value could genuinely contain it.
  assert.equal(JSON.parse("1e400"), Infinity, "sanity check on the JSON.parse overflow behavior this test relies on");
  const raw = `[{
    "ticker": "AAPL",
    "timestamp": 1000,
    "baseIntrinsicValuePerShare": 1e400,
    "marketPrice": 175.3,
    "marketGapPct": 0.0692,
    "qualityLevel": "ordinary",
    "assumptionMode": "historical",
    "terminalGrowthRate": 0.025
  }]`;
  fakeStorage.setRaw(STORAGE_KEY, raw);
  assert.deepEqual(readValuationHistory(), []);
});

test("nonfinite values: a non-numeric type in a numeric field is rejected", () => {
  fakeStorage.setRaw(
    STORAGE_KEY,
    JSON.stringify([
      {
        ticker: "AAPL",
        timestamp: 1_000,
        baseIntrinsicValuePerShare: "not-a-number",
        marketPrice: 175.3,
        marketGapPct: 0.0692,
        qualityLevel: "ordinary",
        assumptionMode: "historical",
        terminalGrowthRate: 0.025,
      },
    ])
  );
  assert.deepEqual(readValuationHistory(), []);
});

test("readValuationHistory: gracefully returns empty when localStorage itself throws (e.g. blocked storage)", () => {
  const throwingStorage = {
    getItem(): string {
      throw new Error("storage disabled");
    },
    setItem(): void {
      throw new Error("storage disabled");
    },
  };
  (globalThis as unknown as { localStorage: unknown }).localStorage = throwingStorage;
  assert.deepEqual(readValuationHistory(), []);
  // recordValuationRun must not throw either, even though the write itself is a no-op.
  assert.doesNotThrow(() => recordValuationRun(baseSnapshot(), 1_000));
});
