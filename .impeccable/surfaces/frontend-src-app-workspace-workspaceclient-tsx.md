---
version: 1
slug: "frontend-src-app-workspace-workspaceclient-tsx"
primary_target: "frontend/src/app/workspace/WorkspaceClient.tsx"
related_targets: ["frontend/src/app/globals.css","frontend/src/components/valuation/ThesisRail.tsx","frontend/src/components/valuation/ForecastChart.tsx"]
---

# Valuation workspace

Mode: Operate

Audience: public reviewers evaluating the working model, including college-application reviewers, plus the private operator building and reviewing a company DCF. Job: choose a ticker, set or accept assumptions, run the model, compare Bear/Base/Bull outcomes, inspect sensitivity and projected cash flow, and understand source freshness. Primary action: run valuation. Constraints: the model and company analysis are public, while portfolio, trades, risk, backtests, paper-trading telemetry, and their APIs stay session-protected. Public evaluations are limited to 30 requests per client per 15 minutes; quota and temporary rate-limit-service failures remain honest 429/503 states rather than being visually hidden. Preserve every existing calculation, API contract, scenario linkage, diagnostic state, and financial distinction.

## Direction contract

THESIS: The workspace is an analyst's decision surface: price, scenario value, drivers, and evidence stay in one visual field. It refuses both the long document of equally weighted sections and the decorative trading terminal.

OWN-WORLD: The same near-black graphite financial shell as the research home, with cool-white data ink, blue-gray raised surfaces, restrained periwinkle controls and selection fields, fine structural borders, sharp 4–5px radii, and one persistent Thesis Rail. It is institutional charting software, not a neon crypto terminal.

STORY: A visitor opens the live model without an account, selects a company, verifies the operating assumptions, runs the model, cross-checks market price against the three cases, then drills into forecast, sensitivity, sector-relative context, and cash-flow evidence. Authentication appears only when the operator enters private data surfaces.

FIRST VIEWPORT: The command and assumption strip sit above a two-column analysis field. A dominant sourced one-year daily-close market chart occupies the left with 1M/3M/1Y controls, quiet grid, exact latest value, period change, and inspection crosshair; a 320px sticky Thesis Rail on the right keeps market price, selected intrinsic value, delta, WACC, terminal growth, provenance, and the shared scenario selector visible. The annual operating forecast remains in the Forecast detail below.

FORM: The permanent TailAdmin V2, TailAdmin V1, and NextAdmin image set in `.impeccable/references/tailadmin-stock-dashboard.md` is the composition and craft benchmark, translated through the user's explicit black-theme, sharp-corner, direct-entry, and traditional-stock-chart direction. Seed b8aa9a83. Earlier light generated mocks are superseded critique references, not user-approved pixel specifications.

FINISH: unreviewed and undocumented is unfinished; complete responsive visual QA, an independent finish review, and DESIGN.md.
