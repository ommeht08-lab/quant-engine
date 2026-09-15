import assert from "node:assert/strict";
import test from "node:test";

import { normalizeOverviewTicker, overviewRouteForTicker, resolveMarginOfSafetyDisplay } from "./overview-response.ts";
import { isPublicRoute } from "./public-route.ts";

test("normalizeOverviewTicker: trims and uppercases", () => {
  assert.equal(normalizeOverviewTicker("  aapl  "), "AAPL");
  assert.equal(normalizeOverviewTicker("msft"), "MSFT");
});

test("normalizeOverviewTicker: empty/whitespace-only input normalizes to null", () => {
  assert.equal(normalizeOverviewTicker(""), null);
  assert.equal(normalizeOverviewTicker("   "), null);
});

test("overviewRouteForTicker: builds the /overview/{TICKER} path", () => {
  assert.equal(overviewRouteForTicker("  aapl  "), "/overview/AAPL");
  assert.equal(overviewRouteForTicker("msft"), "/overview/MSFT");
});

test("overviewRouteForTicker: null for input that normalizes to nothing — caller must not navigate", () => {
  assert.equal(overviewRouteForTicker(""), null);
  assert.equal(overviewRouteForTicker("   "), null);
});

test("overviewRouteForTicker: URL-encodes an unusual ticker rather than concatenating it raw", () => {
  assert.equal(overviewRouteForTicker("brk.b"), "/overview/BRK.B");
});

test("/overview/[ticker] is not classified as a public route — regression guard for the prefix-matching landmine", () => {
  // isPublicRoute matches by PREFIX ("/research" matches every
  // "/research/*" path), which is exactly why the new overview route was
  // deliberately placed outside both public prefixes rather than under
  // "/research" — this proves that placement still holds.
  assert.equal(isPublicRoute("/overview/AAPL"), false);
  assert.equal(isPublicRoute("/overview/MSFT"), false);
  assert.equal(isPublicRoute("/overview"), false);
});

test("resolveMarginOfSafetyDisplay: withheld when market comparison is not allowed, even with real prices present", () => {
  const result = resolveMarginOfSafetyDisplay({
    currentPrice: 150,
    intrinsicValuePerShare: 200,
    allowsMarketComparison: false,
  });
  assert.equal(result.withheld, true);
  assert.equal(result.hasMarginOfSafety, null);
  assert.equal(result.deltaPct, null);
  assert.equal(result.label, "Withheld");
  // hasObservedPrice may still be true — that's a fact about the raw
  // data, not a reconstructed comparison ratio.
  assert.equal(result.hasObservedPrice, true);
});

test("resolveMarginOfSafetyDisplay: computes a genuine margin of safety when price is at or below intrinsic value", () => {
  const result = resolveMarginOfSafetyDisplay({
    currentPrice: 80,
    intrinsicValuePerShare: 100,
    allowsMarketComparison: true,
  });
  assert.equal(result.withheld, false);
  assert.equal(result.hasMarginOfSafety, true);
  assert.equal(result.deltaPct, 0.25);
  assert.equal(result.label, "Margin of safety");
});

test("resolveMarginOfSafetyDisplay: 'Downside' when price exceeds intrinsic value", () => {
  const result = resolveMarginOfSafetyDisplay({
    currentPrice: 120,
    intrinsicValuePerShare: 100,
    allowsMarketComparison: true,
  });
  assert.equal(result.hasMarginOfSafety, false);
  assert.equal(result.label, "Downside");
  assert.ok(result.deltaPct !== null && result.deltaPct < 0);
});

test("resolveMarginOfSafetyDisplay: no observed price is distinct from withheld", () => {
  const result = resolveMarginOfSafetyDisplay({
    currentPrice: null,
    intrinsicValuePerShare: 100,
    allowsMarketComparison: true,
  });
  assert.equal(result.withheld, false);
  assert.equal(result.hasObservedPrice, false);
  assert.equal(result.hasMarginOfSafety, null);
  assert.equal(result.deltaPct, null);
  assert.equal(result.label, "Upside / downside");
});
