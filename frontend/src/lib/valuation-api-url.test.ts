// Table-driven security regression tests for `assertSafeValuationApiUrl`
// (frontend/src/lib/valuation-api-url.ts), which gates every request
// carrying VALUATION_API_TOKEN. No real HTTP request is made anywhere
// in this file. Run with Node's built-in test runner:
//
//   node --test src/lib/valuation-api-url.test.ts

import assert from "node:assert/strict";
import { test } from "node:test";

import { assertSafeValuationApiUrl } from "./valuation-api-url.ts";

interface Case {
  name: string;
  url: string;
  isProduction: boolean;
}

const SAFE_CASES: Case[] = [
  { name: "loopback http, non-production, localhost", url: "http://localhost:8000", isProduction: false },
  { name: "loopback http, non-production, 127.0.0.1", url: "http://127.0.0.1:9000", isProduction: false },
  { name: "loopback http, non-production, IPv6 ::1", url: "http://[::1]:8000", isProduction: false },
  { name: "https backend, non-production", url: "https://backend.example.com", isProduction: false },
  { name: "https backend, production", url: "https://valuation.internal.example.com", isProduction: true },
  { name: "https backend with explicit default port, production", url: "https://backend.example.com:443", isProduction: true },
];

const UNSAFE_CASES: Case[] = [
  { name: "non-loopback http, non-production", url: "http://evil.example.com", isProduction: false },
  { name: "http in production (even loopback)", url: "http://localhost:8000", isProduction: true },
  { name: "http in production (non-loopback)", url: "http://backend.example.com", isProduction: true },
  { name: "loopback in production", url: "https://localhost:8000", isProduction: true },
  { name: "embedded userinfo", url: "https://user:pass@backend.example.com", isProduction: false },
  { name: "embedded userinfo, production", url: "https://user:pass@backend.example.com", isProduction: true },
  { name: "unexpected path", url: "https://backend.example.com/api/evaluate", isProduction: false },
  { name: "unexpected query string", url: "https://backend.example.com?x=1", isProduction: false },
  { name: "fragment", url: "https://backend.example.com#section", isProduction: false },
  { name: "malformed URL", url: "not a url", isProduction: false },
  { name: "empty string", url: "", isProduction: false },
  { name: "protocol-relative URL", url: "//evil.example.com", isProduction: false },
  { name: "ftp scheme", url: "ftp://backend.example.com", isProduction: false },
  { name: "javascript scheme", url: "javascript:alert(1)", isProduction: false },
  { name: "file scheme", url: "file:///etc/passwd", isProduction: false },

  // -- loopback-canonicalization bypass regressions (adversarial review) --
  // The ORIGINAL reported bug: a trailing DNS root-label dot made
  // "localhost." look like a distinct, non-loopback hostname.
  { name: "trailing-dot localhost, production", url: "https://localhost.", isProduction: true },
  { name: "double-trailing-dot localhost, production", url: "https://localhost..", isProduction: true },
  { name: "mixed-case localhost, production", url: "https://LOCALHOST", isProduction: true },
  { name: "mixed-case + trailing-dot localhost, production", url: "https://LoCaLhOsT.", isProduction: true },
  // Node's URL parser already canonicalizes these numeric IPv4 forms to
  // "127.0.0.1" before this module ever reads `.hostname` — asserted
  // here end-to-end, not just as an implementation-detail claim.
  { name: "short-form IPv4 loopback (127.1), production", url: "https://127.1", isProduction: true },
  { name: "decimal IPv4 loopback, production", url: "https://2130706433", isProduction: true },
  { name: "hex IPv4 loopback, production", url: "https://0x7f.0.0.1", isProduction: true },
  { name: "octal IPv4 loopback, production", url: "https://0177.0.0.1", isProduction: true },
  // IPv4-mapped IPv6 loopback — several equivalent textual encodings,
  // all canonicalized by Node's URL parser to the same hostname string.
  { name: "IPv4-mapped IPv6 loopback, production", url: "https://[::ffff:127.0.0.1]", isProduction: true },
  { name: "IPv4-mapped IPv6 loopback (expanded), production", url: "https://[0:0:0:0:0:ffff:127.0.0.1]", isProduction: true },
  { name: "IPv4-mapped IPv6 loopback (uppercase hex), production", url: "https://[::FFFF:127.0.0.1]", isProduction: true },
  // Unspecified addresses.
  { name: "IPv4 unspecified address, production", url: "https://0.0.0.0", isProduction: true },
  { name: "IPv6 unspecified address, production", url: "https://[::]", isProduction: true },
  { name: "IPv6 loopback, production", url: "https://[::1]", isProduction: true },
  // The unspecified/alias forms are NOT part of the narrow dev-loopback
  // allowance outside production either — only localhost/127.0.0.1/::1 are.
  { name: "IPv4 unspecified address, non-production http", url: "http://0.0.0.0", isProduction: false },
  { name: "IPv6 unspecified address, non-production http", url: "http://[::]", isProduction: false },
  { name: "IPv4-mapped IPv6 loopback, non-production http", url: "http://[::ffff:127.0.0.1]", isProduction: false },

  // -- full 127.0.0.0/8 range coverage regressions (adversarial review) --
  // The ORIGINAL bug: only "127.0.0.1" was checked, missing the rest of
  // the entire loopback /8 range, which is ALL loopback (RFC 5735).
  { name: "127.0.0.0 (range start), production", url: "https://127.0.0.0", isProduction: true },
  { name: "127.0.0.1 (already covered), production", url: "https://127.0.0.1", isProduction: true },
  { name: "127.0.0.2, production", url: "https://127.0.0.2", isProduction: true },
  { name: "127.1.2.3 (mid-range), production", url: "https://127.1.2.3", isProduction: true },
  { name: "127.255.255.255 (range end), production", url: "https://127.255.255.255", isProduction: true },
  // Equivalent decimal/hex/octal forms Node canonicalizes to a
  // dotted-decimal address inside 127.0.0.0/8.
  { name: "decimal form of 127.0.0.2, production", url: "https://2130706434", isProduction: true },
  { name: "hex form of 127.0.0.2, production", url: "https://0x7f000002", isProduction: true },
  { name: "octal form of 127.0.0.2, production", url: "https://0177.0.0.2", isProduction: true },
  { name: "hex form of 127.255.255.255, production", url: "https://0x7fffffff", isProduction: true },
  // IPv4-mapped IPv6 versions from different points in the range.
  { name: "IPv4-mapped IPv6 of 127.0.0.0, production", url: "https://[::ffff:127.0.0.0]", isProduction: true },
  { name: "IPv4-mapped IPv6 of 127.0.0.2, production", url: "https://[::ffff:127.0.0.2]", isProduction: true },
  { name: "IPv4-mapped IPv6 of 127.1.2.3, production", url: "https://[::ffff:127.1.2.3]", isProduction: true },
  {
    name: "IPv4-mapped IPv6 of 127.255.255.255, production",
    url: "https://[::ffff:127.255.255.255]",
    isProduction: true,
  },
  // Outside production, ONLY the exact 127.0.0.1 dev origin is allowed
  // as http: — the rest of the /8 range must still be rejected there too.
  { name: "127.0.0.2, non-production http", url: "http://127.0.0.2", isProduction: false },
  { name: "127.255.255.255, non-production http", url: "http://127.255.255.255", isProduction: false },
];

