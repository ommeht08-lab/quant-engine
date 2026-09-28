"use client";

// MSFT assumption-sensitivity research case — built on the SAME public
// research shell as /research/aapl (FlagshipResearchPrototype), reusing
// its CSS module directly for layout/styling/navigation fidelity. This
// case is fundamentally different data (a 13-scenario funding-
// feasibility sensitivity experiment, not a DCF valuation), so it uses
// its own view content rather than forcing the narrow AAPL
// ResearchCaseFixture shape.
//
// Every number here is read from a static, bundled JSON file
// (src/data/msft-model-data.json), generated once, offline, from already
// -archived and independently reviewed evidence reports. Switching the
// selected scenario (persisted via the ?s= query param across tabs) only
// changes which pre-computed slice of that JSON is displayed — it never
// calls the forecasting engine, a database, or any API route.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState, type CSSProperties, type ReactNode } from "react";

import rawData from "@/data/msft-model-data.json";
import type { MsftModelData, ScenarioData } from "@/lib/msft-model-types";
import {
  formatPercent,
  formatRate,
  formatUsdMillions,
  exactString,
  YEAR_STATUS_LABEL,
} from "@/lib/msft-model-format";
import styles from "./FlagshipResearchPrototype.module.css";

const data = rawData as unknown as MsftModelData;

export type MsftView = "overview" | "scenarios" | "statements" | "funding" | "evidence";

const WORKFLOW: ReadonlyArray<{ id: MsftView; href: string; label: string; description: string }> = [
  { id: "overview", href: "/research/msft", label: "Overview", description: "Experiment summary" },
  { id: "scenarios", href: "/research/msft/scenarios", label: "Scenarios", description: "FCF & sensitivity" },
  { id: "statements", href: "/research/msft/statements", label: "Statements", description: "Linked 3-statement" },
  { id: "funding", href: "/research/msft/funding", label: "Funding", description: "Cash, debt & revolver" },
  { id: "evidence", href: "/research/msft/evidence", label: "Evidence", description: "Hashes & provenance" },
];

const SCENARIO_ORDER = ["R", "O1", "O2", "O3", "O4", "O5", "O6", "F1", "F2", "F3", "X1", "X2", "X3"] as const;
type ScenarioName = (typeof SCENARIO_ORDER)[number];

/** Appends the currently-selected scenario as `?s=` to an internal href so
 * tab links, evidence links, and browser back/forward all preserve the
 * user's scenario choice instead of silently resetting it to R. */
function withScenario(href: string, selected: ScenarioName): string {
  return `${href}?s=${selected}`;
}

const SCENARIO_LABELS: Record<ScenarioName, string> = {
  R: "R — Reference", O1: "O1 — Growth 10→20%", O2: "O2 — Growth 10→2%",
  O3: "O3 — Margin 42→55%", O4: "O4 — Margin 42→20%", O5: "O5 — Capex 15→25%",
  O6: "O6 — AR/Inv. 5→12%", F1: "F1 — Div+buyback+debt", F2: "F2 — Min cash+revolver",
  F3: "F3 — Designed refusal", X1: "X1 — O4+O5", X2: "X2 — O1+O6", X3: "X3 — O3+F1",
};

function WorkflowIcon({ name }: { name: MsftView }) {
  if (name === "overview") return <svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="6.5" height="6.5" rx="1.2" /><rect x="14" y="3.5" width="6.5" height="6.5" rx="1.2" /><rect x="3.5" y="14" width="6.5" height="6.5" rx="1.2" /><rect x="14" y="14" width="6.5" height="6.5" rx="1.2" /></svg>;
  if (name === "scenarios") return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 19.5h16M5.5 17l4-4 3 2 5.5-7" /><path d="m15 8 3-.5.5 3" /></svg>;
  if (name === "statements") return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3.5h9l3 3v14H6z" /><path d="M15 3.5v3h3M9 11h6M9 14.5h6M9 18h4" /></svg>;
  if (name === "funding") return <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" /><path d="M14.7 8.7c-.7-.6-1.5-.9-2.6-.9-1.5 0-2.5.7-2.5 1.8 0 2.8 5.2 1.4 5.2 4.4 0 1.2-1 2.1-2.7 2.1-1.2 0-2.2-.4-3-1.1M12 6.3v11.4" /></svg>;
  return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3.5 19 6v5.2c0 4.4-2.8 7.6-7 9.3-4.2-1.7-7-4.9-7-9.3V6z" /><path d="m8.7 12 2.1 2.1 4.6-4.7" /></svg>;
}

function NavArrowIcon() {
  return <svg aria-hidden="true" viewBox="0 0 20 20"><path d="m7.5 5 5 5-5 5" /></svg>;
}
function ArrowIcon() {
  return <svg aria-hidden="true" viewBox="0 0 20 20"><path d="M4 10h11M11 6l4 4-4 4" /></svg>;
}

