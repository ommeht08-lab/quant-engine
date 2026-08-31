import { classifySensitivityCell, sensitivityCellAccessibleLabel } from "@/lib/sensitivity-cell";
import { formatPercent, formatPreciseCurrency } from "./format";
import { sensitivityCellToneAndGlyph } from "./ValuationSpectrum";
import ScrollHintTable from "./ScrollHintTable";

interface SensitivityAxis {
  label: string;
  values: number[];
  baseline_index: number;
}

export interface DCFSensitivityMatrix {
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

interface SensitivityMatrixSectionProps {
  matrix: DCFSensitivityMatrix;
  marketPrice: number | null;
}

// Renders the already-computed grid — every cell is exactly what the API
// returned; no DCF math happens in this component.
export default function SensitivityMatrix({ matrix, marketPrice }: SensitivityMatrixSectionProps) {
  const hasMarketPrice = marketPrice !== null;

  return (
    <div>
      <h2 className="section-title">DCF sensitivity</h2>

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

        <ScrollHintTable>
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
                <th scope="col" className="sticky left-0 z-10 bg-[var(--ink-raised)] py-2 pr-4 font-medium">
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
        </ScrollHintTable>

        <p className="mt-4 text-xs leading-5 text-[var(--paper-dim)]">
          Projected cash flows and the equity bridge remain fixed. Rows vary WACC; columns vary
          terminal growth.
        </p>
      </div>
    </div>
  );
}
