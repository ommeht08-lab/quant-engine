import BacktestChart from "@/components/BacktestChart";

export default function BacktestsPage() {
  return (
    <div className="page-shell">
      <div className="shell-container pb-16">
        <header className="page-header">
          <div>
            <p className="eyebrow mb-2">Historical evidence</p>
            <h1 className="display-title">Backtests</h1>
          </div>
          <p className="page-deck">
            The Top-N Conviction Score strategy&rsquo;s equity curve against the S&amp;P 500.
          </p>
        </header>

        <BacktestChart />
      </div>
    </div>
  );
}
