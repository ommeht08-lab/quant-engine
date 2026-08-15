// `URL#hostname` keeps the brackets for a bracketed IPv6 literal
// (`new URL("http://[::1]:8000").hostname === "[::1]"`, not `"::1"`) —
// matched here in the same bracketed form, not the bare address.
//
// The ONLY hostnames the "outside production, http: is allowed" dev
// exception applies to — deliberately narrow (see
// `assertSafeValuationApiUrl`'s docstring), and deliberately NOT the
// full 127.0.0.0/8 range checked by `isIPv4LoopbackRangeHostname` below
// — only the exact address a real `uvicorn --reload` default binds to.
const DEV_LOOPBACK_HOSTNAMES = new Set(["localhost", "127.0.0.1", "[::1]"]);

// Loopback/unspecified hostnames that are NOT expressible as a numeric
// IPv4 range check (see `isIPv4LoopbackRangeHostname` and
// `isIPv4MappedIPv6LoopbackRangeHostname` below, which handle the
// entire 127.0.0.0/8 range and its IPv4-mapped-IPv6 form respectively)
// — matched by exact, already-canonicalized string. "127.0.0.1" is
// deliberately NOT listed here: it's already covered by the range
// check below, since it's simply one address inside 127.0.0.0/8.
//   - "0.0.0.0" / "[::]" — the IPv4/IPv6 "unspecified" address, which a
//     CLIENT connecting to it commonly reaches the local host on many
//     platforms.
//   - "[::1]" — the IPv6 loopback address itself.
// This is NOT an exhaustive enumeration of every possible textual
// encoding of a loopback/private address (see this module's docstring
// for what a shape-only validator can and can't guarantee).
const NAMED_LOOPBACK_OR_UNSPECIFIED_HOSTNAMES = new Set(["localhost", "[::1]", "0.0.0.0", "[::]"]);

/**
 * Strip DNS trailing-root-label dot(s) before comparing a hostname
 * against the loopback/unspecified sets above — "localhost." (and even
 * "localhost..") name the EXACT SAME host as "localhost" (a trailing
 * "." in a domain name marks it as already fully-qualified; it is not
 * part of the name itself), but `URL#hostname` preserves it verbatim.
 * Without stripping it, `https://localhost.` would parse to a hostname
 * string that doesn't literal-match "localhost" and so would be (WRONGLY)
 * treated as some other, non-loopback production hostname — the exact
 * gap an adversarial probe found. Mixed case ("LOCALHOST") needs no
 * equivalent handling here: `URL#hostname` already lowercases it.
 */
function canonicalizeHostnameForLoopbackCheck(hostname: string): string {
  return hostname.replace(/\.+$/, "");
}

/**
 * Parse a canonical dotted-decimal IPv4 hostname — the ONLY shape
 * `URL#hostname` ever produces for an IPv4 address, regardless of how
 * it was originally written (decimal/hex/octal/short forms are all
 * already normalized to this by the URL parser itself before this
 * module ever sees `.hostname`) — into its four octets. Returns `null`
 * for anything that isn't in exactly that shape (including a real
 * hostname like "backend.example.com", which is the common case).
 */
function parseCanonicalIPv4(hostname: string): [number, number, number, number] | null {
  const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(hostname);
  if (!match) return null;
  const octets = [match[1], match[2], match[3], match[4]].map(Number);
  if (octets.some((octet) => octet > 255)) return null;
  return octets as [number, number, number, number];
}

/**
 * True if `hostname` is a canonical dotted-decimal IPv4 address
 * anywhere in 127.0.0.0/8 — the ENTIRE loopback range (RFC 5735), not
 * just 127.0.0.1. Any address in this range routes to the local host,
 * the same as 127.0.0.1 — a production deployment pointing at ANY of
 * them is exactly as broken/dangerous as pointing at 127.0.0.1 itself.
 */
