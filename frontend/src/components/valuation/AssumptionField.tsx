"use client";

import { useState } from "react";
import { formatPercentInputValue, parsePercentInput, type PercentFieldRange } from "@/lib/percent-field";

export interface AssumptionFieldProps {
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

// Shared by the assumption tray (below the ticker command bar, where
// these values are actually set) — a paired numeric box + range slider
// for one percent-valued assumption.
export default function AssumptionField({
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
