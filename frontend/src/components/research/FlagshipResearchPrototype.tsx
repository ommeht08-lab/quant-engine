"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import type { ResearchCaseFixture, ResearchFactFixture } from "@/lib/research-fixture";
import styles from "./FlagshipResearchPrototype.module.css";

const WORKFLOW = [
  { id: "overview", label: "Overview" },
  { id: "statements", label: "Historical statements" },
  { id: "forecast", label: "Forecast" },
  { id: "valuation", label: "Valuation" },
  { id: "evidence", label: "Evidence" },
  { id: "methodology", label: "Methodology" },
] as const;

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

export default function FlagshipResearchPrototype({ researchCase }: { researchCase: ResearchCaseFixture }) {
  const [selectedFact, setSelectedFact] = useState<ResearchFactFixture | null>(null);
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);

  return (
    <div className={styles.prototypeShell}>
      <aside
        className={`${styles.sidebar} ${mobileNavigationOpen ? styles.sidebarOpen : ""}`}
        aria-hidden={selectedFact ? true : undefined}
      >
        <div className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true">VE</span>
          <span><strong>Valuation Engine</strong><small>Research system</small></span>
        </div>

        <p className={styles.navEyebrow}>Research workflow</p>
        <nav className={styles.workflowNav} aria-label="Research workflow">
          {WORKFLOW.map((item, index) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              className={item.id === "overview" ? styles.activeNavItem : styles.navItem}
              onClick={() => setMobileNavigationOpen(false)}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>{item.label}
            </a>
          ))}
        </nav>

        <div className={styles.sidebarStatus}>
          <span className={styles.verifiedDot} aria-hidden="true" />
          <div><strong>Fixture artifact loaded</strong><small>{researchCase.artifactVersion}</small></div>
        </div>
      </aside>

      <div className={styles.workspace} aria-hidden={selectedFact ? true : undefined}>
        <header className={styles.utilityBar}>
          <button
            type="button"
            className={styles.menuButton}
            aria-label="Toggle research navigation"
            aria-expanded={mobileNavigationOpen}
            onClick={() => setMobileNavigationOpen((open) => !open)}
          >
            <span /><span /><span />
          </button>
          <div className={styles.mobileBrand}>Valuation Engine</div>
          <div className={styles.breadcrumb}><span>Research</span><b>/</b>Flagship case</div>
          <div className={styles.utilityActions}>
            <div className={styles.searchField} aria-hidden="true"><SearchIcon /><span>Search company or filing</span><kbd>⌘ K</kbd></div>
            <Link className={styles.workspaceLink} href="/workspace">Open workspace</Link>
          </div>
        </header>

        <main className={styles.main}>
          <div className={styles.prototypeNotice} role="note">
            UI prototype · illustrative fixture data · no live valuation request
          </div>

          <section className={styles.caseIntro} id="overview">
            <div>
              <div className={styles.companyLine}>
                <span className={styles.companyMonogram} aria-hidden="true">A</span>
                <p><strong>{researchCase.companyName}</strong><span>{researchCase.ticker} · NASDAQ · {researchCase.sector}</span></p>
              </div>
              <h1>Point-in-time valuation case</h1>
              <p className={styles.caseDeck}>
                A five-year operating forecast and DCF built only from facts eligible by the selected knowledge cutoff.
              </p>
            </div>
            <div className={styles.cutoffBlock}>
              <span>Knowledge cutoff</span>
              <strong>{researchCase.knowledgeCutoff}</strong>
              <button type="button" onClick={() => setSelectedFact(researchCase.facts[0])}>
                Inspect filing provenance <ArrowIcon />
              </button>
            </div>
          </section>

          <section className={styles.summaryBand} aria-label="Valuation summary">
            <div><span>Market price</span><strong>{researchCase.marketPrice}</strong><small>At knowledge cutoff</small></div>
            <div><span>Intrinsic value</span><strong>{researchCase.intrinsicValue}</strong><small>Base case · per share</small></div>
            <div><span>Margin of safety</span><strong className={styles.positive}>{researchCase.marginOfSafety}</strong><small>Intrinsic vs. market</small></div>
            <div><span>Data quality</span><strong className={styles.quality}><i className={styles.verifiedDot} />Complete</strong><small>0 unresolved required facts</small></div>
          </section>

          <section className={styles.primaryGrid} id="forecast">
            <article className={styles.chartPanel}>
              <div className={styles.panelHeading}>
                <div><p className={styles.kicker}>Operating trajectory</p><h2>Revenue history & forecast</h2></div>
                <div className={styles.legend}><span><i className={styles.actualLegend} />Reported</span><span><i className={styles.forecastLegend} />Forecast</span></div>
              </div>
              <RevenueChart data={researchCase.chart} />
              <p className={styles.chartFootnote}>USD billions · fiscal years · illustrative prototype values</p>
            </article>

            <aside className={styles.modelPanel} id="valuation">
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
          </section>

          <section className={styles.statementSection} id="statements">
            <div className={styles.sectionHeading}>
              <div><p className={styles.kicker}>Auditable actuals</p><h2>Historical statement bridge</h2></div>
              <p>Select any value to inspect its filing-level lineage.</p>
            </div>
            <div className={styles.tableFrame}>
              <table>
                <thead><tr><th>Line item</th><th>FY 2022</th><th>FY 2023</th><th>FY 2024</th><th>Source status</th></tr></thead>
                <tbody>
                  {researchCase.facts.map((fact, index) => (
                    <tr key={fact.id}>
                      <th scope="row">{fact.label}</th>
                      <td>${[394.33, 119.44, 23.65, 122.15][index].toFixed(2)}B</td>
                      <td>${[383.29, 114.30, 29.97, 110.54][index].toFixed(2)}B</td>
                      <td><button type="button" onClick={() => setSelectedFact(fact)}>{fact.value}<span>Audit</span></button></td>
                      <td><span className={styles.tableStatus}><i className={styles.verifiedDot} />Eligible · 10-K</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className={styles.evidenceStrip} id="evidence">
            <div><p className={styles.kicker}>Evidence state</p><h2>Every conclusion remains inspectable.</h2></div>
            <div><span>4</span><p><strong>Audited facts shown</strong>Each opens to accession-level evidence.</p></div>
            <div><span>2</span><p><strong>Independent cutoffs</strong>Knowledge time and data vintage are separate.</p></div>
            <div><span>0</span><p><strong>Silent fallbacks</strong>Questionable inputs stop the model.</p></div>
          </section>

          <footer className={styles.footer} id="methodology">
            <span>{researchCase.artifactVersion}</span>
            <span className={styles.methodologyPending}>Methodology page follows visual approval</span>
          </footer>
        </main>
      </div>

      {selectedFact && <AuditDrawer fact={selectedFact} onClose={() => setSelectedFact(null)} />}
    </div>
  );
}
