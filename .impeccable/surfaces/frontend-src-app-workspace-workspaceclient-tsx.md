---
version: 1
slug: "frontend-src-app-workspace-workspaceclient-tsx"
primary_target: "frontend/src/app/workspace/WorkspaceClient.tsx"
related_targets: ["frontend/src/app/globals.css","frontend/src/components/valuation/ThesisRail.tsx","frontend/src/components/valuation/ForecastChart.tsx"]
---

# Valuation workspace

Mode: Operate

Audience: the private operator building and reviewing a company DCF. Job: choose a ticker, set or accept assumptions, run the model, compare Bear/Base/Bull outcomes, inspect sensitivity and projected cash flow, and understand source freshness. Primary action: run valuation. Constraints: preserve every existing calculation, API contract, scenario linkage, diagnostic state, and financial distinction.

## Direction contract

THESIS: The workspace is an analyst's decision surface: price, scenario value, drivers, and evidence stay in one visual field. It refuses both the long document of equally weighted sections and the decorative trading terminal.

OWN-WORLD: The same near-black graphite financial shell as the research home, with cool-white data ink, blue-gray raised surfaces, restrained periwinkle controls and selection fields, fine structural borders, sharp 4–5px radii, and one persistent Thesis Rail. It is institutional charting software, not a neon crypto terminal.

STORY: The operator selects the company, verifies the operating assumptions, runs the model, cross-checks market price against the three cases, then drills into forecast, sensitivity, sector-relative context, and cash-flow evidence.

FIRST VIEWPORT: The command and assumption strip sit above a two-column analysis field. A dominant dual-line annual forecast occupies the left with labeled axes, quiet grid, exact latest values, and inspection crosshair; a 320px sticky Thesis Rail on the right keeps market price, selected intrinsic value, delta, WACC, terminal growth, provenance, and the shared scenario selector visible. Supporting analysis continues below without changing visual grammar.

FORM: User-pinned TailAdmin V1/V2 and NextAdmin information architecture, revised by the user's explicit black-theme and traditional-stock-chart direction. Seed b8aa9a83. Earlier light generated mocks are superseded critique references, not user-approved pixel specifications.

FINISH: unreviewed and undocumented is unfinished; complete responsive visual QA, an independent finish review, and DESIGN.md.