function useSelectedScenario(): [ScenarioName, (name: ScenarioName) => void] {
  const router = useRouter();
  const searchParams = useSearchParams();
  const raw = searchParams.get("s");
  const selected: ScenarioName = (SCENARIO_ORDER as readonly string[]).includes(raw ?? "") ? (raw as ScenarioName) : "R";
  const setSelected = (name: ScenarioName) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("s", name);
    router.replace(`?${params.toString()}`, { scroll: false });
  };
  return [selected, setSelected];
}

function ScenarioPicker({ selected, onSelect }: { selected: ScenarioName; onSelect: (name: ScenarioName) => void }) {
  return (
    <div className={styles.scenarioPicker} style={{ marginTop: "0.6rem" }} role="group" aria-label="Select scenario">
      {SCENARIO_ORDER.map((name) => {
        const active = name === selected;
        return (
          <button
            key={name}
            type="button"
            onClick={() => onSelect(name)}
            aria-pressed={active}
            className={active ? styles.scenarioChipActive : styles.scenarioChip}
          >
            <span style={{ fontFamily: "var(--utility)", fontSize: "0.72rem", fontWeight: 650 }}>{name}</span>
          </button>
        );
      })}
    </div>
  );
}

function CombinedStatusStrip() {
  const completed = data.tenScenarioStatus.filter((r) => r.status === "completed").length;
  const expected = data.tenScenarioStatus.filter((r) => r.status === "expected_refusal_confirmed").length;
  const stopped = data.tenScenarioStatus.filter((r) => r.status === "stopped_unexpected_refusal").length;
  return (
    <section className={styles.summaryBand} aria-label="Combined ten-scenario status">
      <div><span>Completed</span><strong>{completed}/10</strong><small>Checks passed, 3/3 years</small></div>
      <div><span>Expected refusal</span><strong className={styles.positive}>{expected}/10</strong><small>F3 — designed test, confirmed</small></div>
      <div><span>Stopped</span><strong style={{ color: "var(--rp-cobalt-deep)" }}>{stopped}/10</strong><small>X1 — unexpected year-2 refusal</small></div>
      <div><span>Combined result</span><strong style={{ fontSize: "1rem" }}>Not a full pass</strong><small>X1 unresolved by design</small></div>
    </section>
  );
}

function TenScenarioTable({ selected, onSelect }: { selected: ScenarioName; onSelect: (name: ScenarioName) => void }) {
  return (
    <div className={styles.tableFrame}>
      <table>
        <thead><tr><th>Scenario</th><th>Status</th><th>Years</th><th>Run</th></tr></thead>
        <tbody>
          {data.tenScenarioStatus.map((row) => (
            <tr key={row.name} style={row.name === selected ? { background: "rgba(98,135,255,0.07)" } : undefined}>
              <th scope="row">
                <button
                  type="button"
                  onClick={() => onSelect(row.name as ScenarioName)}
                  style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem", color: "inherit", fontWeight: 600 }}
                >
                  {row.name}
                  <span style={{ borderRadius: "4px", background: "var(--rp-cobalt-soft)", padding: "0.13rem 0.4rem", fontSize: "0.6rem", fontWeight: 600, color: "var(--rp-cobalt-deep)" }}>
                    View
                  </span>
                </button>
              </th>
              <td>
                <span className={styles.tableStatus} style={row.status !== "completed" && row.status !== "expected_refusal_confirmed" ? { color: "var(--rp-cobalt-deep)" } : undefined}>
                  <i className={styles.verifiedDot} style={row.status !== "completed" && row.status !== "expected_refusal_confirmed" ? { background: "#6287ff" } : undefined} />
                  {row.status.replaceAll("_", " ")}
                </span>
              </td>
              <td>{row.completedYears}/3</td>
              <td>{row.runSource === "original_execution" ? "Original run" : row.runSource === "continuation" ? "Continuation" : "Development"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AssumptionsTable({ s }: { s: ScenarioData }) {
  return (
    <div className={styles.tableFrame}>
      <table>
        <thead><tr><th>Input</th>{s.assumptionYears.map((y) => <th key={y.year}>Year {y.year}</th>)}</tr></thead>
        <tbody>
          <tr><th scope="row">Resulting revenue (USD millions)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatUsdMillions(y.resultingRevenue)}</td>)}</tr>
          <tr><th scope="row">Revenue growth (%)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatRate(y.revenueGrowth)}</td>)}</tr>
          <tr><th scope="row">Operating margin (%)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatRate(y.operatingMargin)}</td>)}</tr>
          <tr><th scope="row">Tax rate (%)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatRate(y.taxRate)}</td>)}</tr>
          <tr><th scope="row">Capex (USD millions)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatUsdMillions(y.capex)}</td>)}</tr>
          <tr><th scope="row">Depreciation (USD millions)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatUsdMillions(y.depreciation)}</td>)}</tr>
          <tr><th scope="row">Dividends (USD millions)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatUsdMillions(y.dividends)}</td>)}</tr>
          <tr><th scope="row">Share repurchases (USD millions)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatUsdMillions(y.shareRepurchases)}</td>)}</tr>
          <tr><th scope="row">LT debt movement, schedule (USD millions)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatUsdMillions(y.longTermDebtNoncurrentMovement)}</td>)}</tr>
          <tr><th scope="row">Minimum cash (USD millions)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatUsdMillions(y.minimumCash)}</td>)}</tr>
          <tr><th scope="row">Revolver limit (USD millions)</th>{s.assumptionYears.map((y) => <td key={y.year}>{formatUsdMillions(y.revolverLimit)}</td>)}</tr>
        </tbody>
      </table>
    </div>
  );
}

