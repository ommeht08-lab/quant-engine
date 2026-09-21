---
name: Valuation Engine
description: An institutional graphite workspace for auditable intrinsic-value research.
colors:
  graphite-canvas: "#080b10"
  graphite-chrome: "#090c11"
  graphite-surface: "#0f141c"
  graphite-instrument: "#0c1118"
  graphite-raised: "#121923"
  graphite-ledger: "#151c27"
  rule: "#202938"
  rule-strong: "#303c50"
  ink-cool: "#e8edf6"
  ink-strong: "#edf2f9"
  ink-muted: "#a1adbf"
  ink-dim: "#758196"
  periwinkle: "#8292ff"
  periwinkle-hover: "#9aa7ff"
  periwinkle-soft: "rgba(130,146,255,.13)"
  cash-flow-cyan: "#53b7dc"
  positive: "#48cfa2"
  caution: "#e2ad62"
  negative: "#ff746d"
  on-accent: "#ffffff"
typography:
  flagship:
    fontFamily: "Outfit, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "clamp(1.75rem, 2.7vw, 2.5rem)"
    fontWeight: 600
    lineHeight: 1.05
    letterSpacing: "-0.04em"
  display:
    fontFamily: "Instrument Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "clamp(1.2rem, 1.6vw, 1.55rem)"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.03em"
  body:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.8rem"
    fontWeight: 400
    lineHeight: 1.5
  data:
    fontFamily: "IBM Plex Mono, SFMono-Regular, ui-monospace, Menlo, Consolas, monospace"
    fontSize: "0.82rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.04em"
  label:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.625rem"
    fontWeight: 650
    lineHeight: 1
    letterSpacing: "0.07em"
rounded:
  micro: "3px"
  control: "4px"
  panel: "5px"
  selector: "6px"
  pill: "999px"
spacing:
  micro: "0.25rem"
  xs: "0.5rem"
  sm: "0.75rem"
  md: "1rem"
  panel: "1.125rem"
  lg: "1.5rem"
  shell: "1.75rem"
components:
  button-primary:
    backgroundColor: "{colors.periwinkle}"
    textColor: "{colors.on-accent}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 1.15rem"
    height: "2.75rem"
  button-primary-hover:
    backgroundColor: "{colors.periwinkle-hover}"
    textColor: "{colors.on-accent}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 1.15rem"
    height: "2.75rem"
  button-secondary:
    backgroundColor: "{colors.graphite-surface}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 1.15rem"
    height: "2.75rem"
  input-default:
    backgroundColor: "{colors.graphite-surface}"
    textColor: "{colors.ink-strong}"
    typography: "{typography.data}"
    rounded: "{rounded.control}"
    padding: "0 1rem"
    height: "2.75rem"
  panel-default:
    backgroundColor: "{colors.graphite-surface}"
    textColor: "{colors.ink-cool}"
    rounded: "{rounded.panel}"
    padding: "1.125rem"
  instrument-panel:
    backgroundColor: "{colors.graphite-instrument}"
    textColor: "{colors.ink-strong}"
    rounded: "{rounded.panel}"
    padding: "1.1rem"
  nav-active:
    backgroundColor: "{colors.periwinkle-soft}"
    textColor: "{colors.periwinkle}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 11px"
    height: "42px"
---

# Design System: Valuation Engine

## Overview

**Creative North Star: "The Graphite Research Instrument"**

Valuation Engine is an edge-to-edge institutional charting workspace: near-black chrome and canvas hold dense blue-gray surfaces, cool-white data, and a single restrained periwinkle interaction voice. It should feel like purpose-built equity-research software for one serious operator, not a public market-data terminal, a decorative admin template, or a neon trading product.

TailAdmin V1/V2 and NextAdmin inform the information architecture only: persistent navigation, compact controls, clear grouping, and decision-first scanning. The visual world is darker, sharper, and flatter. Fine rules, tonal layering, tabular figures, and exact state feedback carry the hierarchy; ornament does not. Source visibility and honest financial distinctions remain part of the interface's visual discipline.

