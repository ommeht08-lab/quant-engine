import { formatPercent, formatPreciseCurrency } from "./format";

export interface ValuationHeadlineProps {
  ticker: string;
  sector: string;
  intrinsicValue: number;
  marketPrice: number | null;
  wacc: number;
  isUpdating: boolean;
}

// The primary valuation thesis, read like an analyst's conclusion rather
// than a dashboard of equal-weight cards: intrinsic value and
// upside/downside are the dominant figures; market price and WACC are
// compact supporting facts underneath. No DCF math happens here.
export default function ValuationHeadline({
  ticker,
  sector,
  intrinsicValue,
  marketPrice,
  wacc,
  isUpdating,
}: ValuationHeadlineProps) {
  const priceDelta = marketPrice !== null ? intrinsicValue - marketPrice : null;
  const priceDeltaPct = priceDelta !== null && marketPrice ? priceDelta / marketPrice : null;
  const deltaTone =
    priceDelta === null ? "text-[var(--paper)]" : priceDelta >= 0 ? "text-[var(--verdigris)]" : "text-[var(--signal)]";

  return (
    <div className="thesis-panel" aria-busy={isUpdating}>
      <div className="thesis-byline">
        <span className="thesis-ticker">{ticker}</span>
        <span className="thesis-sector">{sector}</span>
        {isUpdating && <span className="thesis-updating">Updating…</span>}
      </div>

      <div className="thesis-primary">
        <div>
          <p className="data-label">Intrinsic value / share</p>
          <p className="thesis-value">{formatPreciseCurrency(intrinsicValue)}</p>
        </div>
        <div>
          <p className="data-label">Upside / downside</p>
          <p className={`thesis-delta ${deltaTone}`}>
            {priceDeltaPct !== null ? `${priceDeltaPct >= 0 ? "+" : ""}${(priceDeltaPct * 100).toFixed(1)}%` : "—"}
          </p>
          {priceDelta !== null && (
            <p className="thesis-delta-note">
              {priceDelta >= 0 ? "+" : ""}
              {formatPreciseCurrency(priceDelta)} vs. market
            </p>
          )}
        </div>
      </div>

      <div className="thesis-supporting">
        <span>
          Market price <b>{formatPreciseCurrency(marketPrice)}</b>
        </span>
        <span className="thesis-supporting-sep" aria-hidden="true">
          ·
        </span>
        <span>
          WACC <b>{formatPercent(wacc, 2)}</b>
        </span>
      </div>
    </div>
  );
}
