import Link from "next/link";

export interface ModelOverviewProps {
  styles: Record<string, string>;
}

/**
 * A plain-language description of the staged DCF, grounded in
 * `docs/model-specifications/dcf.md` and the fields the API itself
 * returns (`forecast_path` stages, `assumptions`, `valuation_quality`) —
 * not a marketing summary. Every claim below is checkable against that
 * spec; nothing here is asserted about the model that the code doesn't
 * actually do.
 */
export default function ModelOverview({ styles }: ModelOverviewProps) {
  return (
    <div className={styles.panel}>
      <div className={styles.panelHeading}>
        <div>
          <p>Grounded in the implementation</p>
          <h2>How the model works</h2>
        </div>
        <Link href="/methodology" className={styles.modelLink}>
          Full methodology →
        </Link>
      </div>

      <ol className={styles.modelSteps}>
        <li>
          <strong>Growth and margin come from the company itself.</strong>
          <span>
            Unless you supply custom assumptions, revenue growth and operating margin are derived
            from the company&apos;s own reported financial history, not a single generic assumption
            applied to every ticker.
          </span>
        </li>
        <li>
          <strong>A five-year staged forecast.</strong>
          <span>
            Two near-term years hold the resolved growth rate; the three maturation years after
            them fade only the excess above a mature ceiling toward it — a weak or negative
            growth rate is never turned into an assumed recovery.
          </span>
        </li>
        <li>
          <strong>Unlevered free cash flow (FCFF), built up explicitly.</strong>
          <span>
            Each projected year&apos;s FCFF is operating profit after tax, plus depreciation
            &amp; amortization, minus capital expenditure, minus the change in net working
            capital.
          </span>
        </li>
        <li>
          <strong>Discounted at the company&apos;s own cost of capital, plus a terminal value.</strong>
          <span>
            The five explicit years and a terminal value covering everything beyond them are both
            discounted back to the present at the Weighted Average Cost of Capital (WACC) — a
            policy floor/ceiling can bound an extreme computed rate; the workspace flags it
            explicitly when that happens.
          </span>
        </li>
        <li>
          <strong>Enterprise value bridges to a per-share figure.</strong>
          <span>
            Enterprise value becomes equity value after adjusting for cash and total debt, then
            divides by diluted shares outstanding.
          </span>
        </li>
        <li>
          <strong>Bear, Base, and Bull are policy cases, not predictions.</strong>
          <span>
            The three cases apply fixed, disclosed shifts to growth, margin, WACC, and terminal
            growth — they are not probabilities, not a forecast range, and not a recommendation.
          </span>
        </li>
        <li>
          <strong>Quality checks withhold comparisons, not numbers.</strong>
          <span>
            When a result trips a diagnostic (e.g. a negative terminal cash flow), the underlying
            calculations stay visible for audit, but the market-price and peer comparisons are
            withheld rather than shown as if they meant something they don&apos;t.
          </span>
        </li>
      </ol>

      <div className={styles.modelLimitations}>
        <p className={styles.modelLimitationsLabel}>Read this before treating a result as advice</p>
        <ul>
          <li>This is a discounted cash flow model, not a full three-statement forecast.</li>
          <li>The live dashboard sources financial statements from its existing data provider.</li>
          <li>Every result is a research output — not investment advice, and not a price target.</li>
          <li>A mathematically valid result is not automatically an economically credible one.</li>
        </ul>
      </div>
    </div>
  );
}
