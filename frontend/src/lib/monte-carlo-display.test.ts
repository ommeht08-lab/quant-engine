import assert from "node:assert/strict";
import test from "node:test";

import {
  ABOVE_MARKET_LABEL,
  EXPERIMENT_LABELS,
  MARGIN_BASIS_LABEL,
  buildProvenanceSections,
  describeAboveMarket,
  describeQuoteTime,
  fallbackReasonText,
  failureSummary,
  histogramBars,
  interquantileWidth,
  marginBasisNotice,
  marginRowsText,
  markerPlacement,
  parseSeedInput,
  refusalCopy,
  spreadIllustrationText,
  statementAgeText,
  validCountText,
} from "./monte-carlo-display.ts";
import { makeExperiment, makeProvenance } from "./monte-carlo-fixtures.ts";

test("the comparison sentence uses the required wording and the actual valid denominator", () => {
  assert.equal(ABOVE_MARKET_LABEL, "Simulated valuations above market price under these assumptions");
  const complete = describeAboveMarket(makeExperiment({ valid: 10000, target: 10000, above_market: { count: 6234, denominator: 10000, fraction: 0.6234 } }));
  assert.equal(complete?.sentence, "Simulated valuations above market price under these assumptions: 62.3% (6,234 of 10,000).");
  const incomplete = describeAboveMarket(makeExperiment({ valid: 9450, target: 10000, status: "incomplete", above_market: { count: 4725, denominator: 9450, fraction: 0.5 } }));
  assert.equal(incomplete?.detail, "4,725 of 9,450");
  assert.ok(!incomplete?.sentence.includes("10,000"));
});

test("nothing is claimed when no valuations were simulated", () => {
  assert.equal(describeAboveMarket(makeExperiment({ above_market: null })), null);
  assert.equal(describeAboveMarket(makeExperiment({ above_market: { count: 0, denominator: 0, fraction: 0 } })), null);
});

test("valid-count text never presents the target as reached", () => {
  assert.equal(validCountText({ valid: 10000, target: 10000, status: "complete" }), "10,000 valid valuations");
  assert.equal(validCountText({ valid: 9450, target: 10000, status: "incomplete" }), "Incomplete: 9,450 valid valuations (target 10,000)");
  assert.equal(validCountText({ valid: 0, target: 10000, status: "refused" }), "Not run");
});

test("failure summaries list only non-zero causes", () => {
  assert.deepEqual(failureSummary({ failed_by_code: { nonfinite_projection: 295, nonfinite_equity: 0 } }), ["projection not finite: 295"]);
  assert.deepEqual(failureSummary({ failed_by_code: {} }), []);
});

test("refusal copy covers known codes and falls back safely", () => {
  assert.match(refusalCopy("insufficient_history"), /Too few annual periods/);
  assert.equal(refusalCopy("brand_new_code"), "This experiment could not be run.");
  assert.equal(refusalCopy(null), "This experiment could not be run.");
});

test("histogram bars are scaled to the tallest bin and reject mismatched shapes", () => {
  const bars = histogramBars([0, 1, 2, 3], [5, 10, 0]);
  assert.deepEqual(bars.map((b) => b.height), [0.5, 1, 0]);
  assert.deepEqual(bars.map((b) => [b.x0, b.x1]), [[0, 1], [1, 2], [2, 3]]);
  assert.deepEqual(histogramBars([0, 1], [1, 2]), []);
  assert.deepEqual(histogramBars([0], []), []);
});

test("markers outside the shared axis are flagged, never clamped onto it", () => {
  const edges = [100, 200, 300];
  assert.deepEqual(markerPlacement(150, edges), { inRange: true, fraction: 0.25 });
  assert.deepEqual(markerPlacement(100, edges), { inRange: true, fraction: 0 });
  assert.deepEqual(markerPlacement(99, edges), { inRange: false, side: "below" });
  assert.deepEqual(markerPlacement(301, edges), { inRange: false, side: "above" });
  assert.equal(markerPlacement(Number.NaN, edges), null);
  assert.equal(markerPlacement(150, [100]), null);
});

test("interquantile width", () => {
  assert.equal(interquantileWidth(makeExperiment()), 30);
  assert.equal(interquantileWidth(makeExperiment({ statistics: null })), null);
});

test("typed seeds must be canonical safe integers", () => {
  assert.equal(parseSeedInput(" 42 "), 42);
  assert.equal(parseSeedInput("0"), 0);
  for (const bad of ["", "-1", "1.5", "1e3", "007", "abc", String(Number.MAX_SAFE_INTEGER + 1)]) assert.equal(parseSeedInput(bad), null, bad);
});

import { describeRequestError } from "./monte-carlo-display.ts";

