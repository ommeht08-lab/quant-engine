import assert from "node:assert/strict";
import test from "node:test";

import {
  fixtureToResearchOverviewViewModel,
  liveEvaluationToResearchOverviewViewModel,
  type LiveEvaluationResponse,
} from "./research-overview-view-model.ts";
import { AAPL_RESEARCH_FIXTURE } from "./research-fixture.ts";
import type { ValuationQuality } from "./valuation-quality.ts";

const ORDINARY_QUALITY: ValuationQuality = {
  level: "ordinary",
  codes: [],
  allows_market_comparison: true,
  terminal_value_share_of_enterprise_value: 0.4,
  observed_effective_tax_rate: 0.21,
};

function liveScenario(overrides: Partial<LiveEvaluationResponse["scenarios"]["base"]> = {}) {
  return {
    name: "Base",
    assumptions: { revenue_growth_rate: 0.08, operating_margin: 0.3, wacc: 0.09, terminal_growth_rate: 0.025 },
    intrinsic_value_per_share: 150,
    is_valid: true,
    invalid_reason: null,
    ...overrides,
  };
}

function liveResponse(overrides: Partial<LiveEvaluationResponse> = {}): LiveEvaluationResponse {
  return {
    ticker: "MSFT",
    current_price: 400,
    intrinsic_value_per_share: 420,
    sector: "Technology",
    price_to_intrinsic_value: 0.95,
    sector_median_p_iv: 1.1,
    sector_median_unavailable_code: null,
    sector_median_snapshot: { generated_at: "2026-08-01T00:00:00Z", universe_size: 500, tickers_used: 480, sector_sample_count: 60 },
    valuation_quality: ORDINARY_QUALITY,
    forecast_method: "maturation",
    capex_pct_revenue: 0.04,
    capex_pct_revenue_source: "default",
    scenarios: {
      bear: liveScenario({ name: "Bear", intrinsic_value_per_share: 360 }),
      base: liveScenario({ name: "Base", intrinsic_value_per_share: 420 }),
      bull: liveScenario({ name: "Bull", intrinsic_value_per_share: 480 }),
    },
    ...overrides,
  };
}

// --- fixture adapter -------------------------------------------------

test("fixtureToResearchOverviewViewModel: maps the AAPL fixture faithfully", () => {
  const vm = fixtureToResearchOverviewViewModel(AAPL_RESEARCH_FIXTURE);
  assert.equal(vm.mode, "fixture");
  assert.equal(vm.ticker, "AAPL");
  assert.equal(vm.companyName, "Apple Inc.");
  assert.equal(vm.sector, "Technology");
  assert.equal(vm.exchange, "NASDAQ");
  assert.equal(vm.monogram, "A");
  assert.equal(vm.marketPrice.display, "$202.38");
  assert.equal(vm.marketPrice.status, "available");
  assert.equal(vm.intrinsicValue.display, "$236.70");
  assert.equal(vm.marginOfSafety.display, "+17.0%");
  assert.equal(vm.marginOfSafety.positive, true);
  assert.equal(vm.qualityLevel, null);
  assert.deepEqual(vm.qualityCodes, []);
});

test("fixtureToResearchOverviewViewModel: summary captions describe a point-in-time case, never 'Live' — this is a static fixture with no live request", () => {
  const vm = fixtureToResearchOverviewViewModel(AAPL_RESEARCH_FIXTURE);
  assert.equal(vm.marketPrice.caption, "At knowledge cutoff");
  assert.equal(vm.intrinsicValue.caption, "Base case · per share");
  assert.equal(vm.marginOfSafety.caption, "Intrinsic vs. market");
  for (const cell of [vm.marketPrice, vm.intrinsicValue, vm.marginOfSafety]) {
    assert.notEqual(cell.caption, "Live");
    assert.notEqual(cell.caption, "Calculated from live fields");
  }
});

test("liveEvaluationToResearchOverviewViewModel: summary captions describe response provenance without claiming an observation timestamp", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse(), new Date());
  assert.equal(vm.marketPrice.caption, "Returned by valuation API");
  assert.equal(vm.intrinsicValue.caption, "Base model · per share");
  assert.equal(vm.marginOfSafety.caption, "Calculated from returned values");
  assert.equal(vm.asOfLabel, "Response received");
});

