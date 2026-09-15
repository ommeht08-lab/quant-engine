import type { Metadata } from "next";

import ResearchHomeClient from "./ResearchHomeClient";

export const metadata: Metadata = {
  title: "Research Home | Valuation Engine",
  description: "Summary cards, saved valuation history, and market context for the equity research desk.",
};

// The real authenticated landing destination (`DEFAULT_OVERVIEW_PATH`,
// see `lib/default-route.ts`) — this used to redirect to a single
// hardcoded company (`/overview/MSFT`) instead of being a home page in
// its own right; see git history and that constant's own docstring.
// Session protection is unaffected either way: this route isn't listed
// in `PUBLIC_ROUTE_PREFIXES`, so `proxy.ts`'s default-deny still applies
// before this component ever renders.
export default function OverviewIndexPage() {
  return <ResearchHomeClient />;
}
