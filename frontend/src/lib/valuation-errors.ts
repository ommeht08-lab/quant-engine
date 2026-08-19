export type ValuationErrorKind = "input" | "unavailable" | "request";

export interface ValuationRequestError {
  kind: ValuationErrorKind;
  message: string;
}

interface ErrorPayload {
  code?: unknown;
  detail?: unknown;
  error?: unknown;
}

const UNAVAILABLE_CODES = new Set([
  "VALUATION_BACKEND_UNCONFIGURED",
  "VALUATION_BACKEND_UNREACHABLE",
]);

function readableMessage(payload: ErrorPayload | null): string | null {
  if (typeof payload?.detail === "string" && payload.detail.trim()) return payload.detail;
  if (typeof payload?.error === "string" && payload.error.trim()) return payload.error;
  return null;
}

export function valuationErrorFromResponse(
  status: number,
  payload: ErrorPayload | null
): ValuationRequestError {
  const code = typeof payload?.code === "string" ? payload.code : null;
  const message = readableMessage(payload);

  if (status === 502 || status === 503 || (code !== null && UNAVAILABLE_CODES.has(code))) {
    return {
      kind: "unavailable",
      message:
        message ??
        "The live valuation service is unavailable. Portfolio evidence below remains accessible.",
    };
  }

  return {
    kind: status === 400 || status === 422 ? "input" : "request",
    message: message ?? `Valuation request failed (HTTP ${status}).`,
  };
}
