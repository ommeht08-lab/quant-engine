import { hashIdentifierWithSubkey } from "./client-identifier.ts";
import { incrementRateLimitCounter, type RateLimitOutcome } from "./redis.ts";

export const PUBLIC_EVALUATION_MAX_REQUESTS = 30;
export const PUBLIC_EVALUATION_WINDOW_SECONDS = 15 * 60;
export const PUBLIC_EVALUATION_RATE_LIMIT_FAIL_OPEN_ENV_VAR =
  "PUBLIC_EVALUATION_RATE_LIMIT_FAIL_OPEN";

export type PublicEvaluationRateLimitDecision = "allow" | "limited" | "unavailable";

type IncrementCounter = (
  key: string,
  windowSeconds: number,
) => Promise<RateLimitOutcome>;

export function publicEvaluationRateLimitKey(
  rawClientIdentifier: string,
  subkey: string,
): string {
  return `public-evaluation:${hashIdentifierWithSubkey(rawClientIdentifier, subkey)}`;
}

export function shouldFailOpenPublicEvaluationRateLimiter(
  isProduction: boolean,
  overrideValue: string | undefined,
): boolean {
  if (!isProduction) return true;
  return overrideValue === "true";
}

/**
 * Apply a fixed-window public-evaluation quota. Production fails closed
 * if shared Redis state is unavailable unless the deliberately named
 * emergency override is set to exactly `true`; local development and CI
 * remain usable without Upstash.
 */
export async function decidePublicEvaluationRateLimit(
  {
    key,
    isProduction,
    failOpenOverride,
  }: {
    key: string;
    isProduction: boolean;
    failOpenOverride: string | undefined;
  },
  incrementCounter: IncrementCounter = incrementRateLimitCounter,
): Promise<PublicEvaluationRateLimitDecision> {
  const outcome = await incrementCounter(key, PUBLIC_EVALUATION_WINDOW_SECONDS);
  if (outcome.ok) {
    return outcome.count > PUBLIC_EVALUATION_MAX_REQUESTS ? "limited" : "allow";
  }

  return shouldFailOpenPublicEvaluationRateLimiter(isProduction, failOpenOverride)
    ? "allow"
    : "unavailable";
}
