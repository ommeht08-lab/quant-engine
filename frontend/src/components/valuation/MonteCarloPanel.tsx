"use client";

import { useEffect, useRef, useState, type FormEvent, useSyncExternalStore } from "react";

import {
  EXPERIMENT_LABELS,
  MARGIN_BASIS_LABEL,
  axisDomain,
  buildProvenanceSections,
  describeAboveMarket,
  describeQuoteTime,
  describeRequestError,
  failureSummary,
  histogramBars,
  interquantileWidth,
  marginBasisNotice,
  markerPlacement,
  parseSeedInput,
  refusalCopy,
  spreadIllustrationText,
  validCountText,
  type MonteCarloRequestError,
  type ProvenanceSection,
} from "@/lib/monte-carlo-display";
import {
  EXPERIMENT_NAMES,
  isMonteCarloResponse,
  readBaselineProvenance,
  type ExperimentName,
  type McExperiment,
  type MonteCarloDescription,
  type MonteCarloResponse,
} from "@/lib/monte-carlo-types";
import { settingsMismatch, type SettingsMismatch } from "@/lib/monte-carlo-metadata";
import { formatPercent, formatPreciseCurrency } from "./format";
import styles from "./MonteCarloPanel.module.css";
import ScrollHintTable from "./ScrollHintTable";

const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

function axisLabel(value: number, span: number): string {
  return span < 20 ? formatPreciseCurrency(value) : usd0.format(value);
}

function hasResults(experiment: McExperiment): boolean {
  return experiment.valid > 0 && experiment.histogram !== null;
}

function dateText(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}

// ---------------------------------------------------------------- chart ----

const W = 760;
const H = 300;
const PAD = { left: 56, right: 20, top: 56, bottom: 44 };

