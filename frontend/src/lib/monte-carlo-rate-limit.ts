import { hashIdentifierWithSubkey } from "./client-identifier.ts";
import {
  shouldFailOpenPublicEvaluationRateLimiter,
  type PublicEvaluationRateLimitDecision,
} from "./public-evaluation-rate-limit.ts";
import { incrementRateLimitCounter, type RateLimitOutcome } from "./redis.ts";

// Monte Carlo requests have their own quotas, independent of the public
// valuation quota. Running a simulation is the expensive call (a cache hit is
// cheap, but the proxy cannot know which it will be), so it gets the tighter
// limit; reading the allowlist and exporting a snapshot are light.
export const MONTE_CARLO_RUN_MAX_REQUESTS = 10;
export const MONTE_CARLO_LIGHT_MAX_REQUESTS = 60;
export const MONTE_CARLO_WINDOW_SECONDS = 15 * 60;
export const MONTE_CARLO_RATE_LIMIT_FAIL_OPEN_ENV_VAR = "MONTE_CARLO_RATE_LIMIT_FAIL_OPEN";

// A distinct, stable `deriveSubkey` label so this limiter's identifier digests
// are independent of the login and public-evaluation limiters'.
export const MONTE_CARLO_RATE_LIMIT_IDENTIFIER_LABEL = "monte-carlo-rate-limit-client-identifier-v1";

export type MonteCarloRateLimitKind = "run" | "light";

export function monteCarloRateLimitMax(kind: MonteCarloRateLimitKind): number {
  return kind === "run" ? MONTE_CARLO_RUN_MAX_REQUESTS : MONTE_CARLO_LIGHT_MAX_REQUESTS;
}

export function monteCarloRateLimitKey(
  kind: MonteCarloRateLimitKind,
  rawClientIdentifier: string,
  subkey: string,
): string {
  return `monte-carlo-${kind}:${hashIdentifierWithSubkey(rawClientIdentifier, subkey)}`;
}

type IncrementCounter = (key: string, windowSeconds: number) => Promise<RateLimitOutcome>;

/**
 * Fixed-window quota, same failure policy as the public valuation limiter:
 * production fails closed when shared Redis state is unavailable unless the
 * deliberately named emergency override is exactly `true`.
 */
export async function decideMonteCarloRateLimit(
  {
    kind,
    key,
    isProduction,
    failOpenOverride,
  }: {
    kind: MonteCarloRateLimitKind;
    key: string;
    isProduction: boolean;
    failOpenOverride: string | undefined;
  },
  incrementCounter: IncrementCounter = incrementRateLimitCounter,
): Promise<PublicEvaluationRateLimitDecision> {
  const outcome = await incrementCounter(key, MONTE_CARLO_WINDOW_SECONDS);
  if (outcome.ok) {
    return outcome.count > monteCarloRateLimitMax(kind) ? "limited" : "allow";
  }
  return shouldFailOpenPublicEvaluationRateLimiter(isProduction, failOpenOverride)
    ? "allow"
    : "unavailable";
}