// Neighboring, genuinely non-loopback addresses just outside
// 127.0.0.0/8 — proves the range check is bounded correctly and
// doesn't overmatch beyond the actual /8 boundary.
const NEIGHBORING_NON_LOOPBACK_SAFE_CASES: Case[] = [
  { name: "126.255.255.255 (just below range), production", url: "https://126.255.255.255", isProduction: true },
  { name: "128.0.0.0 (just above range), production", url: "https://128.0.0.0", isProduction: true },
  {
    name: "IPv4-mapped IPv6 of 126.255.255.255 (just below range), production",
    url: "https://[::ffff:126.255.255.255]",
    isProduction: true,
  },
  {
    name: "IPv4-mapped IPv6 of 128.0.0.0 (just above range), production",
    url: "https://[::ffff:128.0.0.0]",
    isProduction: true,
  },
];

for (const { name, url, isProduction } of SAFE_CASES) {
  test(`SAFE: ${name} (isProduction=${isProduction})`, () => {
    assert.doesNotThrow(() => assertSafeValuationApiUrl(url, { isProduction }));
  });
}

for (const { name, url, isProduction } of UNSAFE_CASES) {
  test(`UNSAFE: ${name} (isProduction=${isProduction})`, () => {
    assert.throws(() => assertSafeValuationApiUrl(url, { isProduction }));
  });
}

for (const { name, url, isProduction } of NEIGHBORING_NON_LOOPBACK_SAFE_CASES) {
  test(`SAFE (neighboring, not in range): ${name} (isProduction=${isProduction})`, () => {
    assert.doesNotThrow(() => assertSafeValuationApiUrl(url, { isProduction }));
  });
}

test("returns the bare origin, stripping any trailing slash", () => {
  const result = assertSafeValuationApiUrl("https://backend.example.com", { isProduction: true });
  assert.equal(result, "https://backend.example.com");
});

test("a non-loopback http origin is rejected even outside production (no cleartext token transmission)", () => {
  assert.throws(() => assertSafeValuationApiUrl("http://backend.example.com", { isProduction: false }));
});

test("a hostname that merely starts with 'localhost' but is a distinct real domain is NOT treated as loopback", () => {
  // Proves the trailing-dot fix doesn't over-match: "localhost.evil.com"
  // is a genuinely different, attacker-controlled hostname, not an
  // alias for the loopback address, and must remain a valid production origin.
  assert.doesNotThrow(() => assertSafeValuationApiUrl("https://localhost.evil.com", { isProduction: true }));
});

test("trailing dot on a legitimate non-loopback production hostname is still accepted", () => {
  assert.doesNotThrow(() =>
    assertSafeValuationApiUrl("https://valuation.example.com.", { isProduction: true })
  );
});

test("the narrow dev-loopback allowance still works outside production after the fix", () => {
  for (const url of ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"]) {
    assert.doesNotThrow(() => assertSafeValuationApiUrl(url, { isProduction: false }));
  }
});
