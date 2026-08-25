"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { valuationErrorFromResponse, type ValuationRequestError } from "@/lib/valuation-errors";
import { formatPercentInputValue, parsePercentInput, type PercentFieldRange } from "@/lib/percent-field";
import { computeValuationSpread } from "@/lib/valuation-spread";
import { computeValuationRange, type ValuationRangePoint } from "@/lib/valuation-range";
import { computeMarketSpread, formatMarketSpread } from "@/lib/market-spread";
import {
  classifySensitivityCell,
  sensitivityCellAccessibleLabel,
  type SensitivityCellStatus,
} from "@/lib/sensitivity-cell";

interface FreeCashFlowYear {
  year: number;
  revenue: number;
  ebit: number;
  nopat: number;
  da: number;
  capex: number;
  change_in_nwc: number;
  fcf: number;
}

interface SensitivityAxis {
  label: string;
  values: number[];
  baseline_index: number;
}

interface DCFSensitivityMatrix {
  wacc_axis: SensitivityAxis;
  terminal_growth_axis: SensitivityAxis;
  // cells[i][j]: intrinsic value per share for wacc_axis.values[i] x
  // terminal_growth_axis.values[j], or null for a combination the model
  // refuses to value (e.g. WACC not strictly greater than terminal
  // growth) — never NaN/Infinity.
  cells: (number | null)[][];
  baseline_row: number;
  baseline_col: number;
  baseline_wacc: number;
  baseline_terminal_growth_rate: number;
  baseline_intrinsic_value_per_share: number | null;
}

interface ScenarioAssumptions {
  revenue_growth_rate: number;
  operating_margin: number;
  wacc: number;
  terminal_growth_rate: number;
}

interface ScenarioResult {
  name: "bear" | "base" | "bull";
  assumptions: ScenarioAssumptions;
  // null when this scenario's (clamped) assumptions aren't economically
  // valid for the model — see `is_valid`/`invalid_reason`, never NaN/Infinity.
  intrinsic_value_per_share: number | null;
  is_valid: boolean;
  invalid_reason: string | null;
}

interface DCFScenarioSet {
  bear: ScenarioResult;
  base: ScenarioResult;
  bull: ScenarioResult;
}

interface EvaluationResponse {
  ticker: string;
  current_price: number | null;
  wacc: number;
  enterprise_value: number;
  equity_value: number;
  intrinsic_value_per_share: number;
  projected_free_cash_flows: FreeCashFlowYear[];
  assumptions: {
    revenue_growth_rate: number;
    operating_margin: number;
    terminal_growth_rate: number;
    projection_years: number;
  };
  // "historical" = derived from the company's own financials (the
  // default); "custom" = an explicit slider value was sent and used
  // instead. Lets the UI say what was ACTUALLY used rather than
  // guessing from the numeric value alone.
  revenue_growth_rate_source: "historical" | "custom";
  operating_margin_source: "historical" | "custom";
  sector: string;
  price_to_intrinsic_value: number | null;
  sector_median_p_iv: number | null;
  sector_median_unavailable_reason: string | null;
  sensitivity: DCFSensitivityMatrix;
  scenarios: DCFScenarioSet;
}

const compactCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});

const preciseCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatCompactCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return compactCurrencyFormatter.format(value);
}

function formatPreciseCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return preciseCurrencyFormatter.format(value);
}

function formatPercent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

interface AssumptionFieldProps {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  precision: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  helper?: string;
}

