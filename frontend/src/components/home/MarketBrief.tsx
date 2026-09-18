"use client";

import { safeHeadlineHref } from "@/lib/headline-links";
import type { TickerSentiment } from "@/lib/sentiment";

export type MarketBriefStatus = "loading" | "ready" | "error";

export interface MarketBriefProps {
  ticker: string;
  status: MarketBriefStatus;
  sentiment: TickerSentiment | null;
  styles: Record<string, string>;
}

function formatHeadlineTime(publishedAt: string | null): string | null {
  if (!publishedAt) return null;
  const date = new Date(publishedAt);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/**
 * Recent headlines for either the most recent valuation's ticker or the
 * documented fallback (`DEFAULT_OVERVIEW_TICKER`) — see
 * `ResearchHomeClient.tsx`. Reuses the existing `GET /api/sentiment/[symbol]`
 * endpoint and the existing headline-link sanitizer; this component owns
 * no fetching of its own and adds no second news provider.
 *
 * Treasury yield and VIX stay compact context alongside the headlines;
 * neither is a model input.
 */
export default function MarketBrief({ ticker, status, sentiment, styles }: MarketBriefProps) {
  return (
    <div className={styles.panel}>
      <div className={styles.panelHeading}>
        <div>
          <h2>Recent headlines — {ticker}</h2>
          <p>Supplemental context · never a model input</p>
        </div>
      </div>

      {status === "ready" && sentiment && (sentiment.macro.treasury10y != null || sentiment.macro.vix != null) && (
        <div className={styles.macroGrid}>
          <div><span>10-year Treasury</span><strong>{sentiment.macro.treasury10y == null ? "—" : `${(sentiment.macro.treasury10y * 100).toFixed(2)}%`}</strong></div>
          <div><span>VIX</span><strong>{sentiment.macro.vix == null ? "—" : sentiment.macro.vix.toFixed(1)}</strong></div>
        </div>
      )}

      {status === "loading" && <p className={styles.briefStatusNote}>Loading headlines…</p>}

      {status === "error" && (
        <p className={styles.briefStatusNote}>
          Headlines are temporarily unavailable. This is supplemental context only — it never
          affects the valuation model itself.
        </p>
      )}

      {status === "ready" && sentiment && sentiment.headlines.length === 0 && (
        <p className={styles.briefStatusNote}>No recent headlines found for {ticker}.</p>
      )}

      {status === "ready" && sentiment && sentiment.headlines.length > 0 && (
        <ul className={styles.briefHeadlineList}>
          {sentiment.headlines.slice(0, 4).map((headline, index) => {
            const href = safeHeadlineHref(headline.link);
            const time = formatHeadlineTime(headline.publishedAt);
            return (
              <li key={`${headline.link ?? headline.title}-${index}`} className={styles.briefHeadlineItem}>
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer" className={styles.briefHeadlineLink}>
                    {headline.title}
                    <span className={styles.briefHeadlineLinkGlyph} aria-hidden="true">↗</span>
                  </a>
                ) : (
                  <span>{headline.title}</span>
                )}
                <small>
                  {headline.publisher}
                  {headline.publisher && time && " · "}
                  {time}
                </small>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
