# Research Overview

Status: living document. This is the framing document for the project's research
program — what question it is asking, why the hypothesis is plausible, what would
prove it wrong, and what "success" is actually defined as. It is written to be
readable by both a technical reviewer and a non-technical reader (e.g. a college
admissions reader) without requiring either to read the source code first.

See also: [`docs/model-development-roadmap.md`](model-development-roadmap.md) (the
gated Track A/Track B plan this document sits inside), and
[`docs/security-threat-model.md`](security-threat-model.md) §7 ("Safe deployment
mode") for the corresponding non-advice/non-institutional framing from a security
angle.

## The central research question

**Can a valuation-driven, fundamentals-based stock-selection strategy — a Discounted
Cash Flow (DCF) intrinsic value estimate, ranked and filtered against sector peers,
combined with quality/momentum/distress screens and risk-adjusted position sizing —
produce durable, statistically credible, out-of-sample outperformance versus a
passive benchmark, net of realistic trading costs?**

That is a question to be tested, not a conclusion to be assumed, targeted, or
manufactured through parameter searching or selective reporting of favorable runs.
A carefully documented **negative** answer — "this doesn't survive realistic costs"
or "this doesn't hold out of sample" — is a complete, legitimate, and valuable
result for this project, not a failure to be hidden or reframed. See
[`docs/model-development-roadmap.md`](model-development-roadmap.md)'s "Purpose and
non-negotiable framing" section, which states this identically; it is repeated here
because it is the single most important sentence in the whole research program.

## The economic hypothesis

The strategy implemented in this codebase (`src/dcf_model/`, `src/backtesting/`,
`src/trading/`) rests on a classical, well-known hypothesis from value-investing
and asset-pricing research: that a company's market price and a reasonable
estimate of its discounted future cash flows can diverge, that this divergence is
partly predictable from public financial-statement data, and that a portfolio
tilted toward the cheap side of that divergence — while also screening for
financial distress, earnings quality, and technical extremes — earns a return
premium over time that a passive market-cap-weighted benchmark does not capture.

Concretely, this project's specific implementation of that hypothesis is:

1. Estimate each company's intrinsic value per share via a DCF model
   (`src/dcf_model/dcf.py`), using assumptions derived from the company's own
   historical financials rather than one-size-fits-all inputs.
2. Compare each company's market price to that intrinsic value (Price / Intrinsic
   Value, "P/IV") **relative to its own sector peers**, not against one fixed
   threshold — a technology company's P/IV of 1.3x is not directly comparable to a
   utility's, since different sectors structurally trade at different multiples
   for reasons this project does not attempt to model.
3. Among sector-cheap candidates, favor genuine **compounders** — companies
   growing free cash flow while earning a positive return on invested capital
   (the Conviction Score,
   [`docs/model-specifications/conviction-and-portfolio-rules.md`](model-specifications/conviction-and-portfolio-rules.md)) —
   rather than every statistically cheap name, since "cheap" and "value trap" are
   not the same thing.
4. Screen out companies showing signs of financial distress (Altman Z-Score),
   deteriorating fundamental quality (Piotroski F-Score), or a broken long-term
   price trend (200-day SMA), and time entries to a short-term technical cooling-off
   point (RSI) — narrowing the universe further before capital is actually risked.
5. Size positions by risk (inverse-beta weighting with position/sector caps) rather
   than equally, and manage portfolio-level tail risk explicitly (Monte Carlo VaR,
   a SPY put hedge) — because even a genuinely positive expected-return signal can
   destroy a portfolio through poor risk management around it.

## Why mispricing could persist

For this hypothesis to be more than a restatement of the Efficient Market
Hypothesis's null case, there has to be a reason a persistent, exploitable
divergence between price and estimated intrinsic value survives a market of
well-informed, well-capitalized participants who would otherwise arbitrage it away
instantly. Candidate reasons, none of which this project has yet tested for or
established:

- **Behavioral biases** — recency bias, narrative-driven overreaction to short-term
  news, and career/benchmark risk for professional managers (a fund manager
  underperforming a benchmark by holding an unloved, statistically cheap stock
  bears real business risk that a purely rational, unconstrained investor would not)
  can keep a mispricing open longer than a frictionless-market model predicts.
- **Structural neglect** — small- and mid-cap names, or names outside a popular
  narrative, receive less analyst coverage and less trading volume, so information
  incorporates into price more slowly.
