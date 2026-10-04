---
name: Valuation Engine
description: A dark workspace for auditable company valuation, with a separate archived-research shell.
colors:
  app-canvas: "#101828"
  app-surface: "#1a2232"
  app-rule: "#293141"
  app-rule-strong: "#475467"
  app-ink: "#f2f4f7"
  app-ink-muted: "#b4bdcc"
  app-ink-dim: "#98a2b3"
  action-blue: "#465fff"
  action-blue-hover: "#3641f5"
  chart-blue: "#6172f3"
  link-periwinkle: "#a4b1ff"
  nav-selected-field: "#465fff22"
  on-accent: "#ffffff"
  positive: "#48cfa2"
  caution: "#e2ad62"
  negative: "#ff746d"
  research-canvas: "#070b14"
  research-surface: "#0f1929"
  research-surface-soft: "#141f32"
  research-secondary: "#162a4f"
  research-ink: "#f4f7fc"
  research-ink-muted: "#a8b2c4"
  research-ink-dim: "#748198"
  research-rule: "rgba(158, 177, 207, 0.14)"
  research-rule-strong: "rgba(158, 177, 207, 0.26)"
  research-blue: "#6287ff"
  research-periwinkle: "#91aaff"
  research-selected-field: "rgba(98, 135, 255, 0.14)"
  research-positive: "#48c9aa"
typography:
  headline:
    fontFamily: "Outfit, sans-serif"
    fontSize: "26px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Outfit, sans-serif"
    fontSize: "19px"
    fontWeight: 500
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Outfit, sans-serif"
    fontSize: "14px"
    fontWeight: 400
  supporting:
    fontFamily: "Outfit, sans-serif"
    fontSize: "13px"
    lineHeight: 1.7
  metric:
    fontFamily: "Outfit, sans-serif"
    fontSize: "28px"
    fontWeight: 600
    lineHeight: 1.4
  research-interface:
    fontFamily: "Outfit, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
  research-data:
    fontFamily: "IBM Plex Mono, SFMono-Regular, ui-monospace, Menlo, Consolas, monospace"
rounded:
  chart-bar: "3px"
  legacy-control: "4px"
  selector: "6px"
  control: "8px"
  brand-mark: "9px"
  sidebar-note: "12px"
  panel: "14px"
  pill: "999px"
  research-panel: "13px"
  research-nav: "11px"
spacing:
  micro: "4px"
  xs: "8px"
  sm: "12px"
  md: "16px"
  compact-panel: "20px"
  panel: "24px"
  section: "32px"
components:
  button-primary:
    backgroundColor: "{colors.action-blue}"
    textColor: "{colors.on-accent}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 1.15rem"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.action-blue-hover}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.control}"
  button-secondary:
    backgroundColor: "{colors.app-surface}"
    textColor: "{colors.app-ink-muted}"
    rounded: "{rounded.legacy-control}"
    padding: "0 1.15rem"
    height: "44px"
  input-default:
    backgroundColor: "{colors.app-canvas}"
    textColor: "{colors.app-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
  panel-default:
    backgroundColor: "{colors.app-surface}"
    textColor: "{colors.app-ink}"
    rounded: "{rounded.panel}"
    padding: "24px"
  nav-active:
    backgroundColor: "{colors.nav-selected-field}"
    textColor: "{colors.link-periwinkle}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
    height: "44px"
  scenario-active:
    backgroundColor: "{colors.app-rule}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.selector}"
    padding: "8px 16px"
    height: "44px"
  research-panel:
    backgroundColor: "{colors.research-surface}"
    textColor: "{colors.research-ink}"
    rounded: "{rounded.research-panel}"
---

# Design System: Valuation Engine

## Overview

**Creative North Star: "The Dark Research Workspace"**

The live application uses the user-approved dark preview direction recorded in `frontend/design-preview` in the primary checkout. Deep navy chrome, blue-gray panels, Outfit typography, and saturated blue actions support a clear route index and spacious analytical groups. The preview defines visual direction; live company values come from the valuation service.

**Scope:** the app system applies where `AppHeader` renders `[data-app-chrome]`. `frontend/src/app/dark-workspace.css`, imported after `globals.css`, supplies the scoped overrides; `AppHeader.module.css` owns navigation. The live workspace comprises `/workspace`, `/workspace/valuation`, `/workspace/cash-flows`, `/workspace/projections`, `/workspace/evidence`, and `/workspace/assumptions`. Operator pages receive the shared chrome and generic scoped tokens while retaining their existing internal layouts.

