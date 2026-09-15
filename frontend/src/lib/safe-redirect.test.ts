import assert from "node:assert/strict";
import test from "node:test";

import { safeInternalRedirectPath } from "./safe-redirect.ts";

// Control characters are built with String.fromCharCode rather than
// \u-style escapes in this file's own source text — a deliberate
// precaution after review found an earlier version of safe-redirect.ts
// had raw NUL/Unit-Separator BYTES embedded directly in a regex
// literal (not escape sequences), which made git treat that entire
// file as binary; `git diff` produced no output for it at all, so
// nothing about it was ever actually code-reviewed via a normal diff.
// Building these strings at runtime instead keeps this test file
// itself plain, diffable text no matter what characters the cases
// below need to exercise.
const NUL = String.fromCharCode(0);
const TAB = String.fromCharCode(9);
const LF = String.fromCharCode(10);
const CR = String.fromCharCode(13);
const UNIT_SEPARATOR = String.fromCharCode(31);
const DEL = String.fromCharCode(127);
const SPACE = String.fromCharCode(32);

test("accepts real internal destinations and returns the validated path", () => {
  const accepted: [string, string][] = [
    ["/overview/MSFT", "/overview/MSFT"],
    ["/workspace", "/workspace"],
    ["/ticker/MSFT", "/ticker/MSFT"],
    ["/", "/"],
  ];
  for (const [input, expected] of accepted) {
    assert.equal(safeInternalRedirectPath(input), expected, `expected ${input} to be accepted as ${expected}`);
  }
});

test("rejects non-string and out-of-range values", () => {
  const rejected: unknown[] = [null, undefined, 42, {}, [], true, ""];
  for (const input of rejected) {
    assert.equal(safeInternalRedirectPath(input), null, `expected ${JSON.stringify(input)} to be rejected`);
  }
});

test("rejects an excessively long value", () => {
  const tooLong = "/" + "a".repeat(600);
  assert.equal(safeInternalRedirectPath(tooLong), null);
  // A long-but-within-bound value of the same shape is still accepted,
  // proving this is a length check and not an incidental rejection of
  // repeated characters.
  const withinBound = "/" + "a".repeat(100);
  assert.equal(safeInternalRedirectPath(withinBound), withinBound);
});

test("rejects the login page itself and safe-looking variants of it", () => {
  const loginVariants = [
    "/login",
    "/login/",
    "/login//",
    "/LOGIN",
    "/Login",
    "/Login/",
    "/login?next=/workspace",
    "/login#section",
    "/%6Cogin", // percent-encoded 'l' — Next.js's router still decodes and routes this to /login
    "/%4Cogin", // percent-encoded 'L'
  ];
  for (const input of loginVariants) {
    assert.equal(safeInternalRedirectPath(input), null, `expected ${JSON.stringify(input)} to be rejected as a /login variant`);
  }
});

test("rejects a protocol-relative absolute URL (network-path reference)", () => {
  assert.equal(safeInternalRedirectPath("//evil.example"), null);
  assert.equal(safeInternalRedirectPath("//evil.example/overview/AAPL"), null);
});

test("rejects the backslash variant some browsers also normalize to an absolute URL", () => {
  assert.equal(safeInternalRedirectPath("/" + "\\" + "evil.example"), null);
});

test("rejects a value that does not start with a single leading slash", () => {
  assert.equal(safeInternalRedirectPath("overview/AAPL"), null);
  assert.equal(safeInternalRedirectPath("https://evil.example"), null);
  assert.equal(safeInternalRedirectPath("javascript:alert(1)"), null);
});

test("rejects encoded and double-encoded separator tricks", () => {
  const encodedTricks = [
    "/%2Fevil.example", // encoded slash
    "/%2fevil.example", // lowercase
    "/%5Cevil.example", // encoded backslash
    "/%5cevil.example",
    "/%252Fevil.example", // double-encoded slash
    "/%255Cevil.example", // double-encoded backslash
    "/redirect?to=" + "%2F%2Fevil.example",
  ];
  for (const input of encodedTricks) {
    assert.equal(safeInternalRedirectPath(input), null, `expected ${JSON.stringify(input)} to be rejected`);
  }
});

test("rejects leading or trailing whitespace", () => {
  assert.equal(safeInternalRedirectPath(SPACE + "/overview/MSFT"), null);
  assert.equal(safeInternalRedirectPath("/overview/MSFT" + SPACE), null);
  assert.equal(safeInternalRedirectPath(TAB + "/overview/MSFT"), null);
});

test("rejects embedded control characters: newline, tab, null byte, carriage return, unit separator, DEL", () => {
  const controlCases = [
    "/overview/" + LF + "MSFT",
    "/overview/" + TAB + "MSFT",
    "/overview/" + NUL + "MSFT",
    "/overview/" + CR + "MSFT",
    "/overview/" + UNIT_SEPARATOR + "MSFT",
    "/overview/" + DEL + "MSFT",
  ];
  for (const input of controlCases) {
    assert.equal(safeInternalRedirectPath(input), null, `expected a value containing code point ${input.codePointAt(10)} to be rejected`);
  }
});

test("rejects malformed percent encoding", () => {
  const malformed = [
    "/overview/%",
    "/overview/%z",
    "/overview/%zz",
    "/overview/%e0%a4%a", // truncated multi-byte UTF-8 sequence
    "/overview/%",
  ];
  for (const input of malformed) {
    assert.equal(safeInternalRedirectPath(input), null, `expected ${JSON.stringify(input)} to be rejected`);
  }
});

test("rejects a query string on an otherwise-valid destination — queries are intentionally not part of the accepted contract", () => {
  assert.equal(safeInternalRedirectPath("/overview/MSFT?x=1"), null);
  assert.equal(safeInternalRedirectPath("/workspace?ticker=AAPL"), null);
});

test("rejects a fragment on an otherwise-valid destination", () => {
  assert.equal(safeInternalRedirectPath("/overview/MSFT#section"), null);
});

test("returns the exact validated pathname, not the raw input, for an accepted value", () => {
  // Demonstrates the string|null contract: the caller uses the RETURN
  // value, never the original `value` it passed in, so there is no
  // possibility of using a raw value that merely passed a check
  // performed on some other representation of the same string.
  const result = safeInternalRedirectPath("/overview/MSFT");
  assert.equal(typeof result, "string");
  assert.equal(result, "/overview/MSFT");
});