function Histogram({ result, name }: { result: MonteCarloResponse; name: ExperimentName }) {
  const experiment = result.experiments[name];
  const edges = result.histogram_edges;
  const histogram = experiment.histogram;
  const scroller = useRef<HTMLDivElement>(null);

  // On narrow screens the chart scrolls sideways; open it centered on the simulated median
  // rather than on an empty left edge.
  useEffect(() => {
    const el = scroller.current;
    const stats = experiment.statistics;
    if (!el || !edges || !stats || el.scrollWidth <= el.clientWidth) return;
    const domain = axisDomain(edges, [result.market_price, result.baseline.intrinsic_value_per_share]);
    const medianX = PAD.left + ((stats.p50 - domain.low) / (domain.high - domain.low)) * (W - PAD.left - PAD.right);
    el.scrollLeft = Math.max(0, (medianX / W) * el.scrollWidth - el.clientWidth / 2);
  }, [result, name, edges, experiment.statistics]);

  if (edges === null || histogram === null || experiment.statistics === null) return null;

  const bars = histogramBars(edges, histogram.counts);
  const tallest = Math.max(...histogram.counts, 1);
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const baseline = result.baseline.intrinsic_value_per_share;
  const stats = experiment.statistics;
  // The axis is stretched to include the market price and baseline when that is a modest stretch.
  const domain = axisDomain(edges, [result.market_price, baseline]);
  const axis = [domain.low, domain.high];
  const span = domain.high - domain.low;
  const x = (value: number) => PAD.left + ((value - domain.low) / span) * plotW;

  const market = markerPlacement(result.market_price, axis);
  const model = markerPlacement(baseline, axis);
  const median = markerPlacement(stats.p50, axis);

  const markers = [
    { key: "market", label: `Market ${formatPreciseCurrency(result.market_price)}`, place: market, cls: styles.marketLine },
    { key: "baseline", label: `Baseline ${formatPreciseCurrency(baseline)}`, place: model, cls: styles.baselineLine },
    { key: "median", label: `Median ${formatPreciseCurrency(stats.p50)}`, place: median, cls: styles.medianLine },
  ]
    .filter((m): m is typeof m & { place: { inRange: true; fraction: number } } => m.place !== null && m.place.inRange)
    .sort((a, b) => a.place.fraction - b.place.fraction);

  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const outside = [
    market && !market.inRange ? `The market price (${formatPreciseCurrency(result.market_price)}) is ${market.side} the plotted range.` : null,
    model && !model.inRange ? `The deterministic baseline (${formatPreciseCurrency(baseline)}) is ${model.side} the plotted range.` : null,
    histogram.underflow > 0 ? `${histogram.underflow.toLocaleString("en-US")} valuation(s) fall below ${axisLabel(edges[0], span)}.` : null,
    histogram.overflow > 0 ? `${histogram.overflow.toLocaleString("en-US")} valuation(s) fall above ${axisLabel(edges[edges.length - 1], span)}.` : null,
  ].filter((line): line is string => line !== null);

  const aria =
    `Histogram of ${experiment.valid.toLocaleString("en-US")} simulated values per share for ${EXPERIMENT_LABELS[name]}. ` +
    `Median ${formatPreciseCurrency(stats.p50)}, 10th to 90th percentile ${formatPreciseCurrency(stats.p10)} to ${formatPreciseCurrency(stats.p90)}. ` +
    `Market price ${formatPreciseCurrency(result.market_price)}.`;

  const bandLow = Math.max(stats.p10, edges[0]);
  const bandHigh = Math.min(stats.p90, edges[edges.length - 1]);

  return (
    <>
      <div ref={scroller} className={styles.chartScroll} tabIndex={0} aria-label="Simulated value histogram; scroll on small screens">
        <svg className={styles.chart} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={aria}>
          <title>{`Simulated value per share: ${EXPERIMENT_LABELS[name]}`}</title>
          {[0, 0.5, 1].map((f) => (
            <g key={f}>
              <line x1={PAD.left} x2={W - PAD.right} y1={PAD.top + plotH * (1 - f)} y2={PAD.top + plotH * (1 - f)} className={styles.grid} />
              <text x={PAD.left - 8} y={PAD.top + plotH * (1 - f) + 4} textAnchor="end">
                {Math.round(tallest * f).toLocaleString("en-US")}
              </text>
            </g>
          ))}
          {bandHigh > bandLow && (
            <rect x={x(bandLow)} y={PAD.top} width={Math.max(x(bandHigh) - x(bandLow), 0)} height={plotH} className={styles.band} />
          )}
          {bars.map((bar, i) => (
            <rect key={i} x={x(bar.x0) + 0.5} y={PAD.top + plotH * (1 - bar.height)} width={Math.max(x(bar.x1) - x(bar.x0) - 1, 0.5)} height={plotH * bar.height} className={styles.bar}>
              <title>{`${axisLabel(bar.x0, span)} to ${axisLabel(bar.x1, span)}: ${bar.count.toLocaleString("en-US")} valuations`}</title>
            </rect>
          ))}
          <line x1={PAD.left} x2={W - PAD.right} y1={PAD.top + plotH} y2={PAD.top + plotH} className={styles.axis} />
          {ticks.map((f) => (
            <text key={f} x={PAD.left + f * plotW} y={H - PAD.bottom + 20} textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"}>
              {axisLabel(domain.low + span * f, span)}
            </text>
          ))}
          {markers.map((marker, i) => {
            const mx = PAD.left + marker.place.fraction * plotW;
            const anchor = marker.place.fraction > 0.8 ? "end" : marker.place.fraction < 0.2 ? "start" : "middle";
            return (
              <g key={marker.key}>
                <line x1={mx} x2={mx} y1={PAD.top - 8 - (i % 3) * 14} y2={PAD.top + plotH} className={marker.cls} />
                <text x={mx} y={PAD.top - 12 - (i % 3) * 14} textAnchor={anchor} className={styles.markerLabel}>
                  {marker.label}
                </text>
              </g>
            );
          })}
          <text x={14} y={PAD.top + plotH / 2} transform={`rotate(-90 14 ${PAD.top + plotH / 2})`} textAnchor="middle">
            Valuations per bin
          </text>
        </svg>
      </div>
      <p className={styles.scrollHint}>Swipe the chart sideways to see all of it.</p>
      <div className={styles.legend} aria-hidden="true">
        <span className={styles.legendMarket}>Market price at snapshot</span>
        <span className={styles.legendBaseline}>Deterministic baseline</span>
        <span className={styles.legendMedian}>Simulated median</span>
        <span className={styles.legendBand}>10th–90th percentile</span>
      </div>
      {outside.length > 0 && <p className={styles.outside}>{outside.join(" ")}</p>}
    </>
  );
}

