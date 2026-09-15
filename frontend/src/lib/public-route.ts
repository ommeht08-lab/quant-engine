import { DEFAULT_OVERVIEW_PATH } from "./default-route.ts";

const PUBLIC_ROUTE_PREFIXES = ["/research", "/methodology"] as const;

// `/` now resolves to the default live overview — itself session-
// protected, same as every other page not listed in
// PUBLIC_ROUTE_PREFIXES below — rather than the curated public
// research case. The curated case remains fully reachable at its own
// direct URL (`/research/aapl`, still covered by PUBLIC_ROUTE_PREFIXES
// below); it just isn't where "/" sends visitors anymore. See
// `default-route.ts` for the shared constant.
export function publicRedirectPath(pathname: string): string | null {
  return pathname === "/" ? DEFAULT_OVERVIEW_PATH : null;
}

export function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
