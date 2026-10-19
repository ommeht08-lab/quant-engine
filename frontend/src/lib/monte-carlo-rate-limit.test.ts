import assert from "node:assert/strict";
import test from "node:test";

import {
  MONTE_CARLO_LIGHT_MAX_REQUESTS,
  MONTE_CARLO_RUN_MAX_REQUESTS,
  MONTE_CARLO_WINDOW_SECONDS,
  decideMonteCarloRateLimit,
  monteCarloRateLimitKey,
} from "./monte-carlo-rate-limit.ts";

test("keys are namespaced by kind and never contain the raw client address", () => {
  const run = monteCarloRateLimitKey("run", "203.0.113.7", "subkey");
  const light = monteCarloRateLimitKey("light", "203.0.113.7", "subkey");
  assert.match(run, /^monte-carlo-run:[a-f0-9]{64}$/);
  assert.match(light, /^monte-carlo-light:[a-f0-9]{64}$/);
  assert.notEqual(run.split(":")[1], "203.0.113.7");
  assert.equal(run.split(":")[1], light.split(":")[1]); // same digest, separate counters
});

test("running a simulation has a tighter quota than the light routes", () => {
  assert.equal(MONTE_CARLO_RUN_MAX_REQUESTS, 10);
  assert.ok(MONTE_CARLO_LIGHT_MAX_REQUESTS > MONTE_CARLO_RUN_MAX_REQUESTS);
});

test("the final request in the window is allowed and the next is limited", async () => {
  let window = 0;
  const base = { kind: "run" as const, key: "k", isProduction: true, failOpenOverride: undefined };
  const atLimit = await decideMonteCarloRateLimit(base, async (_k, seconds) => {
    window = seconds;
    return { ok: true, count: MONTE_CARLO_RUN_MAX_REQUESTS };
  });
  const over = await decideMonteCarloRateLimit(base, async () => ({ ok: true, count: MONTE_CARLO_RUN_MAX_REQUESTS + 1 }));
  assert.equal(atLimit, "allow");
  assert.equal(window, MONTE_CARLO_WINDOW_SECONDS);
  assert.equal(over, "limited");
});

test("each kind is judged against its own limit", async () => {
  const count = MONTE_CARLO_RUN_MAX_REQUESTS + 1;
  const common = { key: "k", isProduction: true, failOpenOverride: undefined };
  assert.equal(await decideMonteCarloRateLimit({ ...common, kind: "run" }, async () => ({ ok: true, count })), "limited");
  assert.equal(await decideMonteCarloRateLimit({ ...common, kind: "light" }, async () => ({ ok: true, count })), "allow");
});

test("an unavailable limiter fails closed in production and open elsewhere", async () => {
  const down = async () => ({ ok: false as const, reason: "provider_error" as const });
  assert.equal(await decideMonteCarloRateLimit({ kind: "run", key: "k", isProduction: true, failOpenOverride: undefined }, down), "unavailable");
  assert.equal(await decideMonteCarloRateLimit({ kind: "run", key: "k", isProduction: false, failOpenOverride: undefined }, down), "allow");
  assert.equal(await decideMonteCarloRateLimit({ kind: "run", key: "k", isProduction: true, failOpenOverride: "true" }, down), "allow");
  assert.equal(await decideMonteCarloRateLimit({ kind: "run", key: "k", isProduction: true, failOpenOverride: "yes" }, down), "unavailable");
});