`AppHeader` is absent from `/research/*`, `/methodology/*`, and `/login`. The archived MSFT study and methodology retain the independent research shell in `FlagshipResearchPrototype.module.css`, its `--rp-*` tokens, navigation, and bundled evidence conventions. Research-prefixed frontmatter tokens describe that protected system. They are not replacements for app tokens. The older unscoped foundations in `globals.css` also remain intact; its earlier 220px/64px app offsets are superseded only on app-chrome routes.

**Key Characteristics:**

- A fixed 290px sidebar and 77px top bar meet the viewport edges.
- Panels use 14px corners, one-pixel rules, 24px padding, and no resting shadow.
- Outfit carries app headings, copy, navigation, controls, and tabular financial figures.
- Blue primary actions, lighter periwinkle links and focus, and blue chart totals have distinct roles.
- At 820px and below, a native modal navigation drawer replaces the sidebar; the top bar remains 77px.
- Model provenance, cautions, scenario validity, historical/custom assumptions, and base-case cash-flow labels remain visible.

## Colors

The app palette uses deep navy and blue-gray layers with clear text hierarchy. The frontmatter owns exact values; descriptive roles below apply only within the stated scope.

### Primary

- **Action Blue:** primary commands and the brand mark. White text remains on the darker action fill; hover uses `action-blue-hover`.
- **Link Periwinkle:** links, active navigation text, range accents, and keyboard focus.
- **Selected Navigation Field:** the translucent blue fill behind the current route.

### Supporting series

- **Chart Blue:** reported-history bars, projected FCFF bars, bridge totals, and the intrinsic-estimate comparison bar. A visible zero baseline supports both positive and negative financial amounts.
- **Positive Green / Signal Red:** signed cash-flow bridge contributions and genuine favorable/unfavorable meaning. Labels and values accompany color.
- **Caution Amber:** analytical limitations and diagnostic states.

### Neutral

- **App Canvas:** page background, sidebar, top bar, input fields, and segmented-control tray.
- **App Surface:** cards, analytical panels, and the sidebar context note.
- **App Rule / Strong Rule:** structural dividers and stronger field borders. Selected scenario buttons use the normal rule as a solid fill.
- **App Ink / Muted Ink / Dim Ink:** primary text and figures; supporting copy and labels; placeholders and quiet metadata. The observed market-price comparison bar uses dim ink.

### Independent research palette

The protected research shell uses `research-canvas` and `research-surface`, softer blue-gray fields, translucent rules, `research-blue` accents, and `research-periwinkle` selections. Its `research-positive` value is separate from the live app semantic green. Preserve the module's `--rp-*` assignments rather than inheriting app-chrome overrides.

**The Semantic Restraint Rule.** Use green, amber, and red only for actual outcomes, signed contributions, cautions, connectivity, or errors. Scenario names are not outcome colors.

## Typography

**App Interface Font:** Outfit, loaded through `next/font/google`; app overrides use `var(--font-outfit), sans-serif` for body, display, and utility roles.

**Independent Research Fonts:** Outfit for the research interface and IBM Plex Mono through the unscoped utility stack for research figures. Instrument Sans and IBM Plex Sans remain loaded for unscoped existing surfaces.

### Hierarchy

- **Headline:** app page titles use 26px, weight 600, 1.3 leading, and -0.025em tracking; they become 24px at 560px and below.
- **Title:** model panel headings use 19px, weight 500, and -0.01em tracking. Shared section titles use 18px, weight 500, and normal tracking.
- **Body:** app base copy and navigation use 14px. Page decks use 1.7 leading and a maximum measure of 72ch.
- **Supporting:** analytical explanations and context use 13px with comfortable leading. Model labels use sentence case and normal tracking; the sidebar group label is uppercase at 12px.
- **Metric:** headline figures use 28px, weight 600, and 1.4 leading; they reduce to 25px below 820px and 24px below 560px.

**The Figure-as-Evidence Rule.** Preserve tabular numerals on monetary values, percentages, equations, and chart axes. The app uses Outfit figures; the independent research shell retains its existing mono treatment.

**The One-Title Rule.** Give each analytical section one useful heading; avoid an ornamental label that repeats it.

## Layout

The desktop app has a fixed 290px sidebar, fixed 77px top bar, and matching page offsets. Content is centered within a maximum 1440px container with 24px side gutters. The page header has 24px vertical padding. Workspace panels and results are separated by 24px; larger context groups use 32px gaps. App chrome remains edge-to-edge.

