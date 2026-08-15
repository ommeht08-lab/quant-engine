// Security regression tests for the atomic Lua-based rate-limit
// increment/reset (frontend/src/lib/redis.ts). Run with Node's built-in
// test runner — NO real Redis connection is made anywhere in this file:
//
//   node --test src/lib/redis-rate-limit.test.ts
//
// SCOPE, STATED PLAINLY: every test below exercises this codebase's OWN
// calling code (`incrementRateLimitCounterWithClient`/
// `resetRateLimitCounterWithClient`) against an in-memory JavaScript
// fake, never a real Redis or Upstash server. Two different kinds of
// claim follow from that, and this file is careful to keep them
// separate:
//
//   1. `FakeRedisStore` below is a small, faithful in-memory MODEL of
//      EXACTLY the semantics `INCREMENT_WITH_TTL_LUA` depends on (INCR,
//      conditionally EXPIRE only on the creating increment, DEL) — its
//      tests prove the CALLING code's contract (single round trip,
//      correct TTL semantics, explicit failure representation) IF the
//      real Lua script behaves the way this model assumes.
//   2. `TestEvalCallCapturesExactScriptKeysAndArgs` below additionally
//      asserts the EXACT script text, key, and argument this codebase
//      actually SENDS to `.eval(...)` — proving what request would
//      reach Redis, not that Redis (or Upstash's implementation of it)
//      executes that script correctly once received.
//
// NEITHER of these proves Upstash's or Redis's real Lua engine executes
// `INCREMENT_WITH_TTL_LUA` atomically or otherwise bug-free — that
// would require an actual `EVAL` round trip against a real `redis-server`
// (or the Upstash REST API), which this offline test suite is not
// permitted to contact. A real local-Redis integration test (spinning
// up `redis-server`, sending the actual script, and asserting on ITS
// response) remains optional future work, not something covered here.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  incrementRateLimitCounterWithClient,
  resetRateLimitCounterWithClient,
  INCREMENT_WITH_TTL_LUA,
  type RateLimitRedisClient,
} from "./redis.ts";

class FakeRedisStore implements RateLimitRedisClient {
  private counters = new Map<string, { count: number; hasTtl: boolean }>();
  public evalCallCount = 0;
  public incrCallCount = 0;
  public expireCallCount = 0;

  async eval<TArgs extends unknown[], TData>(_script: string, keys: string[], args: TArgs): Promise<TData> {
    this.evalCallCount++;
    // Faithfully model INCREMENT_WITH_TTL_LUA's exact logic: a single
    // synchronous state transition per call, with no `await` inside it
    // — the same all-or-nothing atomicity a real Redis EVAL provides
    // (the whole script body runs to completion before any OTHER script
    // invocation can observe or mutate this key).
    const key = keys[0];
    const windowSeconds = Number((args as unknown[])[0]);
    this.incrCallCount++;
    const existing = this.counters.get(key);
    const current = (existing?.count ?? 0) + 1;
    if (current === 1) {
      this.expireCallCount++;
      this.counters.set(key, { count: current, hasTtl: true });
    } else {
      this.counters.set(key, { count: current, hasTtl: existing!.hasTtl });
    }
    void windowSeconds;
    return current as unknown as TData;
  }

  async del(...keys: string[]): Promise<number> {
    let deleted = 0;
    for (const key of keys) {
      if (this.counters.delete(key)) deleted++;
    }
    return deleted;
  }

  getCount(key: string): number | undefined {
    return this.counters.get(key)?.count;
  }

  hasTtl(key: string): boolean {
    return this.counters.get(key)?.hasTtl ?? false;
  }

  has(key: string): boolean {
    return this.counters.has(key);
  }
}

class AlwaysFailingClient implements RateLimitRedisClient {
  async eval(): Promise<never> {
    throw new Error("simulated provider failure");
  }
  async del(): Promise<number> {
    throw new Error("simulated provider failure");
  }
}

// -- item 1: atomicity ------------------------------------------------

