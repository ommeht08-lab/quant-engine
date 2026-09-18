---
name: Valuation Engine
description: A disciplined, cool-white equity-research console for a private operator.
colors:
  cobalt: "#3448d8"
  cobalt-hover: "#3547d5"
  cobalt-soft: "rgba(70,95,255,.1)"
  cobalt-field: "#eef1ff"
  canvas: "#f6f8fc"
  surface: "#ffffff"
  ledger: "#eef1f6"
  ink: "#101828"
  ink-muted: "#475467"
  ink-dim: "#667085"
  rule: "#e4e7ec"
  rule-strong: "#d0d5dd"
  success: "#027a48"
  success-soft: "#ecfdf3"
  warning: "#b54708"
  warning-soft: "#fff4ed"
  danger: "#d92d20"
  danger-soft: "#fef3f2"
  chart-cash-flow: "#7c8db5"
typography:
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
  utility:
    fontFamily: "IBM Plex Mono, SFMono-Regular, ui-monospace, Menlo, Consolas, monospace"
    fontSize: "0.66rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.06em"
rounded:
  control: "5px"
  input: "7px"
  nav: "8px"
  panel: "11px"
  pill: "999px"
spacing:
  micro: "0.2rem"
  compact: "0.75rem"
  control: "1.15rem"
  panel: "1.1rem"
  shell: "28px"
  grid: "1rem"
components:
  button-primary:
    backgroundColor: "{colors.cobalt}"
    textColor: "{colors.surface}"
    rounded: "{rounded.input}"
    padding: "0 1.15rem"
    height: "2.75rem"
  button-primary-hover:
    backgroundColor: "{colors.cobalt-hover}"
    textColor: "{colors.surface}"
    rounded: "{rounded.input}"
    padding: "0 1.15rem"
    height: "2.75rem"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.input}"
    padding: "0 1.15rem"
    height: "2.75rem"
  input-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.input}"
    padding: "0 1rem"
  card-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "1.1rem"
  nav-active:
    backgroundColor: "{colors.cobalt-field}"
    textColor: "{colors.cobalt}"
    rounded: "{rounded.nav}"
    padding: "0 11px"
    height: "42px"
---

# Design System: Valuation Engine

## Overview

**Creative North Star: "The Disciplined Research Desk"**

Valuation Engine is a compact, cool-white operating console: an operator should see the next decision, the current model state, and its evidence before seeing decoration. Its visual world belongs to the TailAdmin/NextAdmin family—quiet structural rules, dense but breathable labeled zones, navy ink, and a single cobalt interaction voice.

The interface treats market and model numbers as working evidence. Panels are flat white paper on a mineral canvas; the selected scenario, action, focus, and chart revenue line are all connected by cobalt. Positive, warning, and negative colors stay semantic so that case labels never imply a market outcome. The populated workspace makes the forecast and its compact thesis rail read as one cross-check field, without confusing absolute value with sector-relative context.

**Key Characteristics:**

- A persistent white navigation rail and utility bar organize the workspace without competing with the data.
- Fine borders and labeled rows create hierarchy; empty decoration and ornamental card stacks do not.
- Instrument Sans gives decision headings compact authority, IBM Plex Sans carries prose, and IBM Plex Mono identifies auditable figures and small data labels.
- Cobalt is reserved for direct interaction, selection, and the primary forecast series.
- Mobile retains the same operating sequence with a fixed bottom navigation and 44px interactive targets.

## Colors

The palette is a cool mineral shell with one assertive cobalt action color and strictly semantic outcome tones.

### Primary

- **Research Cobalt:** the sole action, selection, focus, and primary revenue-series color. Use it for the Run action, active navigation, selected modes/scenarios, and the forecast line.
- **Cobalt Wash:** a translucent selection field for active navigation, scenario buttons, and focus support; it never becomes a full-page tint.

### Secondary

- **Cash-Flow Slate:** the quiet supporting series in the operating forecast. It is subordinate to cobalt and never signals state.

### Neutral

- **Mineral Canvas:** the cool working field behind the persistent shell and panels.
- **White Surface:** the only resting panel and control surface.
- **Ledger Fill:** the soft neutral fill for grouped inactive controls, disabled states, and low-emphasis tracks.
- **Navy Ink:** primary headings, values, and decisive text.
- **Slate Copy:** readable support copy; use dim slate for labels, timestamps, and metadata.
- **Structural Rules:** fine cool-gray dividers distinguish zones without shadows.

### Semantic

- **Verified Green:** genuine favorable outcomes and connected status only.
- **Caution Brass:** interpretive cautions and market-context attention states only.
- **Signal Red:** genuine unfavorable outcomes and errors only.

**The One Interaction Voice Rule.** Cobalt is the only non-semantic accent for interaction and selection. Do not color Bear, Base, and Bull as if they were outcome states.

## Typography

**Display Font:** Instrument Sans, with the system sans stack as fallback.
**Body Font:** IBM Plex Sans, with the system sans stack as fallback.
**Label/Mono Font:** IBM Plex Mono for financial data, tickers, audit-oriented labels, and tabular figures.

**Character:** The pairing is compact and legible rather than editorial or terminal-like. Display type gives titles a small amount of authority; body copy remains calm at dense dashboard sizes; mono type makes money, rates, and provenance easy to scan.

### Hierarchy

- **Display:** used for page and panel headings, generally with a dense 650 weight, tight negative tracking, and compact leading.
- **Headline:** the mobile and desktop workspace title stays prominent without becoming a marketing hero.
- **Title:** compact Instrument Sans headings identify panels, forecast sections, and high-value summary modules.
- **Body:** IBM Plex Sans carries explanatory and state copy at compact dashboard sizes with comfortable line-height.
- **Label:** uppercase or compact utility labels use IBM Plex Mono with measured tracking; do not apply that treatment to ordinary prose.

