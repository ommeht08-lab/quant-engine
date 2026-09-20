"use client";

import {
  CartesianGrid,
  ComposedChart,
  Line,
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
  const latest = data.at(-1);

  return (
    <section className="forecast-chart" aria-labelledby="forecast-chart-title">
      <div className="forecast-chart-heading">
        <div>
          <h2 id="forecast-chart-title">Operating forecast</h2>
          <p className="forecast-chart-subtitle">Base case · five-year operating path</p>
        </div>
        <div className="forecast-chart-legend" aria-label="Chart legend">
          <span><i className="forecast-legend-revenue" />Revenue {latest ? formatCompactCurrency(latest.revenue) : ""}</span>
          <span><i className="forecast-legend-fcf" />Free cash flow {latest ? formatCompactCurrency(latest.fcf) : ""}</span>
        </div>
      </div>

      <div className="forecast-chart-canvas">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 10, right: 6, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="#202938" strokeDasharray="2 4" />
            <XAxis
              dataKey="year"
              tickFormatter={(year: number) => `Y${year}`}
              tick={{ fill: "#7f8ba0", fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: "#303c50" }}
            />
            <YAxis
              yAxisId="revenue"
              tickFormatter={(value: number) => formatCompactCurrency(value)}
              tick={{ fill: "#7f8ba0", fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={66}
            />
            <YAxis
              yAxisId="fcf"
              orientation="right"
              tickFormatter={(value: number) => formatCompactCurrency(value)}
              tick={{ fill: "#7f8ba0", fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={66}
            />
            <Tooltip content={(props) => <ForecastTooltip {...props} />} cursor={{ stroke: "#59667a", strokeWidth: 1, strokeDasharray: "4 4" }} />
            <Line
              yAxisId="revenue"
              type="linear"
              dataKey="revenue"
              stroke="#8292ff"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              activeDot={{ r: 3.5, fill: "#8292ff", stroke: "#0c1118", strokeWidth: 2 }}
            />
            <Line
              yAxisId="fcf"
              type="linear"
              dataKey="fcf"
              stroke="#53b7dc"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              activeDot={{ r: 3.5, fill: "#53b7dc", stroke: "#0c1118", strokeWidth: 2 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="forecast-chart-note">Annual forecast · left revenue / right free cash flow · hover to inspect</p>
    </section>
  );
}
