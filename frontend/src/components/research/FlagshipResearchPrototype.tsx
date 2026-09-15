"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import type { ResearchCaseFixture, ResearchFactFixture } from "@/lib/research-fixture";
import styles from "./FlagshipResearchPrototype.module.css";

const WORKFLOW = [
  { id: "overview", href: "/research/aapl", label: "Overview", description: "Case summary" },
  { id: "statements", href: "/research/aapl/statements", label: "Historical statements", description: "Eligible actuals" },
  { id: "forecast", href: "/research/aapl/forecast", label: "Forecast", description: "Operating model" },
  { id: "valuation", href: "/research/aapl/valuation", label: "Valuation", description: "DCF and scenarios" },
  { id: "evidence", href: "/research/aapl/evidence", label: "Evidence", description: "Checks and lineage" },
  { id: "methodology", href: "/methodology", label: "Methodology", description: "Methods and limits" },
] as const;

export type ResearchView = (typeof WORKFLOW)[number]["id"];
type WorkflowId = ResearchView;

const VIEW_COPY: Record<Exclude<ResearchView, "overview">, { eyebrow: string; title: string; description: string }> = {
  statements: {
    eyebrow: "Eligible actuals",
    title: "Historical statements",
    description: "Reported values selected as they were knowable at the chosen cutoff, with filing-level lineage on every displayed fact.",
  },
  forecast: {
    eyebrow: "Five-year operating model",
    title: "Forecast",
    description: "A focused view of the operating trajectory and the transparent historical ratios that anchor the forward case.",
  },
  valuation: {
    eyebrow: "Linked model output",
    title: "Valuation",
    description: "DCF scenarios derived from the balanced operating model, with the market comparison kept separate from business forecasts.",
  },
  evidence: {
    eyebrow: "Audit register",
    title: "Evidence",
    description: "The quality gates, filing references, and immutable versions supporting this research case.",
  },
  methodology: {
    eyebrow: "Methods and limitations",
    title: "Methodology",
    description: "How the prototype handles knowledge time, data vintage, model completeness, and claims it is not designed to make.",
  },
};

function WorkflowIcon({ name }: { name: WorkflowId }) {
  if (name === "overview") {
    return <svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="6.5" height="6.5" rx="1.2" /><rect x="14" y="3.5" width="6.5" height="6.5" rx="1.2" /><rect x="3.5" y="14" width="6.5" height="6.5" rx="1.2" /><rect x="14" y="14" width="6.5" height="6.5" rx="1.2" /></svg>;
  }
  if (name === "statements") {
    return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3.5h9l3 3v14H6z" /><path d="M15 3.5v3h3M9 11h6M9 14.5h6M9 18h4" /></svg>;
  }
  if (name === "forecast") {
    return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 19.5h16M5.5 17l4-4 3 2 5.5-7" /><path d="m15 8 3-.5.5 3" /></svg>;
  }
  if (name === "valuation") {
    return <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" /><path d="M14.7 8.7c-.7-.6-1.5-.9-2.6-.9-1.5 0-2.5.7-2.5 1.8 0 2.8 5.2 1.4 5.2 4.4 0 1.2-1 2.1-2.7 2.1-1.2 0-2.2-.4-3-1.1M12 6.3v11.4" /></svg>;
  }
  if (name === "evidence") {
    return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3.5 19 6v5.2c0 4.4-2.8 7.6-7 9.3-4.2-1.7-7-4.9-7-9.3V6z" /><path d="m8.7 12 2.1 2.1 4.6-4.7" /></svg>;
  }
  return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4.5 5.5c2.6-.8 5.1-.5 7.5 1.1v13c-2.4-1.6-4.9-1.9-7.5-1.1zM19.5 5.5c-2.6-.8-5.1-.5-7.5 1.1v13c2.4-1.6 4.9-1.9 7.5-1.1z" /></svg>;
}

function NavArrowIcon() {
  return <svg aria-hidden="true" viewBox="0 0 20 20"><path d="m7.5 5 5 5-5 5" /></svg>;
}

function CloseIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20">
      <path d="m5 5 10 10M15 5 5 15" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20">
      <circle cx="8.4" cy="8.4" r="5.6" />
      <path d="m12.5 12.5 4 4" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20">
      <path d="M4 10h11M11 6l4 4-4 4" />
    </svg>
  );
}

