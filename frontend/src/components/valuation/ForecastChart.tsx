"use client";

import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";

import { formatCompactCurrency, formatPercent } from "./format";
import type { FreeCashFlowYear } from "./ProjectedCashFlows";

interface ForecastStep {
  year: number;
  stage: "constant" | "near_term" | "maturation";
  revenue_growth_rate: number;
  operating_margin: number;
}

interface ForecastChartProps {
  rows: FreeCashFlowYear[];
  forecastPath: ForecastStep[];
}

function ForecastTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload as (FreeCashFlowYear & ForecastStep) | undefined;
  if (!point) return null;

  return (
    <div className="forecast-tooltip">
      <p>Year {point.year} · {point.stage === "near_term" ? "Near term" : point.stage === "maturation" ? "Maturation" : "Constant"}</p>
      <dl>
        <div><dt>Revenue</dt><dd>{formatCompactCurrency(point.revenue)}</dd></div>
        <div><dt>Free cash flow</dt><dd>{formatCompactCurrency(point.fcf)}</dd></div>
        <div><dt>Growth</dt><dd>{formatPercent(point.revenue_growth_rate)}</dd></div>
        <div><dt>EBIT margin</dt><dd>{formatPercent(point.operating_margin)}</dd></div>
      </dl>
    </div>
  );
}

export default function ForecastChart({ rows, forecastPath }: ForecastChartProps) {
  const pathByYear = new Map(forecastPath.map((step) => [step.year, step]));
  const data = rows.map((row) => ({ ...row, ...pathByYear.get(row.year) }));

  return (
    <section className="forecast-chart" aria-labelledby="forecast-chart-title">
      <div className="forecast-chart-heading">
        <div>
          <p className="panel-kicker">Base case · five years</p>
          <h2 id="forecast-chart-title">Operating forecast</h2>
        </div>
        <div className="forecast-chart-legend" aria-label="Chart legend">
          <span><i className="forecast-legend-revenue" />Revenue</span>
          <span><i className="forecast-legend-fcf" />Free cash flow</span>
        </div>
      </div>

      <div className="forecast-chart-canvas">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 10, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#6f86ff" stopOpacity={0.28} />
                <stop offset="100%" stopColor="#6f86ff" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="rgba(151,164,184,.12)" vertical={false} />
            <XAxis
              dataKey="year"
              tickFormatter={(year: number) => `Y${year}`}
              tick={{ fill: "#8490a1", fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: "rgba(151,164,184,.18)" }}
            />
            <YAxis
              yAxisId="revenue"
              tickFormatter={(value: number) => formatCompactCurrency(value)}
              tick={{ fill: "#8490a1", fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={66}
            />
            <YAxis
              yAxisId="fcf"
              orientation="right"
              tickFormatter={(value: number) => formatCompactCurrency(value)}
              tick={{ fill: "#8490a1", fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={66}
            />
            <Tooltip content={(props) => <ForecastTooltip {...props} />} cursor={{ fill: "rgba(111,134,255,.05)" }} />
            <Area
              yAxisId="revenue"
              type="monotone"
              dataKey="revenue"
              stroke="#7890ff"
              strokeWidth={2.25}
              fill="url(#revenueFill)"
              activeDot={{ r: 4, fill: "#9bacff", stroke: "#0f141b", strokeWidth: 2 }}
            />
            <Bar
              yAxisId="fcf"
              dataKey="fcf"
              fill="#35c997"
              opacity={0.72}
              radius={[3, 3, 0, 0]}
              barSize={24}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="forecast-chart-note">Revenue uses the left scale; free cash flow uses the right. Hover a year for the assumptions behind it.</p>
    </section>
  );
}