test("fixtureToResearchOverviewViewModel: scenarios map 1:1, all valid, base flagged", () => {
  const vm = fixtureToResearchOverviewViewModel(AAPL_RESEARCH_FIXTURE);
  assert.equal(vm.scenarios.length, 3);
  const base = vm.scenarios.find((scenario) => scenario.key === "base");
  assert.ok(base);
  assert.equal(base.isBase, true);
  assert.equal(base.isValid, true);
  assert.equal(base.valueDisplay, "$236.70");
  const bear = vm.scenarios.find((scenario) => scenario.key === "bear");
  assert.ok(bear);
  assert.equal(bear.isBase, false);
});

test("fixtureToResearchOverviewViewModel: revenue and evidence panels are available; sector-relative is not modeled", () => {
  const vm = fixtureToResearchOverviewViewModel(AAPL_RESEARCH_FIXTURE);
  assert.equal(vm.revenuePanel.status, "available");
  assert.equal(vm.evidencePanel.status, "available");
  assert.equal(vm.sectorRelativePanel.status, "unavailable");
  assert.match(vm.sectorRelativePanel.reason, /does not model a sector-relative comparison/);
});

// --- live adapter: basic mapping --------------------------------------

test("liveEvaluationToResearchOverviewViewModel: maps top-level identity/valuation fields, mode 'live'", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse(), new Date("2026-09-14T12:00:00Z"));
  assert.equal(vm.mode, "live");
  assert.equal(vm.ticker, "MSFT");
  assert.equal(vm.sector, "Technology");
  assert.equal(vm.monogram, "M");
  assert.equal(vm.marketPrice.status, "available");
  assert.equal(vm.marketPrice.display, "$400.00");
  assert.equal(vm.intrinsicValue.display, "$420.00");
});

test("liveEvaluationToResearchOverviewViewModel: companyName and exchange are never fabricated for live data", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse(), new Date());
  assert.equal(vm.companyName, null);
  assert.equal(vm.exchange, null);
});

test("liveEvaluationToResearchOverviewViewModel: revenue and evidence panels are always unavailable with a true, specific reason", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse(), new Date());
  assert.equal(vm.revenuePanel.status, "unavailable");
  assert.equal(vm.evidencePanel.status, "unavailable");
  if (vm.revenuePanel.status === "unavailable") {
    assert.match(vm.revenuePanel.reason, /Historical revenue/);
  }
  if (vm.evidencePanel.status === "unavailable") {
    assert.match(vm.evidencePanel.reason, /SEC filing evidence/);
  }
});

test("liveEvaluationToResearchOverviewViewModel: no observed market price is 'unavailable', not '$0.00' or a guess", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse({ current_price: null }), new Date());
  assert.equal(vm.marketPrice.status, "unavailable");
  assert.equal(vm.marketPrice.display, null);
  assert.ok(vm.marketPrice.reason);
});

// --- unavailable vs. withheld: sector-relative panel -------------------

test("sectorRelativePanel: 'available' when both live comparison fields are present", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse(), new Date());
  assert.equal(vm.sectorRelativePanel.status, "available");
  if (vm.sectorRelativePanel.status === "available") {
    assert.equal(vm.sectorRelativePanel.priceToIntrinsicValue, 0.95);
    assert.equal(vm.sectorRelativePanel.sectorMedianPIV, 1.1);
    assert.match(vm.sectorRelativePanel.snapshotCaption ?? "", /480\/500 tickers valued/);
  }
});

test("sectorRelativePanel: 'withheld' (not 'unavailable') specifically when the code is valuation_quality", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({
      price_to_intrinsic_value: null,
      sector_median_p_iv: null,
      sector_median_unavailable_code: "valuation_quality",
    }),
    new Date()
  );
  assert.equal(vm.sectorRelativePanel.status, "withheld");
  assert.match(vm.sectorRelativePanel.reason, /interpretation cautions/);
});

test("sectorRelativePanel: 'unavailable' (not 'withheld') for every other missing-data code", () => {
  for (const code of ["incompatible_assumptions", "insufficient_peers", "snapshot_unavailable"] as const) {
    const vm = liveEvaluationToResearchOverviewViewModel(
      liveResponse({ price_to_intrinsic_value: null, sector_median_p_iv: null, sector_median_unavailable_code: code }),
      new Date()
    );
    assert.equal(vm.sectorRelativePanel.status, "unavailable", `code ${code} should be 'unavailable'`);
  }
});

