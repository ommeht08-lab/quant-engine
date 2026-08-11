/**
 * Institutional-style risk/return metrics for a strategy-vs-benchmark
 * equity curve (as returned by `/api/backtest`).
 *
 * Annualization is based on the actual observed date range and period
 * spacing in `data` (via CAGR / sqrt-of-time scaling), not a hardcoded
 * "daily" or "monthly" assumption — this auto-adapts whether the curve
 * is sampled daily, weekly, or monthly.
 */

export interface BacktestPoint {
  date: string;
  strategy: number;
  benchmark: number;
}

export interface BacktestMetrics {
  /** Total (non-annualized) return over the full period, % */
  totalReturnStrategy: number;
  totalReturnBenchmark: number;
  /** CAGR over the observed date range, % */
  annualizedReturnStrategy: number;
  annualizedReturnBenchmark: number;
  /** Annualized excess return of strategy over benchmark, % */
  alpha: number;
  /** Annualized standard deviation of strategy log returns, % */
  annualizedVolatility: number;
  /** (Annualized return - risk-free rate) / annualized volatility */
  sharpeRatio: number;
  /** Worst peak-to-trough decline for the strategy curve, % (positive = worse) */
  maxDrawdown: number;
  /** Same, for the benchmark curve */
  benchmarkMaxDrawdown: number;
  /** % of periods where the strategy's period-over-period return beat the benchmark's */
  winRate: number;
}

const RISK_FREE_RATE = 0.045; // 4.5%, per spec
const DAYS_PER_YEAR = 365.25;
const MS_PER_DAY = 1000 * 60 * 60 * 24;

const EMPTY_METRICS: BacktestMetrics = {
  totalReturnStrategy: 0,
  totalReturnBenchmark: 0,
  annualizedReturnStrategy: 0,
  annualizedReturnBenchmark: 0,
  alpha: 0,
  annualizedVolatility: 0,
  sharpeRatio: 0,
  maxDrawdown: 0,
  benchmarkMaxDrawdown: 0,
  winRate: 0,
};

function periodicReturns(values: number[]): number[] {
  const returns: number[] = [];
  for (let i = 1; i < values.length; i++) {
    const prev = values[i - 1];
    const curr = values[i];
    if (prev > 0) returns.push(curr / prev - 1);
  }
  return returns;
}

function logReturns(values: number[]): number[] {
  const returns: number[] = [];
  for (let i = 1; i < values.length; i++) {
    const prev = values[i - 1];
    const curr = values[i];
    if (prev > 0 && curr > 0) returns.push(Math.log(curr / prev));
  }
  return returns;
}

function stdDev(values: number[]): number {
  if (values.length < 2) return 0;
  const mean = values.reduce((sum, v) => sum + v, 0) / values.length;
  const variance = values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance);
}

function maxDrawdownPct(values: number[]): number {
  let peak = values[0];
  let worst = 0;
  for (const value of values) {
    if (value > peak) peak = value;
    if (peak > 0) {
      const drawdown = (peak - value) / peak;
      if (drawdown > worst) worst = drawdown;
    }
  }
  return worst * 100;
}

/**
 * Compute Total Return, Alpha, Annualized Volatility, Sharpe Ratio, Max
 * Drawdown, and Win Rate for a strategy-vs-benchmark equity curve.
 *
 * Returns an all-zero `BacktestMetrics` (rather than throwing or
 * producing NaN/Infinity) when there's fewer than 2 data points to
 * compute a return from, or when a computed ratio's denominator would
 * be zero (e.g. zero volatility) — callers can render "0.00%" safely
 * for a loading/empty state without special-casing.
 */
export function calculateBacktestMetrics(data: BacktestPoint[]): BacktestMetrics {
  if (!data || data.length < 2) {
    return EMPTY_METRICS;
  }

  const sorted = [...data].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
  const strategyValues = sorted.map((d) => d.strategy);
  const benchmarkValues = sorted.map((d) => d.benchmark);

  const firstStrategy = strategyValues[0];
  const lastStrategy = strategyValues[strategyValues.length - 1];
  const firstBenchmark = benchmarkValues[0];
  const lastBenchmark = benchmarkValues[benchmarkValues.length - 1];

  const totalReturnStrategy = firstStrategy > 0 ? (lastStrategy / firstStrategy - 1) * 100 : 0;
  const totalReturnBenchmark = firstBenchmark > 0 ? (lastBenchmark / firstBenchmark - 1) * 100 : 0;

  const firstDate = new Date(sorted[0].date).getTime();
  const lastDate = new Date(sorted[sorted.length - 1].date).getTime();
  const years = Math.max((lastDate - firstDate) / (MS_PER_DAY * DAYS_PER_YEAR), 1 / DAYS_PER_YEAR);

  const annualizedReturnStrategy =
    firstStrategy > 0 ? (Math.pow(lastStrategy / firstStrategy, 1 / years) - 1) * 100 : 0;
  const annualizedReturnBenchmark =
    firstBenchmark > 0 ? (Math.pow(lastBenchmark / firstBenchmark, 1 / years) - 1) * 100 : 0;

  const alpha = annualizedReturnStrategy - annualizedReturnBenchmark;

  // Infer the sampling frequency from the actual average gap between
  // points so volatility annualizes correctly whether the curve is
  // daily, weekly, or monthly.
  const avgPeriodDays = (lastDate - firstDate) / MS_PER_DAY / (sorted.length - 1);
  const periodsPerYear = avgPeriodDays > 0 ? DAYS_PER_YEAR / avgPeriodDays : 12;

  const annualizedVolatility = stdDev(logReturns(strategyValues)) * Math.sqrt(periodsPerYear) * 100;

  const sharpeRatio =
    annualizedVolatility > 0
      ? (annualizedReturnStrategy / 100 - RISK_FREE_RATE) / (annualizedVolatility / 100)
      : 0;

  const maxDrawdown = maxDrawdownPct(strategyValues);
  const benchmarkMaxDrawdown = maxDrawdownPct(benchmarkValues);

  const strategyReturns = periodicReturns(strategyValues);
  const benchmarkReturns = periodicReturns(benchmarkValues);
  const comparablePeriods = Math.min(strategyReturns.length, benchmarkReturns.length);
  let outperformingPeriods = 0;
  for (let i = 0; i < comparablePeriods; i++) {
    if (strategyReturns[i] > benchmarkReturns[i]) outperformingPeriods++;
  }
  const winRate = comparablePeriods > 0 ? (outperformingPeriods / comparablePeriods) * 100 : 0;

  return {
    totalReturnStrategy,
    totalReturnBenchmark,
    annualizedReturnStrategy,
    annualizedReturnBenchmark,
    alpha,
    annualizedVolatility,
    sharpeRatio,
    maxDrawdown,
    benchmarkMaxDrawdown,
    winRate,
  };
}
