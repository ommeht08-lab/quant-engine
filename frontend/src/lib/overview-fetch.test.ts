import assert from "node:assert/strict";
import test from "node:test";

import { fetchLiveResearchOverview, overviewEvaluationUrl } from "./overview-fetch.ts";
import { AAPL_RESEARCH_FIXTURE } from "./research-fixture.ts";
import type { LiveEvaluationResponse } from "./research-overview-view-model.ts";

function liveBody(overrides: Partial<LiveEvaluationResponse> = {}): LiveEvaluationResponse {
  const scenario = {
    name: "Base",
    assumptions: { revenue_growth_rate: 0.08, operating_margin: 0.3, wacc: 0.09, terminal_growth_rate: 0.025 },
    intrinsic_value_per_share: 420,
    is_valid: true,
    invalid_reason: null,
  };
  return {
    ticker: "MSFT",
    current_price: 400,
    intrinsic_value_per_share: 420,
    sector: "Technology",
    price_to_intrinsic_value: 0.95,
    sector_median_p_iv: 1.1,
    sector_median_unavailable_code: null,
    sector_median_snapshot: null,
    valuation_quality: {
      level: "ordinary",
      codes: [],
      allows_market_comparison: true,
      terminal_value_share_of_enterprise_value: 0.4,
      observed_effective_tax_rate: 0.21,
    },
    forecast_method: "maturation",
    capex_pct_revenue: 0.04,
    capex_pct_revenue_source: "default",
    scenarios: { bear: { ...scenario, name: "Bear" }, base: scenario, bull: { ...scenario, name: "Bull" } },
    ...overrides,
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

// --- staged forecast request contract -------------------------------------

test("overviewEvaluationUrl: requests the staged maturation forecast at the dashboard's default terminal growth rate", () => {
  const url = overviewEvaluationUrl("MSFT");
  assert.equal(url.startsWith("/api/evaluate/MSFT?"), true);
  const params = new URLSearchParams(url.split("?")[1]);
  assert.equal(params.get("forecast_mode"), "maturation");
  assert.equal(params.get("terminal_growth_rate"), "0.025");
});

test("overviewEvaluationUrl: URL-encodes the ticker segment", () => {
  const url = overviewEvaluationUrl("brk.b");
  assert.equal(url.startsWith(`/api/evaluate/${encodeURIComponent("brk.b")}?`), true);
});

test("fetchLiveResearchOverview: the actual fetch call carries the staged forecast contract", async () => {
  let capturedUrl: string | null = null;
  const fetchImpl = (async (input: RequestInfo | URL) => {
    capturedUrl = String(input);
    return jsonResponse(200, liveBody());
  }) as typeof fetch;

  await fetchLiveResearchOverview("MSFT", new AbortController().signal, { fetchImpl });

  assert.ok(capturedUrl);
  const params = new URLSearchParams((capturedUrl as string).split("?")[1]);
  assert.equal(params.get("forecast_mode"), "maturation");
  assert.equal(params.get("terminal_growth_rate"), "0.025");
});

// --- success / error / loading derivation ---------------------------------

test("fetchLiveResearchOverview: ok response maps to a 'success' result carrying the adapted view model", async () => {
  const fetchImpl = (async () => jsonResponse(200, liveBody({ ticker: "MSFT" }))) as typeof fetch;
  const result = await fetchLiveResearchOverview("MSFT", new AbortController().signal, {
    fetchImpl,
    now: () => new Date("2026-09-14T12:00:00Z"),
  });
  assert.equal(result.status, "success");
  if (result.status === "success") {
    assert.equal(result.viewModel.mode, "live");
    assert.equal(result.viewModel.ticker, "MSFT");
  }
});

test("fetchLiveResearchOverview: a non-ok response normalizes through valuationErrorFromResponse", async () => {
  const fetchImpl = (async () => jsonResponse(503, { code: "VALUATION_BACKEND_UNREACHABLE" })) as typeof fetch;
  const result = await fetchLiveResearchOverview("MSFT", new AbortController().signal, { fetchImpl });
  assert.equal(result.status, "error");
  if (result.status === "error") {
    assert.equal(result.error.kind, "unavailable");
  }
});

test("fetchLiveResearchOverview: a 422 input error is classified as 'input', not 'request'", async () => {
  const fetchImpl = (async () => jsonResponse(422, { detail: "Unrecognized ticker." })) as typeof fetch;
  const result = await fetchLiveResearchOverview("ZZZZ", new AbortController().signal, { fetchImpl });
  assert.equal(result.status, "error");
  if (result.status === "error") {
    assert.equal(result.error.kind, "input");
    assert.equal(result.error.message, "Unrecognized ticker.");
  }
});

test("fetchLiveResearchOverview: a thrown network error (not an HTTP response) falls back to 'unavailable'", async () => {
  const fetchImpl = (async () => {
    throw new TypeError("Failed to fetch");
  }) as typeof fetch;
  const result = await fetchLiveResearchOverview("MSFT", new AbortController().signal, { fetchImpl });
  assert.equal(result.status, "error");
  if (result.status === "error") {
    assert.equal(result.error.kind, "unavailable");
  }
});

// --- stale-request / request-identity protection ---------------------------

test("fetchLiveResearchOverview: a signal already aborted before the fetch settles yields 'aborted', never 'success'", async () => {
  const controller = new AbortController();
  const fetchImpl = (async () => {
    // Simulate a slow response that resolves only after the caller has
    // moved on to a newer ticker and aborted this one.
    controller.abort();
    return jsonResponse(200, liveBody());
  }) as typeof fetch;

  const result = await fetchLiveResearchOverview("MSFT", controller.signal, { fetchImpl });
  assert.equal(result.status, "aborted");
});

test("fetchLiveResearchOverview: an AbortError thrown by fetch itself (real aborted-signal behavior) yields 'aborted', not 'error'", async () => {
  const controller = new AbortController();
  const fetchImpl = (async () => {
    controller.abort();
    const abortError = new DOMException("The operation was aborted.", "AbortError");
    throw abortError;
  }) as typeof fetch;

  const result = await fetchLiveResearchOverview("MSFT", controller.signal, { fetchImpl });
  assert.equal(result.status, "aborted");
});

test("fetchLiveResearchOverview: an aborted signal suppresses even an error response — the stale ticker's failure must not surface", async () => {
  const controller = new AbortController();
  const fetchImpl = (async () => {
    controller.abort();
    throw { kind: "request", message: "should never be shown" };
  }) as typeof fetch;

  const result = await fetchLiveResearchOverview("MSFT", controller.signal, { fetchImpl });
  assert.equal(result.status, "aborted");
});

test("fetchLiveResearchOverview: a NOT-aborted signal reaches 'success' normally (control case for the abort tests above)", async () => {
  const controller = new AbortController();
  const fetchImpl = (async () => jsonResponse(200, liveBody())) as typeof fetch;
  const result = await fetchLiveResearchOverview("MSFT", controller.signal, { fetchImpl });
  assert.equal(result.status, "success");
});

// --- non-AAPL tickers never receive AAPL fixture values, end-to-end --------

test("fetchLiveResearchOverview: a real fetch-and-adapt round trip for a non-AAPL ticker carries no AAPL fixture value", async () => {
  const fetchImpl = (async () => jsonResponse(200, liveBody({ ticker: "MSFT", current_price: 401.23, intrinsic_value_per_share: 388.5 }))) as typeof fetch;
  const result = await fetchLiveResearchOverview("MSFT", new AbortController().signal, { fetchImpl });
  assert.equal(result.status, "success");
  if (result.status === "success") {
    assert.equal(result.viewModel.ticker, "MSFT");
    assert.notEqual(result.viewModel.marketPrice.display, AAPL_RESEARCH_FIXTURE.marketPrice);
    assert.notEqual(result.viewModel.intrinsicValue.display, AAPL_RESEARCH_FIXTURE.intrinsicValue);
    assert.equal(result.viewModel.companyName, null);
  }
});
