import { Redis } from "@upstash/redis";

/**
 * A Redis client is stashed on `globalThis` (not a plain module-level
 * variable) so it survives Next.js dev-server HMR reloads instead of
 * constructing a new one on every hot reload — same pattern already
 * used for the `pg.Pool` instances elsewhere in this app.
 */
declare global {
  var _redisClient: Redis | undefined;
}

function getRedisClient(): Redis | null {
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) return null;

  if (!global._redisClient) {
    global._redisClient = new Redis({ url, token });
  }
  return global._redisClient;
}

/**
 * Cache-aside wrapper: return the cached value for `key` if present,
 * otherwise call `fetcher`, cache its result for `ttlSeconds`, and
 * return it.
 *
 * Never throws — any Redis failure (unreachable, misconfigured,
 * `UPSTASH_REDIS_REST_URL`/`TOKEN` unset) falls through to calling
 * `fetcher` directly, since caching is a performance / rate-limit
 * optimization, not a correctness requirement.
 *
 * `fetcher`'s resolved value must be JSON-serializable — the Upstash
 * SDK handles the (de)serialization of plain objects/arrays itself.
 */
export async function cacheAside<T>(
  key: string,
  ttlSeconds: number,
  fetcher: () => Promise<T>
): Promise<T> {
  const client = getRedisClient();
  if (!client) return fetcher();

  try {
    const cached = await client.get<T>(key);
    if (cached !== null && cached !== undefined) {
      return cached;
    }
  } catch (error) {
    console.error(`Redis GET failed for "${key}"; calling fetcher directly:`, error);
    return fetcher();
  }

  const result = await fetcher();

  try {
    await client.set(key, result, { ex: ttlSeconds });
  } catch (error) {
    console.error(`Redis SET failed for "${key}":`, error);
  }

  return result;
}

/**
 * Result of a rate-limit provider operation. Failures are represented
 * EXPLICITLY (`ok: false` with a `reason`) rather than collapsed into a
 * bare `null`/`undefined` — the caller (`src/app/login/actions.ts`)
 * must be able to tell "Redis isn't configured for this deployment"
 * apart from "Redis is configured but this specific call failed" apart
 * from a genuine count, because the production fail-open/fail-closed
 * policy depends on knowing definitively that rate limiting did NOT
 * run, not just receiving an ambiguous falsy value.
 */
export type RateLimitOutcome =
  | { ok: true; count: number }
  | { ok: false; reason: "unconfigured" | "provider_error" };

/**
 * Minimal client surface `incrementRateLimitCounterWithClient`/
 * `resetRateLimitCounterWithClient` need — narrower than the full
 * `@upstash/redis` `Redis` class, so tests can pass a small in-memory
 * fake implementing just these two methods instead of a real network
 * client.
 */
export interface RateLimitRedisClient {
  eval<TArgs extends unknown[] = unknown[], TData = unknown>(
    script: string,
    keys: string[],
    args: TArgs
  ): Promise<TData>;
  del(...keys: string[]): Promise<number>;
}

/**
 * Increment-with-TTL as a SINGLE Redis-side Lua script (`EVAL`), run
 * with `KEYS[1]` = the counter key and `ARGV[1]` = the fixed window's
 * length in seconds:
 *
 *   1. INCR the counter (creating it at 1 if it didn't exist).
 *   2. ONLY if this increment just created the key (current == 1), set
 *      its expiry to the window length.
 *   3. Return the new count.
 *
 * This is NOT the same guarantee as sending `INCR` then `EXPIRE` as two
 * separate client calls (the previous implementation) — that is two
 * independent round trips, and if the process crashes, the connection
 * drops, or Upstash's REST endpoint fails BETWEEN them, the key is left
 * incremented with NO expiry at all, i.e. a permanent lockout for
 * whoever that key identifies. It is also not the same guarantee as an
 * Upstash "pipeline": a pipeline batches multiple commands into one
 * HTTP round trip for efficiency, but does NOT make them execute as a
 * single atomic/isolated unit — Redis can still interleave another
 * client's commands between two pipelined commands. Only a Lua script
 * run via `EVAL` (or a `MULTI`/`EXEC` transaction) is guaranteed to run
 * to completion as one atomic, isolated unit on the server — no other
 * command from any other client can be interleaved partway through it.
 * That is the specific guarantee this script depends on, and a
 * pipeline would not provide it.
 */
