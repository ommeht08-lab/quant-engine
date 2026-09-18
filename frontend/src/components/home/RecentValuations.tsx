"use client";

import Link from "next/link";

import { formatPercent, formatPreciseCurrency } from "@/components/valuation/format";
import { overviewRouteForTicker } from "@/lib/overview-response";
import type { ValuationHistoryEntry } from "@/lib/valuation-history";

export interface RecentValuationsProps {
  entries: ValuationHistoryEntry[];
  /** The `ResearchHome.module.css` class map, threaded down from the page — see that file for why this single dashboard route shares one CSS Module rather than one per component. */
  styles: Record<string, string>;
}

const QUALITY_LABEL: Record<ValuationHistoryEntry["qualityLevel"], string> = {
  ordinary: "Ordinary",
  caution: "Caution",
  diagnostic_only: "Diagnostic only",
};

// An absolute timestamp, not a "2h ago"-style relative one — computing a
// relative label would need the current time as of RENDER, which is an
// impure read (see the React Compiler's purity rule); an exact date/time
// is also more useful for an audit trail than a vague relative label.
function formatRunTimestamp(timestamp: number): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "Unknown time";
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function ValuationRow({ entry, styles }: { entry: ValuationHistoryEntry; styles: Record<string, string> }) {
  const overviewHref = overviewRouteForTicker(entry.ticker);
  const workspaceHref = `/workspace?ticker=${encodeURIComponent(entry.ticker)}`;
  const hasGap = entry.marketGapPct !== null;

  return (
    <li className={styles.recentRow}>
      <div className={styles.recentRowMain}>
        <strong>{entry.ticker}</strong>
        <span>{entry.baseIntrinsicValuePerShare === null ? "Not computable" : formatPreciseCurrency(entry.baseIntrinsicValuePerShare)}</span>
        <i className={styles[`quality-${entry.qualityLevel}`]}>{QUALITY_LABEL[entry.qualityLevel]}</i>
      </div>
      <div className={styles.recentRowMeta}>
        <span>{entry.marketPrice === null ? "Market price unavailable" : `Market ${formatPreciseCurrency(entry.marketPrice)}`}</span>
        <span className={hasGap ? (entry.marketGapPct! >= 0 ? styles.recentGapPositive : styles.recentGapNegative) : undefined}>
          {hasGap ? formatPercent(entry.marketGapPct!) : "Gap withheld"}
        </span>
        <span>{entry.assumptionMode === "custom" ? "Custom assumptions" : "Historical assumptions"}</span>
        <span>{formatRunTimestamp(entry.timestamp)}</span>
      </div>
      <div className={styles.recentRowActions}>
        {overviewHref && (
          <Link href={overviewHref} className={styles.recentAction}>
            Open overview
          </Link>
        )}
        <Link href={workspaceHref} className={styles.recentAction}>
          Value again
        </Link>
      </div>
    </li>
  );
}

/**
 * The research home page's main module: every explicit valuation run
 * saved in this browser (see `lib/valuation-history.ts`), most recent
 * first. Never a server-backed history — this repo has none — so the
 * empty state and the section label both say so plainly rather than
 * implying a account-wide record.
 */
export default function RecentValuations({ entries, styles }: RecentValuationsProps) {
  return (
    <div className={styles.panel}>
      <div className={styles.panelHeading}>
        <div>
          <h2>Recent valuations</h2>
          <p>Saved privately in this browser</p>
        </div>
        <span>{entries.length} of 12 kept</span>
      </div>

      {entries.length === 0 ? (
        <div className={styles.recentEmpty}>
          <strong>No valuations run yet.</strong>
          <span>Runs from the valuation workspace appear here — nothing is recorded until you press Run.</span>
          <Link href="/workspace" className={styles.recentEmptyAction}>
            Run your first valuation
          </Link>
        </div>
      ) : (
        <ul className={styles.recentList}>
          {entries.map((entry) => (
            <ValuationRow key={`${entry.ticker}-${entry.timestamp}`} entry={entry} styles={styles} />
          ))}
        </ul>
      )}
    </div>
  );
}
