interface UnavailablePanelProps {
  title: string;
  reason: string;
}

// A research section with no live data source yet. Renders the SAME
// explicit-unavailable shape as `SectorRelativeValuation`'s own
// no-comparison branch, so "not available" reads consistently across
// every panel on the overview rather than each panel inventing its own
// empty state. `reason` must be a true, specific statement about why the
// panel is empty (see its two current call sites) — never generic
// "coming soon" copy, and never a value borrowed from another ticker or
// from the static AAPL fixture.
export default function UnavailablePanel({ title, reason }: UnavailablePanelProps) {
  return (
    <div>
      <h2 className="section-title">{title}</h2>
      <div className="panel p-5 sm:p-6">
        <p className="text-sm leading-6 text-[var(--paper-dim)]">{reason}</p>
      </div>
    </div>
  );
}
