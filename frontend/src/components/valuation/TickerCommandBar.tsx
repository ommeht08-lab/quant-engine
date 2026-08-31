"use client";

import type { FormEvent } from "react";

export interface TickerCommandBarProps {
  ticker: string;
  onTickerChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  isLoading: boolean;
  useCustomAssumptions: boolean;
  onModeChange: (useCustom: boolean) => void;
}

// The one place data entry happens: ticker + Run are the obvious,
// immediate starting point. The assumption MODE toggle lives here too
// (it is a primary, one-tap decision); the mode-appropriate growth/
// margin/terminal-growth fields themselves live in the assumption tray
// directly beneath this bar (see AssumptionTray), always reachable
// without scrolling. "Assumptions used and equity bridge" further down
// is a read-only report of what was actually used, not a second place
// to edit it.
export default function TickerCommandBar({
  ticker,
  onTickerChange,
  onSubmit,
  isLoading,
  useCustomAssumptions,
  onModeChange,
}: TickerCommandBarProps) {
  return (
    <form onSubmit={onSubmit} className="run-bar">
      <div className="run-bar-field">
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
          autoFocus
          className="input-field px-4 py-3 font-mono text-base tracking-[.06em]"
        />
      </div>

      <div className="run-bar-actions">
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

        <button type="submit" disabled={isLoading} className="button-primary gap-2">
          {isLoading ? (
            <>
              <span className="spinner" aria-hidden="true" />
              Running…
            </>
          ) : (
            "Run Valuation"
          )}
        </button>
      </div>
    </form>
  );
}
