/**
 * Pure scenario-selection resolution shared by the Valuation Spectrum
 * instrument and the Thesis Rail — both need to agree on exactly which
 * case (Bear/Base/Bull, persisted) or transient inspection state
 * (Market, hover-preview) is currently "displayed", without either
 * component re-deriving its own copy of this logic.
 */
export type CaseKey = "bear" | "base" | "bull";
export type SpectrumKey = CaseKey | "market";

/**
 * Resolves which key the Valuation Spectrum instrument should actually
 * render its readout/indicator for, in priority order:
 *   1. An active hover preview (if any) always wins — hovering is a
 *      pure, transient "what if I looked at this instead" inspection
 *      that must never persist past the hover.
 *   2. A local Market inspection (clicking the Market selector button)
 *      — also transient, layered independently of the shared
 *      Bear/Base/Bull selection so selecting Market never overwrites
 *      the persisted scenario the Thesis Rail is showing.
 *   3. Otherwise, the persisted, shared Bear/Base/Bull selection.
 */
export function resolveDisplayedKey(params: {
  hoveredKey: SpectrumKey | null;
  isMarketSelected: boolean;
  selectedScenario: CaseKey;
}): SpectrumKey {
  const { hoveredKey, isMarketSelected, selectedScenario } = params;
  if (hoveredKey !== null) return hoveredKey;
  if (isMarketSelected) return "market";
  return selectedScenario;
}

/**
 * A scenario/Market case is only ever a valid Bear/Base/Bull selection
 * target once a real market price exists — a result can arrive (or a
 * re-run can drop to) `marketPrice: null` while Market was still
 * selected from a PREVIOUS result. This resolves what the local
 * "Market selected" flag should become for the new result, never
 * leaving it pointed at a case that no longer has a selector button.
 */
export function resolveMarketSelectedForNewResult(params: {
  wasMarketSelected: boolean;
  marketPrice: number | null;
}): boolean {
  return params.wasMarketSelected && params.marketPrice !== null;
}
