import { useMemo } from "react";

import type { ResearchOverviewViewModel, ValueCell } from "@/lib/research-overview-view-model";
import { sectorRelativeBadgeLabel, sectorRelativeDisclaimer } from "@/lib/sector-median-copy";
import styles from "./FlagshipResearchPrototype.module.css";

/**
 * Renders the research-overview VIEW — company identity, summary band,
 * revenue/valuation panels, sector-relative comparison, and the
 * unavailable-panel treatment for anything the live API doesn't
 * provide — driven entirely by a `ResearchOverviewViewModel`. Used by
 * both `FlagshipResearchPrototype.tsx` (fixture adapter) and
 * `/overview/[ticker]`'s `OverviewClient.tsx` (live adapter); this
 * component itself never reads a fixture or makes a network request.
 */

function ArrowIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20">
      <path d="M4 10h11M11 6l4 4-4 4" />
    </svg>
  );
}

function SummaryCell({ label, cell, toneClass }: { label: string; cell: ValueCell; toneClass?: string }) {
  const isEmpty = cell.status === "withheld" || cell.status === "unavailable";
  return (
    <div>
      <span>{label}</span>
      {isEmpty ? (
        <>
          <strong className={styles.summaryUnavailable}>{cell.status === "withheld" ? "Withheld" : "Unavailable"}</strong>
          <small title={cell.reason}>{cell.reason}</small>
        </>
      ) : (
        <>
          <strong className={toneClass}>{cell.display}</strong>
          <small>{cell.caption}</small>
        </>
      )}
    </div>
  );
}

function QualityLevelLabel({ summary }: { summary: ResearchOverviewViewModel["qualitySummary"] }) {
  return (
    <strong className={summary.tone === "positive" ? styles.quality : styles.qualityWarning}>
      <i className={summary.tone === "positive" ? styles.verifiedDot : styles.warningDot} />
      {summary.label}
    </strong>
  );
}