function AssumptionField({
  id,
  label,
  value,
  min,
  max,
  step,
  precision,
  onChange,
  disabled = false,
  helper,
}: AssumptionFieldProps) {
  const range: PercentFieldRange = { min, max, step };
  const [text, setText] = useState(() => formatPercentInputValue(value, precision));
  // Keep the numeric box in sync when `value` changes from outside (e.g. a
  // slider drag) without re-formatting on every keystroke — adjusted during
  // render rather than in an effect, per React's "you might not need an
  // effect" guidance, so mid-typing input isn't clobbered by a render caused
  // by something else.
  const [syncedValue, setSyncedValue] = useState(value);
  if (syncedValue !== value) {
    setSyncedValue(value);
    setText(formatPercentInputValue(value, precision));
  }

  function commit(raw: string) {
    const next = parsePercentInput(raw, range);
    if (next === null) {
      // Blank or invalid: revert to the last valid value rather than
      // silently coercing to 0.
      setText(formatPercentInputValue(value, precision));
      return;
    }
    // Always reformat from the quantized value, even when `next` equals the
    // current `value` (e.g. the user typed an off-step number that snapped
    // back to what was already selected) — otherwise the box could keep
    // showing the raw, un-quantized text the user typed while the slider and
    // submitted value have already moved to the snapped one.
    setText(formatPercentInputValue(next, precision));
    onChange(next);
  }

  return (
    <div className="numeric-slider-field">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <label htmlFor={id} className="text-xs font-medium text-[var(--paper-muted)]">
          {label}
        </label>
        <span className="flex items-center gap-1">
          <input
            id={id}
            type="number"
            inputMode="decimal"
            className="numeric-input"
            value={text}
            min={min * 100}
            max={max * 100}
            step={step * 100}
            disabled={disabled}
            aria-disabled={disabled}
            onChange={(event) => setText(event.target.value)}
            onBlur={(event) => commit(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
          />
          <span className="text-xs text-[var(--paper-dim)]">%</span>
        </span>
      </div>
      <input
        type="range"
        aria-label={label}
        className="range-input"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        aria-disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {helper && <p className="mt-1.5 text-[11px] leading-4 text-[var(--paper-dim)]">{helper}</p>}
    </div>
  );
}

interface SectorBadgeProps {
  sector: string;
}

function SectorBadge({ sector }: SectorBadgeProps) {
  return (
    <span className="inline-flex items-center rounded-[4px] border border-[var(--line-strong)] bg-[var(--ledger)] px-2.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-[.08em] text-[var(--paper-muted)]">
      {sector}
    </span>
  );
}

interface ValuationSpreadRailProps {
  ticker: string;
  marketPrice: number | null;
  intrinsicValue: number;
}

function ValuationSpreadRail({ ticker, marketPrice, intrinsicValue }: ValuationSpreadRailProps) {
  const spread = computeValuationSpread({ marketPrice, intrinsicValue });

  if (spread === null) {
    return (
      <div className="empty-state">
        Market price is unavailable, so the valuation spread cannot be plotted for {ticker}.
      </div>
    );
  }

  const { direction, percent, marketPct, intrinsicPct, fillStartPct, fillWidthPct } = spread;
  const toneClass =
    direction === "upside"
      ? "text-[var(--verdigris)]"
      : direction === "downside"
        ? "text-[var(--signal)]"
        : "text-[var(--paper-muted)]";

  let headline: string;
  if (percent === null) {
    headline = "Percent spread unavailable at a $0 market price";
  } else if (direction === "equal") {
    headline = "Trading exactly at intrinsic value";
  } else {
    headline = `${percent >= 0 ? "+" : ""}${percent.toFixed(1)}% ${direction} to intrinsic value`;
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <span className="data-label">Valuation spread</span>
        <span className={`tabular-nums font-mono text-sm font-semibold ${toneClass}`}>{headline}</span>
      </div>

      <div
        className="spread-rail-track"
        role="img"
        aria-label={`${ticker} market price ${formatPreciseCurrency(marketPrice)} versus intrinsic value ${formatPreciseCurrency(intrinsicValue)}`}
      >
        <div
          className={`spread-rail-fill ${direction === "downside" ? "is-downside" : "is-upside"}`}
          style={{ left: `${fillStartPct}%`, width: `${fillWidthPct}%` }}
        />
        <div className="spread-marker is-market" style={{ left: `${marketPct}%` }} />
        <div className="spread-marker is-intrinsic" style={{ left: `${intrinsicPct}%` }} />
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: "var(--paper-muted)" }} />
          <span className="text-[var(--paper-muted)]">Market price</span>
          <span className="tabular-nums font-mono font-semibold text-[var(--paper)]">
            {formatPreciseCurrency(marketPrice)}
          </span>
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: "var(--cobalt)" }} />
          <span className="text-[var(--paper-muted)]">Intrinsic value</span>
          <span className="tabular-nums font-mono font-semibold text-[var(--cobalt)]">
            {formatPreciseCurrency(intrinsicValue)}
          </span>
        </span>
      </div>
    </div>
  );
}

interface SectorValuationComparisonProps {
  ticker: string;
  sector: string;
  priceToIntrinsicValue: number | null;
  sectorMedianPIV: number | null;
  sectorMedianUnavailableReason: string | null;
}