test("request errors map to plain-language kinds for backend and proxy bodies", () => {
  assert.equal(describeRequestError(409, { detail: { code: "computation_in_progress", message: "x" } }).kind, "busy");
  assert.equal(describeRequestError(429, { code: "MONTE_CARLO_RATE_LIMITED", error: "Slow down." }).message, "Slow down.");
  assert.equal(describeRequestError(404, { detail: { code: "ticker_not_supported", message: "m" } }).kind, "input");
  assert.equal(describeRequestError(422, { detail: { code: "invalid_seed", message: "bad seed" } }).message, "bad seed");
  assert.equal(describeRequestError(400, { code: "MONTE_CARLO_INVALID_REQUEST", error: "bad request" }).message, "bad request");
  assert.equal(describeRequestError(503, { detail: { code: "cache_unavailable", message: "cache down" } }).kind, "unavailable");
  assert.equal(describeRequestError(504, null).kind, "unavailable");
  assert.equal(describeRequestError(500, null).message, "Simulation request failed (HTTP 500).");
  assert.equal(describeRequestError(422, "weird").kind, "input");
});

import { axisDomain } from "./monte-carlo-display.ts";

test("the axis stays on the histogram range when references already fall inside it", () => {
  assert.deepEqual(axisDomain([100, 200, 300], [150, 250]), { low: 100, high: 300 });
});

test("the axis stretches, with padding, to include a nearby market price", () => {
  const domain = axisDomain([100, 200, 300], [60, 320]);
  assert.ok(domain.low < 60 && domain.high > 320);
  assert.ok(domain.high - domain.low <= 200 * 3 * 1.1);
});

test("a reference that would stretch the axis too far is left off it", () => {
  assert.deepEqual(axisDomain([100, 200, 300], [5000]), { low: 100, high: 300 });
  // ...and the nearer one is still included
  const domain = axisDomain([100, 200, 300], [5000, 80]);
  assert.ok(domain.low < 80 && domain.high === 300);
});

test("non-finite references are ignored", () => {
  assert.deepEqual(axisDomain([100, 200], [Number.NaN, Number.POSITIVE_INFINITY]), { low: 100, high: 200 });
});

test("markers on the stretched axis land inside it", () => {
  const edges = [328, 511];
  const domain = axisDomain(edges, [300]);
  const placed = markerPlacement(300, [domain.low, domain.high]);
  assert.equal(placed?.inRange, true);
});

// ---- baseline sources -------------------------------------------------------

const fmt = (iso: string) => `<${iso}>`;
const sections = (overrides = {}) => buildProvenanceSections(makeProvenance(overrides), fmt);
const item = (id: string, label: string, overrides = {}) =>
  sections(overrides).find((s) => s.id === id)?.items.find((i) => i.label === label);

test("the quote time is never replaced by the capture time", () => {
  assert.equal(describeQuoteTime(null, fmt), "quote time not provided by the data source");
  assert.equal(describeQuoteTime(undefined, fmt), "quote time unknown");
  assert.equal(describeQuoteTime("2026-10-16T20:00:01+00:00", fmt), "quoted <2026-10-16T20:00:01+00:00>");

  const price = item("timing", "Market price observed");
  assert.equal(price?.value, "Not provided by the data source");
  assert.equal(price?.flag, "caution");
  const captured = item("timing", "Snapshot captured");
  assert.equal(captured?.value, "<2026-10-20T15:30:00+00:00>");
  assert.ok(!JSON.stringify(sections()).includes("quoted"), "no quote time is implied when none exists");
});

test("a genuine quote time is shown as the observation time", () => {
  const base = makeProvenance();
  const price = buildProvenanceSections(
    { ...base, timing: { ...base.timing, market_price: { ...base.timing.market_price, observed_at: "2026-10-16T20:00:01+00:00", status: "provider_quote_time" } } },
    fmt,
  )
    .find((s) => s.id === "timing")
    ?.items.find((i) => i.label === "Market price observed");
  assert.equal(price?.value, "<2026-10-16T20:00:01+00:00>");
  assert.equal(price?.flag, undefined);
});

test("statement dates and age are listed", () => {
  const timing = sections().find((s) => s.id === "timing")!;
  assert.equal(timing.items.find((i) => i.label === "Latest annual statements")?.value, "2024-12-31");
  assert.equal(statementAgeText(384), "384 days (12.6 months) before capture");
  assert.equal(statementAgeText(null), null);
  assert.match(timing.items.find((i) => i.label === "Revenue basis")?.value ?? "", /not trailing twelve months/);
  assert.match(timing.items.find((i) => i.label === "Currency")?.detail ?? "", /statement currency only/);
});

