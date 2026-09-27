# CAT ME&T gross-margin policy

Status: approved measurement policy. The audited source and the pilot
integration are implemented but **no segment facts are published**, so
until a reviewed backfill publishes them the pilot refuses CAT (see "Before
a pilot run"). The live valuation route is unchanged.

The Piotroski gross-margin factor compares two trailing-year margins. For CAT,
use Machinery, Energy & Transportation (ME&T) sales and ME&T cost of goods
sold on **both** sides of the ratio:

`ME&T gross margin = (ME&T sales - ME&T cost of goods sold) / ME&T sales`.

Do not substitute CAT's consolidated sales and revenues for ME&T sales. The
consolidated top line includes Financial Products revenue, but cost of goods
sold does not include Financial Products' interest expense and other costs.
Do not combine an isolated CAT `GrossProfit` tag with consolidated revenue or
cost of revenue. This policy is issuer-specific; draft PR #50's broad fallback
is not its implementation.

## Source

CAT prints the ME&T columns in the "Supplemental Data for Results of
Operations" tables of every 10-K and 10-Q. ME&T sales are dimension-tagged
in XBRL: the FY2023 10-K tags 63,869 (FY2023) and 56,574 (FY2022) as
`us-gaap:Revenues` with `cat:MachineryEnergyTransportationMember`. ME&T cost
of goods sold is not tagged (42,776 for FY2023 appears only as text), so
Company Facts and filing XBRL cannot supply the cost side, and the margin
needs both sides from one presentation. `src/fundamentals/adapters/sec_filing_document.py`
therefore reads both rows from each filing's primary HTML document under
`CAT_MET_SUPPLEMENTAL_RULE` (`src/fundamentals/segment_gross_margin.py`,
version `cat-met-supplemental-results-v2`) and refuses a changed layout,
an unreadable cell, a missing or duplicated row, a table whose columns do
not add up to its Consolidated column, or two disagreeing copies of one
period in a document.

Because a value shifted into a neighbouring column can still add up, each
row must also meet the rule's declared invariants: the ME&T value is
positive, Financial Products (which never reports these lines) is zero, and
the consolidating adjustment is at most 1% of Consolidated (the largest in
the filings reviewed is 9 of 42,767). A rule cannot be built without one
invariant per row.

Two layouts are recognized. CAT's 2020 Q1 and Q2 10-Qs (older EDGAR markup)
put a superscript footnote number after the ME&T column header and print a
negative across two adjacent cells (`(1` then `)`); from Q3 2020 (Workiva
markup) footnote numbers are small raised spans. The parser accepts exactly
these: a trailing superscript number on a column header, a split negative
closed by the very next cell, and footnote cells identified by `<sup>`,
`vertical-align: super`, a raised relative offset, or a smaller font than
the row label. A table cell's own `vertical-align: top` is layout, never a
footnote, so a top-aligned value such as an adjustment of 9 is read, not
dropped. A marker inside a label or value, a non-numeric marker, an
unclosed or stray parenthesis, or a split whose `)` is not in the next cell
refuses.

Facts are ordinary `FinancialFact`s with source adapter `sec_filing_document`,
dimension `srt:ProductOrServiceAxis = cat:MachineryEnergyTransportationMember`
(CAT's own XBRL member), filing provenance (accession, form, acceptance time
from SEC submissions metadata), and lineage (document URL, document SHA-256
in `raw_tag`, rule version, ingestion batch, ingestion time). They publish
through the existing append-only store in a `+sec_filing_document` batch.

The dry run keeps three instants apart: when the submissions list was
downloaded, when each document's bytes were captured (`CapturedDocument`),
and the facts' ingestion time (the data vintage), which is the **latest
document capture**. A fact is therefore never visible at a data-vintage
cutoff before every document in its batch was captured locally; the earlier
submissions-download time is never used as the vintage. A capture time
earlier than the submissions download refuses. Publication is a later,
separately approved step that records the facts with this ingestion time
unchanged; the store has no separate publication timestamp.

Only 10-K/10-Q documents (and amendments) are read, never earnings 8-Ks:
they are the filings the CAT fiscal calendar classifies. The source can
therefore lag the press release (the Q2 2024 10-Q followed the 8-K by one
day); every 8-K figure compared below is identical.

Every filing accepted by the cutoff must contain a recognized table. An
amendment without one refuses the dry run unless its accession is listed,
with a reviewed reason, in `REVIEWED_AMENDMENTS_WITHOUT_TABLE` (empty for
CAT: no amendment has been reviewed). A listed amendment still refuses if it
contains the table, or any row the rule reads under an unrecognized layout,
so a restating amendment can never leave a superseded value in place.

Point-in-time rules (`load_segment_gross_margin_pair`):

* only facts accepted by the knowledge cutoff and ingested by the
  data-vintage cutoff are read; a repository returning anything else refuses;
* a later-accepted filing supersedes an earlier value for the same period
  from its acceptance onward; superseded and corroborating filings are
  recorded per component;
* values that cannot be ordered (one filing reporting two, or two filings
  accepted at the same instant) refuse, as does any missing component;
* a trailing year ending on a fiscal year end uses that year's annual fact
  and refuses if more than one annual fact ends there; any other trailing
  year is the prior fiscal year plus the current year-to-date less the prior
  year-to-date (Q1, six-month, or nine-month; never a three-month quarter).

## Independent verification (September 3, 2024 pilot)

USD millions, ME&T column. Each value was read from the SEC filing and
matches PR #56's manual transcription. Acceptance times are SEC submissions
metadata in UTC (EDGAR index pages show the same instants in Eastern time).

| Period | ME&T sales | ME&T COGS | Filing the source uses (accepted, UTC) | Also reported, identical |
| --- | ---: | ---: | --- | --- |
| FY2022 | 56,574 | 41,356 | FY2023 10-K `0000018230-24-000009` (2024-02-16 15:05:13) | FY2022 10-K `0000018230-23-000011` |
| FY2023 | 63,869 | 42,776 | FY2023 10-K `0000018230-24-000009` (2024-02-16 15:05:13) | none compared (the Q4 2023 earnings 8-K `0000018230-24-000005`, accepted 2024-02-05, was earlier; not checked) |
| H1 2022 | 26,425 | 19,538 | Q2 2023 10-Q `0000018230-23-000047` (2023-08-02 14:16:35) | Q2 2023 8-K `0000018230-23-000044` (PR #56's citation) |
| H1 2023 | 31,644 | 21,172 | Q2 2024 10-Q `0000018230-24-000045` (2024-08-07 14:40:25) | Q2 2023 10-Q; Q2 2023 and Q2 2024 8-Ks |
| H1 2024 | 30,800 | 19,816 | Q2 2024 10-Q `0000018230-24-000045` (2024-08-07 14:40:25) | Q2 2024 8-K `0000018230-24-000042` (PR #56's citation) |

PR #56's cited accessions and acceptance times (10-K 2024-02-16 15:05:13;
8-Ks 2023-08-01 10:31:57 and 2024-08-06 10:32:05 UTC) are also correct. No
period was restated between filings.

`TTM Jun 2024 = FY2023 + H1 2024 - H1 2023`: sales 63,025; COGS 41,420;
margin 34.2800476002%. `TTM Jun 2023 = FY2022 + H1 2023 - H1 2022`: sales
61,793; COGS 42,990; margin 30.4290129950%. The gross-margin factor alone
would be 1 at this date. This says nothing about CAT's other gates or any
investment return.

## Historical parse check (offline, cached documents)

Run 2026-09-27 against the SEC documents a reviewer cached on 2026-09-25
(no fresh download: no SEC contact identity is configured here). Three of
the cached documents' SHA-256s equal the hashes pinned in the committed
excerpts; the others have no independent hash.

* **September 3, 2024 cutoff: all 18 periodic filings** (Q1 2020 10-Q
  through Q2 2024 10-Q, no amendments) parse through
  `run_segment_document_dry_run`: 100 facts, no refusals. 42 of 54
  (concept, period) values are printed by more than one filing and none
  disagree. The pair above (34.2800476% vs 30.4290130%) is reproduced.
  Trailing-year pairs ending 2021-12-31 through 2024-06-30 compute; pairs
  ending 2020-03-31 through 2021-09-30 refuse by design, because their
  prior year needs FY2019, before the FY2020 calendar start.
* **Intended backfill (FY2020 to FY2026 Q2): 26 filings, 18 verified.** The
  8 filings accepted after the pilot cutoff (Q3 2024 10-Q
  `0000018230-24-000053` through Q2 2026 10-Q `0000018230-26-000046`) were
  not cached and have **not** been parsed; a dry run to the present stops
  at the first of them. Their layout is unverified until the approved live
  dry run.

## Pilot integration

`src/backtesting/sec_pilot.py` (policy `sec-backtest-pilot-v2`):

1. For an issuer with a segment rule (CAT only), the quality statements omit
   consolidated gross profit and cost of revenue, which nothing else reads.
2. The ME&T pair for the same two trailing-year ends as the consolidated
   statements is passed to Piotroski as `gross_margin_override`, which
   replaces factor 8 only. Asset turnover and every other factor, and every
   other issuer, keep consolidated figures.
3. If the pair cannot be reproduced, CAT is refused before valuation, so it
   never enters the sector medians. The record carries the pair's full
   provenance under `segment_gross_margin`.

The manually transcribed constants are no longer in `src/`; they remain in
`tests/fundamentals/test_segment_gross_margin.py` only as an independent
oracle the source must match.

## Before a pilot run

Each step needs separate approval: a dry run of `run_segment_document_dry_run`
for CAT against live SEC data (every periodic filing in the calendar, not
only the three used here), review of its document hashes, publication of the
resulting batch, verification at the 2024-09-03 and publish cutoffs, and only
then a pilot run labeled pipeline validation rather than performance
evidence.
