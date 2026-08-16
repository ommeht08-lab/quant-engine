# Model Specification: Historical DCF Backtester

Source: [`src/backtesting/historical_tester.py`](../../src/backtesting/historical_tester.py)
Tests: [`tests/backtesting/test_conviction_score.py`](../../tests/backtesting/test_conviction_score.py) (9),
[`tests/backtesting/test_equity_curve.py`](../../tests/backtesting/test_equity_curve.py) (4),
[`tests/backtesting/test_point_in_time.py`](../../tests/backtesting/test_point_in_time.py) (6)
Consumers: run manually (`python -m src.backtesting.historical_tester`); shares its
Pass 1/Pass 2 pipeline verbatim with the live trading engine
(`src/trading/alpaca_execution.run_todays_scan`)

## Objective

Reconstruct point-in-time financial statements, market price, and share count for a
universe of tickers as of a past target date, run each through the DCF pipeline to
derive a historical intrinsic value, rank by Conviction Score
(see [`docs/model-specifications/conviction-and-portfolio-rules.md`](conviction-and-portfolio-rules.md)),
and measure how a theoretical Top-N portfolio actually performed from that date to
today versus a benchmark (default SPY).

**This backtester is a pipeline sanity check, not a performance-claim engine.** It
has no cost model, no survivorship-bias correction, no walk-forward/holdout
discipline, and no statistical confidence reporting — see
[`docs/research-overview.md`](../research-overview.md)'s "Current evidence status"
section and Track B in
[`docs/model-development-roadmap.md`](../model-development-roadmap.md).