test("sectorRelativePanel: 'unavailable' when the code is null (no snapshot at all)", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({ price_to_intrinsic_value: null, sector_median_p_iv: null, sector_median_unavailable_code: null }),
    new Date()
  );
  assert.equal(vm.sectorRelativePanel.status, "unavailable");
});

test("sectorRelativePanel: quality policy wins even if a contradictory response includes comparison ratios", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({
      valuation_quality: {
        level: "caution",
        codes: ["high_terminal_value_concentration"],
        allows_market_comparison: false,
        terminal_value_share_of_enterprise_value: 0.88,
        observed_effective_tax_rate: 0.21,
      },
    }),
    new Date()
  );
  assert.equal(vm.sectorRelativePanel.status, "withheld");
});

// --- quality-policy presentation inputs ---------------------------------

test("quality policy: ordinary level carries through with no codes", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse(), new Date());
  assert.equal(vm.qualityLevel, "ordinary");
  assert.deepEqual(vm.qualityCodes, []);
});

test("quality policy: caution level surfaces human-readable copy per code, not raw codes", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({
      valuation_quality: {
        level: "caution",
        codes: ["extreme_observed_tax_rate"],
        allows_market_comparison: false,
        terminal_value_share_of_enterprise_value: 0.5,
        observed_effective_tax_rate: 0.65,
      },
      price_to_intrinsic_value: null,
      sector_median_p_iv: null,
      sector_median_unavailable_code: "valuation_quality",
    }),
    new Date()
  );
  assert.equal(vm.qualityLevel, "caution");
  assert.equal(vm.qualityCodes.length, 1);
  assert.match(vm.qualityCodes[0], /effective tax rate/);
  assert.doesNotMatch(vm.qualityCodes[0], /^extreme_observed_tax_rate$/);
  assert.deepEqual(vm.qualitySummary, {
    heading: "Valuation quality",
    label: "Caution",
    caption: "1 interpretation note",
    tone: "warning",
  });
  assert.deepEqual(vm.qualityDetails, ["Observed effective tax rate is 65.0%."]);
});

test("quality policy: ordinary live output does not claim complete filing evidence", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse(), new Date());
  assert.deepEqual(vm.qualitySummary, {
    heading: "Valuation quality",
    label: "Ordinary",
    caption: "No valuation cautions",
    tone: "positive",
  });
  assert.notEqual(vm.qualitySummary.label, "Complete");
  assert.doesNotMatch(vm.qualitySummary.caption, /required facts/i);
});

test("quality policy: terminal-value concentration retains the numeric diagnostic", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({
      valuation_quality: {
        level: "caution",
        codes: ["high_terminal_value_concentration"],
        allows_market_comparison: false,
        terminal_value_share_of_enterprise_value: 0.8831,
        observed_effective_tax_rate: 0.21,
      },
    }),
    new Date()
  );
  assert.deepEqual(vm.qualityDetails, ["Discounted terminal value supplies 88.3% of enterprise value."]);
});

test("quality policy: diagnostic_only level withholds margin of safety even though both prices are real", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({
      valuation_quality: {
        level: "diagnostic_only",
        codes: ["nonpositive_enterprise_value"],
        allows_market_comparison: false,
        terminal_value_share_of_enterprise_value: null,
        observed_effective_tax_rate: null,
      },
    }),
    new Date()
  );
  assert.equal(vm.qualityLevel, "diagnostic_only");
  assert.equal(vm.marginOfSafety.status, "withheld");
  assert.equal(vm.marginOfSafety.display, null);
  assert.equal(vm.marginOfSafety.positive, false);
});

test("quality policy: allows_market_comparison true with a real price computes a 'calculated' margin of safety", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse({ current_price: 350, intrinsic_value_per_share: 420 }), new Date());
  assert.equal(vm.marginOfSafety.status, "calculated");
  assert.equal(vm.marginOfSafety.positive, true);
});

test("quality policy: allowed comparison but no observed price is 'unavailable', not 'withheld'", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse({ current_price: null }), new Date());
  assert.equal(vm.marginOfSafety.status, "unavailable");
});

// --- invalid scenarios ---------------------------------------------------