test("first attempt creates a TTL", async () => {
  const store = new FakeRedisStore();
  const outcome = await incrementRateLimitCounterWithClient(store, "k1", 900);
  assert.deepEqual(outcome, { ok: true, count: 1 });
  assert.equal(store.hasTtl("k1"), true);
});

test("subsequent attempts increment without extending/refreshing the window", async () => {
  const store = new FakeRedisStore();
  await incrementRateLimitCounterWithClient(store, "k1", 900);
  assert.equal(store.expireCallCount, 1);

  const second = await incrementRateLimitCounterWithClient(store, "k1", 900);
  const third = await incrementRateLimitCounterWithClient(store, "k1", 900);

  assert.deepEqual(second, { ok: true, count: 2 });
  assert.deepEqual(third, { ok: true, count: 3 });
  // EXPIRE was only ever called once — on the creating increment.
  assert.equal(store.expireCallCount, 1);
  assert.equal(store.hasTtl("k1"), true);
});

test("increments are a single round trip (one eval call), not separate incr+expire calls", async () => {
  const store = new FakeRedisStore();
  await incrementRateLimitCounterWithClient(store, "k1", 900);
  assert.equal(store.evalCallCount, 1);
});

test("concurrent attempts against the same key cannot create a counter without a TTL", async () => {
  const store = new FakeRedisStore();

  const results = await Promise.all([
    incrementRateLimitCounterWithClient(store, "shared-key", 900),
    incrementRateLimitCounterWithClient(store, "shared-key", 900),
    incrementRateLimitCounterWithClient(store, "shared-key", 900),
    incrementRateLimitCounterWithClient(store, "shared-key", 900),
    incrementRateLimitCounterWithClient(store, "shared-key", 900),
  ]);

  const counts = results.map((r) => (r.ok ? r.count : -1)).sort((a, b) => a - b);
  assert.deepEqual(counts, [1, 2, 3, 4, 5]);
  assert.equal(store.getCount("shared-key"), 5);
  // Exactly one of the five increments created the key -> exactly one EXPIRE.
  assert.equal(store.expireCallCount, 1);
  assert.equal(store.hasTtl("shared-key"), true);
});

// -- item 1: explicit failure representation ---------------------------

test("a provider error is represented explicitly, not as a thrown exception or ambiguous null", async () => {
  const failing = new AlwaysFailingClient();
  const outcome = await incrementRateLimitCounterWithClient(failing, "k1", 900);
  assert.deepEqual(outcome, { ok: false, reason: "provider_error" });
});

class NonNumericResultClient implements RateLimitRedisClient {
  async eval<TData>(): Promise<TData> {
    return "not-a-number" as unknown as TData;
  }
  async del(): Promise<number> {
    return 0;
  }
}

test("a non-numeric eval result is treated as a provider error, not trusted", async () => {
  const outcome = await incrementRateLimitCounterWithClient(new NonNumericResultClient(), "k1", 900);
  assert.deepEqual(outcome, { ok: false, reason: "provider_error" });
});

// -- item 4: reset on success, key isolation ---------------------------

test("resetting a counter clears it back to a fresh state", async () => {
  const store = new FakeRedisStore();
  await incrementRateLimitCounterWithClient(store, "k1", 900);
  await incrementRateLimitCounterWithClient(store, "k1", 900);
  assert.equal(store.getCount("k1"), 2);

  await resetRateLimitCounterWithClient(store, "k1");
  assert.equal(store.has("k1"), false);

  const afterReset = await incrementRateLimitCounterWithClient(store, "k1", 900);
  assert.deepEqual(afterReset, { ok: true, count: 1 });
  assert.equal(store.hasTtl("k1"), true);
});

