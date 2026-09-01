# Point-in-time fundamentals — glossary

Terms as used throughout `src/fundamentals/`. This file is glossary-only —
see the Phase 3 architecture notes for design rationale.

## Issuer

The company a fact is reported about, identified by SEC CIK — never by
ticker. Ticker symbols change and are reassigned over time; a CIK does
not. CIK resolution is out of scope for this module (`src/fundamentals`
core operates on facts already scoped to one issuer by the caller).

## Knowledge cutoff

The exact, timezone-aware instant a point-in-time question is asked
against: "what was known at exactly this moment?" Always a
timezone-aware `datetime`, never a bare `date` — a calendar date alone
cannot distinguish a filing accepted at 9am from one accepted at 6pm the
same day.

## Eligible at

The exact instant a fact became knowable. Equal to the filing's SEC
`accepted_at` timestamp when known; when genuinely missing, a
conservative stand-in of 23:59:59 US/Eastern on the filing's `filed_date`
— the latest moment consistent with the known date, never an earlier
guess. A fact is selectable for a given knowledge cutoff only if its
`eligible_at` is on or before that cutoff.

## Fact identity

The full identity a reported number is compared and replaced under:
canonical concept, period start and end, unit, currency, and reporting
context (consolidated vs. a dimensional segment/member breakout). Two
facts are "the same fact, possibly restated" only if every one of these
matches — a different unit, a different currency, a different period
start (e.g. a quarter vs. its year-to-date cumulative sharing one period
end), or a different context is a genuinely different identity, never
merged or treated as competing for the same slot.

## Statement period

One `(period_start, period_end, periodicity)` combination a set of facts
is reported against — annual, quarterly, year-to-date, or a filing
cover-page date (which is not a fiscal statement period at all and is
never bundled into a statement's period collection).

## Fundamental history

The result of resolving a bag of facts against one knowledge cutoff:
multiple statement periods, across the income statement, balance sheet,
and cash flow statement, each carrying only the facts that actually won
selection for that period — not a single "latest snapshot" per concept.
Provenance stays attached to each individual fact; the history itself
carries no wall-clock generation timestamp or singular source label.

## Amendment

A later filing (e.g. a 10-K/A) that restates one or more previously
reported facts. An amendment does not replace an entire prior filing's
worth of facts — only the specific fact identities it actually restates
win selection from it; every other identity continues resolving from
whichever filing most recently and legitimately reported it.
