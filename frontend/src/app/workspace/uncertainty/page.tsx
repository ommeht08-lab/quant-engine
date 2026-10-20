import type { Metadata } from "next";

import MonteCarloPanel from "@/components/valuation/MonteCarloPanel";
import { MONTE_CARLO_METADATA } from "@/lib/monte-carlo-metadata";

export const metadata: Metadata = {
  title: "Valuation Uncertainty | Valuation Workspace",
  description: "How much do uncertain growth, operating margins and discount rates change estimated value per share?",
};

// The workspace layout's persistent client steps aside for this view (see
// WorkspaceClient), so this page renders its own shell. The controls are built from bundled metadata, so they
// are in the first HTML and never wait for the valuation backend.
export default function UncertaintyPage() {
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
        <MonteCarloPanel metadata={MONTE_CARLO_METADATA} />
        <footer className="model-footer">
          <span>Valuation Workspace</span>
        </footer>
      </div>
    </main>
  );
}
