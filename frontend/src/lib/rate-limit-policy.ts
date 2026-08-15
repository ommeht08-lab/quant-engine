// Intentionally named emergency override: when set to exactly "true" in
// a PRODUCTION deployment, an unavailable rate limiter fails OPEN
// (login proceeds unthrottled) instead of the production default of
// failing CLOSED.
export const FAIL_OPEN_OVERRIDE_ENV_VAR = "LOGIN_RATE_LIMIT_FAIL_OPEN";

export interface FailOpenPolicyInput {
  isProduction: boolean;
  /** The raw, unvalidated `LOGIN_RATE_LIMIT_FAIL_OPEN` env value (or undefined if unset). */
  overrideValue: string | undefined;
}

/**
 * Pure policy decision: whether an UNAVAILABLE rate limiter (Redis
 * unconfigured, or a provider error on the increment call — see
 * `RateLimitOutcome` in `src/lib/redis.ts`) should fail OPEN (let the
 * login attempt through unthrottled) or fail CLOSED (reject it outright
 * with a generic error).
 *
 * - Outside production (local development, CI, a preview deployment
 *   running with `NODE_ENV` unset/non-"production"): ALWAYS fails
 *   OPEN — a contributor without Upstash configured locally must still
 *   be able to log in. `overrideValue` is not consulted here.
 * - In production: fails CLOSED by default. If rate limiting can't
 *   run, brute-force protection is silently disabled — worse than a
 *   temporary login outage for the (single) legitimate operator.
 *   `LOGIN_RATE_LIMIT_FAIL_OPEN=true` is an explicit, intentionally
 *   named escape hatch for an operator who has decided availability
 *   matters more than throttling for some window; it must be set to
 *   exactly the string `"true"` (any other value, including unset,
 *   empty, "1", or "TRUE", stays fail-closed) and defaults to fail
 *   closed.
 */
export function shouldFailOpenWhenRateLimiterUnavailable({
  isProduction,
  overrideValue,
}: FailOpenPolicyInput): boolean {
  if (!isProduction) return true;
  return overrideValue === "true";
}