- **Short-horizon incentive misalignment** — many market participants (retail
  traders, momentum funds, quarterly-benchmarked institutions) are not optimizing
  for the multi-year horizon a DCF's terminal value implicitly assumes, leaving a
  structural gap for a longer-horizon, fundamentals-based approach to exploit.
- **Complexity/estimation-cost neglect** — a full DCF with sector-relative
  comparison, quality/distress screening, and risk-adjusted sizing is expensive to
  compute and maintain at scale; not every participant does this work for every
  name, which can leave gaps un-arbitraged even when the information to close them
  is technically public.

These are hypotheses about *why* an edge could exist, not evidence that it does.
Track B (below) is designed to test whether any of them actually shows up as a
measurable, durable, cost-net return premium — not to assume they do.

## Where this hypothesis is expected to fail

A hypothesis that cannot fail is not a hypothesis. This strategy is expected to
underperform, or show no measurable edge, in at least these circumstances:

- **A genuinely efficient sub-universe.** Large, heavily covered, highly liquid
  large-cap names (much of `DEFAULT_SP500_TOP_100_TICKERS` in
  [`src/backtesting/historical_tester.py`](../src/backtesting/historical_tester.py))
  are exactly the names professional arbitrage capital is most likely to have
  already priced efficiently — if an edge exists anywhere, this specific universe
  is a relatively hard place to find it, not an easy one.
- **Regime shifts that break the DCF's structural assumptions.** A sustained
  environment of near-zero or negative real rates, a sudden repricing of long-
  duration cash flows, or a macro shock that invalidates historical growth/margin
  extrapolation (see `MAX_REVENUE_GROWTH_RATE` in
  [`src/dcf_model/dcf.py:49`](../src/dcf_model/dcf.py)) can make the model's
  central estimate systematically wrong in one direction for an extended period.
- **Cost erosion.** A strategy with meaningful turnover (the drift-threshold
  rebalancing in
  [`src/trading/alpaca_execution.py`](../src/trading/alpaca_execution.py)) can
  show an apparent edge gross of costs that fully or partially disappears once
  realistic spread, slippage, and execution delay are modeled — Track B item 6 in
  the roadmap exists specifically to test this, and no cost model exists in the
  codebase yet (see the limitations register, `L-009`).
- **Survivorship- and data-quality-driven illusions.** A backtest run against
  today's index membership (`DEFAULT_SP500_TOP_100_TICKERS`) is structurally
  blind to companies that would have qualified historically but have since been
  delisted, acquired, or shrunk out of the ranking — see
  `UNIVERSE_SURVIVORSHIP_NOTE` in
  [`src/backtesting/historical_tester.py:171`](../src/backtesting/historical_tester.py).
  Any apparent edge measured against this universe could be partly or wholly an
  artifact of that bias rather than a real, tradeable signal.
- **Small-sample or short-window luck.** A short walk-forward window, a small
  number of independent periods, or a single favorable macro regime in the test
  data can produce an apparently strong result that is statistically
  indistinguishable from noise once confidence intervals are actually computed
  (Track B item 9) — which the codebase does not currently do.

If, once Track B's infrastructure exists and is actually run, the strategy fails to
clear these bars, **that is the expected and honest possible outcome of this
research program**, not a sign the project failed. See "Current evidence status"
below.

## Valuation accuracy is not the same as tradeable profitability

This is a distinction the codebase itself does not currently measure and this
document wants to state explicitly, since it is easy to conflate the two:

- **Valuation accuracy** asks: is the DCF's intrinsic-value estimate close to some
  independently-derived "true" value for a company, or internally consistent and
  free of calculation bugs? This is what
  [`docs/independent-validation-plan.md`](independent-validation-plan.md) is
  designed to test — a spreadsheet built independently from the same written spec,
  checked against this codebase's output.
- **Tradeable profitability** asks: does *acting* on the valuation signal (buying
  what it says is cheap, selling what it says is expensive, sized and risk-managed
  the way `src/trading/alpaca_execution.py` does it) produce a return, net of
  costs, that beats a passive benchmark over a genuinely out-of-sample period?

