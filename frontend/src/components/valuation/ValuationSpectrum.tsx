"use client";

import { useState } from "react";
import { computeValuationRange, type ValuationRangePoint } from "@/lib/valuation-range";
import { computeMarketSpread, formatMarketSpread } from "@/lib/market-spread";
import type { SensitivityCellStatus } from "@/lib/sensitivity-cell";
import { formatPercent, formatPreciseCurrency } from "./format";
import ScrollHintTable from "./ScrollHintTable";

export interface ScenarioAssumptions {
  revenue_growth_rate: number;
  operating_margin: number;
  wacc: number;
  terminal_growth_rate: number;
}

export interface ScenarioResult {
  name: "bear" | "base" | "bull";
  assumptions: ScenarioAssumptions;
  // null when this scenario's (clamped) assumptions aren't economically
  // valid for the model — see `is_valid`/`invalid_reason`, never NaN/Infinity.
  intrinsic_value_per_share: number | null;
  is_valid: boolean;
  invalid_reason: string | null;
}

export interface DCFScenarioSet {
  bear: ScenarioResult;
  base: ScenarioResult;
  bull: ScenarioResult;
}

type CaseKey = "bear" | "base" | "bull";
type SpectrumKey = CaseKey | "market";

const CASE_ORDER: CaseKey[] = ["bear", "base", "bull"];
const CASE_LABELS: Record<CaseKey, string> = { bear: "Bear", base: "Base", bull: "Bull" };

// Maps a sensitivity/market-spread classification to its visual tone and
// glyph — tone, glyph, and accessible wording are never computed
// independently of one another. Shared by the Valuation Spectrum's
// comparison table and the separate DCF sensitivity grid.
export function sensitivityCellToneAndGlyph(status: SensitivityCellStatus): {
  toneClass: string;
  glyph: string | null;
} {
  switch (status) {
    case "above":
      return { toneClass: "text-[var(--verdigris)]", glyph: "▲" };
    case "below":
      return { toneClass: "text-[var(--signal)]", glyph: "▼" };
    case "invalid":
      return { toneClass: "text-[var(--paper-dim)]", glyph: null };
    case "equal":
    case "comparison-unavailable":
      return { toneClass: "text-[var(--paper)]", glyph: null };
  }
}

// Bear/Bull are neutral WHAT-IF policy cases, not good/bad outcomes — so
// their markers deliberately avoid red/green (that coding is reserved for
// the separate "vs. market" comparison in the table below). Shape, not
// color, tells Bear apart from Bull; Base is the only one singled out by
// color (cobalt) since it's the case selected by default.
function markerGlyphClass(key: SpectrumKey): string {
  switch (key) {
    case "base":
      return "h-3.5 w-3.5 rounded-full bg-[var(--cobalt)]";
    case "bear":
      return "h-3 w-3 rounded-[2px] bg-[var(--instrument-text-dim)]"; // rotated to a diamond below
    case "bull":
      return "h-3 w-3 rounded-[2px] bg-[var(--instrument-text-dim)]"; // square
    default:
      return "h-2.5 w-2.5 rounded-full bg-[var(--instrument-text-dim)]"; // market price
  }
}

interface ValuationSpectrumProps {
  scenarios: DCFScenarioSet;
  marketPrice: number | null;
}