test("the margin row, periods and the EBIT caveat are disclosed", () => {
  const margin = sections().find((s) => s.id === "margin")!;
  assert.equal(margin.items[0].value, "EBIT");
  assert.deepEqual(margin.table?.columns, ["Fiscal period", "Row used", "Margin"]);
  assert.deepEqual(margin.table?.rows[0], ["2024-12-31", "EBIT", "26.40%"]);
  assert.match(margin.items.find((i) => i.label === "About the EBIT row")?.value ?? "", /non-operating and special items/);

  assert.equal(marginRowsText(["EBIT", "Operating Income"], true), "EBIT and Operating Income (rows differ by period)");
  assert.equal(marginRowsText([], false), "none available");

  const onOperatingIncome = buildProvenanceSections(makeProvenance({ margin: { ...makeProvenance().margin, rows_used: ["Operating Income"] } }), fmt).find((s) => s.id === "margin")!;
  assert.equal(onOperatingIncome.items.some((i) => i.label === "About the EBIT row"), false);
});

test("skipped periods and a margin that fails to reproduce are surfaced", () => {
  const base = makeProvenance().margin;
  const margin = buildProvenanceSections(
    makeProvenance({ margin: { ...base, periods_skipped: [{ period: "2021-12-31", reason: "operating_profit_row_missing" }], periods_reproduce_model_margin: false } }),
    fmt,
  ).find((s) => s.id === "margin")!;
  assert.match(margin.items.find((i) => i.label === "Periods not used")?.value ?? "", /2021-12-31 \(no EBIT or Operating Income value\)/);
  assert.equal(margin.items.find((i) => i.label === "Check")?.flag, "caution");
});

test("tax and cost-of-debt fallbacks are flagged with the default and the reason", () => {
  const ordinary = item("financing", "Cost of debt");
  assert.equal(ordinary?.flag, undefined);
  assert.equal(ordinary?.value, "4.50%");

  const base = makeProvenance();
  const fallback = buildProvenanceSections(
    makeProvenance({
      cost_of_debt: { ...base.cost_of_debt, rate: 0.05, source: "default_fallback", is_fallback: true, fallback_reason: "interest_expense_unavailable_or_zero" },
      tax_rate: { ...base.tax_rate, rate: 0.21, source: "default_fallback", is_fallback: true, fallback_reason: "pretax_income_unavailable_or_zero" },
      beta: { value: 1, source: "default_fallback", is_fallback: true },
    }),
    fmt,
  ).find((s) => s.id === "financing")!;
  const debt = fallback.items.find((i) => i.label === "Cost of debt")!;
  assert.equal(debt.flag, "fallback");
  assert.match(debt.detail ?? "", /Default 5% applied because interest expense is not reported/);
  assert.match(debt.detail ?? "", /discount rate only/);
  assert.equal(fallback.items.find((i) => i.label === "Tax rate")?.flag, "fallback");
  assert.equal(fallback.items.find((i) => i.label === "Beta")?.flag, "fallback");
  assert.equal(fallbackReasonText("unknown_code"), "the statements did not provide a usable figure");
  assert.equal(fallbackReasonText(null), "the statements did not provide a usable figure");
});

test("the risk-free rate does not claim a resolution path the snapshot did not record", () => {
  assert.match(item("financing", "Risk-free rate")?.detail ?? "", /not whether a lookup fallback applied/);
});

test("capex, D&A and working capital are labeled fixed, with the company's own capex ratio as context only", () => {
  const fixed = sections().find((s) => s.id === "fixed")!;
  assert.deepEqual(fixed.items.map((i) => i.flag), ["fixed", "fixed", "fixed", "fixed", "fixed"]);
  assert.equal(fixed.items[0].value, "4.0% of revenue");

  const base = makeProvenance().capex;
  const withContext = buildProvenanceSections(
    makeProvenance({ capex: { ...base, historical_ratio_for_context: { used_in_valuation: false, status: "derived", ratio: 0.0276, periods: ["2025-09-30", "2024-09-30"] } } }),
    fmt,
  ).find((s) => s.id === "fixed")!;
  assert.match(withContext.items[0].detail ?? "", /For context only: .* 2\.8%\. It is not used\./);
});