function isIPv4LoopbackRangeHostname(hostname: string): boolean {
  const octets = parseCanonicalIPv4(hostname);
  return octets !== null && octets[0] === 127;
}

/**
 * Parse the embedded IPv4 address out of a canonical IPv4-mapped IPv6
 * hostname. `URL#hostname` always canonicalizes EVERY textual spelling
 * of an IPv4-mapped IPv6 address (expanded zero groups, uppercase hex,
 * a dotted-decimal or short embedded IPv4 form) to this exact
 * lowercase, compressed, hex-embedded-IPv4 shape —
 * `"[::ffff:XXXX:YYYY]"`, e.g. `"[::ffff:7f00:1]"` for 127.0.0.1 —
 * before this module ever reads `.hostname`, so only this one shape
 * needs to be matched here. Returns `null` for anything else.
 */
function parseIPv4MappedIPv6(hostname: string): [number, number, number, number] | null {
  const match = /^\[::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})\]$/.exec(hostname);
  if (!match) return null;
  const high = Number.parseInt(match[1], 16);
  const low = Number.parseInt(match[2], 16);
  return [(high >> 8) & 0xff, high & 0xff, (low >> 8) & 0xff, low & 0xff];
}

/**
 * True if `hostname` is the canonical IPv4-mapped-IPv6 form of ANY
 * address in 127.0.0.0/8 — the IPv6-syntax equivalent of
 * `isIPv4LoopbackRangeHostname` above, covering the same full range,
 * not just the mapped form of 127.0.0.1.
 */
function isIPv4MappedIPv6LoopbackRangeHostname(hostname: string): boolean {
  const octets = parseIPv4MappedIPv6(hostname);
  return octets !== null && octets[0] === 127;
}

function isLoopbackOrUnspecifiedHostname(hostname: string): boolean {
  const canonical = canonicalizeHostnameForLoopbackCheck(hostname);
  return (
    NAMED_LOOPBACK_OR_UNSPECIFIED_HOSTNAMES.has(canonical) ||
    isIPv4LoopbackRangeHostname(canonical) ||
    isIPv4MappedIPv6LoopbackRangeHostname(canonical)
  );
}

export interface ValuationApiUrlValidationOptions {
  isProduction: boolean;
}

