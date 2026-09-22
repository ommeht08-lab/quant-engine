# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- Public reviewers, including college-application reviewers, who need to evaluate a real working valuation model from a shared link.
- The private operator, Om, who also uses portfolio, trade, risk, backtest, and paper-trading tools.

## Product Purpose

Valuation Engine publicly demonstrates an auditable, staged intrinsic-value model while keeping the operator's portfolio and trading data private. A successful public visit lets someone open the workspace without an account, value a company, compare Bear/Base/Bull outcomes, and inspect the assumptions, cash flows, diagnostics, and source provenance behind the result.

## Positioning

This is a working equity-research model, not a decorative dashboard or static case study. The public experience exposes the real valuation workflow and its limitations. The private experience adds operator telemetry and controls without making that personal data part of the public portfolio project.

## Operating Context

- The public project may be linked from college applications and other portfolio materials.
- `/` and legacy `/overview` entry points lead directly to the public valuation workspace.
- `/workspace`, company overviews under `/overview/*`, the evaluation API, curated research, and methodology are public.
- Portfolio, trades, risk, backtests, paper-trading telemetry, and their APIs remain session-protected.
- The authenticated area is a single-operator environment.

## Capabilities and Constraints

- Preserve existing calculations, API contracts, scenario linkage, diagnostic states, and paper-trading safeguards.
- Bear, Base, and Bull share one selection across the Thesis Rail and Valuation Spectrum.
- Keep absolute intrinsic value and sector-relative context visibly distinct.
- Never fabricate company, market, portfolio, or model values to populate an empty state.
- Keep source and assumption provenance visible where decisions are summarized.
- The primary market chart uses sourced Yahoo Finance daily closes with visible source and as-of provenance; the annual revenue and free-cash-flow forecast remains in the model-detail section.
- The valuation backend credential stays server-side even though the evaluation surface is public.
- Public valuation requests are capped at 30 requests per client per 15-minute fixed window. Client identifiers are secret-keyed HMAC digests in shared Redis, not raw addresses. Production fails closed when that shared state is unavailable unless `PUBLIC_EVALUATION_RATE_LIMIT_FAIL_OPEN=true` is set as an explicit emergency override.
- Public access to the model must never expose private operator data.
- The interface remains usable without page-level horizontal overflow down to 320px.

## Brand Commitments

- Product name: Valuation Engine.
- Attribution: Om Mehta Equity Research.
- The interface uses an institutional graphite market-workspace direction: near-black navigation and utility chrome, blue-gray raised surfaces, cool-white data ink, restrained periwinkle actions, and semantic green/red only for genuine outcomes.
- TailAdmin V2, TailAdmin V1, and NextAdmin are the permanent visual reference set recorded in [`.impeccable/references/tailadmin-stock-dashboard.md`](.impeccable/references/tailadmin-stock-dashboard.md). They govern information architecture, hierarchy, spacing, density, and analytical composition, while the user's black-theme, sharp-corner, direct-entry, and honest-chart instructions override conflicting source details.
- Copy is direct, restrained, and auditable rather than promotional.

## Evidence on Hand

- A working public valuation workspace and company overview backed by the live evaluation service.
- Private operator routes for portfolio, trades, risk, backtests, and paper-trading telemetry.
- Curated research and methodology pages that explain the model and its limitations.
- No testimonials, admissions endorsements, or performance claims are available; never invent them.

## Product Principles

1. Show the working model, not a marketing shell.
2. Keep the public-model/private-data boundary explicit.
3. Put financial truth ahead of visual drama, including refusing to smooth or manufacture market history.
4. Give visitors a direct path to a valuation result.
5. Preserve auditability, provenance, and honest limitations.

## Accessibility & Inclusion

- Preserve keyboard focus, readable contrast, and 44px minimum interactive targets.
- Support responsive use through 320px without page-level horizontal overflow.
- Do not rely on color alone to communicate model states or outcomes.