function SectorValuationComparison({
  ticker,
  sector,
  priceToIntrinsicValue,
  sectorMedianPIV,
  sectorMedianUnavailableReason,
}: SectorValuationComparisonProps) {
  if (priceToIntrinsicValue === null || sectorMedianPIV === null) {
    return (
      <div className="panel p-5 sm:p-6">
        <p className="text-sm leading-6 text-[var(--paper-dim)]">
          {priceToIntrinsicValue === null
            ? "Price-to-intrinsic-value could not be computed for this ticker."
            : (sectorMedianUnavailableReason ?? `No sector median P/IV available yet for ${sector}.`)}
        </p>
      </div>
    );
  }

  const passes = priceToIntrinsicValue <= sectorMedianPIV;
  const maxScale = Math.max(priceToIntrinsicValue, sectorMedianPIV) * 1.15;
  const stockBarPct = Math.min((priceToIntrinsicValue / maxScale) * 100, 100);
  const medianBarPct = Math.min((sectorMedianPIV / maxScale) * 100, 100);

  return (
    <div className="panel p-5 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-[var(--paper-dim)]">
          Passes the sector-relative Margin of Safety filter when {ticker}&rsquo;s P/IV is at or
          below the {sector} sector median.
        </span>
        <span
          className={`rounded-[4px] border px-2.5 py-1 text-xs font-semibold uppercase tracking-wide ${
            passes
              ? "border-[var(--verdigris)] bg-[var(--verdigris-soft)] text-[var(--verdigris)]"
              : "border-[var(--signal)] bg-[var(--signal-soft)] text-[var(--signal)]"
          }`}
        >
          {passes ? "Below median" : "Above median"}
        </span>
      </div>

      <div className="space-y-4">
        <div>
          <div className="mb-1.5 flex items-baseline justify-between text-sm">
            <span className="text-[var(--paper-muted)]">{ticker} P/IV</span>
            <span
              className={`tabular-nums font-mono font-semibold ${passes ? "text-[var(--verdigris)]" : "text-[var(--signal)]"}`}
            >
              {priceToIntrinsicValue.toFixed(2)}x
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--ledger)]">
            <div
              className={`h-full ${passes ? "bg-[var(--verdigris)]" : "bg-[var(--signal)]"}`}
              style={{ width: `${stockBarPct}%` }}
            />
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-baseline justify-between text-sm">
            <span className="text-[var(--paper-muted)]">{sector} sector median</span>
            <span className="tabular-nums font-mono font-semibold text-[var(--paper-muted)]">
              {sectorMedianPIV.toFixed(2)}x
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--ledger)]">
            <div className="h-full bg-[var(--paper-dim)]" style={{ width: `${medianBarPct}%` }} />
          </div>
        </div>
      </div>
    </div>
  );
}

const SCENARIO_ORDER: Array<"bear" | "base" | "bull"> = ["bear", "base", "bull"];
const SCENARIO_LABELS: Record<"bear" | "base" | "bull", string> = { bear: "Bear", base: "Base", bull: "Bull" };

// Bear/Bull are neutral WHAT-IF policy cases, not good/bad outcomes — so
// their rail markers deliberately avoid red/green (that coding is
// reserved for the separate "vs. market" comparison). Shape, not color,
// tells Bear apart from Bull; Base is the only one singled out by color
// (cobalt, matching how the baseline case is marked elsewhere in this
// workspace) since it's the case being emphasized.
function scenarioMarkerShapeClass(key: string): string {
  switch (key) {
    case "base":
      return "h-3.5 w-3.5 rounded-full bg-[var(--cobalt)]";
    case "bear":
      return "h-2.5 w-2.5 rotate-45 bg-[var(--paper-muted)]"; // diamond
    case "bull":
      return "h-2.5 w-2.5 bg-[var(--paper-muted)]"; // square
    default:
      return "h-2 w-2 rounded-full bg-[var(--paper-dim)]"; // market price
  }
}

interface ValuationScenariosSectionProps {
  scenarios: DCFScenarioSet;
  marketPrice: number | null;
}

