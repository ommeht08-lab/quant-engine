# Model Development Roadmap

Status: living document, defining direction rather than reporting completed work. As of
this writing, none of the Track B performance-research infrastructure described below has
been built — the codebase currently has a DCF valuation engine, a Monte Carlo VaR module,
a point-in-time-aware backtester, and a paper-trading execution engine, all covered by the
existing test suite, but none of Track B's data-integrity or validation infrastructure yet
exists. This document is the plan and the gate, not a status report of things already done.

## Purpose and non-negotiable framing

This project pursues a genuine research question: **can this strategy produce durable,
out-of-sample, after-cost outperformance, or not?** That is a hypothesis to test, not a
result to assume, target, or manufacture through parameter searching or selective
reporting. A rigorous, well-documented **negative** result — "we tested this carefully and
the edge doesn't survive realistic costs / doesn't hold out of sample" — is a complete and
valuable outcome for a project like this, and must never be hidden, reframed, or quietly
dropped in favor of a more flattering but less honest narrative.

**Profitability is defined as:** durable, statistically credible outperformance versus a
relevant benchmark (SPY, and simpler baselines — see Track B), measured strictly
out-of-sample on data the strategy's own development never touched, net of realistic
transaction costs, slippage, and execution delay. It is explicitly **not**: in-sample CAGR,
a backtest Sharpe ratio, or any metric computed on data that influenced the strategy's own
parameters or feature choices.

This project makes **no claim** of investment advice, regulatory compliance, or
institutional-grade risk management, at any point in this roadmap. See
`docs/security-threat-model.md` §7 for the corresponding safe-deployment framing.

## Two gated tracks

Performance research (Track B) does not start, and does not resume after being paused,
until its corresponding Track A gates are green. This is the single most important
structural rule in this document — seeking a return on capital before the infrastructure
that could catch a wrong or overfit answer exists is how bad research becomes convincing
by accident.

### Track A — Assurance and governance

Scope: the non-negotiable scaffolding that makes Track B's eventual conclusions
trustworthy, whatever they turn out to be.