A DCF can be arithmetically correct, bug-free, and independently verified, and the
resulting *strategy* can still fail to be profitable — because the market never
converges the price toward the estimated intrinsic value within the strategy's
holding period, because costs eat the gross edge, or because the specific ranking/
screening/sizing rules built on top of the valuation don't actually capture the
mispricing even if it's real. Conversely, a strategy could show a positive backtest
return for reasons entirely unrelated to valuation accuracy (survivorship bias,
overfitting, a lucky sample period). This project treats these as two genuinely
separate questions, tested by two genuinely separate pieces of infrastructure
(independent validation vs. Track B's walk-forward evaluation), not one question
with two names.

## The intended research pipeline

```
Financial statements (yfinance; SEC/XBRL planned — see data dictionary)
        |
        v
DCF valuation (src/dcf_model/dcf.py) — intrinsic value per share
        |
        v
Sector-relative ranking (src/backtesting/historical_tester.py) — P/IV vs. sector median
        |
        v
Quality/distress/momentum screens (Altman Z, Piotroski, 200-SMA, RSI) — src/valuation/
        |
        v
Conviction scoring (compounder gate + saturating normalization)
        |
        v
Portfolio construction (inverse-beta risk weighting, position/sector caps)
        |
        v
Risk control (Monte Carlo VaR/CVaR, src/risk/monte_carlo.py; SPY put hedge, src/risk/hedging.py)
        |
        v
Paper execution (src/trading/alpaca_execution.py) — Alpaca paper trading only, never live
```

Every stage of this pipeline already exists in the codebase and is exercised
end-to-end by `src/backtesting/historical_tester.py` (for a past date) and
`src/trading/alpaca_execution.py` (for today, autonomously, against a real paper
brokerage account). What does **not** yet exist is the Track B measurement
infrastructure that would let this pipeline's *output* support a profitability
claim — see "Current evidence status" immediately below.

## Definition of a successful result

A result from this pipeline counts as evidence of a real, durable edge only if
**all** of the following hold simultaneously. None of these alone is sufficient:

- **Out-of-sample.** Computed on data — and, once Track B's frozen holdout exists,
  on a *time period* — that no parameter choice, feature choice, or manual
  adjustment in this strategy's development ever saw or was tuned against.
- **Net of realistic costs.** Spread, slippage, transaction cost, and execution
  delay are modeled explicitly and subtracted before any return figure is called a
  result (Track B item 6 — not yet built; see `L-009`).
- **Benchmark-relative.** Reported as alpha against SPY *and* against simpler
  baselines (equal-weight, a single-factor version of the same signal) — a complex
  strategy that does not beat a simple one, net of costs, has not demonstrated an
  edge worth the added complexity (Track B item 7).
- **Statistically credible.** Reported with a confidence interval or equivalent
  uncertainty measure, not a single point estimate — a Sharpe ratio or CAGR without
  a sense of its own noise is not by itself evidence of anything (Track B item 9).
- **Robust across walk-forward periods.** Consistent (not necessarily identical,
  but not reversing sign or magnitude) performance across multiple non-overlapping
  out-of-sample windows, not a result that only holds in one favorable period
  (Track B item 5).

**Explicitly not a successful result, on their own:** an in-sample CAGR, a backtest
Sharpe ratio computed on data the strategy's own assumptions were derived from, a
single favorable walk-forward window, or a return figure that has not had costs
subtracted. `src/backtesting/historical_tester.py`'s current `portfolio_return`/
`alpha` output is exactly this kind of number today — informative for debugging and
sanity-checking the pipeline, but not a profitability claim, and not currently
described as one anywhere in this codebase's public output. That `alpha` field is
also, today, **simple excess return** (`portfolio_return - benchmark_return`), not
the regression/Jensen's alpha the "Alpha / Beta" row of the results framework below
refers to — the two are different statistics with the same common name; see
[`docs/model-specifications/backtesting.md`](model-specifications/backtesting.md).
Separately — and independent of the profitability question entirely — the current
backtester does not even validate the specific paper-trading strategy
`src/trading/alpaca_execution.py` runs: it uses the unblended Conviction Score,
none of the four live entry gates, and equal weighting rather than the live
engine's inverse-beta capped sizing (see that same document's opening note). A
backtest result today is evidence about the underlying valuation/screening signal,
not about the deployed strategy as a whole.

## Falsification criteria

This hypothesis is considered **falsified** (a "no, this doesn't work" result,
worth reporting as such rather than quietly dropping) if, once Track B's
infrastructure exists and is actually run:

- The strategy's realized, out-of-sample, walk-forward, cost-net return does not
  exceed the SPY benchmark's return by a margin outside the strategy's own
  confidence interval, across a majority of tested walk-forward windows; or
