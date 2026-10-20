import { Suspense, type ReactNode } from "react";
import WorkspaceShell from "./WorkspaceShell";
// A persistent layout keeps the explicit run and inputs across workspace URLs.
export default function WorkspaceLayout({children}:{children:ReactNode}) {
  return <><Suspense fallback={<main className="page-shell"><p>Loading workspace…</p></main>}><WorkspaceShell initialTicker="AAPL" /></Suspense>{children}</>;
}
