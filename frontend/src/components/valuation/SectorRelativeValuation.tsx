import {
  sectorMedianProvenanceCaption,
  sectorMedianUnavailableCopy,
  sectorRelativeBadgeLabel,
  sectorRelativeDisclaimer,
  type SectorMedianUnavailableCode,
} from "@/lib/sector-median-copy";

export interface SectorMedianSnapshot {
  generated_at: string;
  universe_size: number;
  tickers_used: number;
  sector_sample_count: number;
}

export interface SectorRelativeValuationProps {
  ticker: string;
  sector: string;
  priceToIntrinsicValue: number | null;
  sectorMedianPIV: number | null;
  sectorMedianUnavailableCode: SectorMedianUnavailableCode | null;
  sectorMedianSnapshot: SectorMedianSnapshot | null;
}

// A PEER-relative read, never an intrinsic-value claim — see
// `sectorRelativeDisclaimer`. Trading below the sector median P/IV
// means this ticker is relatively LESS EXPENSIVE than its peers; it
// does not mean the shares are undervalued in an absolute sense (that
// question belongs to the Thesis Rail and its own, correctly-reserved
// "Margin of safety" language).
export default function SectorRelativeValuation({
  ticker,
  sector,
  priceToIntrinsicValue,
  sectorMedianPIV,
  sectorMedianUnavailableCode,
  sectorMedianSnapshot,
}: SectorRelativeValuationProps) {
  if (priceToIntrinsicValue === null || sectorMedianPIV === null) {
    return (
      <div>
        <h2 className="section-title">Sector-relative valuation</h2>
        <div className="panel p-5 sm:p-6">
          <p className="text-sm leading-6 text-[var(--paper-dim)]">
            {priceToIntrinsicValue === null
              ? "Price-to-intrinsic-value could not be computed for this ticker."
              : sectorMedianUnavailableCopy(sectorMedianUnavailableCode)}
          </p>
        </div>
      </div>
    );
  }

  const isBelowSectorMedian = priceToIntrinsicValue <= sectorMedianPIV;
  const maxScale = Math.max(priceToIntrinsicValue, sectorMedianPIV) * 1.15;
  const stockBarPct = Math.min((priceToIntrinsicValue / maxScale) * 100, 100);
  const medianBarPct = Math.min((sectorMedianPIV / maxScale) * 100, 100);

  return (
    <div>
      <h2 className="section-title">Sector-relative valuation</h2>
      <div className="panel p-5 sm:p-6">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <span className="text-xs text-[var(--paper-dim)]">
            {ticker}&rsquo;s Price / Intrinsic Value vs. the {sector} sector median.
          </span>
          <span
            className={`text-xs font-semibold uppercase tracking-wide ${
              isBelowSectorMedian ? "text-[var(--verdigris)]" : "text-[var(--paper-muted)]"
            }`}
          >
            {sectorRelativeBadgeLabel(isBelowSectorMedian)}
          </span>
        </div>

        <div className="space-y-4">
          <div>
            <div className="mb-1.5 flex items-baseline justify-between text-sm">
              <span className="text-[var(--paper-muted)]">{ticker} P/IV</span>
              <span
                className={`tabular-nums font-mono font-semibold ${
                  isBelowSectorMedian ? "text-[var(--verdigris)]" : "text-[var(--paper)]"
                }`}
              >
                {priceToIntrinsicValue.toFixed(2)}x
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--ledger)]">
              <div
                className={`h-full ${isBelowSectorMedian ? "bg-[var(--verdigris)]" : "bg-[var(--paper-muted)]"}`}
                style={{ width: `${stockBarPct}%` }}
              />
            </div>
          </div>

          <div>
            <div className="mb-1.5 flex items-baseline justify-between text-sm">
              <span className="text-[var(--paper-muted)]">{sector} sector median</span>
              <span className="tabular-nums font-mono font-semibold text-[var(--paper-muted)]">
                {sectorMedianPIV.toFixed(2)}x
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--ledger)]">
              <div className="h-full bg-[var(--paper-dim)]" style={{ width: `${medianBarPct}%` }} />
            </div>
          </div>
        </div>

        <p className="mt-4 text-xs leading-5 text-[var(--paper-dim)]">{sectorRelativeDisclaimer()}</p>

        {sectorMedianSnapshot && (
          <p className="mt-2 text-xs text-[var(--paper-dim)]">
            {sectorMedianProvenanceCaption(sectorMedianSnapshot)}
          </p>
        )}
      </div>
    </div>
  );
}
