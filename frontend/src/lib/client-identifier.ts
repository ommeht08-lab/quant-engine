import { createHmac } from "node:crypto";

// A distinct, stable label for `deriveSubkey` (src/lib/auth.ts) — this
// use of SESSION_SECRET (deriving a key to HMAC login-throttle client
// identifiers) must stay cryptographically independent of
// SESSION_SECRET's other use (signing session cookies). Never reuse
// this label for anything else, and never change it without accepting
// that every previously-hashed identifier becomes a different digest
// (harmless: it just resets everyone's rate-limit window).
export const LOGIN_RATE_LIMIT_IDENTIFIER_LABEL = "login-rate-limit-client-identifier-v1";

/**
 * Pure, directly testable core: normalize `rawIdentifier` (trim +
 * lowercase, so e.g. a stray trailing space or inconsistent IPv6
 * casing doesn't silently split one client's attempts across two
 * different rate-limit keys) and HMAC-SHA256 it with `subkey`.
 *
 * The raw identifier (e.g. a client IP address) is NEVER itself stored
 * in Redis, logged, or returned by this function — only this digest is.
 * HMAC (not a plain hash) specifically because a plain SHA-256 of an
 * IPv4 address is trivially reversible via a rainbow table (the whole
 * IPv4 space is only ~4 billion values); a secret-keyed HMAC is not.
 */
export function hashIdentifierWithSubkey(rawIdentifier: string, subkey: string): string {
  const normalized = rawIdentifier.trim().toLowerCase();
  return createHmac("sha256", subkey).update(normalized).digest("hex");
}
