"use client";

import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";

import type { MarketHistoryPoint, MarketHistoryResponse } from "@/lib/market-history";

type MarketHistoryStatus = "loading" | "ready" | "unavailable";
type RangeKey = "1M" | "3M" | "1Y";

interface MarketPriceChartProps {
  ticker: string;
  history: MarketHistoryResponse | null;
  status: MarketHistoryStatus;
}

const RANGE_DAYS: Record<RangeKey, number> = { "1M": 31, "3M": 93, "1Y": 366 };
const RANGE_OPTIONS: RangeKey[] = ["1M", "3M", "1Y"];

function currencyFormatter(currency: string, maximumFractionDigits = 2) {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits,
    });
  } catch {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits,
    });
  }
}

function dateLabel(value: string, compact = false) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("en-US", compact
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", year: "numeric" }).format(date);
}

function chartDomain(points: MarketHistoryPoint[]): [number, number] {
  const prices = points.map((point) => point.close);
  const minimum = Math.min(...prices);
  const maximum = Math.max(...prices);
  const span = Math.max(maximum - minimum, maximum * 0.025, 1);
  return [Math.max(0, minimum - span * 0.12), maximum + span * 0.12];
}

function MarketTooltip({ active, payload, currency }: TooltipContentProps & { currency: string }) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload as MarketHistoryPoint | undefined;
  if (!point) return null;

  return (
    <div className="market-chart-tooltip">
      <p>{dateLabel(point.date)}</p>
      <strong>{currencyFormatter(currency).format(point.close)}</strong>
      <span>Daily close</span>
    </div>
  );
}

export default function MarketPriceChart({ ticker, history, status }: MarketPriceChartProps) {
  const [range, setRange] = useState<RangeKey>("1Y");

  const points = useMemo(() => {
    if (!history?.points.length) return [];
    const end = new Date(history.points.at(-1)!.date).getTime();
    const start = end - RANGE_DAYS[range] * 86_400_000;
    const filtered = history.points.filter((point) => new Date(point.date).getTime() >= start);
    return filtered.length >= 2 ? filtered : history.points;
  }, [history, range]);

  const first = points[0];
  const latest = points.at(-1);
  const change = first && latest ? latest.close - first.close : 0;
  const changePercent = first && first.close !== 0 ? change / first.close : 0;
  const isPositive = change >= 0;
  const lineColor = isPositive ? "#48cfa2" : "#ff746d";
  const currency = history?.currency ?? "USD";
  const money = currencyFormatter(currency);
  const domain = points.length >= 2 ? chartDomain(points) : [0, 1] as [number, number];

  return (
    <section className="market-chart" aria-labelledby="market-chart-title">
      <div className="market-chart-heading">
        <div>
          <h2 id="market-chart-title">{ticker} market price</h2>
          <p>Daily close · {range} · {history?.source ?? "Yahoo Finance"}</p>
        </div>
        <div className="market-chart-quote" aria-label={`${range} market performance`}>
          <strong>{latest ? money.format(latest.close) : "—"}</strong>
          {latest && first && (
            <span className={isPositive ? "market-chart-change--positive" : "market-chart-change--negative"}>
              {change >= 0 ? "+" : ""}{money.format(change)} · {changePercent >= 0 ? "+" : ""}{(changePercent * 100).toFixed(2)}%
            </span>
          )}
        </div>
        <div className="market-chart-ranges" aria-label="Chart range">
          {RANGE_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={range === option}
              onClick={() => setRange(option)}
            >
              {option}
            </button>
          ))}
        </div>
      </div>

      {status === "loading" && (
        <div className="market-chart-state" aria-live="polite">
          <div className="market-chart-loading-line" />
          <span>Loading daily market history…</span>
        </div>
      )}

      {status === "unavailable" && (
        <div className="market-chart-state" role="status">
          <strong>Market history unavailable</strong>
          <span>The valuation is still valid. Yahoo daily prices could not be loaded for this view.</span>
        </div>
      )}

      {status === "ready" && history && points.length >= 2 && (
        <>
          <div className="market-chart-canvas">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={points} margin={{ top: 18, right: 12, bottom: 4, left: 4 }}>
                <CartesianGrid stroke="#202938" strokeDasharray="2 5" vertical={false} />
                <XAxis
                  dataKey="date"
                  minTickGap={54}
                  tickFormatter={(value: string) => dateLabel(value, true)}
                  tick={{ fill: "#8491a6", fontSize: 10, fontFamily: "var(--utility)" }}
                  tickLine={false}
                  axisLine={{ stroke: "#303c50" }}
                  tickMargin={10}
                />
                <YAxis
                  orientation="right"
                  domain={domain}
                  tickCount={5}
                  tickFormatter={(value: number) => money.format(value)}
                  tick={{ fill: "#8491a6", fontSize: 10, fontFamily: "var(--utility)" }}
                  tickLine={false}
                  axisLine={false}
                  width={70}
                  tickMargin={8}
                />
                <ReferenceLine y={first.close} stroke="#3b4658" strokeDasharray="3 5" />
                <Tooltip
                  content={(props) => <MarketTooltip {...props} currency={currency} />}
                  cursor={{ stroke: "#758196", strokeWidth: 1, strokeDasharray: "4 4" }}
                />
                <Line
                  type="linear"
                  dataKey="close"
                  stroke={lineColor}
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  dot={false}
                  isAnimationActive={false}
                  activeDot={{ r: 4, fill: lineColor, stroke: "#0c1118", strokeWidth: 2 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="market-chart-foot">
            <span>{points.length} trading sessions</span>
            <span>Baseline · {money.format(first.close)}</span>
            <span>Through {dateLabel(history.asOf)}</span>
          </div>
        </>
      )}
    </section>
  );
}
