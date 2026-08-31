"use client";

import AssumptionField from "./AssumptionField";

export interface AssumptionTrayProps {
  useCustomAssumptions: boolean;
  revenueGrowthRate: number;
  onRevenueGrowthRateChange: (value: number) => void;
  operatingMargin: number;
  onOperatingMarginChange: (value: number) => void;
  terminalGrowthRate: number;
  onTerminalGrowthRateChange: (value: number) => void;
}

// Sits directly beneath the ticker command bar, ALWAYS visible — an
// analyst can set every assumption the model will actually use before
// the very first run, and adjust them again without scrolling past the
// analysis below: the Run Valuation button immediately above this tray
// re-runs with whatever is set here.
//
// Historical mode only exposes terminal growth, the one assumption
// always sent explicitly regardless of mode — revenue growth and
// operating margin are derived from the company's own financials and
// are not fields a "Historical" run actually uses. Custom mode exposes
// all three the backend will use. Never a field that mode doesn't send.
export default function AssumptionTray({
  useCustomAssumptions,
  revenueGrowthRate,
  onRevenueGrowthRateChange,
  operatingMargin,
  onOperatingMarginChange,
  terminalGrowthRate,
  onTerminalGrowthRateChange,
}: AssumptionTrayProps) {
  return (
    <div className="assumption-tray">
      <p className="assumption-tray-label">
        {useCustomAssumptions ? "Custom assumptions" : "Assumptions"}
      </p>
      <div className={`assumption-tray-fields ${useCustomAssumptions ? "assumption-tray-fields--custom" : ""}`}>
        {useCustomAssumptions && (
          <>
            <AssumptionField
              id="revenue-growth"
              label="Revenue growth rate"
              value={revenueGrowthRate}
              min={-0.1}
              max={0.4}
              step={0.005}
              precision={1}
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
              onChange={onOperatingMarginChange}
            />
          </>
        )}
        <AssumptionField
          id="terminal-growth"
          label="Terminal growth rate"
          value={terminalGrowthRate}
          min={0}
          max={0.05}
          step={0.001}
          precision={1}
          onChange={onTerminalGrowthRateChange}
          helper={useCustomAssumptions ? undefined : "Always sent explicitly, regardless of mode."}
        />
      </div>
    </div>
  );
}