- **Security threat model and least-privilege credentials** — `docs/security-threat-model.md`,
  kept current as the system changes; credential rotation and role separation (see that
  document's §4/§6) completed, not just planned.
- **Formal model specification** — for each model in this repo (DCF, WACC/CAPM, Monte
  Carlo VaR, Altman Z-Score, conviction scoring, backtester), a written spec covering: the
  exact formula, every input's units, every parameter's valid range and source, and every
  documented limitation. The docstrings already in `src/` are the raw material for this;
  the deliverable is a single reviewable document per model, not a re-explanation of code
  that already explains itself.
- **Data dictionary** — every field this system consumes (from yfinance, SEC/XBRL once
  Track B needs it, Alpaca), its type, its unit, and its known quirks (e.g. yfinance's
  occasional `NaN` Close on an unsettled session — already handled in
  `src/risk/monte_carlo.py`/`src/backtesting/historical_tester.py`, but should be
  documented once centrally rather than re-derived from reading each call site).
- **Assumptions register and limitations register** — living lists, not a one-time essay.
  Every modeling assumption (e.g. "risk-free rate = 10-Year Treasury yield,"
  "terminal growth capped at policy default 2.5%") gets an entry; every known limitation
  (e.g. "single-factor CAPM beta, no size/value/momentum factors") gets one too.
- **Change log** — model-affecting changes (not routine refactors) recorded with what
  changed, why, and what output it could plausibly affect.
- **Immutable input snapshots** — once Track B begins consuming point-in-time SEC/XBRL
  data (see Track B below), every input snapshot used in an experiment is hashed and
  timestamped with its retrieval time, so a result can be reproduced against the exact
  data it was computed from, not "whatever the API returns today."
- **Independent validation** — a second, independently built calculation path (e.g. a
  spreadsheet DCF built from the same spec but not the same code) checked against this
  codebase's output for a sample of tickers, specifically to catch a systematic bug that
  the same code checking itself against itself could never catch.
- **Sensitivity analysis and stress testing** — for the DCF: intrinsic value's sensitivity
  to WACC, terminal growth rate, and margin assumptions, each varied independently. For
  VaR: comparison against the historical/bootstrap methods called for in Track B below,
  not presenting the existing Monte-Carlo-normal assumption as the only lens on risk.
- **Ongoing monitoring and outcome analysis** — once live (paper) trading exists, actual
  realized outcomes get compared back against what the models predicted, on a regular
  cadence, not just at the end of a backtest window.
- **Reproducible tagged releases and validation reports** — each meaningful checkpoint
  gets a tag and a validation report, so "which version of the model produced this
  result" is always answerable.

**Track A stop/go gate:** Track B work may proceed only once the specification,
assumptions/limitations registers, and independent-validation-of-DCF items above are in
place for whatever model Track B is about to use them on. A Track B experiment run against
an unspecified, unvalidated model is not evidence of anything.

### Track B — Performance research

Scope: the actual empirical question. Ordered roughly as prerequisites, not as a strict
waterfall — items can be built in parallel where they don't depend on each other, but the
listed stop/go gate at the end of each stage is real.

1. **Point-in-time SEC/XBRL fundamentals.** Financial statement data as it was actually
   *available* on a given date — respecting filing lag — not restated/as-reported data
   pulled with hindsight. `src/backtesting/historical_tester.py` already implements
   point-in-time filing-lag protection for the data sources it currently uses; this item
   extends the same discipline to SEC EDGAR/XBRL once that becomes the fundamentals
   source, per the SEC EDGAR API reference in this project's handoff history.
2. **Survivorship-bias-free historical universe.** The backtest universe must include
   companies that were later delisted, acquired, or renamed — a universe built by looking
   at "the S&P 500 today" and projecting it backward is systematically biased toward
   winners.
3. **Corporate actions handling.** Splits, spin-offs, and ticker changes must not silently
   corrupt a price/return series.
4. **Signal diagnostics, before any portfolio-level backtest.** Information Coefficient
   (IC) and rank IC, calibration (does a predicted decile actually rank the way it
   claims to?), decile spread, turnover, and factor ablations (does the signal survive
   with obvious confounds like size/momentum controlled for?) — computed and reviewed
   *before* ever running a full portfolio backtest on the same signal. A signal that
   fails diagnostics should never reach a backtest dressed up as a strategy.
5. **Walk-forward evaluation with a frozen, untouched holdout.** Parameters are chosen
   using only data before a cutoff; a final holdout period is set aside and genuinely
   never looked at, touched, or tuned against, until the very end.
6. **Realistic costs.** Spread, transaction cost, slippage, liquidity constraints, and
   execution delay, all modeled explicitly — a paper backtest that assumes frictionless
   fills at the closing price is not evidence of anything tradeable.
7. **Benchmark comparison.** Every result reported against SPY and against simpler
   baselines (e.g. equal-weight, a single-factor version of the same signal) — a complex
   strategy that doesn't beat a simple one net of costs has not demonstrated an edge.
8. **Experiment registry.** Every attempted configuration recorded — including ones that
   didn't work — specifically so nothing gets tuned against the final holdout by
   selectively remembering only the runs that looked good.
9. **Uncertainty, not point estimates.** Confidence intervals on every headline number,
   performance degradation over the walk-forward windows, drawdown statistics, and
   explicit discussion of the regimes/conditions where the strategy is expected to (and
   observed to) fail — not just a single CAGR or Sharpe ratio presented without context.
10. **Risk-model pluralism.** Compare the existing Monte-Carlo-normal VaR
    (`src/risk/monte_carlo.py`) against historical-simulation and bootstrap VaR — treat
    the normal-distribution assumption as one lens among several, not ground truth
    (tracked as a Track A sensitivity-analysis item too — the two tracks meet here
    deliberately).

**Track B stop/go gates:**

- Do not proceed past item 4 (signal diagnostics) to a full backtest if the underlying
  data (items 1–3) isn't point-in-time-correct and survivorship-bias-free — a beautiful
  backtest on biased data is worse than no backtest, because it's convincing.
- Do not proceed past item 5 (walk-forward + frozen holdout) to reporting a headline
  result if the holdout was touched, tuned against, or peeked at during development.
- Do not report a performance claim (item 9) without the benchmark comparison (item 7)
  and the cost model (item 6) both applied — an in-sample or cost-free number is not a
  performance claim, it's a different, much weaker statement that must be labeled as such.
- **If, after all of the above, the strategy does not show durable, statistically credible
  out-of-sample outperformance net of costs: report that.** Precisely and without
  softening. That is a legitimate, complete, and honest outcome of this research program —
  arguably a *stronger* demonstration of rigor for a college-application context than a
  suspiciously good backtest would be, because it shows the difference between a
  toy-model number and a claim that actually survived scrutiny.

## How this roadmap and the security work relate

Security, reproducibility, and honesty are not separate from the performance-research
goal — they're what makes any eventual performance claim (positive OR negative) worth
believing. An unauthenticated valuation API, an executable cache format, or an
un-gated live-trading workflow don't just create security exposure; they also mean nobody
downstream can trust that a reported backtest result reflects what the code actually
computed, rather than something an attacker (or an unreviewed automatic run) altered along
the way. Track A's assurance work and Track B's research discipline are both in service of
the same goal: a result that can be trusted precisely because the process that produced it
can be inspected and it held up.
