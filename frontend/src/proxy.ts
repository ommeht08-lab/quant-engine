import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { SESSION_COOKIE_NAME, verifySessionToken } from "@/lib/auth";

/**
 * Route protection for this single-operator dashboard — see
 * `src/lib/auth.ts` for why a shared-passphrase session (rather than a
 * full auth library / user table) is the right scope here.
 *
 * `/login` (the page AND its Server Action, which posts back to the
 * same route) is the only path left open; everything else requires a
 * valid session cookie. API routes get a 401 JSON response — they're
 * called from client-side `fetch()`, not navigated to — page routes get
 * redirected to `/login`.
 *
 * Per the Next.js docs' own caution, Proxy is an optimistic first line
 * of defense, not the only one: each private API route handler
 * independently calls `requireSession()` too (see `src/lib/auth.ts`),
 * so a matcher mistake here can't silently strip protection from a
 * route that directly exposes Alpaca/Postgres data.
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (pathname === "/login") {
    return NextResponse.next();
  }

  let hasValidSession: boolean;
  try {
    hasValidSession = verifySessionToken(request.cookies.get(SESSION_COOKIE_NAME)?.value);
  } catch (error) {
    // SESSION_SECRET missing/misconfigured — a deployment bug, not a bad
    // client token. Fail loudly with an actionable message rather than
    // an opaque framework error page on every single request.
    console.error("Proxy could not verify session — SESSION_SECRET misconfigured:", error);
    return NextResponse.json(
      { error: "This deployment is not configured for authentication (SESSION_SECRET missing)." },
      { status: 500 }
    );
  }

  if (hasValidSession) {
    return NextResponse.next();
  }

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Not authenticated." }, { status: 401 });
  }

  const loginUrl = new URL("/login", request.url);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: [
    /*
     * Every path except:
     * - _next/static, _next/image (Next.js internals)
     * - favicon.ico
     * - Static assets under /public with a file extension (images, etc.)
     */
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