**The Figure-as-Evidence Rule.** Tickers, currency, rates, and audit-oriented values use tabular numerals; prose and navigation do not imitate a trading terminal.

## Layout

The desktop shell fixes a 220px white navigation rail and a 68px utility bar above a cool canvas. Main authenticated pages use a centered content field up to 1320px wide with a 28px side gutter; the home page starts with a title/action row, four compact metrics, then a weighted recent-work/context split.

The valuation workspace puts the command strip and assumption tray before results. When populated, its primary field pairs a dominant forecast with a 320px thesis rail; the rail becomes 270px at intermediate widths and remains sticky above 900px. At 900px and below the field becomes a single column. At 820px the sidebar yields to a mobile top bar and fixed five-item bottom navigation; narrow pages maintain a 14px side gutter and remain usable at 320px without page-level horizontal overflow.

**The Decision-First Rule.** Put the operator's next action and the model cross-check before supporting schedules. Do not turn the workspace into a long document of equally weighted sections.

## Elevation & Depth

Depth is predominantly tonal and structural, not atmospheric. Resting panels use white fill, a fine rule, and no shadow; this keeps a dense research screen calm. Small shadows are reserved for the brand mark, selected segmented-control surface, and transient forecast tooltip. The interface uses no gradients, glass, neon, or floating-card stacks.

**The Flat-by-Default Rule.** A border and background change define a surface; elevation is reserved for a control state or brief contextual overlay.

## Shapes

Forms are compact and softly squared: controls use a 5–7px radius, panels use an 11px radius, navigation uses 8px corners, and only status tags or compact labels become pills. Borders are fine and cool; geometry is reliable and operational, never rounded into a consumer-app aesthetic. Chart bars keep only their top corners softly rounded.

## Components

### Buttons

**Character:** compact, direct controls with cobalt reserved for the action that advances the work.

- **Shape:** gently squared input-radius controls with a consistent 44px minimum height for primary, secondary, segmented, and scenario actions.
- **Primary:** cobalt surface with white 600-weight text. It darkens slightly on hover and moves down 1px when pressed.
- **Secondary:** white with a stronger structural rule and slate text; hover changes its border and text to cobalt rather than filling it.
- **Focus:** every keyboard focus state uses a visible cobalt outline; text fields add a soft cobalt ring.

### Inputs / Fields

**Character:** calm white work fields that look like part of the ledger rather than floating search widgets.

- **Style:** white fill, strong fine border, and compact squared radius. Ticker fields use tabular figures and clear uppercase treatment.
- **Focus:** cobalt border plus a translucent cobalt ring.
- **Disabled:** ledger fill and dim slate text, never hidden controls.

### Navigation

**Character:** a persistent, low-noise route index.

- **Desktop:** the 220px left rail groups 42px icon-and-label links under a small uppercase section label; the active route receives a pale cobalt field and cobalt icon/text.
- **Mobile:** the same five routes move to a fixed white bottom bar; active items retain the cobalt wash and all targets are at least 44px tall.
- **Utility bar:** a slim white top bar holds tear-sheet search and quiet research status.

### Cards / Containers

**Character:** flat ledger panels, not decorative cards.

- **Corner Style:** compact panel corners and fine rules define the module boundary.
- **Background:** white on the mineral canvas; the forecast and thesis rail stay in this same visual family.
- **Shadow Strategy:** no resting shadow; only stateful controls and the forecast tooltip lift.
- **Internal Padding:** compact interior rhythm, tightened further in the paired forecast/thesis field.

### Scenario & Mode Selectors

**Character:** quiet, explicit state controls that never encode Bull or Bear as positive or negative.

- **Unselected:** white or ledger-backed controls with slate labels and fine borders.
- **Selected:** cobalt text and border on a pale cobalt field.
- **Behavior:** historical/custom and Bear/Base/Bull controls retain a 44px touch target; the thesis selector remains the visible companion to the shared scenario state.

### Forecast & Thesis Field

**Character:** one primary analytical cross-check, not competing dashboard widgets.

- **Forecast:** cobalt revenue line over a pale cobalt area with restrained cash-flow slate bars, faint horizontal gridlines, and an elevated tooltip for inspection.
- **Thesis Rail:** a compact white companion panel with labeled rows, tabular values, provenance, market delta, and the shared scenario selector. It is sticky on larger screens and stacks with the forecast on smaller ones.

## Do's and Don'ts

### Do:

- **Do** use the cool-white canvas, white surfaces, fine structural rules, and compact radii as the base grammar.
- **Do** make the next action, selected scenario, source/provenance, and market/model distinction easy to scan in the first viewport.
- **Do** use cobalt for interaction, selection, focus, and the primary forecast series; reserve green, brass, and red for real semantic status.
- **Do** keep values tabular and use the mono face where data must be audited or compared.
- **Do** preserve the desktop rail/topbar and the mobile bottom-navigation pattern when extending authenticated routes.

### Don't:

- **Don't** introduce dark-terminal styling, gradients, glassmorphism, neon, or decorative dashboard ornament.
- **Don't** replace fine structural borders with broad shadows or a stack of floating cards.
- **Don't** color Bear/Base/Bull as favorable or unfavorable outcomes.
- **Don't** hide provenance, cautions, empty/loading/error states, or the difference between absolute value and peer context.
- **Don't** permit page-level horizontal overflow below 320px or reduce primary mobile targets below 44px.
