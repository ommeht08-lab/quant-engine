"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";

import type { ValuationRequestError } from "@/lib/valuation-errors";
import { overviewRouteForTicker } from "@/lib/overview-response";
import { fetchLiveResearchOverview } from "@/lib/overview-fetch";
import type { ResearchOverviewViewModel } from "@/lib/research-overview-view-model";
import ResearchShell, { type ResearchNavItem } from "@/components/research/ResearchShell";
import ResearchOverviewContent from "@/components/research/ResearchOverviewContent";
import styles from "@/components/research/FlagshipResearchPrototype.module.css";

// If a request is still pending past this, the UI says so explicitly
// rather than leaving the operator to wonder whether it is broken.
const PENDING_NOTICE_DELAY_MS = 6000;

interface OverviewClientProps {
  /** Already trimmed/uppercased by the server wrapper (`page.tsx`). */
  initialTicker: string;
}

type LoadState =
  | { status: "loading" }
  | { status: "ready"; viewModel: ResearchOverviewViewModel }
  | { status: "error"; error: ValuationRequestError };

function SkeletonBar({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return <span className={`${styles.skeletonBar} ${className ?? ""}`} style={style} aria-hidden="true" />;
}

/**
 * Sized and positioned like the real overview it stands in for (same
 * caseIntro/summaryBand/primaryGrid containers) — not one oversized
 * blank rectangle, and nothing here implies content that will never
 * arrive is still "loading forever": `PendingNotice` below takes over
 * that message after a real delay.
 */
function LoadingSkeleton() {
  return (
    <>
      <section className={styles.caseIntro}>
        <div>
          <div className={styles.companyLine}>
            <SkeletonBar className={styles.companyMonogram} />
            <p><SkeletonBar style={{ display: "inline-block", width: "9rem", height: "0.85rem" }} /></p>
          </div>
          <SkeletonBar style={{ display: "block", width: "16rem", height: "2.4rem", marginTop: "0.3rem" }} />
        </div>
      </section>
      <section className={styles.summaryBand} aria-hidden="true">
        {["Market price", "Intrinsic value", "Margin of safety", "Data quality"].map((label) => (
          <div key={label}>
            <span>{label}</span>
            <SkeletonBar style={{ display: "block", width: "5rem", height: "1.4rem", marginTop: "0.42rem" }} />
          </div>
        ))}
      </section>
      <section className={styles.primaryGrid}>
        <article className={styles.chartPanel}>
          <SkeletonBar style={{ display: "block", width: "10rem", height: "1rem" }} />
          <SkeletonBar style={{ display: "block", width: "100%", height: "12rem", marginTop: "1rem" }} />
        </article>
        <aside className={styles.modelPanel}>
          <SkeletonBar style={{ display: "block", width: "8rem", height: "1rem" }} />
          {[0, 1, 2].map((row) => (
            <SkeletonBar key={row} style={{ display: "block", width: "100%", height: "2.2rem", marginTop: "0.7rem" }} />
          ))}
        </aside>
      </section>
    </>
  );
}

function PendingNotice() {
  return (
    <p className={styles.pendingNotice} role="status">
      Still waiting on a response from the live valuation service — this can take a few seconds for a ticker with
      more financial history to process.
    </p>
  );
}

function ErrorPanel({ error, onRetry }: { error: ValuationRequestError; onRetry: () => void }) {
  return (
    <div className={styles.errorBanner} role="alert">
      <strong>
        {error.kind === "unavailable" ? "Live valuation is not connected" : error.kind === "input" ? "Check the ticker" : "Valuation could not run"}
      </strong>
      <p>{error.message}</p>
      <button type="button" className={styles.errorRetry} onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

const NAV_ICON_OVERVIEW = (
  <svg aria-hidden="true" viewBox="0 0 24 24">
    <rect x="3.5" y="3.5" width="6.5" height="6.5" rx="1.2" />
    <rect x="14" y="3.5" width="6.5" height="6.5" rx="1.2" />
    <rect x="3.5" y="14" width="6.5" height="6.5" rx="1.2" />
    <rect x="14" y="14" width="6.5" height="6.5" rx="1.2" />
  </svg>
);
const NAV_ICON_VALUATION = (
  <svg aria-hidden="true" viewBox="0 0 24 24">
    <circle cx="12" cy="12" r="8.5" />
    <path d="M14.7 8.7c-.7-.6-1.5-.9-2.6-.9-1.5 0-2.5.7-2.5 1.8 0 2.8 5.2 1.4 5.2 4.4 0 1.2-1 2.1-2.7 2.1-1.2 0-2.2-.4-3-1.1M12 6.3v11.4" />
  </svg>
);
const NAV_ICON_METHODOLOGY = (
  <svg aria-hidden="true" viewBox="0 0 24 24">
    <path d="M4.5 5.5c2.6-.8 5.1-.5 7.5 1.1v13c-2.4-1.6-4.9-1.9-7.5-1.1zM19.5 5.5c-2.6-.8-5.1-.5-7.5 1.1v13c2.4-1.6 4.9-1.9 7.5-1.1z" />
  </svg>
);

// Live mode's own workflow nav is deliberately smaller than the
// fixture's six items — Statements/Forecast/Evidence have no live
// equivalent yet (see ResearchOverviewContent's own note on this same
// point), and pointing them at the static AAPL sub-pages while viewing
// a different ticker would be exactly the "relabeled as another
// company's data" mistake this slice exists to avoid. Valuation goes to
// the real, functional assumption-editing surface; Methodology is a
// real, ticker-agnostic page.
const LIVE_NAV_ITEMS: Omit<ResearchNavItem, "active">[] = [
  { id: "overview", href: "/overview", label: "Overview", description: "Live case summary", icon: NAV_ICON_OVERVIEW },
  { id: "valuation", href: "/workspace", label: "Valuation", description: "Edit assumptions", icon: NAV_ICON_VALUATION },
  { id: "methodology", href: "/methodology", label: "Methodology", description: "Methods and limits", icon: NAV_ICON_METHODOLOGY },
];

export default function OverviewClient({ initialTicker }: OverviewClientProps) {
  const router = useRouter();
  const [tickerInput, setTickerInput] = useState(initialTicker);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [retryToken, setRetryToken] = useState(0);
  const [pendingLong, setPendingLong] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    // No synchronous setState here — this effect's job is to start a
    // fetch and react to ITS result. The "loading" reset for a retry
    // happens in handleRetry below (a real event handler, not this
    // effect); the very first render already starts at
    // {status:"loading"} via useState's own initial value, so nothing
    // needs to re-assert it on mount either.
    const pendingTimer = setTimeout(() => {
      if (active) setPendingLong(true);
    }, PENDING_NOTICE_DELAY_MS);

    fetchLiveResearchOverview(initialTicker, controller.signal).then((result) => {
      clearTimeout(pendingTimer);
      if (!active || result.status === "aborted") return;
      setPendingLong(false);
      if (result.status === "success") {
        setState({ status: "ready", viewModel: result.viewModel });
      } else {
        setState({ status: "error", error: result.error });
      }
    });

    return () => {
      active = false;
      controller.abort();
      clearTimeout(pendingTimer);
    };
  }, [initialTicker, retryToken]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = overviewRouteForTicker(tickerInput);
    if (!target) return;
    router.push(target);
  }

  const navItems: ResearchNavItem[] = LIVE_NAV_ITEMS.map((item) => ({
    ...item,
    href: item.id === "overview" ? overviewRouteForTicker(initialTicker) ?? "/overview" : item.href,
    active: item.id === "overview",
  }));

  const tickerControl = (
    <form className={styles.tickerForm} onSubmit={handleSubmit}>
      <label htmlFor="overview-ticker" className="sr-only">
        Ticker
      </label>
      <input
        id="overview-ticker"
        type="text"
        value={tickerInput}
        onChange={(event) => setTickerInput(event.target.value.toUpperCase())}
        placeholder="AAPL"
        maxLength={10}
        autoComplete="off"
        spellCheck={false}
        className={styles.tickerInput}
      />
      <button type="submit" disabled={state.status === "loading"} className={styles.tickerSubmit}>
        {state.status === "loading" ? "…" : "View"}
      </button>
    </form>
  );

  return (
    <ResearchShell
      navItems={navItems}
      sidebarStatusLabel={
        state.status === "ready" ? "Live valuation loaded" : state.status === "error" ? "Live valuation unavailable" : "Loading live valuation…"
      }
      sidebarStatusDetail={initialTicker}
      breadcrumbSection="Overview"
      breadcrumbTicker={initialTicker}
      breadcrumbViewLabel="Overview"
      noticeText="Live staged valuation case · policy output, not a recommendation or price target"
      utilityActions={tickerControl}
      footer={<span>Figures come from the current valuation response and can change with source data and model assumptions.</span>}
    >
      {state.status === "loading" && (
        <>
          <p className="sr-only" role="status" aria-live="polite">
            Fetching live valuation data for {initialTicker}…
          </p>
          <div className={styles.loadingStatus} aria-hidden="true">
            <span className={styles.loadingSpinner} />
            Loading {initialTicker}…
          </div>
          {pendingLong && <PendingNotice />}
          <LoadingSkeleton />
        </>
      )}

      {state.status === "error" && (
        <ErrorPanel
          error={state.error}
          onRetry={() => {
            setState({ status: "loading" });
            setPendingLong(false);
            setRetryToken((token) => token + 1);
          }}
        />
      )}

      {state.status === "ready" && <ResearchOverviewContent viewModel={state.viewModel} />}
    </ResearchShell>
  );
}