**The backtester also does NOT validate the paper-trading strategy actually run by
`src.trading.alpaca_execution`** — the two share the same Pass 1/Pass 2 valuation
and sector-filter pipeline, but diverge after that in three ways that materially
affect what gets bought and how much of it: the backtester always uses the
**unblended** DCF-based Conviction Score, never the FCF-Yield blend live trading
applies (see [`docs/model-specifications/conviction-and-portfolio-rules.md`](conviction-and-portfolio-rules.md)
§4); the backtester applies **none of the four live entry gates** (Altman Z-Score,
200-day SMA trend, Piotroski F-Score, RSI micro-dip — see that document's §3); and
the backtester's equity curve is **equal-weighted**, never the inverse-beta,
position/sector-capped weighting live trading actually sizes positions with (§5).
A favorable backtest result under this document's methodology is therefore
evidence about the valuation/screening signal in isolation, not evidence that the
specific strategy `src.trading.alpaca_execution` runs (gates, blend, and sizing
included) would have produced the same result.

## Point-in-time data reconstruction

yfinance is **not** a true point-in-time data provider. This module applies
conservative, explicitly documented approximations rather than claiming a precision
the data source does not support:

| Input | Point-in-time handling | Approximation recorded when |
|---|---|---|
| Financial statements | Restricted to columns whose fiscal-period-end date is on/before `as_of_date - STATEMENT_FILING_LAG_DAYS` (90 days, `_columns_on_or_before`, [`historical_tester.py:292`](../../src/backtesting/historical_tester.py)) | n/a — this is the point-in-time mechanism itself, not an approximation flag |
| Market price | Most recent close on/before `as_of_date`, up to 10 calendar days' lookback (`_get_price_on_or_before`, [`historical_tester.py:340`](../../src/backtesting/historical_tester.py)) | n/a |
| Beta | Current (today's) beta used for every target date — yfinance has no historical beta endpoint | Always — `"beta"` added to `approximations` |
| Sector | Current GICS sector used for every target date | Always — `"sector"` added to `approximations` |
| Shares outstanding | Yahoo's historical shares timeseries when it covers the target date, scaled for splits since; falls back to current share count otherwise | When the historical timeseries doesn't cover the date — `"shares_outstanding"` added to `approximations` |
| Universe | Reflects *current* index membership/market-cap ranking unless the caller passes a different `tickers` list | Always — recorded in `BacktestResult.universe_note`, not per-ticker |

Every `ValuationResult`/`TickerAnalysis` carries an `approximations: List[str]`
field recording exactly which current-day proxies stood in for a true historical
value, so a consumer can see this per-ticker, not only in a log line.

### 90-day statement filing lag

`STATEMENT_FILING_LAG_DAYS = 90` is a deliberately conservative, fixed buffer
between a fiscal period's *end* date and when it is treated as *available* for
point-in-time selection. Real SEC 10-K filing deadlines run up to 90 days after
fiscal year-end for non-accelerated filers (60 for large accelerated, 75 for
accelerated) — using the longest realistic deadline as a single uniform default
errs toward treating a period as available *later* than it might really have been.
This is a deliberate choice: a look-ahead bias (using data before it would
plausibly have been public) is judged a much worse failure mode for a backtester
than the reverse (waiting a few extra days longer than some filers actually
needed). See `TestDefaultFilingLagIsConservative` and
`TestFilingLagRemainsPerCallConfigurable` in
[`tests/backtesting/test_point_in_time.py`](../../tests/backtesting/test_point_in_time.py).

### Split-adjusted share count reconstruction

`get_historical_shares_outstanding` ([`historical_tester.py:420`](../../src/backtesting/historical_tester.py))
scales a true historical share count by the cumulative product of every split
ratio that occurred **strictly after** `as_of_ts`
(`_cumulative_split_factor_since`, [`historical_tester.py:392`](../../src/backtesting/historical_tester.py))
— necessary because yfinance's price history is split-adjusted through today, so a
raw historical share count must be scaled onto the same basis for market-cap/
per-share math to be internally consistent.

## Dynamic CAPM discount rate

`compute_valuation` overrides `DCFAssumptions.risk_free_rate` with a live 10-Year
Treasury yield as of `as_of_date` (`src.utils.macro.get_risk_free_rate`,
[`historical_tester.py:1168`](../../src/backtesting/historical_tester.py)) — fetched
**once per whole backtest run**, not once per ticker, both to avoid N redundant
network calls and, more importantly, to avoid feeding today's live rate into what
is supposed to be a historical valuation. `calculate_wacc`'s existing `[5%, 20%]`
clamp still applies (see [`docs/model-specifications/wacc-capm.md`](wacc-capm.md)).

## Sector-relative filter and Conviction Score

Identical to §1–2 of
[`docs/model-specifications/conviction-and-portfolio-rules.md`](conviction-and-portfolio-rules.md) —
this backtester and `src.trading.alpaca_execution.run_todays_scan` share the exact
same `compute_valuation`/`calculate_sector_median_price_to_intrinsic`/`score_ticker`
functions, called with a past `as_of_date` here and with today's date there. The
backtester's Conviction Score is always the **unblended** DCF-based score — the
FCF-Yield blend (§4 of that document) is applied only in live/paper trading, so
historical results stay comparable across runs regardless of live-trading
refinements.

## Forward performance measurement

`get_forward_performance` ([`historical_tester.py:960`](../../src/backtesting/historical_tester.py)):
entry price = the point-in-time close on/before `as_of_date`; exit price = the most
recent available close (today). `total_return = (exit_price / entry_price) - 1`.

## Equity curve construction

`build_equity_curve` ([`historical_tester.py:1035`](../../src/backtesting/historical_tester.py)):
a monthly, **equal-weighted**, buy-and-hold curve (no rebalancing) for the Top-N
portfolio, alongside a same-starting-value benchmark curve.

- Every series (each pick's and the benchmark's) is normalized against the **same**
  first common date — the earliest date every pick and the benchmark all have a
  monthly close for — not each series' independently-first observation. This
  guarantees the strategy and benchmark curves both start at exactly `start_value`
  on the same calendar date. See `TestFirstCommonDateNormalization` in
  [`tests/backtesting/test_equity_curve.py`](../../tests/backtesting/test_equity_curve.py).
- A Top-N pick whose price history cannot be fetched at all is dropped from the
  curve (not the whole run); its weight is redistributed equally among survivors
  (`start_value / len(survivors)`), and the pick is recorded in `dropped_tickers`
  rather than silently changing the effective Top-N weighting. See
  `TestDroppedTickersAreRecorded`. **This redistribution is not the same as
  modeling a loss** — a pick dropped because it was delisted or went bankrupt is
  treated as though it had never been part of the portfolio, not as a realized
  loss a real investor holding it would have experienced, which biases
  `portfolio_return` upward. Tracked as `L-018` (High severity) in
  [`docs/limitations-register.md`](../limitations-register.md).
- **Entry-date alignment is not guaranteed.** The "first common date" every series
  is normalized to (above) is the first MONTHLY bar every pick and the benchmark
  share, requested starting at `as_of_ts` — yfinance's monthly bars align to
  calendar-month boundaries, not to the literal request date, so for a mid-month
  `as_of_date` the actual entry point every return figure is measured from can
  land days to several weeks after the nominal `as_of_date`, silently. Tracked as
  `L-017` (High severity) in [`docs/limitations-register.md`](../limitations-register.md).
- `portfolio_return`/`benchmark_return`/`alpha` are derived from this **same**
  logged equity curve whenever it was successfully built, so the headline return
  and the plotted curve never disagree — falling back to a simple per-ticker
  spot-return average only when the curve itself could not be built at all (e.g.
  every pick's price history failed). **`alpha` here is simple excess return
  (`portfolio_return - benchmark_return`), NOT a regression/Jensen's alpha** — it
  is not adjusted for the portfolio's beta/systematic-risk exposure relative to
  the benchmark, and carries no statistical significance measure. A regression-
  based alpha (with a beta coefficient and a confidence interval) is part of
  Track B item 9's uncertainty-quantification work, not yet built — see
  [`docs/research-overview.md`](../research-overview.md)'s forward-looking results
  framework, where "Alpha / Beta" specifically refers to that not-yet-built
  regression-based measure, distinct from this field.

## Inputs

| Input | Units | Notes |
|---|---|---|
| `tickers` | list of ticker symbols | Default `DEFAULT_SP500_TOP_100_TICKERS` (a hardcoded, currently-ranked list — see survivorship-bias limitation) |
| `as_of_date` | ISO date string | Target valuation date |
| `top_n` | integer | Default 10 |
| `benchmark_ticker` | ticker symbol | Default `"SPY"` |
| `assumptions` | `DCFAssumptions` | Applied uniformly to every ticker; default = dynamic historical growth/margin |

## Outputs

`BacktestResult` (dataclass, [`historical_tester.py:263`](../../src/backtesting/historical_tester.py)):
`analyses` (every ticker's `TickerAnalysis`), `top_picks`, `performance`,
`portfolio_return`, `benchmark_return`, `alpha` (simple excess return — see
"Equity curve construction" above, NOT a regression/Jensen's alpha), `sector_medians`,
`universe_note` (always populated with the survivorship-bias caveat), `dropped_tickers`.

## Failure behavior

A single ticker's unexpected exception during Pass 1 is caught and converted to a
`skip_reason` (`"Unexpected error: ..."`) rather than aborting the whole run — see
`run_backtest`'s `except Exception` guard,
[`historical_tester.py:1175`](../../src/backtesting/historical_tester.py). Equity-
curve logging to Postgres is best-effort: a failure there is logged as a warning
and never blocks the backtest result itself from being returned.

## Known simplifications / limitations (backtester-specific)

- **No survivorship-bias correction** unless the caller explicitly supplies a
  historically-accurate `tickers` list — the default universe is today's top-100
  by market cap, structurally excluding companies that would have qualified
  historically but have since been delisted, acquired, or shrunk out of it. See
  `UNIVERSE_SURVIVORSHIP_NOTE` and Track B item 2 in the roadmap.
- **No cost model** — the equity curve assumes frictionless entry/exit at the
  recorded close prices; no spread, slippage, or transaction cost is subtracted.
- **No corporate-actions handling beyond splits** — spin-offs and ticker changes
  are not specifically detected or corrected for.
- **No walk-forward or holdout discipline** — a single `as_of_date` run has no
  mechanism preventing the same date from being re-run repeatedly during
  development, which is exactly the "peeking" Track B's frozen holdout is designed
  to prevent.
- **Equal-weighted equity curve** — the curve construction here does not reflect
  the inverse-beta risk-adjusted position sizing the live trading engine actually
  uses (see [`docs/model-specifications/conviction-and-portfolio-rules.md`](conviction-and-portfolio-rules.md)
  §5); it is a simpler equal-weight approximation for backtesting purposes.

## Test coverage

`tests/backtesting/test_point_in_time.py` (6 methods):
`TestDefaultFilingLagIsConservative`, `TestFilingLagRemainsPerCallConfigurable`.
`tests/backtesting/test_equity_curve.py` (4 methods):
`TestFirstCommonDateNormalization`, `TestDroppedTickersAreRecorded`.
`tests/backtesting/test_conviction_score.py` (9 methods) — see
[`docs/model-specifications/conviction-and-portfolio-rules.md`](conviction-and-portfolio-rules.md).
There is no dedicated test asserting the equity curve's `portfolio_return`/
`benchmark_return`/`alpha` derivation logic beyond the drop/normalization behavior
above, and no test exercising `run_backtest`'s top-level orchestration against real
(non-mocked) data — appropriate for a unit-test suite, but worth recording
explicitly: this backtester's end-to-end numerical output has not been
independently cross-checked against a second implementation. See
[`docs/independent-validation-plan.md`](../independent-validation-plan.md).
