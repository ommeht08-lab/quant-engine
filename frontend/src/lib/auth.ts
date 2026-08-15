import { createHmac, timingSafeEqual } from "crypto";
import { cookies } from "next/headers";

import {
  assertSecretMeetsRequirements,
  DASHBOARD_PASSWORD_REQUIREMENT,
  SESSION_SECRET_REQUIREMENT,
} from "@/lib/secret-validation";

/**
 * Shared-passphrase session gate for this dashboard.
 *
 * This app has no user accounts — it's a single-operator paper-trading
 * dashboard, not a multi-tenant product — so a full auth library (user
 * table, password hashing, sign-up flow) would be more machinery than
 * the actual threat model calls for. What's needed is simply: don't let
 * an unauthenticated visitor view portfolio positions/trade history or
 * hit the API routes that call Alpaca/Postgres directly.
 *
 * The session token is a signed expiry timestamp (`<expiresAtMs>.<hmac>`),
 * verified with Node's built-in `crypto` (HMAC-SHA256 + a
 * constant-time comparison) — no new dependency needed for this. A
 * client can read the timestamp but cannot forge or extend it without
 * `SESSION_SECRET`.
 */

export const SESSION_COOKIE_NAME = "session";
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

function getSessionSecret(): string {
  const secret = process.env.SESSION_SECRET;
  assertSecretMeetsRequirements(secret, SESSION_SECRET_REQUIREMENT);
  return secret;
}

/**
 * Derive a domain-separated subkey from `SESSION_SECRET` for a use
 * OTHER than signing the session cookie — e.g. the login rate
 * limiter's client-identifier HMAC
 * (`src/lib/client-identifier.ts`/`src/app/login/actions.ts`).
 *
 * `SESSION_SECRET` itself is never reused directly as a second HMAC
 * key for an unrelated purpose: HMACing it with a fixed, purpose-
 * specific `label` keeps the two uses cryptographically independent, so
 * a weakness or environment specific to one use case can't retroactively
 * compromise the other. Every caller MUST use a distinct, stable label.
 */
export function deriveSubkey(label: string): string {
  return createHmac("sha256", getSessionSecret()).update(label).digest("hex");
}

function sign(payload: string): string {
  return createHmac("sha256", getSessionSecret()).update(payload).digest("base64url");
}

export function createSessionToken(): string {
  const expiresAtMs = Date.now() + SESSION_TTL_MS;
  const payload = String(expiresAtMs);
  return `${payload}.${sign(payload)}`;
}

/**
 * Verify a session token's signature and expiry. Never throws on a
 * malformed token — returns `false` — but DOES throw if `SESSION_SECRET`
 * itself is misconfigured (a deployment bug, not a bad client token;
 * callers should let that surface rather than silently treating every
 * request as unauthenticated).
 */
export function verifySessionToken(token: string | undefined | null): boolean {
  if (!token) return false;

  const dotIndex = token.indexOf(".");
  if (dotIndex <= 0) return false;

  const payload = token.slice(0, dotIndex);
  const signature = token.slice(dotIndex + 1);

  const expiresAtMs = Number(payload);
  if (!Number.isFinite(expiresAtMs) || Date.now() >= expiresAtMs) return false;

  const expectedSignature = sign(payload);
  const actual = Buffer.from(signature);
  const expected = Buffer.from(expectedSignature);
  if (actual.length !== expected.length) return false;

  return timingSafeEqual(actual, expected);
}

/**
 * Constant-time check of a submitted password against
 * `DASHBOARD_PASSWORD`. Throws if the env var isn't configured — same
 * "fail loudly on misconfiguration" convention as `getSessionSecret`.
 */
export function verifyPassword(candidate: string): boolean {
  const configured = process.env.DASHBOARD_PASSWORD;
  assertSecretMeetsRequirements(configured, DASHBOARD_PASSWORD_REQUIREMENT);

  const actual = Buffer.from(candidate);
  const expected = Buffer.from(configured);
  if (actual.length !== expected.length) return false;

  return timingSafeEqual(actual, expected);
}

/**
 * Defense-in-depth session check for use directly inside a Route
 * Handler, independent of `proxy.ts`. Per the Next.js docs: "A matcher
 * change or a refactor ... can silently remove Proxy coverage. Always
 * verify authentication ... inside each Server Function rather than
 * relying on Proxy alone" — this is that check for the routes that
 * directly touch Alpaca/Postgres.
 *
 * Returns `true` if the request carries a valid session. A misconfigured
 * `SESSION_SECRET` is treated as "not authenticated" here (rather than
 * throwing) since callers use this in a simple `if (!(await
 * requireSession())) return 401` — `proxy.ts` is what surfaces the
 * clearer 500 for that specific misconfiguration on the first request.
 */
export async function requireSession(): Promise<boolean> {
  const cookieStore = await cookies();
  try {
    return verifySessionToken(cookieStore.get(SESSION_COOKIE_NAME)?.value);
  } catch {
    return false;
  }
}
