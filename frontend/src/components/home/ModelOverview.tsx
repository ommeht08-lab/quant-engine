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
    <section className={styles.modelStrip} aria-labelledby="model-strip-title">
      <div className={styles.modelStripHeading}>
        <p>Model in brief</p>
        <h2 id="model-strip-title">From company history to intrinsic value</h2>
      </div>

      <ol className={styles.modelSteps}>
        <li>
          <strong>Resolve the operating case</strong>
          <span>Growth and margin come from the company&apos;s reported history unless you override them.</span>
        </li>
        <li>
          <strong>Project five years of FCFF</strong>
          <span>Two near-term years lead into a three-year maturation path with explicit cash-flow drivers.</span>
        </li>
        <li>
          <strong>Discount and bridge to equity</strong>
          <span>WACC and terminal value produce enterprise value, then cash, debt, and diluted shares produce value per share.</span>
        </li>
      </ol>

      <Link href="/methodology" className={styles.modelLink}>Methodology and limitations →</Link>
    </section>
  );
}
