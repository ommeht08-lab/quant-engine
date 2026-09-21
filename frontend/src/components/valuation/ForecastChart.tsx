"use client";

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

function chartDomain(values: number[]): [number, number] {
  if (values.length === 0) return [0, 1];

  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const visibleSpan = Math.max(maximum - minimum, Math.abs(maximum) * 0.08, 1);
  const padding = visibleSpan * 0.35;

  return [Math.max(0, minimum - padding), maximum + padding];
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
  const maturationStart = data.find((point) => point.stage === "maturation")?.year;
  const revenueDomain = chartDomain(data.map((point) => point.revenue));
  const fcfDomain = chartDomain(data.map((point) => point.fcf));

  return (
    <section className="forecast-chart" aria-labelledby="forecast-chart-title">
      <div className="forecast-chart-heading">
        <div>
          <h2 id="forecast-chart-title">Operating forecast</h2>
          <p className="forecast-chart-subtitle">Base case · annual USD forecast</p>
        </div>
        <div className="forecast-chart-quotes" aria-label="Final forecast year values">
          <div><span><i className="forecast-legend-revenue" />Revenue</span><strong>{latest ? formatCompactCurrency(latest.revenue) : "—"}</strong></div>
          <div><span><i className="forecast-legend-fcf" />Free cash flow</span><strong>{latest ? formatCompactCurrency(latest.fcf) : "—"}</strong></div>
        </div>
      </div>

      <div className="forecast-chart-canvas">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 24, right: 8, bottom: 2, left: 4 }}>
            <CartesianGrid stroke="#202938" strokeDasharray="2 5" vertical={false} />
            <XAxis
              dataKey="year"
              tickFormatter={(year: number) => `Y${year}`}
              tick={{ fill: "#8491a6", fontSize: 10, fontFamily: "var(--utility)" }}
              tickLine={false}
              axisLine={{ stroke: "#303c50" }}
              tickMargin={10}
            />
            <YAxis
              yAxisId="revenue"
              domain={revenueDomain}
              tickCount={4}
              tickFormatter={(value: number) => formatCompactCurrency(value)}
              tick={{ fill: "#8491a6", fontSize: 10, fontFamily: "var(--utility)" }}
              tickLine={false}
              axisLine={false}
              width={62}
              tickMargin={8}
            />
            <YAxis
              yAxisId="fcf"
              orientation="right"
              domain={fcfDomain}
              tickCount={4}
              tickFormatter={(value: number) => formatCompactCurrency(value)}
              tick={{ fill: "#8491a6", fontSize: 10, fontFamily: "var(--utility)" }}
              tickLine={false}
              axisLine={false}
              width={62}
              tickMargin={8}
            />
            <Tooltip content={(props) => <ForecastTooltip {...props} />} cursor={{ stroke: "#59667a", strokeWidth: 1, strokeDasharray: "4 4" }} />
            {maturationStart !== undefined && (
              <ReferenceLine
                x={maturationStart}
                stroke="#3b4658"
                strokeDasharray="3 5"
                label={{ value: "Maturation", fill: "#758196", fontSize: 9, position: "insideTopRight" }}
              />
            )}
            <Line
              yAxisId="revenue"
              type="linear"
              dataKey="revenue"
              stroke="#8292ff"
              strokeWidth={2.25}
              strokeLinecap="round"
              strokeLinejoin="round"
              dot={{ r: 2.25, fill: "#8292ff", stroke: "#0c1118", strokeWidth: 1.5 }}
              isAnimationActive={false}
              activeDot={{ r: 4, fill: "#8292ff", stroke: "#0c1118", strokeWidth: 2 }}
            />
            <Line
              yAxisId="fcf"
              type="linear"
              dataKey="fcf"
              stroke="#53b7dc"
              strokeWidth={2.25}
              strokeLinecap="round"
              strokeLinejoin="round"
              dot={{ r: 2.25, fill: "#53b7dc", stroke: "#0c1118", strokeWidth: 1.5 }}
              isAnimationActive={false}
              activeDot={{ r: 4, fill: "#53b7dc", stroke: "#0c1118", strokeWidth: 2 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="forecast-chart-foot">
        <span>Revenue · left scale</span>
        <span>FCF · right scale</span>
        <span>Independent axes · hover for values</span>
      </div>
    </section>
  );
}