/**
 * Validate `VALUATION_API_URL` (the Python FastAPI valuation backend's
 * base origin) before it's ever used to build a request that carries
 * `VALUATION_API_TOKEN`.
 *
 * This validator checks SHAPE — the URL's own text, canonicalized only
 * the way `new URL()` and this module's own helpers do (lowercasing,
 * IPv4/IPv6 numeric-form normalization already done by the URL parser
 * itself, stripping a DNS trailing root-label dot, and recognizing
 * every address in the 127.0.0.0/8 loopback range — not just
 * 127.0.0.1 — in both plain-IPv4 and IPv4-mapped-IPv6 form) — NEVER a
 * DNS lookup. It is NOT a general-purpose SSRF defense and does NOT
 * claim to be: DNS rebinding (a hostname that resolves to a public IP
 * at validation time but a private/loopback one at request time) and
 * hostname-to-private-IP resolution in general are both fundamentally
 * unavailable to a validator that only ever looks at the URL's text,
 * never performs a lookup, and never inspects where the request
 * actually lands. This also covers ONLY the loopback/unspecified
 * address space (127.0.0.0/8, 0.0.0.0, ::, ::1) — it does NOT check
 * other private/internal ranges (e.g. RFC 1918 10.0.0.0/8,
 * 172.16.0.0/12, 192.168.0.0/16, or link-local 169.254.0.0/16), which
 * are a different, not-yet-addressed class of finding. Nor is the
 * loopback/unspecified list an exhaustive enumeration of every possible
 * textual encoding of a private/local address — it closes the specific
 * gaps found by adversarial review, not a claim that no other encoding
 * could exist.
 *
 * Unlike `assertSafeAlpacaBaseUrl` (which can check for one fixed,
 * always-correct Alpaca hostname), the valuation backend can
 * legitimately run anywhere the operator deploys it, so there is no
 * single correct hostname to allowlist against from inside this
 * repository either. What IS enforced:
 *
 *   - Must parse as an absolute URL (`new URL`, no base argument — a
 *     protocol-relative string like "//evil.example.com" has no scheme
 *     to resolve without a base and is rejected as malformed, not
 *     silently resolved against some assumed origin).
 *   - `http:`/`https:` scheme only.
 *   - No embedded username/password (`https://user:pass@host`).
 *   - No fragment.
 *   - No path or query string — the caller always appends its own
 *     request path; a configured value that already has one is
 *     rejected rather than silently concatenated onto.
  *   - In production: `https:` is REQUIRED, and ANY loopback/unspecified
 *     hostname or alias (see `isLoopbackOrUnspecifiedHostname` above —
 *     `localhost`, the ENTIRE 127.0.0.0/8 range (not just `127.0.0.1`)
 *     in plain-IPv4 or IPv4-mapped-IPv6 form, `[::1]`, `0.0.0.0`,
 *     `[::]`, any of those with a trailing DNS root-label dot) is
 *     rejected consistently. A production deployment pointing at
 *     "itself" almost always indicates a missing/broken configuration,
 *     not a real backend.
 *   - Outside production: an exact DEV-loopback HTTP origin
 *     (`http://localhost`, `http://127.0.0.1`, `http://[::1]`, any
 *     port — the deliberately narrow `DEV_LOOPBACK_HOSTNAMES` set, NOT
 *     the broader unspecified/alias set) is the ONE case `http:` is
 *     allowed for — local development against `uvicorn --reload`. A
 *     non-dev-loopback `http:` origin is rejected even outside
 *     production, since that would send the bearer token in cleartext
 *     over a real network.
 *
 * Residual deployment requirement, which this validator cannot resolve
 * on its own (documented rather than papered over — see
 * docs/security-threat-model.md): a production deployment MUST set
 * `VALUATION_API_URL` explicitly to its own real backend origin. There
 * is no safe default to fall back to in production — the call site
 * (frontend/src/app/api/evaluate/[ticker]/route.ts) only applies the
 * `http://localhost:8000` development default outside production.
 */
export function assertSafeValuationApiUrl(
  rawUrl: string,
  { isProduction }: ValuationApiUrlValidationOptions
): string {
  if (!rawUrl) {
    throw new Error("VALUATION_API_URL is not configured.");
  }

  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    throw new Error("VALUATION_API_URL is not a valid absolute URL.");
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("VALUATION_API_URL must use the http: or https: scheme.");
  }
  if (parsed.username !== "" || parsed.password !== "") {
    throw new Error("VALUATION_API_URL must not contain embedded credentials.");
  }
  if (parsed.hash !== "") {
    throw new Error("VALUATION_API_URL must not contain a fragment.");
  }
  if ((parsed.pathname !== "" && parsed.pathname !== "/") || parsed.search !== "") {
    throw new Error(
      "VALUATION_API_URL must be a bare origin — no path or query string. This app appends its own request path."
    );
  }

  if (isProduction) {
    if (parsed.protocol !== "https:") {
      throw new Error("VALUATION_API_URL must use https: in production.");
    }
    if (isLoopbackOrUnspecifiedHostname(parsed.hostname)) {
      throw new Error("VALUATION_API_URL must not be a loopback/unspecified address in production.");
    }
  } else if (parsed.protocol === "http:") {
    const canonicalHostname = canonicalizeHostnameForLoopbackCheck(parsed.hostname);
    if (!DEV_LOOPBACK_HOSTNAMES.has(canonicalHostname)) {
      throw new Error(
        "VALUATION_API_URL may only use http: for an exact loopback address " +
          "(localhost/127.0.0.1/::1) outside production."
      );
    }
  }

  return parsed.origin;
}
