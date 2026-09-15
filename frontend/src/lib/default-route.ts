/**
 * The default AUTHENTICATED landing destination for this dashboard —
 * where `/` resolves (see `public-route.ts`), where login lands when no
 * safe requested destination was preserved (see `login/actions.ts`),
 * and what the "Overview" nav item and the app logo point to (see
 * `AppHeader.tsx`). One shared constant so these call sites can never
 * drift from each other.
 *
 * This is the research HOME page (`frontend/src/app/overview/page.tsx`)
 * — summary cards, recent valuations, market context, and a model
 * explainer — not a specific company. The detailed, ticker-selectable
 * live overview lives at `/overview/{TICKER}` and is reached from the
 * home page's own ticker field or "Open overview" links, never as the
 * shared default landing destination itself (an earlier revision of
 * this dashboard defaulted straight to `/overview/${DEFAULT_OVERVIEW_TICKER}`
 * — see git history — which meant "/" always opened one arbitrarily
 * chosen company rather than an actual home screen).
 */

export const DEFAULT_OVERVIEW_PATH = "/overview";

/**
 * The fallback ticker for the home page's market-context module (news +
 * macro) when no valuation has been run in this browser yet — see
 * `ResearchHomeClient.tsx`. Kept as a named constant (rather than an
 * inline literal) so it stays a single, deliberate choice rather than a
 * string that could silently drift between call sites.
 */
export const DEFAULT_OVERVIEW_TICKER = "MSFT";