function RevenueChart({ data }: { data: ResearchCaseFixture["chart"] }) {
  const points = useMemo(() => {
    const minimum = 340;
    const maximum = 540;
    return data.map((item, index) => ({
      ...item,
      x: 62 + index * 84,
      y: 252 - ((item.revenue - minimum) / (maximum - minimum)) * 190,
    }));
  }, [data]);
  const actualPoints = points.filter((point) => !point.forecast);
  const forecastPoints = points.filter((point, index) => point.forecast || index === 4);

  return (
    <div className={styles.chartWrap}>
      <svg
        className={styles.chart}
        viewBox="0 0 880 310"
        role="img"
        aria-label="Revenue history from 2021 through 2025 and illustrative forecast through 2030"
      >
        {[70, 115, 160, 205, 250].map((y, index) => (
          <g key={y}>
            <line className={styles.gridLine} x1="58" x2="842" y1={y} y2={y} />
            <text className={styles.axisLabel} x="4" y={y + 4}>
              ${540 - index * 50}B
            </text>
          </g>
        ))}
        <line className={styles.forecastDivider} x1="440" x2="440" y1="48" y2="252" />
        <text className={styles.phaseLabel} x="368" y="34">REPORTED</text>
        <text className={styles.phaseLabelForecast} x="452" y="34">FORECAST</text>
        <polyline
          className={styles.actualLine}
          points={actualPoints.map((point) => `${point.x},${point.y}`).join(" ")}
        />
        <polyline
          className={styles.forecastLine}
          points={forecastPoints.map((point) => `${point.x},${point.y}`).join(" ")}
        />
        {points.map((point) => (
          <g key={point.year}>
            <circle
              className={point.forecast ? styles.forecastPoint : styles.actualPoint}
              cx={point.x}
              cy={point.y}
              r="4.5"
            />
            <text className={styles.yearLabel} x={point.x} y="284" textAnchor="middle">
              {point.year}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function AuditDrawer({ fact, onClose }: { fact: ResearchFactFixture; onClose: () => void }) {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div className={styles.drawerLayer}>
      <button className={styles.scrim} type="button" onClick={onClose} aria-label="Close audit drawer" />
      <aside className={styles.drawer} role="dialog" aria-modal="true" aria-labelledby="audit-title">
        <div className={styles.drawerHeader}>
          <div>
            <p className={styles.kicker}>Fact audit</p>
            <h2 id="audit-title">{fact.label}</h2>
            <p>{fact.period} · {fact.value}</p>
          </div>
          <button className={styles.iconButton} type="button" onClick={onClose} aria-label="Close audit drawer" autoFocus>
            <CloseIcon />
          </button>
        </div>

        <div className={styles.auditVerdict}>
          <span className={styles.verifiedDot} aria-hidden="true" />
          Eligible at the selected knowledge cutoff
        </div>

        <dl className={styles.auditList}>
          <div><dt>Accession</dt><dd>{fact.accession}</dd></div>
          <div><dt>SEC accepted</dt><dd>{fact.acceptedAt}</dd></div>
          <div><dt>Filed date</dt><dd>{fact.filedDate}</dd></div>
          <div><dt>Form</dt><dd>{fact.form}</dd></div>
          <div className={styles.auditWide}><dt>Raw XBRL tag</dt><dd>{fact.rawTag}</dd></div>
          <div><dt>Concept map</dt><dd>{fact.conceptMapVersion}</dd></div>
          <div><dt>Ingestion batch</dt><dd>{fact.ingestionBatch}</dd></div>
          <div className={styles.auditWide}><dt>Ingested at</dt><dd>{fact.ingestedAt}</dd></div>
        </dl>

        <a className={styles.sourceLink} href={fact.sourceUrl} target="_blank" rel="noreferrer">
          View issuer filings at SEC.gov <ArrowIcon />
        </a>
        <p className={styles.drawerNote}>
          Prototype fixture only. Production values will be generated from a versioned, immutable research artifact.
        </p>
      </aside>
    </div>
  );
}

function SummaryBand({ researchCase }: { researchCase: ResearchCaseFixture }) {
  return (
    <section className={styles.summaryBand} aria-label="Valuation summary">
      <div><span>Market price</span><strong>{researchCase.marketPrice}</strong><small>At knowledge cutoff</small></div>
      <div><span>Intrinsic value</span><strong>{researchCase.intrinsicValue}</strong><small>Base case · per share</small></div>
      <div><span>Margin of safety</span><strong className={styles.positive}>{researchCase.marginOfSafety}</strong><small>Intrinsic vs. market</small></div>
      <div><span>Data quality</span><strong className={styles.quality}><i className={styles.verifiedDot} />Complete</strong><small>0 unresolved required facts</small></div>
    </section>
  );
}

function RevenuePanel({ researchCase, expanded = false }: { researchCase: ResearchCaseFixture; expanded?: boolean }) {
  return (
    <article className={`${styles.chartPanel} ${expanded ? styles.expandedPanel : ""}`}>
      <div className={styles.panelHeading}>
        <div><p className={styles.kicker}>Operating trajectory</p><h2>Revenue history & forecast</h2></div>
        <div className={styles.legend}><span><i className={styles.actualLegend} />Reported</span><span><i className={styles.forecastLegend} />Forecast</span></div>
      </div>
      <RevenueChart data={researchCase.chart} />
      <p className={styles.chartFootnote}>USD billions · fiscal years · illustrative prototype values</p>
    </article>
  );
}

function ValuationPanel({ researchCase, expanded = false }: { researchCase: ResearchCaseFixture; expanded?: boolean }) {
  return (
    <aside className={`${styles.modelPanel} ${expanded ? styles.expandedPanel : ""}`}>
      <div className={styles.panelHeading}>
        <div><p className={styles.kicker}>Model output</p><h2>Valuation range</h2></div>
      </div>
      <div className={styles.scenarioList}>
        {researchCase.scenarios.map((scenario) => (
          <div key={scenario.name} className={scenario.name === "Base" ? styles.baseScenario : undefined}>
            <span>{scenario.name}</span><strong>{scenario.value}</strong><small>{scenario.note}</small>
          </div>
        ))}
      </div>
      <div className={styles.modelChecks}>
        <div><span>Balance check</span><strong><i className={styles.verifiedDot} />Passed</strong></div>
        <div><span>Model</span><strong>{researchCase.modelVersion}</strong></div>
        <div><span>Dataset</span><strong>sec-gaap-v1.0</strong></div>
      </div>
    </aside>
  );
}

function PageHeading({
  researchCase,
  view,
  onInspect,
}: {
  researchCase: ResearchCaseFixture;
  view: Exclude<ResearchView, "overview">;
  onInspect: () => void;
}) {
  const copy = VIEW_COPY[view];
  return (
    <section className={styles.pageHeading}>
      <div>
        <p className={styles.kicker}>{copy.eyebrow}</p>
        <h1>{copy.title}</h1>
        <p>{copy.description}</p>
      </div>
      <div className={styles.pageContext}>
        <span>{researchCase.companyName} · {researchCase.ticker}</span>
        <strong>{researchCase.knowledgeCutoff}</strong>
        <button type="button" onClick={onInspect}>Audit source fact <ArrowIcon /></button>
      </div>
    </section>
  );
}

function StatementTable({
  researchCase,
  onInspect,
}: {
  researchCase: ResearchCaseFixture;
  onInspect: (fact: ResearchFactFixture) => void;
}) {
  return (
    <div className={styles.tableFrame}>
      <table>
        <thead><tr><th>Line item</th><th>FY 2022</th><th>FY 2023</th><th>FY 2024</th><th>Source status</th></tr></thead>
        <tbody>
          {researchCase.facts.map((fact, index) => (
            <tr key={fact.id}>
              <th scope="row">{fact.label}</th>
              <td>${[394.33, 119.44, 23.65, 122.15][index].toFixed(2)}B</td>
              <td>${[383.29, 114.30, 29.97, 110.54][index].toFixed(2)}B</td>
              <td><button type="button" onClick={() => onInspect(fact)}>{fact.value}<span>Audit</span></button></td>
              <td><span className={styles.tableStatus}><i className={styles.verifiedDot} />Eligible · 10-K</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EvidenceStrip() {
  return (
    <section className={styles.evidenceStrip}>
      <div><p className={styles.kicker}>Evidence state</p><h2>Every conclusion remains inspectable.</h2></div>
      <div><span>4</span><p><strong>Audited facts shown</strong>Each opens to accession-level evidence.</p></div>
      <div><span>2</span><p><strong>Independent cutoffs</strong>Knowledge time and data vintage are separate.</p></div>
      <div><span>0</span><p><strong>Silent fallbacks</strong>Questionable inputs stop the model.</p></div>
    </section>
  );
}

function ViewContent({
  researchCase,
  view,
  onInspect,
}: {
  researchCase: ResearchCaseFixture;
  view: ResearchView;
  onInspect: (fact: ResearchFactFixture) => void;
}) {
  if (view === "overview") {
    return (
      <>
        <section className={styles.caseIntro}>
          <div>
            <div className={styles.companyLine}>
              <span className={styles.companyMonogram} aria-hidden="true">A</span>
              <p><strong>{researchCase.companyName}</strong><span>{researchCase.ticker} · NASDAQ · {researchCase.sector}</span></p>
            </div>
            <h1>Point-in-time valuation case</h1>
            <p className={styles.caseDeck}>A five-year operating forecast and DCF built only from facts eligible by the selected knowledge cutoff.</p>
          </div>
          <div className={styles.cutoffBlock}>
            <span>Knowledge cutoff</span>
            <strong>{researchCase.knowledgeCutoff}</strong>
            <button type="button" onClick={() => onInspect(researchCase.facts[0])}>Inspect filing provenance <ArrowIcon /></button>
          </div>
        </section>
        <SummaryBand researchCase={researchCase} />
        <section className={styles.primaryGrid}>
          <RevenuePanel researchCase={researchCase} />
          <ValuationPanel researchCase={researchCase} />
        </section>
      </>
    );
  }

  if (view === "statements") {
    return (
      <>
        <PageHeading researchCase={researchCase} view={view} onInspect={() => onInspect(researchCase.facts[0])} />
        <section className={styles.statementSection}>
          <div className={styles.sectionHeading}>
            <div><p className={styles.kicker}>Auditable actuals</p><h2>Historical statement bridge</h2></div>
            <p>Select any highlighted value to inspect its filing-level lineage.</p>
          </div>
          <StatementTable researchCase={researchCase} onInspect={onInspect} />
        </section>
      </>
    );
  }

  if (view === "forecast") {
    const estimates = researchCase.chart.filter((item) => item.forecast);
    return (
      <>
        <PageHeading researchCase={researchCase} view={view} onInspect={() => onInspect(researchCase.facts[0])} />
        <section className={styles.forecastLayout}>
          <RevenuePanel researchCase={researchCase} expanded />
          <aside className={styles.driverPanel}>
            <div><p className={styles.kicker}>Base-case anchors</p><h2>Operating drivers</h2></div>
            <dl>
              <div><dt>Revenue growth</dt><dd>5.0%</dd><small>Five-year normalized rate</small></div>
              <div><dt>Operating margin</dt><dd>30.8%</dd><small>Historical operating range</small></div>
              <div><dt>Capital expenditure</dt><dd>3.1%</dd><small>As a share of revenue</small></div>
              <div><dt>Cash tax rate</dt><dd>16.2%</dd><small>Normalized effective rate</small></div>
            </dl>
          </aside>
        </section>
        <section className={styles.forecastLedger} aria-label="Revenue forecast schedule">
          {estimates.map((item, index) => (
            <div key={item.year}><span>{item.year}</span><strong>${item.revenue}B</strong><small>{index === 0 ? "Forecast origin" : "Model estimate"}</small></div>
          ))}
        </section>
      </>
    );
  }

  if (view === "valuation") {
    return (
      <>
        <PageHeading researchCase={researchCase} view={view} onInspect={() => onInspect(researchCase.facts[0])} />
        <SummaryBand researchCase={researchCase} />
        <section className={styles.valuationLayout}>
          <ValuationPanel researchCase={researchCase} expanded />
          <article className={styles.valuationNotes}>
            <p className={styles.kicker}>Interpretation</p>
            <h2>Market comparison</h2>
            <p>The base case sits above the fixture market price. The spread is presented as a model comparison—not a recommendation or guaranteed return.</p>
            <dl><div><dt>Terminal value method</dt><dd>Perpetuity growth</dd></div><div><dt>Free cash flow basis</dt><dd>Unlevered</dd></div><div><dt>Execution signal</dt><dd>None</dd></div></dl>
          </article>
        </section>
      </>
    );
  }

  if (view === "evidence") {
    return (
      <>
        <PageHeading researchCase={researchCase} view={view} onInspect={() => onInspect(researchCase.facts[0])} />
        <EvidenceStrip />
        <section className={styles.evidenceRegister}>
          <div className={styles.sectionHeading}>
            <div><p className={styles.kicker}>Displayed source facts</p><h2>Filing register</h2></div>
            <p>Open a row to inspect accession, timing, raw tag, and ingestion lineage.</p>
          </div>
          <div className={styles.evidenceRows}>
            {researchCase.facts.map((fact) => (
              <button type="button" key={fact.id} onClick={() => onInspect(fact)}>
                <span><strong>{fact.label}</strong><small>{fact.period} · {fact.form}</small></span>
                <span><strong>{fact.value}</strong><small>{fact.accession}</small></span>
                <NavArrowIcon />
              </button>
            ))}
          </div>
        </section>
      </>
    );
  }

  return (
    <>
      <PageHeading researchCase={researchCase} view={view} onInspect={() => onInspect(researchCase.facts[0])} />
      <section className={styles.methodologyGrid}>
        <article><span>01</span><h2>Point-in-time selection</h2><p>Facts qualify only when filing eligibility and the independently selected data vintage both precede their cutoffs.</p></article>
        <article><span>02</span><h2>Restatements</h2><p>Later amendments replace only identities they actually restate. Conflicts fail closed rather than falling back silently.</p></article>
        <article><span>03</span><h2>Model discipline</h2><p>The future linked model must balance before its cash flows can reach the DCF. Missing required inputs stop valuation.</p></article>
        <article><span>04</span><h2>Prototype limitation</h2><p>All values on these pages are illustrative fixtures for interface approval. They are not current research or investment advice.</p></article>
      </section>
    </>
  );
}

export default function FlagshipResearchPrototype({
  researchCase,
  view = "overview",
}: {
  researchCase: ResearchCaseFixture;
  view?: ResearchView;
}) {
  const [selectedFact, setSelectedFact] = useState<ResearchFactFixture | null>(null);
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const activeLabel = WORKFLOW.find((item) => item.id === view)?.label ?? "Overview";

  return (
    <div className={styles.prototypeShell} data-research-shell>
      <aside className={`${styles.sidebar} ${mobileNavigationOpen ? styles.sidebarOpen : ""}`} aria-hidden={selectedFact ? true : undefined}>
        <div className={styles.brand}><span className={styles.brandMark} aria-hidden="true">VE</span><span><strong>Valuation Engine</strong><small>Research system</small></span></div>
        <p className={styles.navEyebrow}>Research workflow</p>
        <nav className={styles.workflowNav} aria-label="Research workflow">
          {WORKFLOW.map((item) => (
            <Link
              key={item.id}
              href={item.href}
              prefetch
              aria-current={item.id === view ? "page" : undefined}
              className={item.id === view ? styles.activeNavItem : styles.navItem}
              onClick={(event) => {
                setMobileNavigationOpen(false);
                if (item.id === view) event.preventDefault();
              }}
            >
              <span className={styles.navIcon}><WorkflowIcon name={item.id} /></span>
              <span className={styles.navCopy}><strong>{item.label}</strong><small>{item.description}</small></span>
              <span className={styles.navArrow}><NavArrowIcon /></span>
            </Link>
          ))}
        </nav>
        <div className={styles.sidebarStatus}><span className={styles.verifiedDot} aria-hidden="true" /><div><strong>Fixture artifact loaded</strong><small>{researchCase.artifactVersion}</small></div></div>
      </aside>

      {mobileNavigationOpen && (
        <button
          type="button"
          className={styles.navigationScrim}
          aria-label="Close research navigation"
          onClick={() => setMobileNavigationOpen(false)}
        />
      )}

      <div className={styles.workspace} aria-hidden={selectedFact ? true : undefined}>
        <header className={styles.utilityBar}>
          <button type="button" className={styles.menuButton} aria-label="Toggle research navigation" aria-expanded={mobileNavigationOpen} onClick={() => setMobileNavigationOpen((open) => !open)}><span /><span /><span /></button>
          <div className={styles.mobileBrand}>Valuation Engine</div>
          <div className={styles.breadcrumb}><span>Research</span><b>/</b><span>{researchCase.ticker}</span><b>/</b>{activeLabel}</div>
          <div className={styles.utilityActions}>
            <Link className={styles.evidenceLink} href="/research/aapl/evidence" prefetch>
              <SearchIcon />
              <span>Inspect evidence</span>
              <ArrowIcon />
            </Link>
            <Link className={styles.workspaceLink} href="/workspace" prefetch>Open workspace</Link>
          </div>
        </header>

        <main className={styles.main}>
          <div className={styles.prototypeNotice} role="note">UI prototype · illustrative fixture data · no live valuation request</div>
          <div className={styles.viewContent}>
            <ViewContent researchCase={researchCase} view={view} onInspect={setSelectedFact} />
          </div>
          <footer className={styles.footer}>
            <span>{researchCase.artifactVersion}</span>
            {view === "methodology" ? <Link href="/research/aapl" prefetch>Return to overview <ArrowIcon /></Link> : <Link href="/methodology" prefetch>Methodology & limitations <ArrowIcon /></Link>}
          </footer>
        </main>
      </div>

      {selectedFact && <AuditDrawer fact={selectedFact} onClose={() => setSelectedFact(null)} />}
    </div>
  );
}