test("repeated successful logins do not eventually lock the user out", async () => {
  const store = new FakeRedisStore();
  const key = "login-throttle:client-a";
  const maxAttempts = 5;

  for (let i = 0; i < 20; i++) {
    const outcome = await incrementRateLimitCounterWithClient(store, key, 900);
    assert.ok(outcome.ok, `attempt ${i} should succeed against the provider`);
    assert.ok(
      outcome.ok && outcome.count <= maxAttempts,
      `count should never exceed ${maxAttempts} because each "successful login" resets the counter`
    );
    // Simulate the login action's own behavior: a "successful" login
    // clears its own counter immediately afterward.
    await resetRateLimitCounterWithClient(store, key);
  }

  assert.equal(store.has(key), false);
});

test("failed attempts still accumulate toward and trigger the limit", async () => {
  const store = new FakeRedisStore();
  const key = "login-throttle:client-b";
  const maxAttempts = 5;

  let lastOutcome;
  for (let i = 0; i < maxAttempts + 2; i++) {
    lastOutcome = await incrementRateLimitCounterWithClient(store, key, 900);
    // No reset here — these are FAILED attempts (wrong password), never cleared.
  }

  assert.ok(lastOutcome && lastOutcome.ok);
  assert.ok(lastOutcome.ok && lastOutcome.count > maxAttempts, "count must exceed the limit after enough failures");
});

test("a success for one client cannot clear another client's counter", async () => {
  const store = new FakeRedisStore();
  const keyA = "login-throttle:client-a-digest";
  const keyB = "login-throttle:client-b-digest";

  // Client B has accumulated failed attempts.
  await incrementRateLimitCounterWithClient(store, keyB, 900);
  await incrementRateLimitCounterWithClient(store, keyB, 900);
  await incrementRateLimitCounterWithClient(store, keyB, 900);
  assert.equal(store.getCount(keyB), 3);

  // Client A logs in successfully and resets ONLY its own key.
  await incrementRateLimitCounterWithClient(store, keyA, 900);
  await resetRateLimitCounterWithClient(store, keyA);

  // Client B's counter is completely unaffected by client A's success.
  assert.equal(store.getCount(keyB), 3);
});

// -- item 5: capture the exact eval() call (script, keys, args) ---------
//
// A dedicated recording fake, separate from `FakeRedisStore` — this one
// does NOT model Redis state at all, it exists purely to record exactly
// what `incrementRateLimitCounterWithClient` sends to `.eval(...)`.

class RecordingRedisClient implements RateLimitRedisClient {
  public calls: { script: string; keys: string[]; args: unknown[] }[] = [];

  async eval<TArgs extends unknown[], TData>(script: string, keys: string[], args: TArgs): Promise<TData> {
    this.calls.push({ script, keys, args: args as unknown[] });
    return 1 as unknown as TData;
  }

  async del(): Promise<number> {
    return 0;
  }
}

test("increment sends exactly one eval() call with the exact script, key, and fixed-window argument", async () => {
  const recorder = new RecordingRedisClient();

  await incrementRateLimitCounterWithClient(recorder, "login-throttle:some-digest", 900);

  // Exactly one network-shaped call — this IS the atomicity-relevant
  // claim this suite can actually verify from the JS side: a single
  // round trip, not two separate INCR/EXPIRE calls that could leave a
  // window between them. It does NOT verify Redis executes the script
  // atomically once received — see this file's top-of-file comment.
  assert.equal(recorder.calls.length, 1);

  const [call] = recorder.calls;
  assert.equal(call.script, INCREMENT_WITH_TTL_LUA);
  assert.deepEqual(call.keys, ["login-throttle:some-digest"]);
  assert.deepEqual(call.args, ["900"]); // the fixed window length, stringified for ARGV[1]
});

test("the captured script text contains exactly the INCR + conditional EXPIRE this module documents", () => {
  // A light content check on the exported constant itself (independent
  // of any call) — if this script is ever edited, this test forces the
  // edit to be deliberate rather than accidental.
  assert.match(INCREMENT_WITH_TTL_LUA, /redis\.call\("INCR", KEYS\[1\]\)/);
  assert.match(INCREMENT_WITH_TTL_LUA, /redis\.call\("EXPIRE", KEYS\[1\], ARGV\[1\]\)/);
  assert.match(INCREMENT_WITH_TTL_LUA, /if current == 1 then/);
});
