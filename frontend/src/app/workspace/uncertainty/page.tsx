import type { Metadata } from "next";
import { preload } from "react-dom";

import MonteCarloPanel from "@/components/valuation/MonteCarloPanel";

export const metadata: Metadata = {
  title: "Valuation Uncertainty | Valuation Workspace",
  description: "How much do uncertain growth, operating margins and discount rates change estimated value per share?",
};

// The workspace layout's persistent client steps aside for this view (see
// WorkspaceClient), so this page renders its own shell.
export default function UncertaintyPage() {
  // Start the settings request while the scripts download. The controls need it, and on an idle deployment it
  // is the slowest thing on the page (service cold starts), so it should not wait for hydration.
  preload("/api/monte-carlo", { as: "fetch", crossOrigin: "anonymous" });
  return (
    <main className="page-shell dark-workspace">
      <div className="shell-container pb-16">
        <header className="page-header">
          <div>
            <h1 className="display-title">Valuation uncertainty</h1>
            <p className="page-deck">
              How much does uncertainty in revenue growth, operating margin and the discount rate change estimated equity value per share?
              This is a simulation under stated ranges, not a forecast or a price target.
            </p>
          </div>
        </header>
        <MonteCarloPanel />
        <footer className="model-footer">
          <span>Valuation Workspace</span>
        </footer>
      </div>
    </main>
  );
}
