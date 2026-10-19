import assert from "node:assert/strict";
import test from "node:test";

import {
  MONTE_CARLO_BACKEND_REQUEST_TIMEOUT_MS,
  exportFilename,
  forwardedMonteCarloHeaders,
  isReplayBundleText,
  isValidSnapshotId,
  monteCarloBackendPath,
  normalizeMonteCarloTicker,
  parseSeedQuery,
} from "./monte-carlo-proxy.ts";

test("tickers are normalized and malformed ones rejected", () => {
  assert.equal(normalizeMonteCarloTicker(" aapl "), "AAPL");
  assert.equal(normalizeMonteCarloTicker("BRK-B"), "BRK-B");
  for (const bad of ["", "../etc", "AAPL/x", "A B", "1ABC", "TOOLONGTICKER1", "AAPL?seed=1"]) {
    assert.equal(normalizeMonteCarloTicker(bad), null, bad);
  }
});

test("snapshot ids must be 64 lowercase hex characters", () => {
  assert.equal(isValidSnapshotId("a".repeat(64)), true);
  for (const bad of ["", "a".repeat(63), "A".repeat(64), "g".repeat(64), "../" + "a".repeat(61), "a".repeat(64) + "/x"]) {
    assert.equal(isValidSnapshotId(bad), false, bad);
  }
});

test("only a canonical seed is forwarded", () => {
  assert.deepEqual(parseSeedQuery(""), { ok: true, seed: null });
  assert.deepEqual(parseSeedQuery("?seed=42"), { ok: true, seed: "42" });
  assert.deepEqual(parseSeedQuery("seed=0"), { ok: true, seed: "0" });
  assert.deepEqual(parseSeedQuery(`?seed=${Number.MAX_SAFE_INTEGER}`), { ok: true, seed: String(Number.MAX_SAFE_INTEGER) });
  const bad = [
    "?seed=-1", "?seed=1.5", "?seed=1e3", "?seed=007", "?seed=", "?seed=abc", "?seed=+5",
    `?seed=${Number.MAX_SAFE_INTEGER + 1}`, "?seed=9223372036854775808",
    "?seed=1&seed=2", "?seed=1&extra=x", "?other=1", "?ticker=MSFT",
  ];
  for (const query of bad) assert.deepEqual(parseSeedQuery(query), { ok: false }, query);
});

test("backend paths are built from validated parts only", () => {
  assert.equal(monteCarloBackendPath({ kind: "describe" }), "/api/monte-carlo");
  assert.equal(monteCarloBackendPath({ kind: "run", ticker: "AAPL" }), "/api/monte-carlo/AAPL");
  assert.equal(monteCarloBackendPath({ kind: "run", ticker: "BRK.B" }), "/api/monte-carlo/BRK.B");
  assert.equal(
    monteCarloBackendPath({ kind: "export", snapshotId: "a".repeat(64) }),
    `/api/monte-carlo/snapshots/${"a".repeat(64)}`,
  );
});

test("only a numeric Retry-After is forwarded", () => {
  const headers = (value: string | null) => ({ get: () => value });
  assert.deepEqual(forwardedMonteCarloHeaders(headers("3")), { "Retry-After": "3" });
  assert.deepEqual(forwardedMonteCarloHeaders(headers(null)), {});
  assert.deepEqual(forwardedMonteCarloHeaders(headers("Wed, 21 Oct 2026 07:28:00 GMT")), {});
  assert.deepEqual(forwardedMonteCarloHeaders(headers("3\r\nSet-Cookie: x=1")), {});
});

test("export filenames are derived from validated values", () => {
  assert.equal(exportFilename("a".repeat(64), "42"), "mc-dcf-aaaaaaaaaaaa-seed-42.json");
  assert.equal(exportFilename("a".repeat(64), null), "mc-dcf-aaaaaaaaaaaa.json");
});

test("the proxy waits longer than the backend's 40 s hard stop", () => {
  assert.equal(MONTE_CARLO_BACKEND_REQUEST_TIMEOUT_MS, 45_000);
});

import { withMonteCarloWording } from "./monte-carlo-proxy.ts";

test("backend-failure bodies get simulation wording without the valuation page's copy", () => {
  for (const code of ["VALUATION_BACKEND_TIMEOUT", "VALUATION_BACKEND_UNREACHABLE", "VALUATION_BACKEND_INVALID_RESPONSE"]) {
    const out = withMonteCarloWording({ code, error: "The live valuation service did not respond. Portfolio data remains available below." }) as { code: string; error: string };
    assert.equal(out.code, code);
    assert.match(out.error, /simulation service/);
    assert.ok(!/portfolio|valuation service/i.test(out.error));
  }
});

test("other bodies pass through untouched", () => {
  const body = { detail: { code: "ticker_not_supported", message: "m" } };
  assert.equal(withMonteCarloWording(body), body);
  assert.equal(withMonteCarloWording(null), null);
  assert.deepEqual(withMonteCarloWording({ code: "MONTE_CARLO_RATE_LIMITED", error: "x" }), { code: "MONTE_CARLO_RATE_LIMITED", error: "x" });
});

test("only a replay bundle is forwarded byte-for-byte by the export route", () => {
  assert.equal(isReplayBundleText('{"schema":"mc-dcf-replay-bundle-v1","replay":{}}'), true);
  for (const bad of ["", "not json", "[]", "null", '{"schema":"mc-dcf-result-v1"}', '{"detail":"x"}']) {
    assert.equal(isReplayBundleText(bad), false, bad);
  }
});

test("why export is a passthrough: a JavaScript round trip rewrites numbers that the bundle's hashes cover", () => {
  const backendText = '{"revenue":133050000000.0,"margin":0.31970799762591884}';
  assert.notEqual(JSON.stringify(JSON.parse(backendText)), backendText);
  assert.equal(JSON.stringify(JSON.parse(backendText)), '{"revenue":133050000000,"margin":0.31970799762591884}');
});