- The strategy underperforms one or more of the simpler baselines it is compared
  against (Track B item 7) net of the same costs; or
- The signal fails the pre-backtest diagnostics (Information Coefficient, rank IC,
  decile calibration — Track B item 4) before a portfolio-level backtest is even
  run, meaning the ranking itself does not predict subsequent relative performance
  regardless of how the backtest is constructed.

Any of these outcomes will be reported precisely and without softening, per
[`docs/model-development-roadmap.md`](model-development-roadmap.md)'s Track B
stop/go gates.

## Current evidence status

**As of this writing, no durable, out-of-sample, cost-net profitability conclusion
— positive or negative — has been established by this project.**

What exists today:

- A working, tested implementation of every pipeline stage listed above (DCF,
  sector-relative ranking, quality/distress screens, conviction scoring, risk-
  adjusted position sizing, Monte Carlo VaR, a paper-trading execution engine),
  covered by the test suite referenced throughout
  [`docs/model-specifications/`](model-specifications/).
- A backtester (`src/backtesting/historical_tester.py`) capable of running the
  full pipeline against a past date and measuring the resulting portfolio's actual
  subsequent return versus SPY — but over a *current, non-survivorship-corrected*
  universe, with *no* cost model, *no* walk-forward/holdout discipline, *no*
  benchmark-relative statistical testing, and *no* confidence intervals. Any
  number this produces today is a pipeline sanity check, not a performance claim.

What does not yet exist (Track B, per
[`docs/model-development-roadmap.md`](model-development-roadmap.md)): point-in-time
SEC/XBRL fundamentals, a survivorship-bias-free universe, corporate-actions
handling, signal diagnostics, walk-forward evaluation with a frozen holdout,
realistic cost modeling, benchmark/baseline comparison, an experiment registry, and
uncertainty quantification. Track B does not begin, and does not resume after being
paused, until its corresponding Track A gates (this documentation set, largely)
are in place.

## Forward-looking results framework

Once Track B exists and is run, results will be reported using this fixed set of
metrics — chosen now, before any result exists, specifically so metrics cannot be
chosen after the fact to flatter a particular outcome:

| Metric | What it measures |
|---|---|
| CAGR | Compound annual growth rate of the strategy's equity curve |
| Annualized volatility | Standard deviation of returns, annualized |
| Sharpe ratio | Excess return over the risk-free rate, per unit of volatility |
| Sortino ratio | Excess return per unit of *downside* volatility only |
| Maximum drawdown | Largest peak-to-trough decline in the equity curve |
| Alpha / Beta | Regression of strategy returns against the benchmark's returns (Jensen's alpha — not yet built; distinct from the backtester's current simple-excess-return `alpha` field, see [`docs/model-specifications/backtesting.md`](model-specifications/backtesting.md)) |
| Information Coefficient (IC) / rank IC | Correlation between the model's ranking signal and subsequent realized returns |
| Turnover / cost drag | How much trading the strategy generates, and the resulting cost as a fraction of gross return |
| Benchmark-relative return | Strategy return minus SPY return over the identical period |
| Confidence intervals | Uncertainty bounds on every headline number above, not point estimates alone |
| Performance by regime | The above metrics broken out by macro regime (e.g. rate environment, volatility regime), not only as one blended average |

## Why a rigorous negative result is still valuable

A well-documented "no" is not a lesser outcome than a "yes" for a project built to
demonstrate research rigor rather than to produce a specific answer. Showing that a
plausible-sounding, carefully implemented strategy does *not* survive realistic
costs, or does *not* hold out of sample, and being able to say precisely *why* and
*how much* — with the diagnostics, cost model, and walk-forward discipline to back
that statement up — is a stronger demonstration of research capability than an
unexamined, in-sample number that happens to look good. Per
[`docs/model-development-roadmap.md`](model-development-roadmap.md): "That is a
legitimate, complete, and honest outcome of this research program — arguably a
*stronger* demonstration of rigor for a college-application context than a
suspiciously good backtest would be, because it shows the difference between a
toy-model number and a claim that actually survived scrutiny."

## Disclaimer

Consistent with the project [README](../README.md) and
[`docs/security-threat-model.md`](security-threat-model.md) §7: this project makes
no claim of investment advice, regulatory compliance, or institutional-grade risk
management, at any point in this document or elsewhere in the repository. It trades
exclusively against Alpaca's paper (simulated-money) environment. Nothing here is a
recommendation to buy or sell any security.
