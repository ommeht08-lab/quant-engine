import { valuationErrorFromResponse, type ValuationRequestError } from "./valuation-errors.ts";
import { defaultInteractiveEvaluationParams } from "./evaluation-request-policy.ts";
import {
  liveEvaluationToResearchOverviewViewModel,
  type LiveEvaluationResponse,
  type ResearchOverviewViewModel,
} from "./research-overview-view-model.ts";

/**
 * The live overview's fetch-and-adapt step, pulled out of
 * `OverviewClient.tsx` so it can be unit-tested directly with Node's
 * built-in `fetch`/`AbortController` rather than only indirectly through
 * a mounted React component (this codebase has no DOM/component-testing
 * dependency, and this task does not add one). The component still owns
 * all React state — this function only decides, from a `Response` (or a
 * thrown error, or an aborted signal), which of the four states the
 * live overview must render.
 */
export type OverviewFetchResult =
  | { status: "success"; viewModel: ResearchOverviewViewModel }
  | { status: "error"; error: ValuationRequestError }
  /** The signal aborted before (or as) this settled — the caller must
   * ignore this result entirely rather than let a stale response
   * overwrite a newer ticker's state. */
  | { status: "aborted" };

export interface OverviewFetchDeps {
  /** Injectable for tests; defaults to the global `fetch`. */
  fetchImpl?: typeof fetch;
  /** Injectable clock for tests; defaults to `() => new Date()`. */
  now?: () => Date;
}

export function overviewEvaluationUrl(ticker: string): string {
  return `/api/evaluate/${encodeURIComponent(ticker)}?${defaultInteractiveEvaluationParams().toString()}`;
}

export async function fetchLiveResearchOverview(
  ticker: string,
  signal: AbortSignal,
  deps: OverviewFetchDeps = {}
): Promise<OverviewFetchResult> {
  const fetchImpl = deps.fetchImpl ?? fetch;
  const now = deps.now ?? (() => new Date());

  try {
    const response = await fetchImpl(overviewEvaluationUrl(ticker), { signal });

    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw valuationErrorFromResponse(response.status, body);
    }

    const data: LiveEvaluationResponse = await response.json();
    if (signal.aborted) return { status: "aborted" };

    const viewModel = liveEvaluationToResearchOverviewViewModel(data, now());
    return { status: "success", viewModel };
  } catch (err) {
    if (signal.aborted) return { status: "aborted" };
    const error: ValuationRequestError =
      err && typeof err === "object" && "kind" in err && "message" in err
        ? (err as ValuationRequestError)
        : { kind: "unavailable", message: "The valuation service did not respond." };
    return { status: "error", error };
  }
}