Authenticated entry is deliberately direct: root visits, sign-ins without a preserved destination, the brand link, and old `/overview` bookmarks all open the valuation workspace. The former intermediary research-home dashboard is retired, keeping the operator's primary task one step closer without changing authentication or API protection.

**Key Characteristics:**

- A near-black 220px desktop rail and 64px utility bar meet the canvas edge-to-edge; the rail holds four task routes and no session footer.
- Blue-gray panels use fine borders, 4–5px structural corners, and no resting shadow.
- Cool-white data ink and restrained supporting copy make dense research legible without becoming loud.
- Periwinkle identifies actions, focus, active navigation, selected scenarios, and the primary forecast series.
- Green, amber, and red appear only for genuine favorable, cautionary, and unfavorable meaning.
- Annual revenue and free cash flow appear as honest line series with separate labeled axes, a restrained grid, and an inspection crosshair.

## Colors

The palette is a compact graphite stack with cool-white ink, one periwinkle interaction accent, one supporting chart series, and tightly governed semantic color.

### Primary

- **Operator Periwinkle:** the only non-semantic interaction voice. Use it for the primary action, keyboard focus, active routes, selected scenarios, and the annual revenue series.
- **Periwinkle Field:** a low-opacity selection field behind active or pressed controls. It supports the accent without turning whole panels blue.

### Secondary

- **Cash-Flow Cyan:** the supporting annual free-cash-flow series. It exists to distinguish the second scale from revenue and must not become a general-purpose action color.

### Tertiary

- **Verified Green:** genuine favorable outcomes, connected status, and positive market/model deltas only.
- **Caution Amber:** model qualifications and interpretation cautions only.
- **Signal Red:** genuine unfavorable outcomes, errors, and negative market/model deltas only.

### Neutral

- **Graphite Canvas:** the edge-to-edge application field and page overscroll color.
- **Graphite Chrome:** the persistent navigation and utility chrome, set just apart from the canvas by a fine rule.
- **Graphite Surface:** the standard panel, card, control, and grouped-content surface.
- **Instrument Black:** the analytical surface for the forecast, valuation spectrum, and Thesis Rail.
- **Raised Graphite and Ledger:** quiet nested fields, inactive control groups, and provenance blocks.
- **Cool White Ink:** primary text and figures; use the strong variant inside analytical instruments.
- **Muted and Dim Ink:** secondary explanation, labels, timestamps, axes, and provenance.
- **Structural Rules:** the normal and strong blue-gray borders that establish hierarchy without elevation.

**The One Interaction Voice Rule.** Periwinkle owns action and selection. Cash-flow cyan belongs only to its chart series; semantic colors never substitute for active state.

**The Semantic Restraint Rule.** Green, amber, and red must correspond to actual model, market, connectivity, caution, or error meaning. Never use them to decorate neutral categories.

## Typography

**Flagship Interface Font:** Outfit, with the system sans stack as fallback.
**Display Font:** Instrument Sans, with the system sans stack as fallback.
**Body Font:** IBM Plex Sans, with the system sans stack as fallback.
**Data Font:** IBM Plex Mono, with the system mono stack as fallback.

**Character:** The system is compact, open, and deliberate rather than retro-terminal. Outfit gives the company dashboard calm geometric authority; Instrument Sans carries workspace and shell headings; IBM Plex Sans carries operational copy; IBM Plex Mono is reserved for figures that benefit from fixed-width comparison.

### Hierarchy

- **Flagship:** the company overview title and its major section headings use Outfit at medium-to-semibold weight with tight tracking.
- **Display:** workspace, shell, panel, and instrument headings use Instrument Sans at 600–650 weight and compact leading.
- **Body:** navigation, explanatory copy, state messages, and ordinary control labels use IBM Plex Sans at dense dashboard sizes with clear line-height.
- **Data:** tickers, money, rates, percentages, and compact audit figures use IBM Plex Mono with tabular numerals.
- **Label:** small uppercase labels use the body face at 600–650 weight with measured tracking. They identify fields and groups, but never repeat a nearby title as an ornamental kicker.

