import type { Metadata } from "next";

import OverviewClient from "./OverviewClient";

export const metadata: Metadata = {
  title: "Research Overview | Valuation Engine",
  description: "Live, ticker-selectable intrinsic-value overview built from the same default model the dashboard uses.",
};

// Matches ticker/[symbol]/page.tsx and both api/evaluate, api/sentiment
// routes: this is live, per-request valuation data for a ticker taken
// from the URL, never a page the Router Cache should be allowed to
// reuse across two different ticker segments.
export const dynamic = "force-dynamic";

// Thin server wrapper resolving the dynamic route param, mirroring
// `workspace/page.tsx`'s own split between route plumbing and the
// actual interactive component. `/overview/*` is an intentionally
// public company-model surface; operator data remains on separately
// session-protected routes.
export default async function OverviewPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  // Next.js already decodes a dynamic segment before handing it to
  // `params` (matching `ticker/[symbol]/page.tsx`'s own handling below —
  // a SECOND `decodeURIComponent` here would throw on a ticker containing
  // a literal, already-decoded "%" that isn't a valid escape sequence).
  const { ticker } = await params;
  const normalizedTicker = ticker.trim().toUpperCase();
  // `key` forces a full remount when the ticker changes (see
  // OverviewClient's own comment) rather than resetting state in an
  // effect.
  return <OverviewClient key={normalizedTicker} initialTicker={normalizedTicker} />;
}
