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
 * The 10-Year Treasury yield and VIX that same endpoint also returns are
 * deliberately NOT repeated here — they're already the two "Live" summary
 * cards at the top of the home page (`ResearchHomeClient`), and showing
 * the identical two numbers a second time here would only inflate macro
 * context's visual weight against the page's actual subject: valuation
 * activity. This panel is headlines only.
 */
export default function MarketBrief({ ticker, status, sentiment, styles }: MarketBriefProps) {
  return (
    <div className={styles.panel}>
      <div className={styles.panelHeading}>
        <div>
          <p>Supplemental, not a model input</p>
          <h2>Recent headlines — {ticker}</h2>
        </div>
      </div>

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