function FcfPanel({ s }: { s: ScenarioData }) {
  const name = s.name;
  const sensitivity = data.sensitivityVsR[name] ?? null;
  const invariantKey = name === "F1" ? "F1_vs_R" : name === "F2" ? "F2_vs_R" : name === "X3" ? "X3_vs_O3" : null;
  const invariant = invariantKey ? data.crossScenarioInvariants[invariantKey] : null;
  const x3InfoVsR = name === "X3" ? data.sensitivityVsR["X3_informational_not_invariant"] : null;

  return (
    <article className={styles.chartPanel}>
      <div className={styles.panelHeading}>
        <div><p className={styles.kicker}>Unlevered FCF</p><h2>{name} — {s.years.every((y) => y.status !== "completed") ? "no completed years" : "USD millions"}</h2></div>
      </div>
      <div className={styles.tableFrame} style={{ marginTop: "0.85rem", boxShadow: "none" }}>
        <table>
          <thead><tr><th>Line</th>{s.years.map((y) => <th key={y.year}>Year {y.year}</th>)}</tr></thead>
          <tbody>
            <tr>
              <th scope="row">Unlevered FCF</th>
              {s.years.map((y) => (
                <td key={y.year} title={y.status === "completed" ? exactString(y.period.unleveredFcf) : ""}>
                  {y.status === "completed" ? formatUsdMillions(y.period.unleveredFcf) : YEAR_STATUS_LABEL[y.status]}
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">DCF-equivalence</th>
              {s.years.map((y) => (
                <td key={y.year}>{y.status === "completed" && y.dcfCrossCheck?.applicable ? `diff ${y.dcfCrossCheck.difference}` : "Not applicable"}</td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>

      {invariant && (
        <div className={styles.auditVerdict} style={{ marginTop: "1rem", flexDirection: "column", alignItems: "flex-start", gap: "0.5rem" }}>
          <span style={{ fontWeight: 700 }}>Predeclared invariant: {invariant.scenario} vs. {invariant.reference} — {invariant.allExact ? "held exactly" : "VIOLATED"}</span>
          <div style={{ display: "flex", gap: "1.25rem", fontFamily: "var(--utility)", fontSize: "0.68rem" }}>
            {invariant.perYear.map((row) => (
              <span key={row.year}>Y{row.year}: {row.exactMatch ? "✓ exact" : `✗ ${row.signedDifference}`}</span>
            ))}
          </div>
        </div>
      )}

      {sensitivity && (
        <div style={{ marginTop: "1rem" }}>
          <p className={styles.kicker}>Sensitivity vs. R — illustrative 5% convention</p>
          <div className={styles.tableFrame} style={{ marginTop: "0.6rem", boxShadow: "none" }}>
            <table>
              <thead><tr><th>Year</th><th>Signed diff</th><th>% diff</th><th>Material?</th></tr></thead>
              <tbody>
                {sensitivity.map((row) => (
                  <tr key={row.year}>
                    <th scope="row">{row.year}</th>
                    <td>{formatUsdMillions(row.signedDifference)}</td>
                    <td>{formatPercent(row.percentageDifference)}</td>
                    <td style={row.material ? { color: "#ff9a8f" } : undefined}>{row.material ? "Material" : "Not material"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {x3InfoVsR && (
        <div style={{ marginTop: "1rem" }} className={styles.evidenceRows}>
          <p className={styles.kicker} style={{ marginBottom: "0.5rem" }}>X3 vs. R — informational only, NOT the predeclared invariant</p>
          {x3InfoVsR.map((row) => (
            <div key={row.year} style={{ display: "flex", justifyContent: "space-between", padding: "0.4rem 0", borderBottom: "1px solid var(--rp-line)", fontSize: "0.72rem" }}>
              <span>Year {row.year}</span><span>{formatPercent(row.percentageDifference)} · {row.material ? "material" : "not material"}</span>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}

function LinkedStatementTable({
  s,
  title,
  statementKey,
  lines,
}: {
  s: ScenarioData;
  title: string;
  statementKey: "income" | "cashFlow";
  lines: readonly string[];
}) {
  return (
    <div style={{ marginTop: "1.5rem" }}>
      <p className={styles.kicker} style={{ marginBottom: "0.5rem" }}>{title} — USD millions</p>
      <div className={styles.tableFrame}>
        <table>
          <thead><tr><th>Line</th>{s.years.map((y) => <th key={y.year}>Year {y.year}</th>)}</tr></thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line}>
                <th scope="row" style={{ textTransform: "capitalize" }}>{line.replaceAll("_", " ")}</th>
                {s.years.map((y) => (
                  <td key={y.year}>{y.status === "completed" ? formatUsdMillions(y.period[statementKey][line]) : YEAR_STATUS_LABEL[y.status]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const BALANCE_LINE_GROUPS: ReadonlyArray<{ heading: string; lines: readonly string[] }> = [
  {
    heading: "Assets",
    lines: [
      "CashAndCashEquivalentsAtCarryingValue", "ShortTermInvestments", "AccountsReceivableNetCurrent", "InventoryNet",
      "OtherAssetsCurrent", "PropertyPlantAndEquipmentNet", "OperatingLeaseRightOfUseAsset", "LongTermInvestments",
      "Goodwill", "FiniteLivedIntangibleAssetsNet", "OtherAssetsNoncurrent",
    ],
  },
  {
    heading: "Liabilities",
    lines: [
      "AccountsPayableCurrent", "CommercialPaper", "LongTermDebtCurrent", "EmployeeRelatedLiabilitiesCurrent",
      "AccruedIncomeTaxesCurrent", "ContractWithCustomerLiabilityCurrent", "OtherLiabilitiesCurrent",
      "LongTermDebtNoncurrent", "AccruedIncomeTaxesNoncurrent", "ContractWithCustomerLiabilityNoncurrent",
      "DeferredIncomeTaxLiabilitiesNet", "OperatingLeaseLiabilityNoncurrent", "OtherLiabilitiesNoncurrent",
    ],
  },
  {
    heading: "Equity",
    lines: [
      "CommonStocksIncludingAdditionalPaidInCapital", "RetainedEarningsAccumulatedDeficit",
      "AccumulatedOtherComprehensiveIncomeLossNetOfTax",
    ],
  },
];

/** "CashAndCashEquivalentsAtCarryingValue" -> "Cash And Cash Equivalents At
 * Carrying Value" — a readable label placed next to the exact filed XBRL
 * concept name, which stays visible alongside it for traceability. */
function humanizeXbrlConcept(tag: string): string {
  return tag.replace(/([a-z0-9])([A-Z])/g, "$1 $2");
}

function BalanceSheetSection({ s }: { s: ScenarioData }) {
  const opening = data.openingBalanceUsdMillions;
  const groupTotal = (lines: readonly string[]) => lines.reduce((sum, k) => sum + Number(opening[k]), 0);
  return (
    <div style={{ marginTop: "1.5rem" }}>
      <p className={styles.kicker} style={{ marginBottom: "0.5rem" }}>Balance sheet — USD millions</p>
      <p style={{ color: "var(--rp-dim)", fontSize: "0.66rem", lineHeight: 1.5, marginBottom: "0.6rem" }}>
        &quot;FY2024 (reported)&quot; is the verified opening balance sheet as filed (10-K accession{" "}
        <span style={{ fontFamily: "var(--utility)" }}>{data.foundation.filing_accession}</span>) — not a forecast
        value. The Year 1–3 columns are the illustrative forecast under this scenario&apos;s assumptions.
      </p>
      <div className={styles.tableFrame}>
        <table>
          <thead>
            <tr>
              <th>Line</th>
              <th>FY2024 (reported)</th>
              {s.years.map((y) => <th key={y.year}>Year {y.year} (illustrative)</th>)}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Total assets</th>
              <td>{formatUsdMillions(groupTotal(BALANCE_LINE_GROUPS[0].lines))}</td>
              {s.years.map((y) => <td key={y.year}>{y.status === "completed" ? formatUsdMillions(y.period.balance.total_assets) : YEAR_STATUS_LABEL[y.status]}</td>)}
            </tr>
            <tr>
              <th scope="row">Total liabilities</th>
              <td>{formatUsdMillions(groupTotal(BALANCE_LINE_GROUPS[1].lines))}</td>
              {s.years.map((y) => <td key={y.year}>{y.status === "completed" ? formatUsdMillions(y.period.balance.total_liabilities) : YEAR_STATUS_LABEL[y.status]}</td>)}
            </tr>
            <tr>
              <th scope="row">Total equity</th>
              <td>{formatUsdMillions(groupTotal(BALANCE_LINE_GROUPS[2].lines))}</td>
              {s.years.map((y) => <td key={y.year}>{y.status === "completed" ? formatUsdMillions(y.period.balance.total_equity) : YEAR_STATUS_LABEL[y.status]}</td>)}
            </tr>
          </tbody>
        </table>
      </div>
      <details style={{ marginTop: "0.75rem" }}>
        <summary
          style={{
            display: "inline-flex", alignItems: "center", minHeight: "2.75rem", cursor: "pointer",
            color: "var(--rp-cobalt-deep)", fontSize: "0.72rem", fontWeight: 650,
          }}
        >
          Show full reported balance-sheet detail (27 filed lines)
        </summary>
        {BALANCE_LINE_GROUPS.map((group) => (
          <div key={group.heading} style={{ marginTop: "0.75rem" }}>
            <p className={styles.kicker} style={{ marginBottom: "0.4rem" }}>{group.heading} — USD millions</p>
            <div className={styles.tableFrame}>
              <table>
                <thead>
                  <tr>
                    <th>Reported concept (10-K XBRL tag)</th>
                    <th>FY2024 (reported)</th>
                    {s.years.map((y) => <th key={y.year}>Year {y.year} (illustrative)</th>)}
                  </tr>
                </thead>
                <tbody>
                  {group.lines.map((tag) => (
                    <tr key={tag}>
                      <th scope="row" title={tag}>
                        {humanizeXbrlConcept(tag)}
                        <br />
                        <small style={{ fontFamily: "var(--utility)", color: "var(--rp-dim)", fontWeight: 500 }}>{tag}</small>
                      </th>
                      <td>{formatUsdMillions(opening[tag])}</td>
                      {s.years.map((y) => (
                        <td key={y.year}>{y.status === "completed" ? formatUsdMillions(y.period.balance[tag]) : YEAR_STATUS_LABEL[y.status]}</td>
                      ))}
                    </tr>
                  ))}
                  {group.heading === "Liabilities" && (
                    <tr>
                      <th scope="row" title="revolver">
                        Revolver draw balance (forecast only)
                        <br />
                        <small style={{ fontFamily: "var(--utility)", color: "var(--rp-dim)", fontWeight: 500 }}>revolver</small>
                      </th>
                      <td>Not applicable — no filed opening balance</td>
                      {s.years.map((y) => (
                        <td key={y.year}>{y.status === "completed" ? formatUsdMillions(y.period.balance.revolver) : YEAR_STATUS_LABEL[y.status]}</td>
                      ))}
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </details>
    </div>
  );
}

function FundingTables({ s }: { s: ScenarioData }) {
  const debtLines: Array<[string, string]> = [
    ["ending_existing_debt", "Ending existing debt (term debt + commercial paper)"],
  ];
  const revolverLines: Array<[string, string]> = [
    ["draw", "Revolver draw"], ["repayment", "Revolver repayment"], ["ending_revolver", "Ending revolver balance"],
  ];
  const costLines: Array<[string, string]> = [
    ["interest_on_opening_balances", "Interest"], ["commitment_fee", "Commitment fee"], ["draw_fee", "Draw fee"],
  ];
  const renderRow = ([key, label]: [string, string]) => (
    <tr key={key}>
      <th scope="row">{label}</th>
      {s.years.map((y) => (
        <td key={y.year}>{y.status === "completed" ? formatUsdMillions(y.period.debt[key]) : YEAR_STATUS_LABEL[y.status]}</td>
      ))}
    </tr>
  );
  return (
    <div className={styles.tableFrame}>
      <table>
        <thead><tr><th>Line (USD millions)</th>{s.years.map((y) => <th key={y.year}>Year {y.year}</th>)}</tr></thead>
        <tbody>
          {debtLines.map(renderRow)}
          {revolverLines.map(renderRow)}
          <tr>
            <th scope="row">Revolver facility limit</th>
            {s.years.map((y) => (
              <td key={y.year}>{y.status === "completed" ? formatUsdMillions(y.period.debt.capacity) : YEAR_STATUS_LABEL[y.status]}</td>
            ))}
          </tr>
          <tr>
            <th scope="row">Remaining revolver capacity</th>
            {s.years.map((y) => (
              <td key={y.year}>
                {y.status === "completed"
                  ? formatUsdMillions(Number(y.period.debt.capacity) - Number(y.period.debt.ending_revolver))
                  : YEAR_STATUS_LABEL[y.status]}
              </td>
            ))}
          </tr>
          {costLines.map(renderRow)}
          <tr>
            <th scope="row">Minimum required cash</th>
            {s.assumptionYears.map((y) => <td key={y.year}>{formatUsdMillions(y.minimumCash)}</td>)}
          </tr>
          <tr>
            <th scope="row">Ending cash</th>
            {s.years.map((y) => <td key={y.year}>{y.status === "completed" ? formatUsdMillions(y.period.cashFlow.ending_cash) : YEAR_STATUS_LABEL[y.status]}</td>)}
          </tr>
        </tbody>
      </table>
      <p style={{ marginTop: "0.6rem", color: "var(--rp-dim)", fontSize: "0.66rem", lineHeight: 1.5 }}>
        &quot;Ending existing debt&quot; combines commercial paper and current + noncurrent term debt, matching the
        archived report&apos;s <code style={{ fontFamily: "var(--utility)" }}>ending_existing_debt</code> field — it is not
        term debt alone. &quot;Revolver facility limit&quot; is the total committed size of the facility and does not fall
        as the revolver is drawn; &quot;Remaining revolver capacity&quot; (limit minus the ending revolver balance) is what
        is actually still available to draw.
      </p>
    </div>
  );
}

function X1FundingExplainer() {
  const d = data.x1FundingDetail;
  return (
    <article className={styles.modelPanel} style={{ marginBottom: "1.25rem" }}>
      <div className={styles.panelHeading}>
        <div><p className={styles.kicker}>X1 — original execution, stopped year 2</p><h2>Why X1 refused</h2></div>
      </div>
      <p style={{ color: "var(--rp-slate)", fontSize: "0.8rem", lineHeight: 1.6, marginTop: "0.5rem" }}>{d.refusalMessage}</p>
      <div className={styles.scenarioList}>
        <div><span>Maximum available cash, yr 2</span><strong style={{ color: "#ff9a8f" }}>{formatUsdMillions(d.maximumAvailableCashUsdMillions)}</strong></div>
        <div><span>Minimum required cash</span><strong>{formatUsdMillions(d.minimumRequiredCashUsdMillions)}</strong></div>
        <div><span>Shortfall</span><strong style={{ color: "#ff9a8f" }}>≈ ${d.shortfallUsdBillionsApprox}B ({formatUsdMillions(d.shortfallUsdMillions)})</strong></div>
      </div>
      <p style={{ marginTop: "0.9rem", color: "var(--rp-dim)", fontSize: "0.68rem", lineHeight: 1.55 }}>{d.note}</p>
    </article>
  );
}

/** Full-width, wrapping override for archive hashes/paths. The shared
 * `.evidenceRows small` rule truncates with an ellipsis and no expansion
 * control — appropriate for AAPL's clickable audit-drawer rows, but these
 * MSFT register rows are static and have nowhere else to show the full
 * value, so every cited hash/path must stay fully visible here instead. */
const evidenceValueStyle: CSSProperties = {
  display: "block",
  marginTop: "0.25rem",
  overflow: "visible",
  whiteSpace: "normal",
  textOverflow: "clip",
  wordBreak: "break-all",
  color: "var(--rp-dim)",
  fontFamily: "var(--utility)",
  fontSize: "0.64rem",
  lineHeight: 1.55,
};

const evidenceLabelStyle: CSSProperties = {
  display: "block", fontSize: "0.7rem", fontWeight: 620,
  overflow: "visible", whiteSpace: "normal", textOverflow: "clip",
};

function EvidenceRegisterRow({ label, values, last }: { label: ReactNode; values: readonly string[]; last?: boolean }) {
  return (
    <div style={{ width: "100%", padding: "0.75rem 0", borderBottom: last ? undefined : "1px solid var(--rp-line)" }}>
      <strong style={evidenceLabelStyle}>{label}</strong>
      {values.map((value) => (
        <small key={value} style={evidenceValueStyle}>{value}</small>
      ))}
    </div>
  );
}

function EvidencePanel() {
  const h = data.sourceHashes;
  const runs = data.runs;
  return (
    <>
      <section className={styles.evidenceStrip}>
        <div><p className={styles.kicker}>Provenance</p><h2>Every figure traces to an archived, hashed report.</h2></div>
        <div><span>13/13</span><p><strong>Opening-balance facts matched</strong>Stored-fact cross-check, original execution.</p></div>
        <div><span>{data.tenScenarioStatus.filter((r) => r.status === "completed" || r.status === "expected_refusal_confirmed").length}/10</span><p><strong>Scenarios resolved as designed</strong>Completed or expected-refusal-confirmed.</p></div>
        <div><span>0</span><p><strong>Silent substitutions</strong>Refused/unattempted years shown as such, never zero.</p></div>
      </section>
      <div className={styles.evidenceRegister}>
        <div className={styles.sectionHeading}>
          <div><p className={styles.kicker}>Source reports</p><h2>Archive register</h2></div>
        </div>
        <div className={styles.evidenceRows} style={{ display: "block" }}>
          <EvidenceRegisterRow label="Frozen scenario manifest" values={[h.scenarioManifest]} />
          <EvidenceRegisterRow label="R/O1/O2 development report" values={[h.developmentRunReport]} />
          <EvidenceRegisterRow
            label={<>Original execution (O3–X1) · exit code {runs.originalExecution.processExitCode}</>}
            values={[h.originalExecutionPartialReport, runs.originalExecution.archivePath]}
          />
          <EvidenceRegisterRow
            label={<>X2/X3 continuation · exit code {runs.continuation.processExitCode}</>}
            values={[h.continuationExecutionStdout, runs.continuation.archivePath]}
          />
          <EvidenceRegisterRow label="Closeout report v2" values={[h.closeoutExperimentCloseout]} last />
        </div>
      </div>
    </>
  );
}

const VIEW_COPY: Record<Exclude<MsftView, "overview">, { eyebrow: string; title: string; description: string }> = {
  scenarios: { eyebrow: "13-scenario experiment", title: "Scenarios", description: "Every scenario's unlevered FCF, illustrative 5% sensitivity vs. R, and the three predeclared exact financing invariants." },
  statements: { eyebrow: "Linked three-statement forecast", title: "Statements", description: "Income statement, balance sheet, and cash flow, year by year, for the selected scenario. Refused and not-attempted years are labeled, never shown as zero." },
  funding: { eyebrow: "Funding feasibility", title: "Funding & revolver", description: "Cash, term debt, revolver draws/repayments, interest and fees — including exactly why X1 refused in year 2." },
  evidence: { eyebrow: "Audit register", title: "Evidence", description: "Source archives, SHA-256 hashes, and the stored-fact cross-check backing every figure on this case." },
};

function PageHeading({ view }: { view: Exclude<MsftView, "overview"> }) {
  const copy = VIEW_COPY[view];
  return (
    <section className={styles.pageHeading}>
      <div><p className={styles.kicker}>{copy.eyebrow}</p><h1>{copy.title}</h1><p>{copy.description}</p></div>
      <div className={styles.cutoffBlock}>
        <span>Knowledge cutoff</span>
        <strong>{data.foundation.knowledge_cutoff}</strong>
        <span style={{ marginTop: "0.5rem" }}>Data-vintage cutoff</span>
        <strong>{data.foundation.data_vintage_cutoff}</strong>
      </div>
    </section>
  );
}

/** X1's archived `years` array stops at its year-2 refusal (only 2
 * entries) instead of explicitly recording year 3 as not attempted, the
 * way F3 already does for its own skipped years. Every table here renders
 * one column per `s.years` entry, so a short array produces a truncated,
 * misaligned table (e.g. against `assumptionYears`, which always has all
 * 3) rather than a labeled "Not attempted" column. This fills in only the
 * trailing gap — display-only, no forecast value or archived record is
 * invented — so every scenario always renders 3 year columns. */
function withAllThreeYears(s: ScenarioData): ScenarioData {
  if (s.years.length >= 3) return s;
  const present = new Set(s.years.map((y) => y.year));
  const years = [...s.years];
  for (let year = 1; year <= 3; year += 1) {
    if (!present.has(year)) {
      years.push({ year, status: "not_attempted", reason: "Run stopped before this year; not attempted." });
    }
  }
  years.sort((a, b) => a.year - b.year);
  return { ...s, years };
}

function ViewContent({
  view,
  selected,
  setSelected,
}: {
  view: MsftView;
  selected: ScenarioName;
  setSelected: (name: ScenarioName) => void;
}) {
  const s = useMemo(() => withAllThreeYears(data.scenarios[selected]), [selected]);

  if (view === "overview") {
    return (
      <>
        <section className={styles.caseIntro}>
          <div>
            <div className={styles.companyLine}>
              <span className={styles.companyMonogram} aria-hidden="true">M</span>
              <p><strong>Microsoft Corporation</strong><span>MSFT · NASDAQ · Technology</span></p>
            </div>
            <h1>Illustrative assumption-sensitivity experiment</h1>
            <p className={styles.caseDeck}>
              A bounded, three-year unlevered-FCF and funding-feasibility sensitivity experiment on one
              verified MSFT FY2024 opening balance sheet. No enterprise value, equity value, WACC, terminal
              value, or investment-performance claim is made anywhere in this case.
            </p>
          </div>
          <div className={styles.cutoffBlock}>
            <span>Knowledge cutoff</span>
            <strong>{data.foundation.knowledge_cutoff}</strong>
            <Link href={withScenario("/research/msft/evidence", selected)} prefetch><button type="button">Inspect source provenance <ArrowIcon /></button></Link>
          </div>
        </section>
        <CombinedStatusStrip />
        <section className={styles.primaryGrid}>
          <article className={styles.chartPanel}>
            <div className={styles.panelHeading}><div><p className={styles.kicker}>All ten validation scenarios</p><h2>Combined status</h2></div></div>
            <TenScenarioTable selected={selected} onSelect={setSelected} />
          </article>
          <aside className={styles.modelPanel}>
            <div className={styles.panelHeading}><div><p className={styles.kicker}>Development vs. validation</p><h2>Scope</h2></div></div>
            <dl style={{ marginTop: "1rem", borderTop: "1px solid var(--rp-line)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "0.7rem 0", borderBottom: "1px solid var(--rp-line)" }}><dt style={{ color: "var(--rp-slate)", fontSize: "0.7rem" }}>Development</dt><dd style={{ fontFamily: "var(--utility)", fontSize: "0.7rem" }}>{data.developmentCases.join(", ")}</dd></div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "0.7rem 0" }}><dt style={{ color: "var(--rp-slate)", fontSize: "0.7rem" }}>Validation</dt><dd style={{ fontFamily: "var(--utility)", fontSize: "0.68rem", textAlign: "right" }}>{data.validationCases.join(", ")}</dd></div>
            </dl>
            <p style={{ marginTop: "1rem", color: "var(--rp-dim)", fontSize: "0.68rem", lineHeight: 1.55 }}>
              5% materiality is an illustrative, predeclared convention — not a proven threshold of economic
              significance. 14 balance-sheet lines are fixed at zero movement by the engine&apos;s own design.
            </p>
          </aside>
        </section>
      </>
    );
  }

  if (view === "scenarios") {
    return (
      <>
        <PageHeading view="scenarios" />
        <p className={styles.kicker} style={{ marginTop: "1rem" }}>Selected scenario — {SCENARIO_LABELS[selected]}</p>
        <ScenarioPicker selected={selected} onSelect={setSelected} />
        <div style={{ marginTop: "1.25rem" }}><FcfPanel s={s} /></div>
      </>
    );
  }

  if (view === "statements") {
    return (
      <>
        <PageHeading view="statements" />
        <p className={styles.kicker} style={{ marginTop: "1rem" }}>Selected scenario — {SCENARIO_LABELS[selected]}</p>
        <ScenarioPicker selected={selected} onSelect={setSelected} />
        <div style={{ marginTop: "1.25rem" }} className={styles.tableFrame}><AssumptionsTable s={s} /></div>
        <LinkedStatementTable
          s={s}
          title="Income statement"
          statementKey="income"
          lines={["revenue", "operating_expense", "ebit", "interest", "commitment_fee", "draw_fee", "taxes", "net_income"]}
        />
        <BalanceSheetSection s={s} />
        <LinkedStatementTable
          s={s}
          title="Cash flow statement"
          statementKey="cashFlow"
          lines={["opening_cash", "cfo", "cfi", "cff", "ending_cash"]}
        />
      </>
    );
  }

  if (view === "funding") {
    return (
      <>
        <PageHeading view="funding" />
        {selected === "X1" && <div style={{ marginTop: "1.25rem" }}><X1FundingExplainer /></div>}
        <p className={styles.kicker} style={{ marginTop: "1rem" }}>Selected scenario — {SCENARIO_LABELS[selected]}</p>
        <ScenarioPicker selected={selected} onSelect={setSelected} />
        <div style={{ marginTop: "1.25rem" }}><FundingTables s={s} /></div>
      </>
    );
  }

  return (
    <>
      <PageHeading view="evidence" />
      <EvidencePanel />
    </>
  );
}

function MsftSensitivityShell({ view }: { view: MsftView }) {
  const [selected, setSelected] = useSelectedScenario();
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const activeLabel = WORKFLOW.find((item) => item.id === view)?.label ?? "Overview";

  return (
    <div className={styles.prototypeShell} data-research-shell>
      <aside className={`${styles.sidebar} ${mobileNavigationOpen ? styles.sidebarOpen : ""}`}>
        <div className={styles.brand}><span className={styles.brandMark} aria-hidden="true">VE</span><span><strong>Valuation Engine</strong><small>Research system</small></span></div>
        <p className={styles.navEyebrow}>MSFT sensitivity case</p>
        <nav className={styles.workflowNav} aria-label="MSFT research workflow">
          {WORKFLOW.map((item) => (
            <Link
              key={item.id}
              href={withScenario(item.href, selected)}
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
        <div className={styles.sidebarStatus}><span className={styles.verifiedDot} aria-hidden="true" /><div><strong>Archived evidence loaded</strong><small>Not a full pass — X1 stopped</small></div></div>
      </aside>

      {mobileNavigationOpen && (
        <button
          type="button"
          className={styles.navigationScrim}
          aria-label="Close research navigation"
          onClick={() => setMobileNavigationOpen(false)}
        />
      )}

      <div className={styles.workspace}>
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
          <div className={styles.breadcrumb}><span>Research</span><b>/</b><span>MSFT</span><b>/</b>{activeLabel}</div>
          <div className={styles.utilityActions}>
            <Link className={styles.evidenceLink} href={withScenario("/research/msft/evidence", selected)} prefetch><span>Inspect evidence</span><ArrowIcon /></Link>
            <Link className={styles.workspaceLink} href="/workspace" prefetch>Open workspace</Link>
          </div>
        </header>

        <main className={styles.main}>
          <div className={styles.prototypeNotice} role="note">Illustrative demonstration · archived evidence only · no forecast recalculated</div>
          <div className={styles.viewContent}>
            <ViewContent view={view} selected={selected} setSelected={setSelected} />
          </div>
          <footer className={styles.footer}>
            <span>Illustrative deterministic model results, not investment-performance evidence.</span>
            <Link href="/research/aapl" prefetch>Other research cases <ArrowIcon /></Link>
          </footer>
        </main>
      </div>
    </div>
  );
}

export default function MsftSensitivityPrototype({ view = "overview" }: { view?: MsftView }) {
  return (
    <Suspense fallback={null}>
      <MsftSensitivityShell view={view} />
    </Suspense>
  );
}
