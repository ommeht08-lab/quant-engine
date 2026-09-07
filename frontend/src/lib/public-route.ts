const PUBLIC_ROUTE_PREFIXES = ["/research", "/methodology"] as const;

export const PUBLIC_HOME_PATH = "/research/aapl";

export function publicRedirectPath(pathname: string): string | null {
  return pathname === "/" ? PUBLIC_HOME_PATH : null;
}

export function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