// Exported so `frontend/src/lib/redis-rate-limit.test.ts` can assert
// the EXACT script text/keys/args this module sends to `.eval(...)` —
// see that test file for what such an assertion does and does not
// prove (it exercises this codebase's own calling code, never a real
// Redis/Upstash Lua engine).
export const INCREMENT_WITH_TTL_LUA = `
local current = redis.call("INCR", KEYS[1])
if current == 1 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return current
`;

/**
 * Testable core of `incrementRateLimitCounter`, taking the Redis client
 * as a parameter instead of resolving it internally — see
 * `frontend/src/lib/redis-rate-limit.test.ts`, which exercises this
 * against an in-memory fake implementing `RateLimitRedisClient`, never
 * a real network connection.
 */
export async function incrementRateLimitCounterWithClient(
  client: RateLimitRedisClient,
  key: string,
  windowSeconds: number
): Promise<RateLimitOutcome> {
  try {
    const count = await client.eval<string[], number>(INCREMENT_WITH_TTL_LUA, [key], [String(windowSeconds)]);
    if (typeof count !== "number" || !Number.isFinite(count) || count < 1) {
      return { ok: false, reason: "provider_error" };
    }
    return { ok: true, count };
  } catch (error) {
    console.error(
      "Redis EVAL (rate-limit increment) failed:",
      error instanceof Error ? error.message : "unknown error"
    );
    return { ok: false, reason: "provider_error" };
  }
}

/**
 * Increment a fixed-window rate-limit counter for `key` — used by the
 * login throttle (`src/app/login/actions.ts`) — via a single atomic
 * Redis-side Lua script (see `INCREMENT_WITH_TTL_LUA` above). Only the
 * increment that CREATES the key sets its expiry; later increments
 * never refresh/extend it, so a sustained burst of attempts can't keep
 * the window open indefinitely.
 *
 * A plain module-level in-memory counter would be unsafe here: this app
 * runs as serverless functions (Vercel), where each invocation can land
 * on a different, short-lived instance with its own memory, making any
 * process-local counter trivially bypassed. Upstash Redis is external,
 * shared state, so the count is consistent across every instance.
 *
 * Returns `{ ok: false, reason: "unconfigured" }` if Redis isn't
 * configured for this deployment, or `{ ok: false, reason:
 * "provider_error" }` if it's configured but the call itself failed —
 * the caller (`src/app/login/actions.ts`) applies an explicit
 * fail-open/fail-closed policy based on which of these it got, rather
 * than always failing open the way this function's predecessor did.
 */
export async function incrementRateLimitCounter(key: string, windowSeconds: number): Promise<RateLimitOutcome> {
  const client = getRedisClient();
  if (!client) return { ok: false, reason: "unconfigured" };
  return incrementRateLimitCounterWithClient(client, key, windowSeconds);
}

/** Testable core of `resetRateLimitCounter` — see `incrementRateLimitCounterWithClient`. */
export async function resetRateLimitCounterWithClient(client: RateLimitRedisClient, key: string): Promise<void> {
  try {
    await client.del(key);
  } catch (error) {
    console.error("Redis DEL (rate-limit reset) failed:", error instanceof Error ? error.message : "unknown error");
  }
}

/**
 * Clear a rate-limit counter — called on a SUCCESSFUL login
 * (`src/app/login/actions.ts`) so a legitimate operator logging in
 * repeatedly (different tabs/devices, or after a session expires) never
 * accumulates toward the limit. `key` is derived solely from the
 * calling request's own HMAC'd client identifier
 * (`src/lib/client-identifier.ts`), so this can only ever clear that
 * SAME client's own counter — there is no parameter or code path here
 * that could target a different client's key. Best-effort: if the `DEL`
 * itself fails, the window still expires on its own TTL regardless.
 */
export async function resetRateLimitCounter(key: string): Promise<void> {
  const client = getRedisClient();
  if (!client) return;
  return resetRateLimitCounterWithClient(client, key);
}
