/**
 * The canonical INTERACTIVE valuation request policy — the staged,
 * five-year maturation forecast, at the dashboard's default terminal
 * growth rate. Every interactive page that calls
 * `/api/evaluate/{ticker}` in its default (no custom assumptions) mode
 * must request this explicitly, rather than relying on the backend's
 * own bare defaults.
 *
 * The backend's own default (`forecast_mode="constant"`, see
 * `src/api/main.py`'s `Query(...)` default and its own comment: "Trading
 * and older callers retain the constant default") exists for the
 * autonomous trader and other non-dashboard callers — it is NOT the
 * policy any interactive page has shown since the staged forecast
 * shipped. A page that omits `forecast_mode` gets the OLDER, flatter
 * policy silently, which can show a materially different valuation for
 * no reason a user would notice. This module exists so that mistake has
 * exactly one place to be wrong, not one per page.
 *
 * Deliberately does not set `capex_mode` (both `/workspace` and this
 * overview leave it at the backend's own "default"/flat-4% policy — no
 * drift exists there to fix) or `revenue_growth_rate`/`operating_margin`
 * (omitting those two is itself the deliberate "use this company's own
 * historical growth/margin" mode, not an oversight — see
 * `src/api/main.py`'s own parameter docstrings).
 */

export const STAGED_FORECAST_MODE = "maturation" as const;

export const DEFAULT_TERMINAL_GROWTH_RATE = 0.025;

/**
 * Query parameters for the canonical interactive policy with NO custom
 * assumptions — used by pages (like `/overview/[ticker]`) that don't
 * offer a growth/margin/terminal-growth override UI at all. A page that
 * DOES offer such overrides (`/workspace`) still uses
 * `STAGED_FORECAST_MODE`/`DEFAULT_TERMINAL_GROWTH_RATE` directly as its
 * own constants/initial state, since it needs to build its query string
 * around user-adjustable values this helper doesn't model.
 */
export function defaultInteractiveEvaluationParams(): URLSearchParams {
  return new URLSearchParams({
    forecast_mode: STAGED_FORECAST_MODE,
    terminal_growth_rate: String(DEFAULT_TERMINAL_GROWTH_RATE),
  });
}
