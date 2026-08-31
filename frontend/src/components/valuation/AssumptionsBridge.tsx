import { formatCompactCurrency, formatPercent } from "./format";

export interface AssumptionsBridgeResult {
  enterprise_value: number;
  equity_value: number;
  assumptions: {
    revenue_growth_rate: number;
    operating_margin: number;
    terminal_growth_rate: number;
  };
  revenue_growth_rate_source: "historical" | "custom";
  operating_margin_source: "historical" | "custom";
}

export interface AssumptionsBridgeProps {
  result: AssumptionsBridgeResult;
}

// The last stop in the analysis order, deliberately: by the time an
// analyst reaches this section they have already seen the thesis, the
// sensitivity, and the peer comparison — this is a read-only record of
// exactly what fed the model. Every assumption is set at the top of the
// page, in the assumption tray beneath the ticker command bar; this
// section never duplicates those editable controls, it only reports
// what was actually used and the equity bridge it produced.
export default function AssumptionsBridge({ result }: AssumptionsBridgeProps) {
  return (
    <div>
      <h2 className="section-title">Assumptions used and equity bridge</h2>

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
              {result.revenue_growth_rate_source === "historical" ? "Company historical" : "Custom override"}
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
              {result.operating_margin_source === "historical" ? "Company historical" : "Custom override"}
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
  );
}
