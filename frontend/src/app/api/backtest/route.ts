import { NextResponse } from "next/server";

// TODO: replace this mock series with the real output of
// `src.backtesting.historical_tester` (the point-in-time sector-relative
// DCF backtester) once that's wired up to serve results over the API.
const MONTHS = 12;
const START_EQUITY = 100_000;
const ALGO_TOTAL_RETURN = 0.15;
const SPY_TOTAL_RETURN = 0.1;

export interface BacktestPoint {
  date: string;
  algorithm: number;
  spy: number;
}

/**
 * Deterministically builds a 0..MONTHS equity curve from `startEquity`
 * compounding to `startEquity * (1 + totalReturn)` at the final month,
 * with a small fixed sine wave layered on top so the line isn't a
 * perfectly straight compounding curve. Not random — the same inputs
 * always produce the same curve.
 */
function buildEquityCurve(totalReturn: number): number[] {
  const monthlyRate = Math.pow(1 + totalReturn, 1 / MONTHS) - 1;
  const values: number[] = [START_EQUITY];

  for (let month = 1; month <= MONTHS; month++) {
    const compounded = START_EQUITY * Math.pow(1 + monthlyRate, month);
    const wave = Math.sin((month / MONTHS) * Math.PI * 2.5) * START_EQUITY * 0.008;
    values.push(compounded + wave);
  }

  // Force the final point to land on the exact target return.
  values[MONTHS] = START_EQUITY * (1 + totalReturn);
  return values;
}

function buildMonthLabels(): string[] {
  const now = new Date();
  const labels: string[] = [];
  for (let i = MONTHS; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    labels.push(d.toLocaleString("en-US", { month: "short", year: "2-digit" }));
  }
  return labels;
}

/**
 * GET /api/backtest
 *
 * Returns a 13-point (month 0..12) mock equity curve comparing the
 * strategy's simulated performance against a SPY baseline, both starting
 * at $100,000. Placeholder data for the frontend visualizer while the
 * real Python backtester isn't yet exposed over the API.
 */
export async function GET() {
  const labels = buildMonthLabels();
  const algoCurve = buildEquityCurve(ALGO_TOTAL_RETURN);
  const spyCurve = buildEquityCurve(SPY_TOTAL_RETURN);

  const data: BacktestPoint[] = labels.map((date, index) => ({
    date,
    algorithm: Math.round(algoCurve[index] * 100) / 100,
    spy: Math.round(spyCurve[index] * 100) / 100,
  }));

  return NextResponse.json(data);
}