// Renders the already-computed Bear/Base/Bull set — every value is
// exactly what the API returned; no DCF math happens in this component.
function ValuationScenariosSection({ scenarios, marketPrice }: ValuationScenariosSectionProps) {
  const rangePoints: ValuationRangePoint[] = [
    ...SCENARIO_ORDER.map((key) => ({
      key,
      label: SCENARIO_LABELS[key],
      value: scenarios[key].is_valid ? scenarios[key].intrinsic_value_per_share : null,
    })),
    { key: "market", label: "Market price", value: marketPrice },
  ];
  const range = computeValuationRange(rangePoints);
  const invalidScenarioKeys = SCENARIO_ORDER.filter((key) => !scenarios[key].is_valid);

  return (
    <div>
      <div className="section-intro">
        <div>
          <p className="eyebrow mb-1.5">Policy cases</p>
          <h2>Valuation scenarios</h2>
        </div>
      </div>

      <div className="panel p-5 sm:p-6">
        <p className="mb-5 max-w-2xl text-xs leading-5 text-[var(--paper-dim)]">
          Three assumption sets around the current model. Each reprojects cash flow and
          discounting; none is a probability or recommendation.
        </p>

        {range ? (
          <div className="mb-6">
            <div
              className="relative h-14"
              role="img"
              aria-label={`Valuation range: ${range.markers
                .map((marker) => `${marker.label} ${formatPreciseCurrency(marker.value)}`)
                .join(", ")}`}
            >
              <div className="spread-rail-track absolute left-0 right-0 top-1/2 -translate-y-1/2" />
              {range.markers.map((marker) => (
                <span
                  key={marker.key}
                  aria-hidden="true"
                  className={`absolute rounded-[2px] ${scenarioMarkerShapeClass(marker.key)}`}
                  style={{
                    left: `${marker.pct}%`,
                    top: `calc(50% + ${marker.stackLevel * 9}px)`,
                    transform: `translate(-50%, -50%)${marker.key === "bear" ? " rotate(45deg)" : ""}`,
                  }}
                />
              ))}
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs">
              {range.markers.map((marker) => (
                <span key={marker.key} className="inline-flex items-center gap-1.5">
                  <span
                    aria-hidden="true"
                    className={`inline-block rounded-[1px] ${scenarioMarkerShapeClass(marker.key)}`}
                  />
                  <span className="text-[var(--paper-muted)]">{marker.label}</span>
                  <span className="tabular-nums font-mono font-semibold text-[var(--paper)]">
                    {formatPreciseCurrency(marker.value)}
                  </span>
                </span>
              ))}
            </div>
          </div>
        ) : (
          <div className="empty-state mb-6">No scenario values are available to plot.</div>
        )}

        <div className="overflow-x-auto">
          <table className="data-table w-full min-w-[680px] border-collapse text-sm">
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
              {SCENARIO_ORDER.map((key) => {
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
                      {SCENARIO_LABELS[key]}
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
        </div>

        {invalidScenarioKeys.length > 0 && (
          <div className="mt-4 space-y-1.5">
            {invalidScenarioKeys.map((key) => (
              <p key={key} className="text-xs leading-5 text-[var(--paper-dim)]">
                <span className="font-semibold text-[var(--paper-muted)]">
                  {SCENARIO_LABELS[key]} case not computable:
                </span>{" "}
                {scenarios[key].invalid_reason}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

interface SensitivityMatrixSectionProps {
  matrix: DCFSensitivityMatrix;
  marketPrice: number | null;
}

// Maps the SAME classification driving a cell's accessible label to its
// visual tone and glyph — tone, glyph, and accessible wording are never
// computed independently of one another.
function sensitivityCellToneAndGlyph(status: SensitivityCellStatus): {
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

// Renders the already-computed grid — every cell is exactly what the API
// returned; no DCF math happens in this component.
function SensitivityMatrixSection({ matrix, marketPrice }: SensitivityMatrixSectionProps) {
  const hasMarketPrice = marketPrice !== null;

  return (
    <div>
      <div className="section-intro">
        <div>
          <p className="eyebrow mb-1.5">Assumption stress test</p>
          <h2>DCF sensitivity</h2>
        </div>
      </div>

      <div className="panel p-5 sm:p-6">
        <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="inline-block h-3 w-3 rounded-[2px] bg-[var(--cobalt-soft)] outline outline-2 -outline-offset-2 outline-[var(--cobalt)]"
            />
            <span className="text-[var(--paper-muted)]">Baseline case</span>
          </span>
          {hasMarketPrice ? (
            <>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className="text-[var(--verdigris)]">
                  ▲
                </span>
                <span className="text-[var(--paper-muted)]">Above market price</span>
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className="text-[var(--signal)]">
                  ▼
                </span>
                <span className="text-[var(--paper-muted)]">Below market price</span>
              </span>
            </>
          ) : (
            <span className="text-[var(--paper-muted)]">Market comparison unavailable.</span>
          )}
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden="true" className="text-[var(--paper-dim)]">
              —
            </span>
            <span className="text-[var(--paper-muted)]">Not a valid WACC / terminal growth pair</span>
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="data-table w-full min-w-[560px] border-collapse text-sm">
            <caption className="sr-only">
              Intrinsic value per share at combinations of WACC (rows) and terminal growth rate
              (columns). The baseline case is marked.{" "}
              {hasMarketPrice
                ? "Cells are colored teal when above the current market price and red when below it."
                : "Market price is unavailable, so cells are not compared against it."}
            </caption>
            <thead>
              <tr className="border-b border-[var(--line)] text-left">
                <th
                  scope="col"
                  className="sticky left-0 z-10 bg-[var(--ink-raised)] py-2 pr-4 font-medium"
                >
                  WACC \ Terminal g.
                </th>
                {matrix.terminal_growth_axis.values.map((terminalGrowth, colIndex) => (
                  <th
                    key={colIndex}
                    scope="col"
                    className={`tabular-nums font-mono px-3 py-2 text-right font-medium ${
                      colIndex === matrix.baseline_col ? "text-[var(--cobalt)]" : ""
                    }`}
                  >
                    {formatPercent(terminalGrowth, 1)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.wacc_axis.values.map((wacc, rowIndex) => (
                <tr key={rowIndex} className="border-b border-[var(--line)] last:border-0">
                  <th
                    scope="row"
                    className={`tabular-nums font-mono sticky left-0 z-10 bg-[var(--ink-raised)] py-2 pr-4 text-left font-medium ${
                      rowIndex === matrix.baseline_row ? "text-[var(--cobalt)]" : "text-[var(--paper-muted)]"
                    }`}
                  >
                    {formatPercent(wacc, 1)}
                  </th>
                  {matrix.terminal_growth_axis.values.map((terminalGrowth, colIndex) => {
                    const value = matrix.cells[rowIndex]?.[colIndex] ?? null;
                    const isBaseline = rowIndex === matrix.baseline_row && colIndex === matrix.baseline_col;
                    const status = classifySensitivityCell({ value, marketPrice });
                    const { toneClass, glyph } = sensitivityCellToneAndGlyph(status);
                    const accessibleLabel = sensitivityCellAccessibleLabel({
                      value,
                      marketPrice,
                      isBaseline,
                      formatCurrency: formatPreciseCurrency,
                    });

                    return (
                      <td
                        key={colIndex}
                        aria-label={accessibleLabel}
                        className={`tabular-nums font-mono px-3 py-2 text-right ${toneClass} ${
                          isBaseline
                            ? "bg-[var(--cobalt-soft)] outline outline-2 -outline-offset-2 outline-[var(--cobalt)]"
                            : ""
                        }`}
                      >
                        <span aria-hidden="true">
                          {value === null ? (
                            <span className="text-[var(--paper-dim)]">—</span>
                          ) : (
                            <>
                              {glyph && <span className="mr-0.5">{glyph}</span>}
                              {formatPreciseCurrency(value)}
                            </>
                          )}
                          {isBaseline && (
                            <span className="mt-0.5 block font-sans text-[9px] font-semibold uppercase tracking-wide text-[var(--cobalt)]">
                              Baseline
                            </span>
                          )}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="mt-4 text-xs leading-5 text-[var(--paper-dim)]">
          Projected cash flows and the equity bridge remain fixed. Rows vary WACC; columns vary
          terminal growth.
        </p>
      </div>
    </div>
  );
}

export default function Home() {
  const [ticker, setTicker] = useState("AAPL");
  // Default mode: use each company's own historical revenue growth and
  // operating margin — the growth/margin query params are OMITTED
  // entirely in this mode (never sent as 0 or as the slider's current
  // position), which is also the exact default the sector-median cache
  // (src.api.sector_medians / the trading engine) is generated with, so
  // the default request stays comparable against the default cache.
  const [useCustomAssumptions, setUseCustomAssumptions] = useState(false);
  const [revenueGrowthRate, setRevenueGrowthRate] = useState(0.08);
  const [operatingMargin, setOperatingMargin] = useState(0.25);
  const [terminalGrowthRate, setTerminalGrowthRate] = useState(0.025);

  const [result, setResult] = useState<EvaluationResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<ValuationRequestError | null>(null);

  async function runValuation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const trimmedTicker = ticker.trim().toUpperCase();
    if (!trimmedTicker) {
      setError({ kind: "input", message: "Enter a ticker symbol." });
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const params = new URLSearchParams({
        terminal_growth_rate: String(terminalGrowthRate),
      });
      // Only send explicit growth/margin overrides in custom mode — in
      // historical mode these params are omitted entirely so the backend
      // derives them from the company's own financials, never silently
      // sending the slider's last position while claiming "historical".
      if (useCustomAssumptions) {
        params.set("revenue_growth_rate", String(revenueGrowthRate));
        params.set("operating_margin", String(operatingMargin));
      }

      const response = await fetch(
        `/api/evaluate/${encodeURIComponent(trimmedTicker)}?${params.toString()}`
      );

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw valuationErrorFromResponse(response.status, body);
      }

      const data: EvaluationResponse = await response.json();
      setResult(data);
    } catch (err) {
      setResult(null);
      setError(
        err && typeof err === "object" && "kind" in err && "message" in err
          ? (err as ValuationRequestError)
          : { kind: "unavailable", message: "The valuation service did not respond." }
      );
    } finally {
      setIsLoading(false);
    }
  }

  const priceDelta =
    result && result.current_price !== null
      ? result.intrinsic_value_per_share - result.current_price
      : null;
  const priceDeltaPct =
    priceDelta !== null && result?.current_price ? priceDelta / result.current_price : null;

  return (
    <div className="page-shell">
      <div className="shell-container pb-16">
        <header className="page-header">
          <div>
            <p className="eyebrow mb-2">Valuation workspace</p>
            <h1 className="display-title">Intrinsic value desk</h1>
          </div>
          <p className="page-deck">
            Build a DCF case from company history or your own operating assumptions, then read
            it against market price.
          </p>
        </header>

        <form onSubmit={runValuation} className="panel mb-6 p-5 sm:p-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:gap-4">
            <div className="sm:max-w-xs sm:flex-1">
              <label htmlFor="ticker" className="data-label mb-1.5 block">
                Ticker
              </label>
              <input
                id="ticker"
                type="text"
                value={ticker}
                onChange={(event) => setTicker(event.target.value.toUpperCase())}
                placeholder="AAPL"
                maxLength={10}
                autoComplete="off"
                spellCheck={false}
                className="input-field px-3.5 py-2.5 font-mono text-base tracking-[.06em]"
              />
            </div>

            <button type="submit" disabled={isLoading} className="button-primary w-full gap-2 sm:w-auto">
              {isLoading ? (
                <>
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/35 border-t-white" />
                  Running…
                </>
              ) : (
                "Run Valuation"
              )}
            </button>
          </div>

          <div className="mt-5 border-t border-[var(--line)] pt-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="data-label mb-1.5">Assumption mode</p>
                <p className="max-w-md text-xs leading-5 text-[var(--paper-dim)]">
                  {useCustomAssumptions
                    ? "Growth and margin below are sent as explicit overrides."
                    : "Revenue growth & operating margin are derived from each company's own historical financials."}
                </p>
              </div>
              <div className="mode-tabs" role="group" aria-label="Assumption mode">
                <button
                  type="button"
                  className="mode-tab"
                  aria-pressed={!useCustomAssumptions}
                  onClick={() => setUseCustomAssumptions(false)}
                >
                  Historical
                </button>
                <button
                  type="button"
                  className="mode-tab"
                  aria-pressed={useCustomAssumptions}
                  onClick={() => setUseCustomAssumptions(true)}
                >
                  Custom
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
              <AssumptionField
                id="revenue-growth"
                label="Revenue growth rate"
                value={revenueGrowthRate}
                min={-0.1}
                max={0.4}
                step={0.005}
                precision={1}
                disabled={!useCustomAssumptions}
                onChange={setRevenueGrowthRate}
              />
              <AssumptionField
                id="operating-margin"
                label="Operating margin"
                value={operatingMargin}
                min={0}
                max={0.6}
                step={0.005}
                precision={1}
                disabled={!useCustomAssumptions}
                onChange={setOperatingMargin}
              />
              <AssumptionField
                id="terminal-growth"
                label="Terminal growth rate"
                value={terminalGrowthRate}
                min={0}
                max={0.05}
                step={0.001}
                precision={1}
                onChange={setTerminalGrowthRate}
                helper="Always applied, regardless of mode."
              />
            </div>
          </div>
        </form>

        {error && (
          <div
            className={`${error.kind === "unavailable" ? "status-warning" : "status-error"} mb-6`}
            role="alert"
          >
            <strong className="block text-[var(--paper)]">
              {error.kind === "unavailable" ? "Live valuation is not connected" : "Valuation could not run"}
            </strong>
            <span className="mt-1 block">{error.message}</span>
          </div>
        )}

        {!result && !isLoading && !error && (
          <div className="empty-state px-5 py-10 text-center">
            <strong className="block text-[var(--paper)]">No valuation on the desk yet.</strong>
            <span className="mt-1.5 block">
              Choose a ticker and run the model to compare market price with intrinsic value.
            </span>
          </div>
        )}

        {isLoading && !result && (
          <div className="panel px-5 py-10 text-center text-sm text-[var(--paper-dim)]">
            Fetching financial statements and running the model…
          </div>
        )}

        {result && (
          <div className="space-y-8">
            <div className="panel p-5 sm:p-6">
              <div className="panel-header">
                <h2 className="panel-title">{result.ticker}</h2>
                <SectorBadge sector={result.sector} />
              </div>

              <div className="grid grid-cols-2 divide-x divide-y divide-[var(--line)] border border-[var(--line)] rounded-md sm:grid-cols-4 sm:divide-y-0">
                <div className="p-4">
                  <p className="data-label">Intrinsic value / share</p>
                  <p className="tabular-nums font-mono mt-1.5 text-2xl font-semibold tracking-tight text-[var(--cobalt)]">
                    {formatPreciseCurrency(result.intrinsic_value_per_share)}
                  </p>
                </div>
                <div className="p-4">
                  <p className="data-label">Market price</p>
                  <p className="tabular-nums font-mono mt-1.5 text-2xl font-semibold tracking-tight text-[var(--paper)]">
                    {formatPreciseCurrency(result.current_price)}
                  </p>
                </div>
                <div className="p-4">
                  <p className="data-label">Upside / downside</p>
                  <p
                    className={`tabular-nums font-mono mt-1.5 text-2xl font-semibold tracking-tight ${
                      priceDelta === null
                        ? "text-[var(--paper)]"
                        : priceDelta >= 0
                          ? "text-[var(--verdigris)]"
                          : "text-[var(--signal)]"
                    }`}
                  >
                    {priceDeltaPct !== null
                      ? `${priceDeltaPct >= 0 ? "+" : ""}${(priceDeltaPct * 100).toFixed(1)}%`
                      : "—"}
                  </p>
                  {priceDelta !== null && (
                    <p className="mt-1 text-xs text-[var(--paper-dim)]">
                      {priceDelta >= 0 ? "+" : ""}
                      {formatPreciseCurrency(priceDelta)} vs. market
                    </p>
                  )}
                </div>
                <div className="p-4">
                  <p className="data-label">WACC</p>
                  <p className="tabular-nums font-mono mt-1.5 text-2xl font-semibold tracking-tight text-[var(--paper)]">
                    {formatPercent(result.wacc, 2)}
                  </p>
                </div>
              </div>

              <div className="mt-6 border-t border-[var(--line)] pt-5">
                <ValuationSpreadRail
                  ticker={result.ticker}
                  marketPrice={result.current_price}
                  intrinsicValue={result.intrinsic_value_per_share}
                />
              </div>
            </div>

            <ValuationScenariosSection scenarios={result.scenarios} marketPrice={result.current_price} />

            <SensitivityMatrixSection matrix={result.sensitivity} marketPrice={result.current_price} />

            <div>
              <div className="section-intro">
                <div>
                  <p className="eyebrow mb-1.5">Model detail</p>
                  <h2>Assumptions used</h2>
                </div>
              </div>
              <dl className="divide-y divide-[var(--line)] border-y border-[var(--line)]">
                <div className="flex items-baseline justify-between gap-4 py-2.5">
                  <dt className="text-sm text-[var(--paper-muted)]">Enterprise value</dt>
                  <dd className="tabular-nums font-mono text-sm font-semibold text-[var(--paper)]">
                    {formatCompactCurrency(result.enterprise_value)}
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-4 py-2.5">
                  <dt className="text-sm text-[var(--paper-muted)]">Equity value</dt>
                  <dd className="tabular-nums font-mono text-sm font-semibold text-[var(--paper)]">
                    {formatCompactCurrency(result.equity_value)}
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-4 py-2.5">
                  <dt className="text-sm text-[var(--paper-muted)]">Revenue growth rate used</dt>
                  <dd className="flex items-baseline gap-2 text-right">
                    <span className="tabular-nums font-mono text-sm font-semibold text-[var(--paper)]">
                      {formatPercent(result.assumptions.revenue_growth_rate)}
                    </span>
                    <span className="text-xs text-[var(--paper-dim)]">
                      {result.revenue_growth_rate_source === "historical"
                        ? "Company historical"
                        : "Custom override"}
                    </span>
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-4 py-2.5">
                  <dt className="text-sm text-[var(--paper-muted)]">Operating margin used</dt>
                  <dd className="flex items-baseline gap-2 text-right">
                    <span className="tabular-nums font-mono text-sm font-semibold text-[var(--paper)]">
                      {formatPercent(result.assumptions.operating_margin)}
                    </span>
                    <span className="text-xs text-[var(--paper-dim)]">
                      {result.operating_margin_source === "historical"
                        ? "Company historical"
                        : "Custom override"}
                    </span>
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-4 py-2.5">
                  <dt className="text-sm text-[var(--paper-muted)]">Terminal growth rate</dt>
                  <dd className="tabular-nums font-mono text-sm font-semibold text-[var(--paper)]">
                    {formatPercent(result.assumptions.terminal_growth_rate)}
                  </dd>
                </div>
              </dl>
            </div>

            <div>
              <div className="section-intro">
                <div>
                  <p className="eyebrow mb-1.5">Relative check</p>
                  <h2>Sector-relative valuation</h2>
                </div>
              </div>
              <SectorValuationComparison
                ticker={result.ticker}
                sector={result.sector}
                priceToIntrinsicValue={result.price_to_intrinsic_value}
                sectorMedianPIV={result.sector_median_p_iv}
                sectorMedianUnavailableReason={result.sector_median_unavailable_reason}
              />
            </div>

            <div>
              <div className="section-intro">
                <div>
                  <p className="eyebrow mb-1.5">Forecast detail</p>
                  <h2>Projected free cash flows</h2>
                </div>
              </div>
              <div className="panel p-5 sm:p-6">
                <div className="overflow-x-auto">
                  <table className="data-table w-full min-w-[640px] border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-[var(--line)] text-left">
                        <th className="py-2 pr-4 font-medium">Year</th>
                        <th className="py-2 pr-4 font-medium">Revenue</th>
                        <th className="py-2 pr-4 font-medium">EBIT</th>
                        <th className="py-2 pr-4 font-medium">NOPAT</th>
                        <th className="py-2 pr-4 font-medium">D&amp;A</th>
                        <th className="py-2 pr-4 font-medium">CapEx</th>
                        <th className="py-2 pr-4 font-medium">Δ NWC</th>
                        <th className="py-2 pl-4 text-right font-medium">Free Cash Flow</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.projected_free_cash_flows.map((row) => (
                        <tr key={row.year} className="tabular-nums font-mono text-[var(--paper-muted)]">
                          <td className="py-3 pr-4 font-sans text-[var(--paper-dim)]">Year {row.year}</td>
                          <td className="py-3 pr-4">{formatCompactCurrency(row.revenue)}</td>
                          <td className="py-3 pr-4">{formatCompactCurrency(row.ebit)}</td>
                          <td className="py-3 pr-4">{formatCompactCurrency(row.nopat)}</td>
                          <td className="py-3 pr-4">{formatCompactCurrency(row.da)}</td>
                          <td className="py-3 pr-4">{formatCompactCurrency(row.capex)}</td>
                          <td className="py-3 pr-4">{formatCompactCurrency(row.change_in_nwc)}</td>
                          <td className="py-3 pl-4 text-right font-semibold text-[var(--verdigris)]">
                            {formatCompactCurrency(row.fcf)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
