import { DEFAULT_APP_PATH } from "./default-route.ts";

// The valuation model is a public project surface. Operator telemetry,
// trading controls, portfolio data, and their API routes remain private.
const PUBLIC_ROUTE_PREFIXES = [
  "/workspace",
  "/overview",
  "/api/evaluate",
  "/research",
  "/methodology",
] as const;

// `/` resolves to the public valuation workspace. The curated research
// case remains available at its direct URL.
export function publicRedirectPath(pathname: string): string | null {
  return pathname === "/" ? DEFAULT_APP_PATH : null;
}

export function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