test("invalid scenario: shows the model's own invalid_reason instead of a value", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({
      scenarios: {
        bear: liveScenario({ name: "Bear", is_valid: false, intrinsic_value_per_share: null, invalid_reason: "Negative terminal free cash flow." }),
        base: liveScenario({ name: "Base" }),
        bull: liveScenario({ name: "Bull" }),
      },
    }),
    new Date()
  );
  const bear = vm.scenarios.find((scenario) => scenario.key === "bear");
  assert.ok(bear);
  assert.equal(bear.isValid, false);
  assert.equal(bear.valueDisplay, null);
  assert.equal(bear.reason, "Negative terminal free cash flow.");
});

test("valid scenario: assumptionsNote reports growth/margin/WACC/terminal growth, not the invalid reason", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse(), new Date());
  const base = vm.scenarios.find((scenario) => scenario.key === "base");
  assert.ok(base);
  assert.equal(base.reason, null);
  assert.match(base.assumptionsNote ?? "", /Growth 8\.0%/);
  assert.match(base.assumptionsNote ?? "", /WACC 9\.00%/);
});

test("reversed scenarios are labeled as input cases rather than intuitive downside/upside", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({
      valuation_quality: {
        level: "diagnostic_only",
        codes: ["reversed_scenario_values"],
        allows_market_comparison: false,
        terminal_value_share_of_enterprise_value: null,
        observed_effective_tax_rate: 0.21,
      },
    }),
    new Date()
  );
  assert.deepEqual(vm.scenarios.map((scenario) => scenario.label), ["Bear inputs", "Base", "Bull inputs"]);
});

// --- non-AAPL tickers never receive AAPL fixture values -------------------

test("non-AAPL live ticker: no field in the resulting view model equals an AAPL fixture value", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(liveResponse({ ticker: "MSFT" }), new Date());
  assert.equal(vm.ticker, "MSFT");
  assert.notEqual(vm.ticker, AAPL_RESEARCH_FIXTURE.ticker);
  assert.notEqual(vm.companyName, AAPL_RESEARCH_FIXTURE.companyName);
  assert.notEqual(vm.marketPrice.display, AAPL_RESEARCH_FIXTURE.marketPrice);
  assert.notEqual(vm.intrinsicValue.display, AAPL_RESEARCH_FIXTURE.intrinsicValue);
  assert.notEqual(vm.marginOfSafety.display, AAPL_RESEARCH_FIXTURE.marginOfSafety);
  assert.equal(vm.monogram, "M");
  assert.notEqual(vm.monogram, AAPL_RESEARCH_FIXTURE.companyName.charAt(0));
  // The fixture's chart/evidence never leak into a live view model at all.
  assert.equal(vm.revenuePanel.status, "unavailable");
  assert.equal(vm.evidencePanel.status, "unavailable");
});

test("a live response literally for AAPL still produces mode 'live', not the fixture's mode or values", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({ ticker: "AAPL", current_price: 199.11, intrinsic_value_per_share: 210.5 }),
    new Date()
  );
  assert.equal(vm.mode, "live");
  assert.equal(vm.companyName, null);
  assert.notEqual(vm.marketPrice.display, AAPL_RESEARCH_FIXTURE.marketPrice);
  assert.notEqual(vm.intrinsicValue.display, AAPL_RESEARCH_FIXTURE.intrinsicValue);
});

// --- model checks: live-appropriate, not the fixture's --------------------

test("modelChecks: live mode reports the returned forecast policy and a readable CapEx basis", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({ capex_pct_revenue_source: "historical", capex_pct_revenue: 0.223 }),
    new Date()
  );
  const labels = vm.modelChecks.map((check) => check.label);
  assert.deepEqual(labels, ["Forecast policy", "CapEx basis"]);
  const capexCheck = vm.modelChecks.find((check) => check.label === "CapEx basis");
  assert.equal(capexCheck?.value, "Historical annual average · 22.3% of revenue");
});

test("modelChecks: reports a constant fallback response and the default ratio accurately", () => {
  const vm = liveEvaluationToResearchOverviewViewModel(
    liveResponse({ forecast_method: "constant", capex_pct_revenue_source: "fallback", capex_pct_revenue: 0.04 }),
    new Date()
  );
  assert.deepEqual(vm.modelChecks, [
    { label: "Forecast policy", value: "Constant forecast response" },
    { label: "CapEx basis", value: "Flat default fallback · 4.0% of revenue" },
  ]);
});
