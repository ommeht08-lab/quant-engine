import { formatCompactCurrency } from "./format";
import ScrollHintTable from "./ScrollHintTable";

export interface FreeCashFlowYear {
  year: number;
  revenue: number;
  ebit: number;
  nopat: number;
  da: number;
  capex: number;
  change_in_nwc: number;
  fcf: number;
}

export interface ProjectedCashFlowsProps {
  rows: FreeCashFlowYear[];
}

// The full-detail projection, deliberately last among the DCF-derived
// sections — by this point the analyst has already seen the thesis,
// the spectrum, sensitivity, and peer comparison; this is the
// supporting arithmetic underneath all of them, not the entry point to
// the story. The Year column stays pinned so it stays legible while
// scrolling the wide row of dollar figures on narrow viewports.
export default function ProjectedCashFlows({ rows }: ProjectedCashFlowsProps) {
  return (
    <div>
      <h2 className="section-title">Projected free cash flows</h2>
      <div className="panel p-5 sm:p-6">
        <ScrollHintTable>
          <table className="data-table w-full min-w-[640px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-[var(--line)] text-left">
                <th className="sticky left-0 z-10 bg-[var(--ink-raised)] py-2 pr-4 font-medium">Year</th>
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
              {rows.map((row) => (
                <tr key={row.year} className="tabular-nums font-mono text-[var(--paper-muted)]">
                  <td className="sticky left-0 z-10 bg-[var(--ink-raised)] py-3 pr-4 font-sans text-[var(--paper-dim)]">
                    Year {row.year}
                  </td>
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
        </ScrollHintTable>
      </div>
    </div>
  );
}