test("excluded short-term investments are quantified; an unknown exclusion is not claimed to be zero", () => {
  const unknown = item("bridge", "Short-term investments");
  assert.match(unknown?.value ?? "", /any exclusion is unknown/);

  const base = makeProvenance().cash;
  const excluded = buildProvenanceSections(
    makeProvenance({ cash: { ...base, value: 35934e6, excluded_short_term_investments: 18763e6, excluded_per_share: 1.2856 } }),
    fmt,
  ).find((s) => s.id === "bridge")!;
  const row = excluded.items.find((i) => i.label === "Short-term investments not counted")!;
  assert.equal(row.value, "$18.8B (about $1.29 per share)");
  assert.equal(row.flag, "caution");
  assert.equal(excluded.items.find((i) => i.label === "Cash")?.value, "$35.9B");

  const included = buildProvenanceSections(makeProvenance({ cash: { ...base, includes_short_term_investments: true, definition: "Cash and equivalents plus short-term investments." } }), fmt).find((s) => s.id === "bridge")!;
  assert.equal(included.items.some((i) => i.label.startsWith("Short-term investments")), false);
});

test("baseline WACC clamping and sampled-interval restriction are reported separately", () => {
  const ordinary = sections().find((s) => s.id === "wacc")!;
  assert.equal(ordinary.items[0].flag, undefined);
  assert.match(ordinary.items[0].detail ?? "", /not clamped/);
  assert.equal(ordinary.items[1].flag, undefined);

  const base = makeProvenance().wacc;
  const restrictedOnly = buildProvenanceSections(
    makeProvenance({ wacc: { ...base, baseline_applied: 0.054, baseline_pre_clamp: 0.054, sampled_interval: { lower: 0.05, upper: 0.064, requested_half_width: 0.01, restricted_by_bounds: true, binding_constraints: ["model_lower_bound"] } } }),
    fmt,
  ).find((s) => s.id === "wacc")!;
  assert.match(restrictedOnly.items[0].detail ?? "", /not clamped/);
  assert.equal(restrictedOnly.items[0].flag, undefined);
  assert.equal(restrictedOnly.items[1].flag, "caution");
  assert.match(restrictedOnly.items[1].detail ?? "", /because of the model's lower WACC bound/);

  const clamped = buildProvenanceSections(
    makeProvenance({ wacc: { ...base, baseline_applied: 0.2, baseline_pre_clamp: 0.31, baseline_was_clamped: true, sampled_interval: { lower: 0.19, upper: 0.2, requested_half_width: 0.01, restricted_by_bounds: true, binding_constraints: ["model_upper_bound"] } } }),
    fmt,
  ).find((s) => s.id === "wacc")!;
  assert.equal(clamped.items[0].value, "20.00% (clamped from 31.00%)");
  assert.equal(clamped.items[0].flag, "caution");

  const refused = buildProvenanceSections(makeProvenance({ wacc: { ...base, sampled_interval: null } }), fmt).find((s) => s.id === "wacc")!;
  assert.equal(refused.items[1].value, "none (WACC experiment refused)");
});

test("the spread interval is only ever an illustration under stated assumptions", () => {
  const text = spreadIllustrationText("growth", makeProvenance().spread_illustration.growth);
  assert.ok(text);
  assert.match(text, /^Illustration only: if the 3 growth observations were independent draws from one normal distribution/);
  assert.match(text, /90% interval for the true spread would run from 0\.58x to 4\.42x the estimate/);
  assert.match(text, /autocorrelated, skewed or shifting/);
  assert.match(text, /not a distribution-free confidence statement/);
  assert.equal(spreadIllustrationText("growth", null), null);
});

test("the margin is labeled as the Yahoo EBIT basis wherever it is named, and never as normalized earnings", () => {
  assert.equal(MARGIN_BASIS_LABEL, "Yahoo EBIT basis");
  assert.equal(EXPERIMENT_LABELS.margin, "Operating margin only (Yahoo EBIT basis)");
  const margin = sections().find((s) => s.id === "margin")!;
  assert.equal(margin.title, "Operating margin (Yahoo EBIT basis)");
  const text = JSON.stringify(sections());
  assert.match(text, /v1 default: Yahoo's EBIT row/);
  assert.match(text, /not a normalized or adjusted operating-earnings figure/);
  assert.ok(!/normalized operating (earnings|margin)(?! figure)/i.test(text.replace(/not a normalized or adjusted operating-earnings figure/g, "")), "no row is presented as normalized");
  assert.match(item("margin", "About the EBIT row")?.value ?? "", /ordering of single-input widths can change materially/);
});

test("a margin that did not come purely from the EBIT row says so", () => {
  assert.equal(marginBasisNotice(["EBIT"], false), null);
  assert.match(marginBasisNotice(["Operating Income"], false) ?? "", /did not come purely from Yahoo's EBIT row: Operating Income/);
  assert.match(marginBasisNotice(["EBIT", "Operating Income"], true) ?? "", /mix definitions/);
  assert.match(marginBasisNotice([], false) ?? "", /No margin row/);
});

test("an unknown risk-free lookup path is stated as unknown, not reconstructed", () => {
  assert.match(item("financing", "Risk-free rate")?.detail ?? "", /^Lookup path unknown\./);
});