**The Figure-as-Evidence Rule.** Use mono or explicitly tabular numerals where comparison matters; do not turn all prose and navigation into faux-terminal typography.

**The One-Title Rule.** A section gets one clear heading. Do not stack an eyebrow or kicker above a title when both say the same thing.

## Layout

The authenticated desktop shell uses a fixed 220px rail and a fixed 64px utility bar. It enters directly into the valuation workspace: authenticated root visits, successful sign-ins without a preserved destination, the brand link, and `/overview` all resolve to `/workspace`. The retired research-home summary dashboard is not part of the active information architecture. The detailed company overview can still widen to 1480px for denser analysis. The chrome and canvas remain edge-to-edge—there is no rounded page sheet around the application.

The workspace places command and assumptions before a dominant analysis field. Its annual forecast pairs with a 320px sticky Thesis Rail, narrows the rail to 270px at intermediate widths, and stacks into one column at 900px and below.

At 820px the desktop rail yields to a 60px top bar and fixed four-item bottom navigation. Desktop and mobile expose the same four routes—Valuation, Portfolio, Backtests, and Trades—with four equal mobile columns. Side gutters contract to 14px, then to 10px where the company overview requires it. Metrics collapse progressively while keeping the same reading order. Every authenticated page must remain free of page-level horizontal overflow at the 320px minimum width; locally scrollable data regions must advertise that behavior.

**The Decision-First Rule.** Keep the next action, selected scenario, primary value, and supporting evidence in the first operating field. Supporting schedules may stack or move behind tabs, but they do not outrank the decision surface.

**The Edge-to-Edge Shell Rule.** Persistent chrome meets the viewport edges. Do not wrap the product in an artificial white sheet, floating desktop frame, or oversized outer radius.

**The Direct-to-Work Rule.** The authenticated default is the valuation workspace. Do not place a summary dashboard or Overview route between entry and the operator's primary task.

## Elevation & Depth

Depth comes from graphite tone changes and one-pixel structural rules. Standard panels, cards, navigation, inputs, and analytical instruments have no resting shadow. A compact shadow is permitted only for a transient forecast tooltip; focus uses a visible outline or periwinkle ring, not ambient glow. State changes may shift borders and backgrounds, but surfaces never float by default.

### Shadow Vocabulary

- **Forecast Tooltip:** a compact, high-contrast shadow may separate the temporary inspection overlay from the chart beneath it.

**The Flat-by-Default Rule.** A border and a tonal step define every resting surface. Shadows are transient context, never the page's structural grammar.

## Shapes

The form language is sharp and structural. Inputs, buttons, tabs, alert fields, and nested blocks use 3–4px corners; panels, charts, navigation marks, and major containers use 5px corners. A 6px radius is allowed for compact scenario controls. Fully rounded geometry is reserved for true status tags, dots, and narrow badges, never for panels or primary controls.

Borders are one pixel and blue-gray. Line samples in chart legends stay square. Graphs do not use faux candlestick silhouettes, decorative columns, or softened area fills to imply data the model does not possess.

**The Structural Corner Rule.** Default to 4–5px. Use a pill only when the element is literally a compact tag or status indicator.

## Components

### Buttons

**Character:** compact, direct, and visibly stateful without glow.

- **Shape:** 4px corners and a 44px minimum height for primary, secondary, segmented, and scenario actions.
- **Primary:** periwinkle with white text; hover moves to the lighter periwinkle and press translates by one pixel.
- **Secondary:** graphite surface, strong structural rule, and muted text; hover gains a periwinkle border and text.
- **Focus:** a crisp 2px periwinkle outline with offset. Disabled actions fall back to ledger fill and dim text.

### Inputs / Fields

**Character:** dark ledger fields embedded in the work, not floating search capsules.

- **Style:** graphite surface, strong one-pixel border, 4px corners, and cool-white content. Ticker and numerical inputs use the data face.
- **Focus:** periwinkle border plus a restrained three-pixel field ring.
- **Disabled:** ledger fill, dim text, and a standard rule; retain the control's location and label.

