"use client";

import { useState, type FormEvent } from "react";
import { formatPercentInputValue, parsePercentInput, type PercentFieldRange } from "@/lib/percent-field";

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

export interface AssumptionsPanelProps {
  ticker: string;
  onTickerChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  isLoading: boolean;
  useCustomAssumptions: boolean;
  onModeChange: (useCustom: boolean) => void;
  revenueGrowthRate: number;
  onRevenueGrowthRateChange: (value: number) => void;
  operatingMargin: number;
  onOperatingMarginChange: (value: number) => void;
  terminalGrowthRate: number;
  onTerminalGrowthRateChange: (value: number) => void;
}

// The workspace's control surface — a flatter command bar, not another
// boxed panel: ticker + primary action are prominent and first; the
// assumption mode/sliders are a visually quieter, secondary tier below a
// rule. No valuation math happens here — every value is either local
// form state or passed straight through to the caller.
export default function AssumptionsPanel({
  ticker,
  onTickerChange,
  onSubmit,
  isLoading,
  useCustomAssumptions,
  onModeChange,
  revenueGrowthRate,
  onRevenueGrowthRateChange,
  operatingMargin,
  onOperatingMarginChange,
  terminalGrowthRate,
  onTerminalGrowthRateChange,
}: AssumptionsPanelProps) {
  return (
    <form onSubmit={onSubmit} className="command-surface">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:gap-4">
        <div className="sm:max-w-xs sm:flex-1">
          <label htmlFor="ticker" className="data-label mb-1.5 block">
            Ticker
          </label>
          <input
            id="ticker"
            type="text"
            value={ticker}
            onChange={(event) => onTickerChange(event.target.value.toUpperCase())}
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

      <div className="command-surface-secondary">
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
              onClick={() => onModeChange(false)}
            >
              Historical
            </button>
            <button
              type="button"
              className="mode-tab"
              aria-pressed={useCustomAssumptions}
              onClick={() => onModeChange(true)}
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
            onChange={onRevenueGrowthRateChange}
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
            onChange={onOperatingMarginChange}
          />
          <AssumptionField
            id="terminal-growth"
            label="Terminal growth rate"
            value={terminalGrowthRate}
            min={0}
            max={0.05}
            step={0.001}
            precision={1}
            onChange={onTerminalGrowthRateChange}
            helper="Always applied, regardless of mode."
          />
        </div>
      </div>
    </form>
  );
}
