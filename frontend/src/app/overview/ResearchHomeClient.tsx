"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import MarketBrief, { type MarketBriefStatus } from "@/components/home/MarketBrief";
import HomeMetricCard from "@/components/home/HomeMetricCard";
import ModelOverview from "@/components/home/ModelOverview";
import RecentValuations from "@/components/home/RecentValuations";
import { formatPercent, formatPreciseCurrency } from "@/components/valuation/format";
import { DEFAULT_OVERVIEW_TICKER } from "@/lib/default-route";
import type { TickerSentiment } from "@/lib/sentiment";
import { readValuationHistory, type ValuationHistoryEntry } from "@/lib/valuation-history";
import styles from "./ResearchHome.module.css";

export default function ResearchHomeClient() {
  const router = useRouter();
  const [tickerInput, setTickerInput] = useState("MSFT");
  const [history, setHistory] = useState<ValuationHistoryEntry[] | null>(null);
  const [sentiment, setSentiment] = useState<TickerSentiment | null>(null);
  const [briefStatus, setBriefStatus] = useState<MarketBriefStatus>("loading");

  useEffect(() => {
    function loadHistory() {
      setHistory(readValuationHistory());
    }
    loadHistory();
  }, []);

  const mostRecent = history?.[0] ?? null;
  const newsTicker = mostRecent?.ticker ?? DEFAULT_OVERVIEW_TICKER;

  useEffect(() => {
    if (history === null) return;
    let cancelled = false;
    async function loadBrief() {
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
    loadBrief();
    return () => { cancelled = true; };
  }, [history, newsTicker]);

  function handleValuationSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const symbol = tickerInput.trim().toUpperCase();
    if (symbol) router.push(`/workspace?ticker=${encodeURIComponent(symbol)}`);
  }

  const marketGap = mostRecent?.marketGapPct ?? null;
  return (
    <main className={styles.home}>
      <div className={styles.content}>
        <header className={styles.hero}>
          <div className={styles.heroCopy}>
            <h1>Your valuation desk</h1>
            <p>Run a staged DCF, recover recent work, and keep market context close to the model.</p>
          </div>

          <form onSubmit={handleValuationSubmit} className={styles.quickRun}>
            <label htmlFor="home-valuation-ticker">Start a valuation</label>
            <div>
              <input
                id="home-valuation-ticker"
                value={tickerInput}
                onChange={(event) => setTickerInput(event.target.value.toUpperCase())}
                placeholder="MSFT"
                maxLength={10}
                autoComplete="off"
                spellCheck={false}
              />
              <button type="submit">Open workspace <span aria-hidden="true">→</span></button>
            </div>
          </form>
        </header>

        <section className={styles.metricGrid} aria-label="Research summary">
          <HomeMetricCard label="Latest company" tag="Local" value={history === null ? "Loading" : mostRecent?.ticker ?? "No runs"} sublabel="Most recent valuation saved in this browser" cardClassName={styles.metricCard} tagClassName={styles.metricTagLocal} />
          <HomeMetricCard label="Base intrinsic value" tag="DCF" value={mostRecent?.baseIntrinsicValuePerShare == null ? "—" : formatPreciseCurrency(mostRecent.baseIntrinsicValuePerShare)} sublabel="Per-share value from the latest base case" cardClassName={styles.metricCard} tagClassName={styles.metricTagModel} />
          <HomeMetricCard label="Market gap" tag="Vs price" value={marketGap == null ? "—" : formatPercent(marketGap)} sublabel="Intrinsic value relative to observed market price" cardClassName={styles.metricCard} tagClassName={styles.metricTagMarket} valueClassName={marketGap == null ? undefined : marketGap >= 0 ? styles.positive : styles.negative} />
          <HomeMetricCard label="Saved analyses" tag="Browser" value={history === null ? "—" : String(history.length)} sublabel="Private to this browser · up to 12 retained" cardClassName={styles.metricCard} tagClassName={styles.metricTagLocal} />
        </section>

        <section className={styles.mainGrid}>
          <RecentValuations entries={(history ?? []).slice(0, 4)} styles={styles} />
          <MarketBrief ticker={newsTicker} status={briefStatus} sentiment={sentiment} styles={styles} />
        </section>

        <ModelOverview styles={styles} />

        <footer className={styles.homeFooter}>
          <span>Research output, not investment advice.</span>
          <Link href="/methodology">Read the model methodology →</Link>
        </footer>
      </div>
    </main>
  );
}
