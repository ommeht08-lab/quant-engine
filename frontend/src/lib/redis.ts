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
