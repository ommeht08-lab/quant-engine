/**
 * Validates a caller-supplied "return to this page after login" value
 * before it is ever passed to `redirect()` (`login/actions.ts`). This
 * is the only thing standing between an attacker-crafted
 * `/login?next=...` link and an open redirect — `next` arrives here as
 * a raw hidden form field taken from the URL's own query string, so it
 * must be treated as fully untrusted input regardless of the fact that
 * `proxy.ts` itself only ever sets it to a same-origin pathname (anyone
 * can navigate straight to `/login` with any `next` they like,
 * bypassing `proxy.ts` entirely).
 *
 * Returns the validated destination to redirect to, or `null` if
 * `value` is unsafe — deliberately NOT a boolean type-guard, so the
 * caller can never end up redirecting to a raw value that merely
 * "looked like" it passed a check performed on some other
 * representation of the same string. What is checked is exactly what
 * is used.
 *
 * The accepted contract is deliberately narrow: a bare, same-origin,
 * single-leading-slash PATH ONLY — no query string, no fragment. See
 * the query/fragment check below for why.
 *
 * Bound at MAX_REDIRECT_PATH_LENGTH: `next` reaches `login()`
 * (`login/actions.ts`) as raw POST form data, which an attacker can
 * send directly (bypassing the browser form and any client-side
 * length attribute) at whatever size they choose.
 *
 * Control characters are rejected by numeric code-point comparison
 * (`hasControlOrWhitespaceCharacter` below) rather than a regex
 * Unicode-escape character class — deliberately, so this file's own
 * source never embeds a raw control byte or a `\u`-style escape that
 * could be silently mis-transcribed by tooling into one (a real
 * failure mode caught during review: an earlier version of this file
 * had actual NUL/Unit-Separator bytes embedded directly in a regex
 * literal, which made git treat the entire file as binary — `git diff`
 * produced no output for it at all, so nothing about it was ever
 * actually code-reviewed via a normal diff).
 */

const MAX_REDIRECT_PATH_LENGTH = 512;

// A fixed, non-real marker origin used only to give `new URL(value,
// base)` something to resolve a relative value against. Never sent
// anywhere. Its purpose is to let the SAME URL parser that will
// ultimately interpret the Location header this value ends up in tell
// us, authoritatively, what origin `value` actually resolves to —
// including "//host" (network-path reference) and a leading-backslash
// variant (backslash normalized to "/" for special schemes) cases,
// which a hand-written regex could plausibly miss a variant of. This
// is the "prove it via URL canonicalization, don't assume a regex
// caught every case" check.
const MARKER_ORIGIN = "https://internal.invalid";

// Highest code point still considered a C0 control character (space,
// code point 32, is the first NON-control character and is rejected
// separately as whitespace below).
const MAX_C0_CONTROL_CODE_POINT = 31;
// The DEL control character, code point 127 — not part of the C0
// block above but equally never legitimate in a path.
const DEL_CODE_POINT = 127;

/**
 * True if `value` contains any C0 control character (code points 0
 * through 31, inclusive — covers NUL, tab, newline, carriage return,
 * and every other non-printable byte in that range), DEL (code point
 * 127), or any whitespace character (`String.prototype.trim` already
 * strips exactly the set `\s` matches — reusing that here keeps this
 * function itself the single definition, never re-derived from a
 * second, possibly-inconsistent regex).
 */
function hasControlOrWhitespaceCharacter(value: string): boolean {
  for (const character of value) {
    const codePoint = character.codePointAt(0) ?? 0;
    if (codePoint <= MAX_C0_CONTROL_CODE_POINT || codePoint === DEL_CODE_POINT) return true;
  }
  return value.trim().length !== value.length || /\s/.test(value);
}

/**
 * Encoded/double-encoded slash or backslash anywhere in the raw value.
 * These do NOT change `new URL(...).origin` (percent-encoding in a
 * path is preserved literally, not decoded into a route separator, by
 * both the WHATWG URL parser and Next.js's own router) — so they are
 * not provably dangerous through this codebase's own parser. They are
 * still rejected outright as defense-in-depth: this value's eventual
 * Location header may pass through layers (a CDN, an intermediate
 * proxy, a different browser's own normalization) that do not decode
 * paths identically to Node's URL implementation, and nothing in this
 * app legitimately needs a percent-encoded separator in a redirect
 * target. Matches "%25" generally too, since re-encoding a percent
 * sign is exactly how double-encoding is built and no real destination
 * in this app ever needs a literal "%" either.
 */
const SUSPICIOUS_ENCODED_SEPARATOR = /%2f|%5c|%25/i;

export function safeInternalRedirectPath(value: unknown): string | null {
  if (typeof value !== "string") return null;
  if (value.length === 0 || value.length > MAX_REDIRECT_PATH_LENGTH) return null;

  if (hasControlOrWhitespaceCharacter(value)) return null;
  if (SUSPICIOUS_ENCODED_SEPARATOR.test(value)) return null;
  if (!value.startsWith("/")) return null;

  let resolved: URL;
  try {
    resolved = new URL(value, MARKER_ORIGIN);
  } catch {
    return null;
  }

  // The authoritative same-origin check described above.
  if (resolved.origin !== MARKER_ORIGIN) return null;

  // No query string or fragment. `proxy.ts` only ever sets `next` to a
  // bare pathname — the originally-requested page's own query string
  // is intentionally discarded there (see its comment) because no
  // current route in this app needs one preserved through a login
  // round-trip. Keeping the accepted contract to "pathname only" also
  // avoids having to reason about how an attacker-chosen query or
  // fragment could interact with whatever page it lands on. Fragments
  // specifically are never sent to the server at all, so there is
  // nothing to "preserve" for them regardless.
  if (resolved.search !== "" || resolved.hash !== "") return null;

  // Decode the resolved pathname before the /login comparison below —
  // a raw string comparison alone would miss a percent-encoded variant
  // (e.g. an 'l' encoded as %6C), which Next.js's own router WOULD
  // still decode and route to the login page. A decode failure
  // (malformed/truncated percent escapes) is itself treated as unsafe.
  let decodedPathname: string;
  try {
    decodedPathname = decodeURIComponent(resolved.pathname);
  } catch {
    return null;
  }

  // Reject the login page itself and its trailing-slash variant,
  // case-insensitively. Not because either would technically loop with
  // THIS app's routing (Next.js's default trailingSlash:false makes a
  // trailing-slash variant a distinct, non-matching path, and routes
  // are case-sensitive) — but redirecting a just-authenticated visitor
  // straight back to the login form is a confusing dead end either
  // way, and nothing in this app legitimately needs "next" to be it.
  const normalizedPathname = decodedPathname.toLowerCase().replace(/\/+$/, "") || "/";
  if (normalizedPathname === "/login") return null;

  return resolved.pathname;
}