// -------------------------------------------------------------- sections ----

function Notices({ result }: { result: MonteCarloResponse }) {
  const { baseline, history } = result;
  const notes: { title: string; body: string }[] = [];

  const unfinished = EXPERIMENT_NAMES.filter((name) => result.experiments[name].status !== "complete");
  if (unfinished.length > 0) {
    notes.push({
      title: result.status === "incomplete" ? "No experiment produced results" : "Some experiments are incomplete",
      body: unfinished
        .map((name) => {
          const e = result.experiments[name];
          return e.status === "refused"
            ? `${EXPERIMENT_LABELS[name]}: not run. ${refusalCopy(e.refusal_code)}`
            : `${EXPERIMENT_LABELS[name]}: ${validCountText(e)}.`;
        })
        .join(" "),
    });
  }
  if (baseline.revenue_growth_rate_was_capped && history.growth_center_cagr !== undefined) {
    const distribution = (result.experiments.growth.distributions.growth ?? result.experiments.combined.distributions.growth) as
      | { untruncated_mass_above_upper?: number; upper?: number }
      | undefined;
    const above = distribution?.untruncated_mass_above_upper;
    notes.push({
      title: "Historical growth is above the model's ceiling",
      body:
        `The company's revenue CAGR is ${formatPercent(history.growth_center_cagr, 1)}; the deterministic model caps it at ${formatPercent(baseline.revenue_growth_rate, 0)}. ` +
        `Growth draws come from a normal centered on ${formatPercent(history.growth_center_cagr, 1)} truncated at the ceiling` +
        (above !== undefined ? `; ${formatPercent(above, 1)} of that untruncated distribution lies above it, so sampled growth sits below its center.` : "."),
    });
  }
  if (baseline.wacc_was_clamped) {
    notes.push({
      title: "Baseline discount rate was clamped",
      body: `The model computed ${formatPercent(baseline.wacc_pre_clamp, 2)} WACC and used ${formatPercent(baseline.wacc, 2)}. Sampled WACC stays within the model's bounds, so the interval below may be narrower than ±1 point.`,
    });
  }
  const provenance = readBaselineProvenance(result);
  if (provenance.kind === "ok") {
    const basis = marginBasisNotice(provenance.value.margin.rows_used, provenance.value.margin.mixed_rows);
    if (basis) notes.push({ title: "Margin basis differs from the v1 default", body: basis });
  }
  const negatives = EXPERIMENT_NAMES.reduce((sum, name) => sum + result.experiments[name].negative_value_count, 0);
  if (negatives > 0) {
    notes.push({
      title: "Some simulated values are negative",
      body: "A negative value is a modeled-equity outcome (debt exceeding enterprise value), counted as a valid valuation rather than discarded.",
    });
  }
  if (notes.length === 0) return null;
  return (
    <>
      {notes.map((note) => (
        <div key={note.title} className={styles.notice} role="status">
          <strong>{note.title}</strong>
          {note.body}
        </div>
      ))}
    </>
  );
}