The start panel pairs inputs and context in a `1.5fr / 1fr` grid with a 220px minimum context column and 32px gap. At 1100px and below it stacks. Summary metrics use four columns, falling to two at 1100px. Custom-assumption fields remain one column until 1440px, where they become two. At 560px and below, panel padding becomes 20px, next-step links stack, and evidence labels sit above their values.

At 820px and below the sidebar is hidden, the top bar spans the viewport, and content has 16px side gutters. The menu opens a native `<dialog>` with a 340px drawer capped at viewport width. Escape, close, backdrop click, route change, and resizing to desktop close it; closing returns focus to the menu trigger. This application uses the drawer rather than the retired mobile bottom navigation.

Charts have a 660px minimum plotted width inside a keyboard-focusable local scroll region. The historical chart region has an accessible metric-specific label and region role; its full-amount history table has an 800px minimum width inside local table scrolling. At 560px and below, a visible scroll hint explains how to inspect the complete chart. Projection tables also scroll locally. The supported page minimum is 320px; content must not cause page-level horizontal overflow.

The workspace layout keeps one client instance across its six URLs. The latest completed result, reported financial history, available daily market history, run timestamp, selected scenario, and completed-run assumptions persist in versioned browser-local storage. Reloading restores validated saved data and inputs without a network request or model run. Navigation does not trigger a valuation. Overview places run controls and reported financial history before valuation estimates, then links to detailed estimates. The cash-flow page separates reported history, projected Base FCFF, and the projected-component bridge; dedicated pages expose valuation comparisons and sensitivity, projections, provenance, and assumptions.

The independent research shell retains its 15.5rem rail and its own 1040px/760px responsive rules. Its study navigation and scenario query state remain separate from the live workspace.

**The Scope Rule.** Apply app tokens only to app-chrome surfaces. Preserve the research shell, methodology, login, archived data, and evidence conventions as separate scopes.

## Elevation & Depth

App panels rely on tonal contrast and one-pixel borders; they have no resting shadow. The modal drawer uses a dark `#0009` backdrop to isolate navigation. Existing market-chart inspection tooltips retain their temporary `0 14px 34px rgba(0,0,0,.42)` shadow. Input focus may retain the inherited three-pixel selection ring, while keyboard focus uses a crisp 2px periwinkle outline with 3px offset on app controls and navigation.

Controls retain their short 120ms state transitions; navigation uses 140ms ease. Reduced-motion rules suppress animation and transitions. The independent research module retains its own 180ms state transitions and brand-mark shadow; do not remove these as a consequence of the app's flat-panel rule.

**The Flat-by-Default Rule.** Resting app panels use a border and tonal step. Shadows belong to transient overlays; the independent research shell retains its existing treatment.

## Shapes

App panels use 14px corners. Primary buttons, input fields, navigation rows, and the scenario tray use 8px; selected scenario buttons use 6px; plotted bars use 3px. The brand mark uses 9px and the sidebar note 12px. The avatar is circular. Legacy secondary buttons and diagnostic fields still retain their existing 4px corners, rather than a newly invented universal radius.

The independent research shell retains 13px panel corners, 11px route rows, its existing small controls, and compact pill badges. These values belong to that shell and do not establish app defaults.

## Components

### Buttons / Fields

Primary commands use action blue, white text, 8px corners, a 44px minimum height, and weight 500. Hover deepens the blue; press retains the inherited one-pixel movement. Disabled commands retain their location, dim copy, and inactive fill. Secondary actions retain the existing 4px shape, surface fill, strong rule, and muted copy, gaining periwinkle border and text on hover.

Inputs use canvas fill, strong rules, 8px corners, primary ink, and dim placeholders. Numeric slider inputs retain their compact 88px width and 36px minimum height; range tracks are visible at 6px with a periwinkle accent. Historical mode derives growth and margin from company data; Custom mode sends explicit overrides. Terminal growth applies to every run.

### Navigation

The sidebar has 44px icon-and-label rows with 8px corners. Inline stroke SVGs share a consistent weight. Hover adds the surface field and white text; exact current-route selection uses a translucent blue field and periwinkle text with `aria-current="page"`.

The route index includes workspace Overview, Valuation, Cash flow forecast, Projection detail, Evidence & sources, and Model assumptions. Portfolio, Backtests, and Trade log are absent from this workspace navigation; their physical routes and private-data protection remain. Overview here names `/workspace`; it does not restore the retired `/overview` intermediary dashboard. Archived MSFT study links are absent from workspace navigation, footer and Evidence; direct archived URLs remain preserved. The brand opens `/workspace`.

The top bar contains company search and operator identity. The responsive drawer uses the same links and native modal focus behavior. The loopback development preview opens page shells without a password when explicitly enabled and blocks private APIs before their handlers. Production operator routes retain session protection. There is no Private session link in the sidebar.

