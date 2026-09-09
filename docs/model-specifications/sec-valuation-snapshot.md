# Model Specification: SEC Valuation Fundamentals Snapshot

Status: implemented with an offline SEC-to-DCF composition and shadow-comparison
seam; not connected to the live DCF or web request path.

Implementation: [`src/fundamentals/valuation_snapshot.py`](../../src/fundamentals/valuation_snapshot.py)

## Purpose

A valuation fundamentals snapshot converts append-only, repository-selected SEC
facts into one typed, reproducible historical input. It hides repository querying,
amendment selection, quarter construction, TTM alignment, and completeness checks
behind one interface. It returns either a complete snapshot or one typed refusal.

The snapshot is not a valuation. It contains no market price, beta, sector,
Treasury rate, forecast assumption, or intrinsic value.

## Reproducibility coordinates

Every request fixes:

- SEC issuer CIK;
- filing knowledge cutoff;
- dataset-vintage cutoff;
- source-adapter identity;
- concept-map version; and
- exact issuer-fiscal-calendar version.

The module owns its required concept set and asks the repository for at most 64
periods per statement. Final amendment resolution remains exclusively in the
central point-in-time selector.

## Historical TTM fields

Each aligned period is the exact sum of four consecutive standalone fiscal
quarters on a consolidated USD basis:

| Field | SEC canonical concept | Convention |
|---|---|---|
| Revenue | `revenue` | Positive inflow |
| Operating income | `operating_income` | Reported sign |
| Operating cash flow | `operating_cash_flow` | Reported sign |
| Capital expenditures | `capital_expenditures` | Positive amount spent |
| Free cash flow | derived | Operating cash flow minus capital expenditures |

Q2 and Q3 cash-flow quarters may be exact YTD differences; Q4 may be the exact
full-year residual. Their origin and source facts remain attached below the
snapshot in the quarterly domain objects.

## Comparable revenue growth

The snapshot requires at least eight consecutive quarters. For every possible
observation it compares a TTM period only with the TTM period ending exactly four
fiscal quarters earlier:

`growth = current TTM revenue / prior-year TTM revenue - 1`

This is seasonal like-for-like growth. A strong Q4 influences one four-quarter
window but is never multiplied or treated as though the same quarterly rate
persisted for a full year.

## Ending balance fields

The latest TTM end must also have exactly one consolidated USD value for:

- cash and cash equivalents;
- current term debt; and
- noncurrent term debt.

`reported_term_debt` is the sum of the two debt fields. The name is intentionally
narrow: commercial paper, finance leases, or another issuer-specific obligation
is not included unless a future version explicitly maps and validates it. Cash
and cash equivalents likewise does not silently include marketable securities.

These definitions must be resolved into an explicit DCF capital-structure policy
before the live cutover.

## Source separation

No missing SEC financial-statement field is filled from yfinance. Future live
composition may retain yfinance only for current price, current shares
outstanding, levered beta, and sector. The Treasury yield remains a separate
macro-market observation. Missing SEC statement data refuses the SEC snapshot.

## Current coverage and validation

Apple CIK `0000320193` has exact fiscal 2020-fiscal 2024 coverage backed by 20
official SEC 10-Q/10-K documents in the versioned calendar catalog. The calendar
records Apple fiscal 2023 as a 53-week year rather than forcing a generic date
pattern.

A controlled non-publishing run on 2026-09-09 produced:

- 1,317 classified cutoff-eligible facts;
- 80 standalone-quarter values across the four required duration concepts;
- 68 concept-level TTM values;
- 17 fully aligned valuation TTM periods; and
- 13 comparable year-over-year TTM revenue-growth observations.

The latest fiscal-2024 values were $391.035 billion revenue, $108.807 billion
free cash flow, 2.022% comparable TTM revenue growth, $29.943 billion cash and
cash equivalents, and $96.662 billion reported term debt. No facts were published
and no downloaded payload was retained.

The next offline module and its source/assumption policy are specified in
[`sec-dcf-integration.md`](sec-dcf-integration.md).