function ComparisonTable({ result }: { result: MonteCarloResponse }) {
  return (
    <section className={`panel ${styles.panel}`} aria-labelledby="mc-compare">
      <h2 id="mc-compare">Which input moves the estimate most?</h2>
      <ScrollHintTable label="Experiment comparison">
        <table className={styles.table}>
          <caption>
            Each row varies only the listed input(s) over the selected ranges. These show sensitivity under those ranges —
            not a formal variance attribution, a probability of mispricing, or evidence of predictive accuracy.
          </caption>
          <thead>
            <tr>
              <th scope="col">Experiment</th>
              <th scope="col">Valid</th>
              <th scope="col">10th pct</th>
              <th scope="col">Median</th>
              <th scope="col">90th pct</th>
              <th scope="col">Width (90th − 10th)</th>
              <th scope="col">Above market</th>
            </tr>
          </thead>
          <tbody>
            {EXPERIMENT_NAMES.map((name) => {
              const e = result.experiments[name];
              const above = describeAboveMarket(e);
              if (e.status === "refused") {
                return (
                  <tr key={name} data-refused="true">
                    <td>
                      {EXPERIMENT_LABELS[name]}
                      <small>{refusalCopy(e.refusal_code)}</small>
                    </td>
                    <td colSpan={6}>Not run</td>
                  </tr>
                );
              }
              const width = interquantileWidth(e);
              return (
                <tr key={name}>
                  <td>
                    {EXPERIMENT_LABELS[name]}
                    <small>{e.status === "incomplete" ? validCountText(e) : ""}</small>
                  </td>
                  <td>{e.valid.toLocaleString("en-US")}</td>
                  <td>{e.statistics ? formatPreciseCurrency(e.statistics.p10) : "—"}</td>
                  <td>{e.statistics ? formatPreciseCurrency(e.statistics.p50) : "—"}</td>
                  <td>{e.statistics ? formatPreciseCurrency(e.statistics.p90) : "—"}</td>
                  <td>{width === null ? "—" : formatPreciseCurrency(width)}</td>
                  <td>{above ? `${above.percent} (${above.detail})` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollHintTable>
    </section>
  );
}

function DistributionRow({ label, spec }: { label: string; spec: Record<string, unknown> | undefined }) {
  if (!spec) return null;
  const n = (key: string) => (typeof spec[key] === "number" ? (spec[key] as number) : null);
  if (spec.distribution === "uniform") {
    const lower = n("lower");
    const upper = n("upper");
    const constraints = Array.isArray(spec.binding_constraints) ? (spec.binding_constraints as string[]) : [];
    return (
      <div>
        <dt>{label}</dt>
        <dd>
          Uniform {lower !== null ? formatPercent(lower, 2) : "—"} to {upper !== null ? formatPercent(upper, 2) : "—"} (baseline{" "}
          {n("baseline_applied_wacc") !== null ? formatPercent(n("baseline_applied_wacc")!, 2) : "—"}
          {spec.baseline_was_clamped_to_model_bounds ? ", clamped to a model bound" : ""}
          {constraints.length ? `; limited by ${constraints.join(", ").replaceAll("_", " ")}` : ""}).
        </dd>
      </div>
    );
  }
  const center = n("center");
  const sd = n("sd");
  const lower = n("lower");
  const upper = n("upper");
  const below = n("untruncated_mass_below_lower");
  const above = n("untruncated_mass_above_upper");
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        Normal centered on {center !== null ? formatPercent(center, 2) : "—"}, spread {sd !== null ? formatPercent(sd, 2) : "—"}, truncated to{" "}
        {lower !== null ? formatPercent(lower, 0) : "—"} – {upper !== null ? formatPercent(upper, 0) : "—"}. Untruncated probability outside the
        range: {below !== null ? formatPercent(below, 2) : "—"} below, {above !== null ? formatPercent(above, 2) : "—"} above.
      </dd>
    </div>
  );
}

type SpecMap = Record<string, Record<string, unknown>>;

/** The sampling spec for one input, taken from whichever experiment sampled it. */
function findSpec(result: MonteCarloResponse, key: "growth" | "margin" | "wacc"): Record<string, unknown> | undefined {
  for (const name of ["combined", key] as const) {
    const spec = (result.experiments[name].distributions as SpecMap)[key];
    if (spec) return spec;
  }
  return undefined;
}

function InputsPanel({ result }: { result: MonteCarloResponse }) {
  const { history, baseline } = result;
  const reading = readBaselineProvenance(result);
  const illustrations =
    reading.kind === "ok"
      ? [
          spreadIllustrationText("growth", reading.value.spread_illustration.growth),
          spreadIllustrationText("margin (EBIT basis)", reading.value.spread_illustration.margin),
        ].filter((text): text is string => text !== null)
      : [];
  return (
    <section className={`panel ${styles.panel}`} aria-labelledby="mc-inputs">
      <h2 id="mc-inputs">Ranges used</h2>
      <p className={styles.lede}>
        Spreads come only from this company&apos;s own annual history: sample standard deviations of{" "}
        {history.n_growth ?? "—"} year-over-year growth and {history.n_margin ?? "—"} margin observations ({MARGIN_BASIS_LABEL}). With so few observations
        these are noisy estimates, not precise measurements.
      </p>
      {illustrations.length > 0 && (
        <ul className={styles.disclosures}>
          {illustrations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      )}
      <div className={styles.twoColumns}>
        <div>
          <h3>Distributions</h3>
          <dl className={styles.definitionList}>
            <DistributionRow label="Revenue growth" spec={findSpec(result, "growth")} />
            <DistributionRow label={`Operating margin (${MARGIN_BASIS_LABEL})`} spec={findSpec(result, "margin")} />
            <DistributionRow label="Discount rate" spec={findSpec(result, "wacc")} />
            <div>
              <dt>Terminal growth</dt>
              <dd>Fixed at {formatPercent(Number((result.config as { terminal_growth_rate?: number }).terminal_growth_rate ?? 0), 2)}.</dd>
            </div>
          </dl>
        </div>
        <div>
          <h3>Deterministic baseline</h3>
          <dl className={styles.definitionList}>
            <div>
              <dt>Value per share</dt>
              <dd>{formatPreciseCurrency(baseline.intrinsic_value_per_share)}</dd>
            </div>
            <div>
              <dt>Growth (after ceiling)</dt>
              <dd>
                {formatPercent(baseline.revenue_growth_rate, 2)}
                {history.growth_center_cagr !== undefined ? ` · historical CAGR ${formatPercent(history.growth_center_cagr, 2)}` : ""}
              </dd>
            </div>
            <div>
              <dt>Operating margin ({MARGIN_BASIS_LABEL})</dt>
              <dd>{formatPercent(baseline.operating_margin, 2)}</dd>
            </div>
            <div>
              <dt>WACC</dt>
              <dd>{formatPercent(baseline.wacc, 2)}</dd>
            </div>
            {history.revenue_periods && (
              <div>
                <dt>Revenue periods</dt>
                <dd>{history.revenue_periods.join(", ")}</dd>
              </div>
            )}
            {history.status === "refused" && (
              <div>
                <dt>History</dt>
                <dd>{refusalCopy(history.refusal_code ?? null)}</dd>
              </div>
            )}
          </dl>
        </div>
      </div>
    </section>
  );
}

const FLAG_LABEL: Record<NonNullable<ProvenanceSection["items"][number]["flag"]>, string> = {
  fallback: "Default used",
  fixed: "Fixed",
  caution: "Note",
};

function BaselineSources({ reading }: { reading: ReturnType<typeof readBaselineProvenance> }) {
  if (reading.kind !== "ok") {
    return (
      <section className={`panel ${styles.panel}`} aria-labelledby="mc-sources">
        <h2 id="mc-sources">Where the baseline comes from</h2>
        <p className={styles.lede}>
          {reading.kind === "absent"
            ? "This result predates source reporting, so the statement rows, periods and fallbacks behind the baseline are not listed."
            : "The source details for this result could not be read. The simulated numbers above are unaffected."}
        </p>
      </section>
    );
  }
  const sections = buildProvenanceSections(reading.value, dateText);
  return (
    <section className={`panel ${styles.panel}`} aria-labelledby="mc-sources">
      <h2 id="mc-sources">Where the baseline comes from</h2>
      <p className={styles.lede}>
        Each figure below is taken from how the deterministic valuation actually resolved it. &ldquo;Default used&rdquo; means a documented
        default replaced a figure the statements did not provide; &ldquo;Fixed&rdquo; means a constant that is not company data and is not varied.
      </p>
      <div className={styles.sourceGrid}>
        {sections.map((section) => (
          <div key={section.id} data-section={section.id}>
            <h3>{section.title}</h3>
            <dl className={styles.definitionList}>
              {section.items.map((item) => (
                <div key={item.label}>
                  <dt>{item.label}</dt>
                  <dd>
                    {item.value}
                    {item.flag && (
                      <span className={`${styles.badge} ${styles[`badge_${item.flag}`]}`} data-flag={item.flag}>
                        {FLAG_LABEL[item.flag]}
                      </span>
                    )}
                    {item.detail && <small className={styles.detail}>{item.detail}</small>}
                  </dd>
                </div>
              ))}
            </dl>
            {section.table && (
              <ScrollHintTable>
                <table className={styles.sourceTable}>
                  <caption>{section.table.caption}</caption>
                  <thead>
                    <tr>
                      {section.table.columns.map((column) => (
                        <th key={column} scope="col">
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {section.table.rows.map((row) => (
                      <tr key={row[0]}>
                        {row.map((cell, i) => (
                          <td key={i}>{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ScrollHintTable>
            )}
          </div>
        ))}
      </div>
      <p className={styles.counts}>
        Source digest <span className={styles.mono}>{reading.value.provenance_digest}</span>
      </p>
    </section>
  );
}

// ------------------------------------------------------------- results ----

/** The complete result view for one response. Pure of fetching so it can be rendered from fixtures. */
export function MonteCarloResults({
  result,
  selected,
  onSelect,
}: {
  result: MonteCarloResponse;
  selected: ExperimentName;
  onSelect: (name: ExperimentName) => void;
}) {
  const effective: ExperimentName | null = hasResults(result.experiments[selected])
    ? selected
    : EXPERIMENT_NAMES.find((name) => hasResults(result.experiments[name])) ?? null;
  const combined = result.experiments.combined;
  const headline = combined.statistics ? combined : null;
  const above = headline ? describeAboveMarket(headline) : null;
  const provenance = readBaselineProvenance(result);

  return (
    <>
      <div className={styles.context}>
        <strong>{result.identity.ticker}</strong>
        <span>Snapshot captured {dateText(result.identity.snapshot_captured_at)}</span>
        {result.identity.statement_period_end && <span>Latest statements to {result.identity.statement_period_end}</span>}
        <span>
          Market price {formatPreciseCurrency(result.market_price)} (
          {describeQuoteTime(provenance.kind === "ok" ? provenance.value.timing.market_price.observed_at : undefined, dateText)})
        </span>
        <span>Seed {result.seed}</span>
        <span>{result.meta.cache === "hit" ? "Served from cache" : "Freshly computed"}</span>
      </div>

      <Notices result={result} />

      <section className={`panel ${styles.panel}`} aria-labelledby="mc-headline">
        <h2 id="mc-headline">All three inputs together</h2>
        {headline && headline.statistics ? (
          <>
            <dl className={styles.metrics}>
              <div>
                <dt>Median value / share</dt>
                <dd>{formatPreciseCurrency(headline.statistics.p50)}</dd>
              </div>
              <div>
                <dt>10th – 90th percentile</dt>
                <dd>
                  {formatPreciseCurrency(headline.statistics.p10)} – {formatPreciseCurrency(headline.statistics.p90)}
                </dd>
              </div>
              <div>
                <dt>Deterministic baseline</dt>
                <dd>{formatPreciseCurrency(result.baseline.intrinsic_value_per_share)}</dd>
              </div>
              <div>
                <dt>Market price at snapshot</dt>
                <dd>{formatPreciseCurrency(result.market_price)}</dd>
              </div>
            </dl>
            {above && (
              <p className={styles.comparison}>
                {above.label}: <strong>{above.percent}</strong> ({above.detail}).
              </p>
            )}
            <p className={styles.counts}>
              {validCountText(headline)} · {headline.attempted.toLocaleString("en-US")} attempted ·{" "}
              {headline.failed_total.toLocaleString("en-US")} failed and redrawn
              {failureSummary(headline).length ? ` (${failureSummary(headline).join("; ")})` : ""} · {headline.rejected_proposals} proposals rejected by truncation
            </p>
          </>
        ) : (
          <p className={styles.lede}>
            {combined?.status === "refused" ? `The combined experiment was not run. ${refusalCopy(combined.refusal_code)}` : "The combined experiment produced no valid valuations."}{" "}
            The experiments that did run are shown below.
          </p>
        )}
      </section>

      {effective && (
        <section className={`panel ${styles.panel}`} aria-labelledby="mc-chart">
          <div className={styles.chartHeader}>
            <div>
              <h2 id="mc-chart">Distribution of simulated value per share</h2>
              <p className={styles.lede}>
                {EXPERIMENT_LABELS[effective]} · {validCountText(result.experiments[effective])} · shared axis across experiments
                {result.histogram_edges_basis === "pooled" ? " (scaled from the available experiments)" : ""}
              </p>
            </div>
            <div className={styles.experimentTabs} role="group" aria-label="Experiment shown in the histogram">
              {EXPERIMENT_NAMES.map((name) => (
                <button key={name} type="button" aria-pressed={effective === name} disabled={!hasResults(result.experiments[name])} onClick={() => onSelect(name)}>
                  {name === "combined" ? "Combined" : name === "wacc" ? "WACC" : name === "margin" ? "Margin (EBIT basis)" : "Growth"}
                </button>
              ))}
            </div>
          </div>
          <Histogram result={result} name={effective} />
        </section>
      )}

      <ComparisonTable result={result} />
      <InputsPanel result={result} />
      <BaselineSources reading={provenance} />

      <section className={`panel ${styles.panel}`} aria-labelledby="mc-limits">
        <h2 id="mc-limits">Read this before relying on it</h2>
        <ul className={styles.disclosures}>
          {result.disclosures.map((d) => (
            <li key={d.id}>{d.text}</li>
          ))}
        </ul>
      </section>

      <section className={`panel ${styles.panel}`} aria-labelledby="mc-repro">
        <h2 id="mc-repro">Reproduce this run</h2>
        <dl className={styles.definitionList}>
          <div><dt>Seed</dt><dd>{result.seed}</dd></div>
          <div><dt>Snapshot</dt><dd className={styles.mono}>{result.identity.snapshot_id}</dd></div>
          <div><dt>Result digest</dt><dd className={styles.mono}>{result.result_digest}</dd></div>
          <div><dt>Model version</dt><dd>{result.model_version}</dd></div>
          <div><dt>Computed</dt><dd>{dateText(result.meta.computed_at)}</dd></div>
        </dl>
        <p className={styles.lede}>
          The replay bundle holds the frozen statements and price, the seed and the full configuration. Replaying it offline
          (<span className={styles.mono}>python -m src.dcf_model.monte_carlo --bundle FILE</span>) first checks that nothing in the bundle
          was altered, then recomputes the numbers. On the same kind of computer the result digest matches exactly; on a different one
          the last digits can differ, so the report instead says the numbers agree within a stated tolerance, with every count identical.
          It never calls a replay exact when it is not.
        </p>
        <a className={`button-secondary ${styles.exportLink}`} href={`/api/monte-carlo/snapshots/${encodeURIComponent(result.identity.snapshot_id)}?seed=${result.seed}`} download>
          Download replay bundle
        </a>
      </section>

    </>
  );
}

// ------------------------------------------------------------ main panel ----

const subscribeNever = () => () => {};

export default function MonteCarloPanel({ metadata }: { metadata: MonteCarloDescription }) {
  // The controls are in the server-rendered HTML but do nothing until React attaches (a click then would submit the
  // form natively and reload the page), so they stay disabled until hydration. false on the server and during hydration.
  const hydrated = useSyncExternalStore(subscribeNever, () => true, () => false);
  const [ticker, setTicker] = useState(metadata.tickers[0] ?? "");
  const [seedText, setSeedText] = useState("");
  const [result, setResult] = useState<MonteCarloResponse | null>(null);
  const [error, setError] = useState<MonteCarloRequestError | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  // The controls need no service, but a run does, and an idle service can take several seconds to start.
  const [startSlow, setStartSlow] = useState(false);
  const [mismatch, setMismatch] = useState<SettingsMismatch | null>(null);
  const [selected, setSelected] = useState<ExperimentName>("combined");
  const sequence = useRef(0);

  useEffect(() => {
    if (!isLoading) return;
    const timer = window.setTimeout(() => setStartSlow(true), 3000);
    return () => {
      window.clearTimeout(timer);
      setStartSlow(false);
    };
  }, [isLoading]);

  async function run(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ticker) return;
    const seedRaw = seedText.trim();
    const seed = seedRaw === "" ? null : parseSeedInput(seedRaw);
    if (seedRaw !== "" && seed === null) {
      setError({ kind: "input", message: "The seed must be a whole number from 0 to 9007199254740991." });
      return;
    }
    const id = ++sequence.current;
    setIsLoading(true);
    setError(null);
    try {
      const query = seed === null ? "" : `?seed=${seed}`;
      const response = await fetch(`/api/monte-carlo/${encodeURIComponent(ticker)}${query}`);
      const body: unknown = await response.json().catch(() => null);
      if (id !== sequence.current) return;
      if (!response.ok) {
        setError(describeRequestError(response.status, body));
        return;
      }
      if (!isMonteCarloResponse(body)) {
        setError({ kind: "unavailable", message: "The simulation service returned incomplete data. Please try again." });
        return;
      }
      setResult(body);
      setMismatch(settingsMismatch(metadata, body));
      setSelected("combined");
    } catch {
      if (id === sequence.current) setError({ kind: "unavailable", message: "The simulation service did not respond." });
    } finally {
      if (id === sequence.current) setIsLoading(false);
    }
  }

  const target = Number(metadata.config.target_valid);
  return (
    <div className={styles.stack}>
      <section className={`panel ${styles.panel}`} aria-labelledby="mc-controls">
        <h2 id="mc-controls">Run a simulation</h2>
        <p className={styles.lede}>
          Draws {Number.isFinite(target) ? `${target.toLocaleString("en-US")} valid valuations` : "valid valuations"} for each of
          four experiments — growth only, operating margin only (Yahoo EBIT basis), discount rate only, and all three together — around one frozen snapshot of
          the company&apos;s statements and price.
        </p>
        <form className={styles.controls} onSubmit={run}>
          <label className={styles.field}>
            Company
            <select value={ticker} onChange={(e) => setTicker(e.target.value)} disabled={isLoading || !hydrated}>
              {metadata.tickers.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            Seed (optional)
            <input
              inputMode="numeric"
              autoComplete="off"
              placeholder={String(metadata.default_seed)}
              value={seedText}
              onChange={(e) => setSeedText(e.target.value)}
              disabled={isLoading || !hydrated}
            />
          </label>
          <button className={`button-primary ${styles.runButton}`} type="submit" disabled={!ticker || isLoading || !hydrated}>
            {isLoading ? "Running…" : "Run simulation"}
          </button>
          <p className={styles.hint}>
            The simulation runs only when you select Run simulation. The same snapshot, seed and settings always reproduce the same numbers.
          </p>
        </form>
        {isLoading && (
          <p className={styles.hint} role="status">
            {startSlow
              ? "Still starting the simulation service. It may have been idle, which can add several seconds before the simulation begins."
              : "Starting the simulation…"}
          </p>
        )}
      </section>

      {error && (
        <div className={error.kind === "input" || error.kind === "busy" ? "status-warning" : "status-error"} role="alert">
          <strong className="block text-[var(--paper)]">
            {error.kind === "busy" ? "Simulation in progress" : error.kind === "limited" ? "Too many requests" : error.kind === "input" ? "Check the request" : "Simulation unavailable"}
          </strong>
          <span className="mt-1 block">{error.message}</span>
          {result && <span className="mt-1 block">The previous result below is unchanged.</span>}
        </div>
      )}
      {mismatch && (
        <div className="status-warning" role="alert">
          <strong className="block text-[var(--paper)]">Settings out of date</strong>
          <span className="mt-1 block">{mismatch.message}</span>
        </div>
      )}

      {result && (
        <div className={styles.stack} aria-busy={isLoading}>
          <MonteCarloResults result={result} selected={selected} onSelect={setSelected} />
        </div>
      )}
    </div>
  );
}
