"use server";

import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";

import { createSessionToken, deriveSubkey, verifyPassword, SESSION_COOKIE_NAME } from "@/lib/auth";
import { hashIdentifierWithSubkey, LOGIN_RATE_LIMIT_IDENTIFIER_LABEL } from "@/lib/client-identifier";
import { incrementRateLimitCounter, resetRateLimitCounter } from "@/lib/redis";
import { shouldFailOpenWhenRateLimiterUnavailable, FAIL_OPEN_OVERRIDE_ENV_VAR } from "@/lib/rate-limit-policy";
import { DEFAULT_APP_PATH } from "@/lib/default-route";
import { safeInternalRedirectPath } from "@/lib/safe-redirect";

export interface LoginState {
  error?: string;
}

// Deliberately small: this is a single shared-passphrase dashboard, not
// a multi-user product, so a real operator will rarely need more than a
// couple of tries, while a brute-force attempt needs many more than
// this to have a realistic chance against a reasonable passphrase.
const LOGIN_MAX_ATTEMPTS_PER_WINDOW = 5;
const LOGIN_WINDOW_SECONDS = 15 * 60;

function isProductionEnvironment(): boolean {
  return process.env.NODE_ENV === "production";
}

/**
 * Best-effort per-client identifier for the login throttle. Reads
 * `x-forwarded-for`, trusted ONLY because this app is deployed on
 * Vercel (see the root `vercel.json`) — Vercel's edge network sets this
 * header from its own observed connecting IP, overwriting rather than
 * passing through any client-supplied value. Deploying this app on a
 * different host would need this assumption re-verified (an
 * unvalidated proxy could let a client spoof its own throttle key and
 * bypass the limit entirely) — see docs/security-threat-model.md. This
 * raw value is used ONLY to compute an HMAC digest (`rateLimitKeyFor`
 * below) — it is never itself logged or stored.
 */
async function getRawClientIdentifier(): Promise<string> {
  const headerStore = await headers();
  const forwardedFor = headerStore.get("x-forwarded-for");
  const ip = forwardedFor?.split(",")[0]?.trim();
  return ip && ip.length > 0 ? ip : "unknown";
}

/**
 * Build the Redis key for a client's login-throttle counter. The raw
 * identifier (e.g. an IP address) is normalized and HMAC-SHA256'd
 * (`hashIdentifierWithSubkey`, keyed by a `SESSION_SECRET`-derived
 * subkey — see `deriveSubkey`) before it ever becomes part of a key —
 * only the digest reaches Redis, never the raw value. Two calls with
 * the same raw identifier always produce the same key (so the counter
 * actually accumulates); this is intentionally NOT a per-request-random
 * value.
 */
function rateLimitKeyFor(rawIdentifier: string): string {
  const subkey = deriveSubkey(LOGIN_RATE_LIMIT_IDENTIFIER_LABEL);
  return `login-throttle:${hashIdentifierWithSubkey(rawIdentifier, subkey)}`;
}

export async function login(_prevState: LoginState, formData: FormData): Promise<LoginState> {
  const password = formData.get("password");
  if (typeof password !== "string" || password.length === 0) {
    return { error: "Enter the dashboard password." };
  }

  const rawClientId = await getRawClientIdentifier();
  const rateLimitKey = rateLimitKeyFor(rawClientId);

  // Counts every submission (not just failures) against the client's
  // window, checked BEFORE verifying the password — simpler and safer
  // than only counting failures, and closes the same door against a
  // burst of successful-looking requests. A SUCCESSFUL login clears
  // this same counter afterward (see below), so a legitimate operator
  // logging in repeatedly never accumulates toward the limit even
  // though every attempt (successful or not) increments it first.
  const outcome = await incrementRateLimitCounter(rateLimitKey, LOGIN_WINDOW_SECONDS);

  if (outcome.ok) {
    if (outcome.count > LOGIN_MAX_ATTEMPTS_PER_WINDOW) {
      return { error: "Too many login attempts. Please try again later." };
    }
  } else if (
    !shouldFailOpenWhenRateLimiterUnavailable({
      isProduction: isProductionEnvironment(),
      overrideValue: process.env[FAIL_OPEN_OVERRIDE_ENV_VAR],
    })
  ) {
    console.error(
      `Login rate limiter unavailable (${outcome.reason}); failing closed per production policy. ` +
        `Set ${FAIL_OPEN_OVERRIDE_ENV_VAR}=true to override.`
    );
    return { error: "Authentication is temporarily unavailable. Please try again shortly." };
  }
  // else: rate limiter unavailable AND policy says fail open -> proceed unthrottled.

  let valid: boolean;
  try {
    valid = verifyPassword(password);
  } catch (error) {
    console.error(
      "Login failed — DASHBOARD_PASSWORD misconfigured:",
      error instanceof Error ? error.message : "unknown error"
    );
    return { error: "This deployment is not configured for login. Contact the operator." };
  }

  if (!valid) {
    return { error: "Incorrect password." };
  }

  // Successful login: clear THIS client's own counter. `rateLimitKey`
  // is derived solely from this request's own HMAC'd identifier, so
  // this can only ever clear this same client's counter — never another
  // client's (see `resetRateLimitCounter`'s docstring). Best-effort: if
  // this fails, the window still expires on its own TTL.
  await resetRateLimitCounter(rateLimitKey);

  const cookieStore = await cookies();
  cookieStore.set(SESSION_COOKIE_NAME, createSessionToken(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
  });

  // `next` is a raw hidden-form field (see `login/page.tsx`, populated
  // from the URL `proxy.ts` builds when it redirects an unauthenticated
  // visitor here) — untrusted input, validated fresh regardless of how
  // it usually gets set. Never logged (a redirect target is not a
  // credential, but there is no reason to record it either). Falls
  // back to the shared default landing page when absent, invalid, or
  // crafted to point off-site. `safeInternalRedirectPath` returns the
  // exact value that was validated (never the raw, merely-checked
  // input) — this is what actually gets redirected to.
  const destination = safeInternalRedirectPath(formData.get("next")) ?? DEFAULT_APP_PATH;
  redirect(destination);
}