### Navigation

**Character:** a persistent route index with quiet default states and exact selection.

- **Desktop:** a 220px near-black rail uses 42px icon-and-label rows for exactly four routes: Valuation, Portfolio, Backtests, and Trades. Hover adds a subtle blue-gray field and border; the active route uses a restrained periwinkle field, border, icon, and label. The brand opens Valuation directly.
- **Mobile:** the same four routes move to four equal columns in a fixed near-black bottom bar with 50px targets. The utility bar remains fixed above the content.
- **Rail ending:** the task-route list is the end of the desktop rail. Do not reserve a sidebar footer for paper-environment status or sign-out controls.
- **Status:** a small flat semantic dot may communicate connectivity; it never receives a halo or glow.

### Cards / Containers

**Character:** flat operational modules that read as one system rather than a stack of promotional cards.

- **Corner Style:** 5px structural corners.
- **Background:** standard modules use graphite surface; analytical instruments use instrument black; nested provenance uses raised graphite.
- **Border:** a fine blue-gray rule is always the primary separator.
- **Shadow Strategy:** none at rest.
- **Internal Padding:** generally 16–20px, reduced only where dense readouts require it.

### Scenario & Mode Selectors

**Character:** explicit shared-state controls with a single selected voice.

- **Unselected:** raised graphite, structural border, and muted copy.
- **Selected:** periwinkle border and text on a low-opacity periwinkle field.
- **Behavior:** Bear, Base, and Bull retain a 44px target and the same shared selection across the Valuation Spectrum and Thesis Rail. Their names do not receive red, neutral, or green outcome colors.

### Forecast & Thesis Field

**Character:** one paired analytical instrument, not two competing dashboard cards.

- **Forecast:** annual Revenue and Free Cash Flow are two un-smoothed 2px line series with no point markers at rest. Revenue uses the left labeled axis; free cash flow uses the right. A restrained dashed grid, latest-value legend, dashed inspection crosshair, and exact tooltip expose the real five-year path.
- **Thesis Rail:** a 320px sticky companion holds price, selected intrinsic value, delta, WACC, terminal growth, provenance, and the shared scenario selector. It stacks below or above the analysis according to the preserved reading order on narrower screens.
- **Truthfulness:** never substitute candlesticks, OHLC marks, volume bars, fabricated intraday history, or interpolated curve drama for annual model data.

## Do's and Don'ts

### Do:

- **Do** preserve the near-black edge-to-edge shell, blue-gray surfaces, cool-white ink, fine rules, and 4–5px structural corners.
- **Do** use periwinkle consistently for action, focus, active navigation, selected scenarios, and the Revenue line.
- **Do** keep financial values tabular, source and assumption provenance visible, and absolute valuation distinct from sector-relative context.
- **Do** show Revenue and Free Cash Flow as honest annual dual-axis lines with a restrained grid and inspection crosshair.
- **Do** preserve 44px primary touch targets, fixed mobile navigation, and usability without page-level overflow at 320px.
- **Do** open authenticated entry, fallback sign-in, the brand link, and old `/overview` bookmarks in the valuation workspace; keep Valuation, Portfolio, Backtests, and Trades as the four primary routes on desktop and mobile.

### Don't:

- **Don't** reintroduce a light palette because TailAdmin or NextAdmin were used as information-architecture references.
- **Don't** add resting shadows, gradients, glass, blur, glow, neon accents, or rounded floating-card stacks.
- **Don't** add redundant eyebrow or kicker copy above page, panel, or instrument titles.
- **Don't** add halos around status dots or use semantic color as decoration.
- **Don't** color Bear, Base, and Bull as favorable or unfavorable outcomes.
- **Don't** fabricate candlesticks, intraday history, volume bars, or any chart encoding not supported by the annual model data.
- **Don't** hide loading, empty, error, caution, or provenance states to make the workspace look populated.
- **Don't** reintroduce the intermediary Overview dashboard, an Overview navigation item, or the paper-environment/sign-out sidebar footer.
