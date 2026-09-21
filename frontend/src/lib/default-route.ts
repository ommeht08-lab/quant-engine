/**
 * The default authenticated destination. The valuation workflow is the
 * product's primary job, so root visits, successful logins without a
 * preserved destination, and the brand link all open it directly.
 */
export const DEFAULT_APP_PATH = "/workspace";

/**
 * The fallback ticker for the home page's market-context module (news +
 * macro) when no valuation has been run in this browser yet — see
 * `ResearchHomeClient.tsx`. Kept as a named constant (rather than an
 * inline literal) so it stays a single, deliberate choice rather than a
 * string that could silently drift between call sites.
 */
export const DEFAULT_OVERVIEW_TICKER = "MSFT";
