import { isMonteCarloDescription, type MonteCarloDescription } from "./monte-carlo-types.ts";

export const DESCRIPTION_CACHE_KEY = "mc-dcf:description:v1";

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/**
 * The last simulation settings this browser saw (allowed companies, default seed, configuration summary).
 * It only lets the controls appear immediately on a repeat visit; the page always revalidates it with the
 * server, and an entry that is missing, unreadable or fails validation is ignored. Nothing in it is a result.
 */
export function readCachedDescription(storage: StorageLike | null | undefined): MonteCarloDescription | null {
  try {
    const raw = storage?.getItem(DESCRIPTION_CACHE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isMonteCarloDescription(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function writeCachedDescription(storage: StorageLike | null | undefined, description: MonteCarloDescription): void {
  try {
    storage?.setItem(DESCRIPTION_CACHE_KEY, JSON.stringify(description));
  } catch {
    // Private mode or a full quota: the cache is an optimisation only.
  }
}

/** Keep the user's selection when it is still allowed, otherwise fall back to the first allowed company. */
export function reconcileTicker(current: string, allowed: readonly string[]): string {
  return allowed.includes(current) ? current : allowed[0] ?? "";
}
