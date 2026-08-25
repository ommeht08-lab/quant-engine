import PortfolioAllocation from "@/components/PortfolioAllocation";
import RiskHistogram from "@/components/RiskHistogram";

export default function PortfolioPage() {
  return (
    <div className="page-shell">
      <div className="shell-container pb-16">
        <header className="page-header">
          <div>
            <p className="eyebrow mb-2">Paper portfolio</p>
            <h1 className="display-title">Portfolio &amp; risk</h1>
          </div>
          <p className="page-deck">
            Exposure and risk from the paper account, kept separate from the valuation case so
            unavailable data is never mistaken for zero.
          </p>
        </header>

        <div className="grid gap-5 xl:grid-cols-2">
          <PortfolioAllocation />
          <RiskHistogram />
        </div>
      </div>
    </div>
  );
}
