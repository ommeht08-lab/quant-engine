/**
 * The default AUTHENTICATED landing destination for this dashboard —
 * where `/` resolves (see `public-route.ts`), where login lands when no
 * safe requested destination was preserved (see `login/actions.ts`),
 * and what the "Overview" nav item and the app logo point to (see
 * `AppHeader.tsx`). One shared constant so these call sites can never
 * drift to different tickers or diverge from each other.
 */

export const DEFAULT_OVERVIEW_TICKER = "MSFT";

export const DEFAULT_OVERVIEW_PATH = `/overview/${DEFAULT_OVERVIEW_TICKER}`;
