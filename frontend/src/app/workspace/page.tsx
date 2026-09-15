import type { Metadata } from "next";

import { parseTickerQueryParam } from "@/lib/ticker-query";
import WorkspaceClient from "./WorkspaceClient";

export const metadata: Metadata = {
  title: "Valuation Workspace | Valuation Engine",
  description: "Build a staged DCF case from company history or your own assumptions.",
};

// This route used to simply re-export the root page's component
// (`import Workspace from "../page"`), back when `/` itself served the
// valuation workflow before `src/proxy.ts` unconditionally redirected
// "/" away from it. That made `/` and `/workspace` two names for the
// same file for no reason a reader could tell apart — the workflow now
// lives here directly (`WorkspaceClient.tsx`), and `/` is its own thin
// redirect (`app/page.tsx`).
//
// Thin server wrapper resolving the optional `?ticker=` query param,
// mirroring `overview/[ticker]/page.tsx`'s own split between route
// plumbing and the interactive component. `parseTickerQueryParam`
// rejects anything not shaped like a real ticker (wrong charset, empty,
// overlong) rather than passing raw, unvalidated query input into the
// client component's initial state.
export default async function WorkspacePage({
  searchParams,
}: {
  searchParams: Promise<{ ticker?: string | string[] }>;
}) {
  const params = await searchParams;
  const initialTicker = parseTickerQueryParam(params.ticker) ?? "AAPL";
  return <WorkspaceClient initialTicker={initialTicker} />;
}