function QualityBanner({ viewModel }: { viewModel: ResearchOverviewViewModel }) {
  if (viewModel.qualityLevel === null || viewModel.qualityLevel === "ordinary") return null;
  const isDiagnostic = viewModel.qualityLevel === "diagnostic_only";
  return (
    <div
      className={`${styles.qualityBanner} ${isDiagnostic ? styles.qualityBannerDiagnostic : styles.qualityBannerCaution}`}
      role="alert"
    >
      <strong>{isDiagnostic ? "Diagnostic model output — not an actionable share valuation" : "Valuation interpretation caution"}</strong>
      <p>The calculations and assumptions below remain visible, but market and peer comparisons are withheld.</p>
      <ul>
        {viewModel.qualityCodes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      {viewModel.qualityDetails.length > 0 && (
        <div className={styles.qualityDetails}>
          {viewModel.qualityDetails.map((detail) => <span key={detail}>{detail}</span>)}
        </div>
      )}
    </div>
  );
}

function RevenuePanelBody({ viewModel }: { viewModel: ResearchOverviewViewModel }) {
  if (viewModel.revenuePanel.status === "unavailable") {
    return (
      <article className={styles.chartPanel}>
        <div className={styles.panelHeading}>
          <div><p className={styles.kicker}>Operating trajectory</p><h2>Revenue history &amp; forecast</h2></div>
        </div>
        <p className={styles.unavailableNote}>{viewModel.revenuePanel.reason}</p>
      </article>
    );
  }
  // The fixture's own RevenueChart (SVG polyline rendering) stays in
  // FlagshipResearchPrototype.tsx — it is fixture-chart-shape-specific
  // and only ever reached via this same "available" branch, so there is
  // nothing live-side to duplicate; see that file's own RevenuePanel.
  return null;
}

function ValuationPanelBody({ viewModel }: { viewModel: ResearchOverviewViewModel }) {
  return (
    <aside className={styles.modelPanel}>
      <div className={styles.panelHeading}>
        <div><p className={styles.kicker}>Model output</p><h2>Valuation range</h2></div>
      </div>
      <div className={styles.scenarioList}>
        {viewModel.scenarios.map((scenario) => (
          <div key={scenario.key} className={scenario.isBase ? styles.baseScenario : undefined}>
            <span>{scenario.label}</span>
            <strong>{scenario.isValid ? scenario.valueDisplay : "Not computable"}</strong>
            <small>{scenario.isValid ? scenario.assumptionsNote : scenario.reason}</small>
          </div>
        ))}
      </div>
      <div className={styles.modelChecks}>
        {viewModel.modelChecks.map((line) => (
          <div key={line.label}>
            <span>{line.label}</span>
            <strong>{line.value}</strong>
          </div>
        ))}
      </div>
    </aside>
  );
}

function SectorRelativePanel({ viewModel }: { viewModel: ResearchOverviewViewModel }) {
  const panel = viewModel.sectorRelativePanel;
  if (panel.status !== "available") {
    return (
      <aside className={styles.modelPanel}>
        <div className={styles.panelHeading}>
          <div><p className={styles.kicker}>Peer comparison</p><h2>Sector-relative valuation</h2></div>
        </div>
        <p className={styles.unavailableNote}>{panel.reason}</p>
      </aside>
    );
  }
  const isBelowMedian = panel.priceToIntrinsicValue <= panel.sectorMedianPIV;
  return (
    <aside className={styles.modelPanel}>
      <div className={styles.panelHeading}>
        <div><p className={styles.kicker}>Peer comparison</p><h2>Sector-relative valuation</h2></div>
      </div>
      <div className={styles.modelChecks}>
        <div>
          <span>{panel.ticker} P/IV</span>
          <strong>{panel.priceToIntrinsicValue.toFixed(2)}x</strong>
        </div>
        <div>
          <span>{panel.sector} sector median</span>
          <strong>{panel.sectorMedianPIV.toFixed(2)}x</strong>
        </div>
        <div>
          <span>Read</span>
          <strong>{sectorRelativeBadgeLabel(isBelowMedian)}</strong>
        </div>
      </div>
      {panel.snapshotCaption && <p className={styles.chartFootnote}>{panel.snapshotCaption}</p>}
      <p className={styles.unavailableNote}>{sectorRelativeDisclaimer()}</p>
    </aside>
  );
}

function EvidencePanel({ viewModel }: { viewModel: ResearchOverviewViewModel }) {
  if (viewModel.evidencePanel.status === "available") return null;
  return (
    <article className={styles.chartPanel}>
      <div className={styles.panelHeading}>
        <div><p className={styles.kicker}>Audit register</p><h2>Filing evidence</h2></div>
      </div>
      <p className={styles.unavailableNote}>{viewModel.evidencePanel.reason}</p>
    </article>
  );
}

export default function ResearchOverviewContent({
  viewModel,
  revenueChart,
  onInspectProvenance,
}: {
  viewModel: ResearchOverviewViewModel;
  /** The fixture's own SVG chart, rendered by the caller when
   * `viewModel.revenuePanel.status === "available"` — kept out of this
   * shared component since it is fixture-chart-specific rendering, not
   * view-model data. */
  revenueChart?: React.ReactNode;
  onInspectProvenance?: () => void;
}) {
  const companyLabel = viewModel.companyName ?? viewModel.ticker;
  const identityLine = useMemo(() => {
    const parts = [viewModel.ticker];
    if (viewModel.exchange) parts.push(viewModel.exchange);
    parts.push(viewModel.sector);
    return parts.join(" · ");
  }, [viewModel.ticker, viewModel.exchange, viewModel.sector]);

  return (
    <>
      <section className={styles.caseIntro}>
        <div>
          <div className={styles.companyLine}>
            <span className={styles.companyMonogram} aria-hidden="true">{viewModel.monogram}</span>
            <p><strong>{companyLabel}</strong><span>{identityLine}</span></p>
          </div>
          <h1>{viewModel.mode === "fixture" ? "Point-in-time valuation case" : "Live research overview"}</h1>
          <p className={styles.caseDeck}>{viewModel.deck}</p>
        </div>
        <div className={styles.cutoffBlock}>
          <span>{viewModel.asOfLabel}</span>
          <strong>{viewModel.asOfValue}</strong>
          {onInspectProvenance && (
            <button type="button" onClick={onInspectProvenance}>
              Inspect filing provenance <ArrowIcon />
            </button>
          )}
        </div>
      </section>

      <QualityBanner viewModel={viewModel} />

      <section className={styles.summaryBand} aria-label="Valuation summary">
        <SummaryCell label="Market price" cell={viewModel.marketPrice} />
        <SummaryCell label="Intrinsic value" cell={viewModel.intrinsicValue} />
        <SummaryCell label="Margin of safety" cell={viewModel.marginOfSafety} toneClass={viewModel.marginOfSafety.positive ? styles.positive : undefined} />
        <div>
          <span>{viewModel.qualitySummary.heading}</span>
          <QualityLevelLabel summary={viewModel.qualitySummary} />
          <small>{viewModel.qualitySummary.caption}</small>
        </div>
      </section>

      <section className={styles.primaryGrid}>
        {viewModel.revenuePanel.status === "available" ? revenueChart : <RevenuePanelBody viewModel={viewModel} />}
        <ValuationPanelBody viewModel={viewModel} />
      </section>

      {/* Sector-relative comparison and an inline evidence panel are
          part of the LIVE overview's design specifically — the fixture
          prototype never modeled a sector comparison, and its own
          filing evidence already has a full, separate
          /research/aapl/evidence page, so repeating an "available,
          see elsewhere" placeholder here would be new UI the original
          design never had. This is a structural difference driven by
          `mode` (already an intentional, documented top-level field),
          not a per-value fixture/live distinction. */}
      {viewModel.mode === "live" && (
        <section className={styles.primaryGrid}>
          <EvidencePanel viewModel={viewModel} />
          <SectorRelativePanel viewModel={viewModel} />
        </section>
      )}
    </>
  );
}
