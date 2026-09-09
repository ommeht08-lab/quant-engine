# Model Specification: SEC DCF Integration and Shadow Comparison

Status: implemented offline; not connected to the live endpoint, scheduled
publishing, or deployment.

Implementation:
[`src/fundamentals/valuation_integration.py`](../../src/fundamentals/valuation_integration.py)

## Purpose

This module is the single translation seam between a complete SEC valuation
fundamentals snapshot and the existing DCF. A caller supplies one already-built
SEC snapshot and already-fetched market observations. The module performs no
network or database I/O.

It returns either complete prepared inputs or a typed refusal. Complete inputs
can then be run beside legacy Yahoo-statement inputs to produce a typed shadow
report. A shadow report is diagnostic evidence, not permission to change the
customer-facing source.

## Source policy

Candidate financial-statement fields come only from the SEC snapshot:

- latest TTM revenue and operating income;
- latest TTM operating cash flow and capital expenditures;
- period-end cash and cash equivalents; and
- period-end current plus noncurrent term debt.

Market observations remain separate and carry their own observation time and
adapter names:

- current price;
- current shares outstanding;
- levered beta;
- sector; and
- risk-free rate.

The currently intended equity adapter is Yahoo Finance. The current risk-free
proxy is Yahoo's `^TNX`, recorded separately from issuer market observations.
Neither is allowed to provide a missing candidate financial-statement field.

## Anti-Q4 growth policy

The forecast revenue-growth baseline is the median of the four most recent
year-over-year TTM revenue-growth observations. Each observation compares periods
ending exactly four fiscal quarters apart. At least four such observations are
required.

This means that immediately after an isolated Q4 jump, the recent Q1, Q2, and Q3
comparisons still inform the forecast instead of treating the Q4 result as the
new permanent annual rate. The operating-margin and capital-expenditure baselines
use the median of the four latest TTM periods for the same reason.

## Capital structure and explicit assumptions

Policy version `sec-dcf-composition-v1` uses:

- cash: SEC cash and cash equivalents only; marketable securities are excluded;
- debt: SEC current plus noncurrent term debt, named reported term debt;
- shares: current market shares outstanding, because the output is a current
  per-share valuation;
- tax rate: explicit 21% model assumption;
- pre-tax cost of debt: explicit 5% model assumption;
- depreciation and amortization: explicit 3% of revenue model assumption;
- change in net working capital: explicit 1% of revenue change model assumption;
- terminal growth: explicit 2.5%; and
- market risk premium: explicit 5.5%.

Capital expenditures are derived from the median recent TTM SEC
capital-expenditure-to-revenue ratio. Every assumption is preserved in the typed
prepared input. A future richer SEC concept set can replace policy assumptions by
creating a new version rather than silently changing this one.

## Refusal conditions

Preparation refuses when:

- the SEC and market CIKs differ;
- market observations occur after the SEC knowledge cutoff;
- fewer than four comparable TTM growth observations exist; or
- derived growth or margin falls outside the DCF's supported explicit range.

DCF failures are returned as sanitized candidate- or legacy-side refusal codes.
The module does not expose provider/database exception text.

## Shadow comparison

Both sides use the same current price, current shares, beta, sector, risk-free
rate, market-risk premium, terminal growth rate, and projection length. The
candidate side uses only SEC statement fields and its explicit policy. The legacy
side retains Yahoo statement fields solely for comparison.

The report records candidate and legacy values plus absolute and relative
differences for base revenue, growth, operating margin, tax rate, cost of debt,
cash, debt, WACC, enterprise value, equity value, and intrinsic value per share.
No comparison threshold is yet declared. Tolerances must be reviewed from real
shadow samples before any live cutover.