// Consolidates the former headline market-vs-intrinsic rail and the
// former Bear/Base/Bull scenario rail into one instrument: Market,
// Bear, Base, and Bull all share the same scale, the same interaction
// model, and one readout. Every value rendered is exactly what the API
// returned or a pure lib function computed — no DCF math happens here.
//
// The rail's dot positions are informative but too easily clustered to
// serve as real touch targets, so the AUTHORITATIVE control is a
// separate, evenly-spaced selector row (>=44px targets) below it — the
// rail is a purely visual indicator.
export default function ValuationSpectrum({ scenarios, marketPrice }: ValuationSpectrumProps) {
  const [selectedKey, setSelectedKey] = useState<SpectrumKey>("base");
  const [hoveredKey, setHoveredKey] = useState<SpectrumKey | null>(null);

  // A new result can arrive with no market price at all (or a re-run can
  // drop it) while Market was still selected/hovered from a previous
  // result — fall back to Base rather than pointing the readout at a
  // case that no longer has a selector button. Adjusting state during
  // render like this is safe: the condition is self-clearing, so it
  // settles in the same render pass and never loops.
  if (marketPrice === null && selectedKey === "market") {
    setSelectedKey("base");
  }
  if (marketPrice === null && hoveredKey === "market") {
    setHoveredKey(null);
  }

  const displayedKey = hoveredKey ?? selectedKey;

  const rangePoints: ValuationRangePoint[] = [
    ...CASE_ORDER.map((key) => ({
      key,
      label: CASE_LABELS[key],
      value: scenarios[key].is_valid ? scenarios[key].intrinsic_value_per_share : null,
    })),
    { key: "market", label: "Market price", value: marketPrice },
  ];
  const range = computeValuationRange(rangePoints);
  const invalidKeys = CASE_ORDER.filter((key) => !scenarios[key].is_valid);
  const displayedMarker = range?.markers.find((marker) => marker.key === displayedKey);

  // The selector always offers Bear/Base/Bull (even when invalid — picking
  // one then shows exactly why via the readout) and Market only when an
  // observed price actually exists.
  const selectableKeys: SpectrumKey[] = marketPrice !== null ? [...CASE_ORDER, "market"] : [...CASE_ORDER];

  function selectorLabel(key: SpectrumKey): string {
    if (key === "market") return `Market: ${formatPreciseCurrency(marketPrice)}, observed price`;
    const scenario = scenarios[key];
    return scenario.is_valid
      ? `${CASE_LABELS[key]}: ${formatPreciseCurrency(scenario.intrinsic_value_per_share)}`
      : `${CASE_LABELS[key]}: not computable`;
  }

  function selectionAnnouncement(key: SpectrumKey): string {
    if (key === "market") {
      return `Market selected: observed price ${formatPreciseCurrency(marketPrice)}.`;
    }
    const scenario = scenarios[key];
    if (!scenario.is_valid) {
      return `${CASE_LABELS[key]} selected: not computable. ${scenario.invalid_reason ?? ""}`.trim();
    }
    return (
      `${CASE_LABELS[key]} selected: ${formatPreciseCurrency(scenario.intrinsic_value_per_share)}, ` +
      `growth ${formatPercent(scenario.assumptions.revenue_growth_rate)}, ` +
      `margin ${formatPercent(scenario.assumptions.operating_margin)}, ` +
      `WACC ${formatPercent(scenario.assumptions.wacc, 2)}, ` +
      `terminal growth ${formatPercent(scenario.assumptions.terminal_growth_rate)}.`
    );
  }

  return (
    <div>
      <h2 className="section-title">Valuation spectrum</h2>

      <div className="instrument-panel">
        <p className="instrument-caption">
          Market, Bear, Base, and Bull on one scale. Bear/Base/Bull reproject cash flow and
          discounting from this valuation&rsquo;s own assumptions — transparent policy cases, not
          probabilities, forecasts, recommendations, or price targets.
        </p>

        <div
          className="instrument-selector"
          role="group"
          aria-label="Valuation case"
          onMouseLeave={() => setHoveredKey(null)}
        >
          {selectableKeys.map((key) => {
            const isSelected = selectedKey === key;
            return (
              <button
                key={key}
                type="button"
                className="instrument-selector-button"
                aria-pressed={isSelected}
                aria-label={`${selectorLabel(key)}${isSelected ? ", selected" : ""}`}
                onMouseEnter={() => setHoveredKey(key)}
                onClick={() => setSelectedKey(key)}
              >
                <span
                  aria-hidden="true"
                  className={`instrument-marker-glyph ${markerGlyphClass(key)}`}
                  style={key === "bear" ? { transform: "rotate(45deg)" } : undefined}
                />
                <span aria-hidden="true">{key === "market" ? "Market" : CASE_LABELS[key]}</span>
              </button>
            );
          })}
        </div>

        {range ? (
          <div className="instrument-rail" aria-hidden="true">
            <div className="instrument-rail-track" />
            {displayedMarker && (
              <div className="instrument-indicator" style={{ left: `${displayedMarker.pct}%` }} />
            )}
            {range.markers.map((marker) => {
              const key = marker.key as SpectrumKey;
              return (
                <span
                  key={key}
                  className={`instrument-marker ${key === displayedKey ? "instrument-marker--current" : ""}`}
                  style={{
                    left: `${marker.pct}%`,
                    top: `calc(50% + ${marker.stackLevel * 11}px)`,
                  }}
                >
                  <span
                    className={`instrument-marker-glyph ${markerGlyphClass(key)}`}
                    style={key === "bear" ? { transform: "rotate(45deg)" } : undefined}
                  />
                </span>
              );
            })}
          </div>
        ) : (
          <p className="instrument-caption mb-0">No scenario values are available to plot.</p>
        )}

        {/* Independent of whether the rail can be plotted — an all-invalid,
            no-market result still has a selected Bear/Base/Bull case whose
            "Not computable" readout (or a valid case's figures) should
            keep working from the selector row above. */}
        <div className="instrument-readout">
          {displayedKey === "market" ? (
            <>
              <p className="instrument-readout-label">Market</p>
              <p className="instrument-readout-value">{formatPreciseCurrency(marketPrice)}</p>
              <p className="instrument-readout-note">
                The observed market price — not a modeled case, shown for comparison only.
              </p>
            </>
          ) : scenarios[displayedKey].is_valid ? (
            <>
              <p className="instrument-readout-label">{CASE_LABELS[displayedKey]}</p>
              <p className="instrument-readout-value">
                {formatPreciseCurrency(scenarios[displayedKey].intrinsic_value_per_share)}
              </p>
              <div className="instrument-readout-grid">
                <span>
                  Growth <b>{formatPercent(scenarios[displayedKey].assumptions.revenue_growth_rate)}</b>
                </span>
                <span>
                  Margin <b>{formatPercent(scenarios[displayedKey].assumptions.operating_margin)}</b>
                </span>
                <span>
                  WACC <b>{formatPercent(scenarios[displayedKey].assumptions.wacc, 2)}</b>
                </span>
                <span>
                  Terminal g. <b>{formatPercent(scenarios[displayedKey].assumptions.terminal_growth_rate)}</b>
                </span>
              </div>
            </>
          ) : (
            <>
              <p className="instrument-readout-label">{CASE_LABELS[displayedKey]}</p>
              <p className="instrument-readout-value instrument-readout-value--muted">Not computable</p>
              <p className="instrument-readout-note">{scenarios[displayedKey].invalid_reason}</p>
            </>
          )}
        </div>

        {/* Announces the PERSISTED selection only — hover-preview never
            triggers a screen-reader announcement, since selection must
            not depend on hover. */}
        <span className="sr-only" aria-live="polite">
          {selectionAnnouncement(selectedKey)}
        </span>
      </div>

      <ScrollHintTable>
        <table className="data-table w-full min-w-[680px] border-collapse text-sm mt-6">
          <caption className="sr-only">
            Bear, Base, and Bull valuation scenarios: intrinsic value per share, comparison to
            market price, and the revenue growth, operating margin, WACC, and terminal growth
            assumptions used for each case.
          </caption>
          <thead>
            <tr className="border-b border-[var(--line)] text-left">
              <th scope="col" className="py-2 pr-4 font-medium">
                Case
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Intrinsic value / share
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                vs. Market
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Revenue growth
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Operating margin
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                WACC
              </th>
              <th scope="col" className="py-2 pl-3 text-right font-medium">
                Terminal growth
              </th>
            </tr>
          </thead>
          <tbody>
            {CASE_ORDER.map((key) => {
              const scenario = scenarios[key];
              const isBase = key === "base";
              const value = scenario.is_valid ? scenario.intrinsic_value_per_share : null;
              const spread = computeMarketSpread({ value, marketPrice });
              const { toneClass, glyph } = sensitivityCellToneAndGlyph(spread.status);
              const { visible: vsMarketVisible, accessible: accessibleVsMarket } = formatMarketSpread(spread);

              return (
                <tr
                  key={key}
                  className={`border-b border-[var(--line)] last:border-0 ${isBase ? "bg-[var(--cobalt-soft)]" : ""}`}
                >
                  <th
                    scope="row"
                    className={`py-2.5 pr-4 text-left font-medium ${isBase ? "text-[var(--cobalt)]" : "text-[var(--paper)]"}`}
                  >
                    {CASE_LABELS[key]}
                  </th>
                  <td className="tabular-nums font-mono px-3 py-2.5 text-right text-[var(--paper)]">
                    {value !== null ? (
                      formatPreciseCurrency(value)
                    ) : (
                      <span className="text-[var(--paper-dim)]">Not computable</span>
                    )}
                  </td>
                  <td aria-label={accessibleVsMarket} className={`px-3 py-2.5 text-right ${toneClass}`}>
                    <span aria-hidden="true" className="tabular-nums font-mono">
                      {glyph && <span className="mr-0.5">{glyph}</span>}
                      {vsMarketVisible}
                    </span>
                  </td>
                  <td className="tabular-nums font-mono px-3 py-2.5 text-right text-[var(--paper-muted)]">
                    {formatPercent(scenario.assumptions.revenue_growth_rate)}
                  </td>
                  <td className="tabular-nums font-mono px-3 py-2.5 text-right text-[var(--paper-muted)]">
                    {formatPercent(scenario.assumptions.operating_margin)}
                  </td>
                  <td className="tabular-nums font-mono px-3 py-2.5 text-right text-[var(--paper-muted)]">
                    {formatPercent(scenario.assumptions.wacc, 2)}
                  </td>
                  <td className="tabular-nums font-mono py-2.5 pl-3 text-right text-[var(--paper-muted)]">
                    {formatPercent(scenario.assumptions.terminal_growth_rate)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollHintTable>

      {invalidKeys.length > 0 && (
        <div className="mt-4 space-y-1.5">
          {invalidKeys.map((key) => (
            <p key={key} className="text-xs leading-5 text-[var(--paper-dim)]">
              <span className="font-semibold text-[var(--paper-muted)]">{CASE_LABELS[key]} case not computable:</span>{" "}
              {scenarios[key].invalid_reason}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
