import type { ValuationErrorKind } from "./valuation-errors";

/**
 * The workspace's own presentation-layer state banners — distinct from
 * `valuation-errors.ts`, which only classifies an HTTP failure into a
 * `ValuationRequestError`. This maps that classification (plus the
 * loading/result shape of the page) to exactly what a banner should
 * say, as pure functions, so the "which banner, which headline" logic
 * is unit-testable without rendering `page.tsx`.
 */

export function errorBannerHeadline(kind: ValuationErrorKind): string {
  switch (kind) {
    case "unavailable":
      return "Live valuation is not connected";
    case "input":
      return "Check the ticker or assumptions";
    case "request":
      return "Valuation could not run";
  }
}

/**
 * `"unavailable"` (the backend itself is unreachable/unconfigured) is
 * the one error kind treated as a warning rather than a hard failure —
 * everything else the analyst typed or requested is presumed
 * correctable, so it reads as an error instead.
 */
export function errorBannerTone(kind: ValuationErrorKind): "warning" | "error" {
  return kind === "unavailable" ? "warning" : "error";
}

export type WorkspaceResultState = "empty" | "first-loading" | "stale-updating" | "previous-result" | "ready";

/**
 * Resolves which of the five result-area states the workspace is in —
 * a pure decision table so "what do we show where the result normally
 * goes" has one definition, independent of the surrounding JSX.
 *
 * A failed rerun while a previous result is still on screen must never
 * silently clear that result — `hasResult` stays true and the state
 * resolves to `"previous-result"` rather than `"ready"`, so the UI can
 * mark it clearly as the previous (not current) result alongside the
 * error banner (rendered separately from this state).
 */
export function resolveWorkspaceResultState(params: {
  hasResult: boolean;
  isLoading: boolean;
  hasError: boolean;
}): WorkspaceResultState {
  const { hasResult, isLoading, hasError } = params;
  if (!hasResult && isLoading) return "first-loading";
  if (!hasResult) return "empty";
  if (isLoading) return "stale-updating";
  if (hasError) return "previous-result";
  return "ready";
}
