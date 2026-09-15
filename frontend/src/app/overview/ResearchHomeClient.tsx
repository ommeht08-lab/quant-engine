"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import HomeMetricCard from "@/components/home/HomeMetricCard";
import MarketBrief, { type MarketBriefStatus } from "@/components/home/MarketBrief";
import ModelOverview from "@/components/home/ModelOverview";
import RecentValuations from "@/components/home/RecentValuations";
import { formatPreciseCurrency } from "@/components/valuation/format";
import { DEFAULT_OVERVIEW_TICKER } from "@/lib/default-route";
import { overviewRouteForTicker } from "@/lib/overview-response";
import type { TickerSentiment } from "@/lib/sentiment";
import { readValuationHistory, type ValuationHistoryEntry } from "@/lib/valuation-history";
import styles from "./ResearchHome.module.css";

/**
 * The research home page (`/overview`) — a real landing screen, not a
 * redirect to an arbitrary company. Summary cards, this browser's own
 * valuation-run history, supplemental market context, and a plain-
 * language model explainer. See `default-route.ts` for why this replaced
 * a hardcoded default ticker as the shared authenticated landing target.
 */
export default function ResearchHomeClient() {
  const router = useRouter();
  const [tickerInput, setTickerInput] = useState("");

  // `null` = "not yet read from localStorage" (distinct from a genuinely
  // empty history) — read only in an effect, never during render, so the
  // server-rendered markup and the first client render match exactly
  // (localStorage does not exist during server rendering at all).
  const [history, setHistory] = useState<ValuationHistoryEntry[] | null>(null);

  useEffect(() => {
    function loadHistory() {
      setHistory(readValuationHistory());
    }
    loadHistory();
  }, []);

  const newsTicker = history && history.length > 0 ? history[0].ticker : DEFAULT_OVERVIEW_TICKER;

  const [sentiment, setSentiment] = useState<TickerSentiment | null>(null);
  const [briefStatus, setBriefStatus] = useState<MarketBriefStatus>("loading");

  useEffect(() => {
    // Wait for history to resolve first, so this fetches the RIGHT
    // ticker once rather than the fallback, then the real one, twice.
    if (history === null) return;

    let cancelled = false;

    async function run() {
      setBriefStatus("loading");
      try {
        const response = await fetch(`/api/sentiment/${encodeURIComponent(newsTicker)}`);
        if (!response.ok) throw new Error("sentiment request failed");
        const data: TickerSentiment = await response.json();
        if (!cancelled) {
          setSentiment(data);
          setBriefStatus("ready");
        }
      } catch {
        if (!cancelled) setBriefStatus("error");
      }
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [history, newsTicker]);

  function handleTickerSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = overviewRouteForTicker(tickerInput);
    if (target) router.push(target);
  }

  const mostRecent = history && history.length > 0 ? history[0] : null;

  return (
    <main className={styles.home}>
      <div className={styles.content}>
        <header className={styles.hero}>
          <div>
            <p className={styles.eyebrow}>Research home</p>
            <h1>Equity research desk</h1>
            <p className={styles.heroDeck}>
              A staged-DCF valuation workspace with run history saved to this browser and
              supplemental market context — start a new valuation, or open a company&apos;s live
              overview directly.
            </p>
          </div>

          <div className={styles.heroActions}>
            <Link href="/workspace" className={styles.runButton}>
              Run valuation
            </Link>
            <form onSubmit={handleTickerSubmit} className={styles.tickerForm}>
              <label htmlFor="home-ticker-field">Open company overview</label>
              <div>
                <input
                  id="home-ticker-field"
                  value={tickerInput}
                  onChange={(event) => setTickerInput(event.target.value.toUpperCase())}
                  placeholder="AAPL"
                  maxLength={10}
                  autoComplete="off"
                  spellCheck={false}
                />
                <button type="submit">Open</button>
              </div>
            </form>
          </div>
        </header>

        <section className={styles.metrics} aria-label="Summary">
          <HomeMetricCard
            label="Most recent valuation"
            tag="Local"
            value={history === null ? "…" : mostRecent ? mostRecent.ticker : "None yet"}
            sublabel={
              history === null
                ? "Loading…"
                : mostRecent === null
                  ? "Run a valuation to populate this"
                  : mostRecent.baseIntrinsicValuePerShare === null
                    ? "Base case not computable"
                    : `Base ${formatPreciseCurrency(mostRecent.baseIntrinsicValuePerShare)}`
            }
            cardClassName={`${styles.metricCard} ${styles.metricPrimary} ${styles.metricAccentBlue}`}
            tagClassName={styles.tagBlue}
          />
          <HomeMetricCard
            label="Valuation runs saved"
            tag="Local"
            value={history === null ? "…" : String(history.length)}
            sublabel="Saved in this browser, up to 12"
            cardClassName={`${styles.metricCard} ${styles.metricAccentViolet}`}
            tagClassName={styles.tagViolet}
          />
          {/* Treasury/VIX are deliberately ONE quieter, secondary card
              (not two cards at the same visual weight as the valuation-
              activity cards above) — macro context supports the research,
              it isn't the subject of this page. The same two numbers are
              never repeated elsewhere on the page (see MarketBrief). */}
          <div className={`${styles.metricCard} ${styles.macroCard}`}>
            <div className={styles.macroCardHeader}>
              <span>Macro snapshot</span>
              <i className={styles.tagGreen}>Live</i>
            </div>
            <div className={styles.macroCardStats}>
              <div>
                <span>10-Yr Treasury</span>
                <strong>
                  {briefStatus === "loading"
                    ? "…"
                    : sentiment?.macro.treasury10y != null
                      ? `${(sentiment.macro.treasury10y * 100).toFixed(2)}%`
                      : "Unavailable"}
                </strong>
              </div>
              <div>
                <span>VIX</span>
                <strong>
                  {briefStatus === "loading"
                    ? "…"
                    : sentiment?.macro.vix != null
                      ? sentiment.macro.vix.toFixed(2)
                      : "Unavailable"}
                </strong>
              </div>
            </div>
          </div>
        </section>

        <section className={styles.mainGrid}>
          <RecentValuations entries={history ?? []} styles={styles} />
          <MarketBrief ticker={newsTicker} status={briefStatus} sentiment={sentiment} styles={styles} />
        </section>

        <section>
          <ModelOverview styles={styles} />
        </section>
      </div>
    </main>
  );
}