### Cards / Scenario Controls

Analytical cards use the standard 14px panel, 24px internal padding, normal rule, and primary ink. Headers can wrap their secondary controls. Base, Bear, and Bull share one selection between the summary and Valuation Spectrum. The summary selector uses a canvas tray and 44px buttons; selection is white text on the solid app-rule field. Existing spectrum controls retain their own selected treatment. Labels communicate the cases without semantic outcome coloring.

### Reported Financial History

The standard flat analytical panel appears on Overview and Cash flow forecast. Its heading, selected-source name, statement currency when supplied, and compact-amount convention establish what is being inspected. If currency is unavailable, say so rather than infer it from market currency. A labeled 44px-minimum-height dropdown selects Revenue, Operating cash flow, Cash CapEx, or Historical FCF.

Bars use chart blue, a common zero baseline, provider-supplied fiscal period-end dates, and tabular figures. The keyboard-focusable, labeled chart region scrolls locally. Missing cells remain unavailable and render N/A or Not reported; zero remains a numerical value. If the selected metric has no reported values, explain the absence and retain the metric control and full-amount table. The table includes every supported metric, row and column headers, and a caption identifying reported inputs and calculated cash FCF.

The data comes from the same selected SEC or Yahoo statement frames used by valuation. Yahoo periods are annual; SEC periods are trailing twelve months, explicitly labeled as overlapping periods that must not be summed. Historical cash FCF is calculated as operating cash flow minus cash CapEx; both inputs must be available. Cash CapEx preserves signed spending semantics, including negative spending for a source cash inflow. Keep the definition and its distinction from projected unlevered FCFF visible. Missing history is an explicit empty state, never filled with projections.

### Cash-Flow Evidence

Annual bars show the service's base-case projected FCFF by year, including negative values below zero. They are not a revenue line, observed market history, or cash flow for the currently selected Bear/Bull scenario. The base-case label remains explicit.

The bridge exposes the selected base forecast year's projected components, with the subtitle Projected components: NOPAT + D&A − CapEx − Δ NWC = FCF. Totals use chart blue; signed contributions use green/red, with values and an equation for inspection. If reconciliation is unavailable, show the warning and preserve the projection table. Keep base cash-flow data and all model calculations unchanged.

### Valuation / Evidence / States

Market-price and intrinsic-estimate bars share a zero baseline, show exact per-share values, and label the selected case. A quality caution, invalid scenario, unavailable price, or negative modeled equity withholds the comparison and explains why. The spectrum, sensitivity matrix, sector context, and sourced daily market-history chart remain available on the valuation page with their existing semantics.

Saved-result context shows the original run time and an explicit warning that prices and statements have not been refreshed. Market values are labeled Market price at run. Clear saved result removes browser-local result data and returns to Overview; cancellation guards prevent an earlier pending response from restoring cleared data. If browser storage is unavailable, disclose that a refresh will lose the result. Invalid nested saved-result data is not rendered.

Source, selection reason, statement period, knowledge cutoff, policy version, and ingestion batches are visible on the evidence page. Provenance does not assert independently reconciled issuer lines. Empty sessions offer a path to run the model; loading, failed refresh, WACC bounds, distress diagnostics, and quality cautions retain explicit copy. An old result remains labeled when a refresh fails. Preview examples never populate live application results.

## Do's and Don'ts

### Do:

- **Do** use the scoped app palette, Outfit typography, 290px sidebar, 77px top bar, 14px panels, and 8px primary controls.
- **Do** preserve the mobile drawer, keyboard focus, labeled local scrolling, and page usability through 320px.
- **Do** keep tabular figures, provenance, quality cautions, scenario validity, and base-case forecast labels visible.
- **Do** keep model execution explicit, restore validated browser-local results without fetching, and show saved-result age and market price at run.
- **Do** preserve independent research tokens, archived-study disclosures, methodology, and the public-model/private-data boundary.

### Don't:

- **Don't** reinstate the old 220px/64px app shell or mobile bottom navigation as the current app system.
- **Don't** apply app-chrome overrides to the independent research shell or mistake archived study figures for live valuation results.
- **Don't** fabricate values, market observations, issuer reconciliation, or chart drama to fill an empty state.
- **Don't** color scenario names as outcomes, hide cautions, or plot comparisons withheld by valuation quality.
- **Don't** change model calculations, base cash flows, sources, authentication, backend contracts, or archives to fit the visual system.
- **Don't** add resting panel shadows, decorative glow, or redundant title kickers to the app workspace.
