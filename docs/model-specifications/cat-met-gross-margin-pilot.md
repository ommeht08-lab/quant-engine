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
`CAT_SEGMENT_MARGIN_SOURCE` (`src/fundamentals/segment_gross_margin.py`,
version `cat-met-supplemental-results-v3`) and refuses a changed layout,
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

## Renaming: ME&T became Machinery, Power & Energy (FY2025 10-K)

From the FY2025 10-K (`0000018230-26-000008`, accepted 2026-02-13) CAT
prints "Machinery, Power & Energy" (MP&E) where it printed "Machinery,
Energy & Transportation", renames the sales row to "Sales of Machinery,
Power & Energy", and tags `cat:MachineryPowerEnergyMember` instead of
`cat:MachineryEnergyTransportationMember`. The Q3 2025 10-Q
(`0000018230-25-000048`) is the last ME&T filing. For this margin the two
are the same scope:

* **Same definition, verbatim.** FY2024 10-K (`0000018230-25-000008`),
  Item 7 "Supplemental Consolidating Data": "We define ME&T as it is
  presented in the supplemental data as Caterpillar Inc. and its
  subsidiaries, excluding Financial Products."; Note 1.A "Nature of
  operations": "We define ME&T as Caterpillar Inc. and its subsidiaries,
  excluding Financial Products." FY2025 10-K, the same two places, word for
  word with MP&E for ME&T: Item 7 "Supplemental Consolidating Data": "We
  define MP&E as it is presented in the supplemental data as Caterpillar
  Inc. and its subsidiaries, excluding Financial Products."; Note 1.A: "We
  define MP&E as Caterpillar Inc. and its subsidiaries, excluding Financial
  Products." The FY2025 10-K states the same scope in Item 1 "Categories of
  Business Organization" item 1 and Item 7 "Glossary of terms" item 14, and
  the Q1 and Q2 2026 10-Qs (`0000018230-26-000021`, `0000018230-26-000046`)
  in their supplemental-data note and glossary item 14 ("The company
  defines MP&E as Caterpillar Inc. and its subsidiaries, excluding
  Financial Products.").
* **Identical comparatives, every line.** Every row of every overlapping
  supplemental table, in all four columns (Consolidated, MP&E/ME&T,
  Financial Products, Consolidating Adjustments), is identical in the MP&E
  and ME&T filings: FY2023 and FY2024 (FY2025 10-K vs FY2024 10-K, 20 of 20
  rows), Q1 2025 (Q1 2026 vs Q1 2025 10-Q, 18 of 18), Q2 2025 and H1 2025
  (Q2 2026 vs Q2 2025 10-Q, 19 of 19 each). The only rows printed on one
  side are zero there (a goodwill impairment line, nil after 2022; a
  noncontrolling-interest line, nil in Q1 2025).
* **The segment changes stay inside MP&E.** The Q1 and Q2 2026 10-Qs, Note
  16 "Segment information", describe the changes effective July 1, 2025
  (wear components to Resource Industries; electronics and automation R&D
  to All Other) and January 1, 2026 (Rail from Power & Energy to Resource
  Industries), "to reflect changes in organizational accountabilities and
  refinements to our internal reporting", with 2025 segment information
  retrospectively adjusted. All of these segments are inside MP&E; none
  moves anything to or from Financial Products, the only boundary of this
  margin. The FY2025 10-K (Item 7, outlook) announced the Rail recast.

The source therefore reads each filing under one of two **reporting
bases** with the same fact identity (version, concepts, dimension) and
guards (title, scale, invariants), differing only in printed labels:

| Basis | Report dates (inclusive) | Column / sales row |
| --- | --- | --- |
| ME&T | calendar start to 2025-09-30 | Machinery, Energy & Transportation |
| MP&E | 2025-12-31 to 2026-06-30 (last reviewed) | Machinery, Power & Energy |

A filing is read only under the basis covering its report date: an MP&E
table in an ME&T-period filing, an ME&T table in an MP&E-period filing, a
document mixing both, or an MP&E column with the old sales label refuses.
A filing after 2026-06-30 refuses until its layout is reviewed and the
basis extended. Facts keep CAT's original member as the one canonical
dimension so trailing years can span the renaming; each fact's `raw_tag`
records the printed row and column it was read from.

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

## Historical parse check (all 26 filings, offline)

Codex downloaded all 26 periodic filings (Q1 2020 10-Q to Q2 2026 10-Q, no
amendments) from SEC at 2026-09-27T18:54:12Z under an authorized contact
header and saved them with their capture times and SHA-256s. SEC appends a
per-response script tag to each document, so whole-document hashes differ
between captures; filing content before that tag was byte-identical to an
earlier 2026-09-25 cache for the 18 documents in both.

An offline rerun of `run_segment_document_dry_run` over the saved copies
(each checked against its captured SHA-256; the original submissions time
kept; document read times labeled as offline reads, not SEC captures):

* **Knowledge cutoff 2026-09-27T18:54:12Z: 26 of 26 filings parse**, 23
  under ME&T and 3 under MP&E: 152 facts, no refusals. 66 of 78
  (concept, period) values are printed by more than one filing, including
  across the renaming, and none disagree. Trailing-year pairs ending
  2021-12-31 through 2026-06-30 compute; pairs ending 2020-03-31 through
  2021-09-30 refuse by design (their prior year needs FY2019, before the
  FY2020 calendar start).
* **September 3, 2024 cutoff: 18 filings**, 100 facts, and the pair above
  is unchanged (34.2800476002% vs 30.4290129950%).

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

## Publishing from a saved capture

`src/fundamentals/segment_publication_command.py` publishes the facts of a
reviewed dry run from the saved SEC capture itself, so the stored facts are
exactly the reviewed ones: each document's SHA-256 (part of every fact's
`raw_tag`, and so of its stored identity) is checked against the capture
record, and whenever a `--manifest` is supplied the documents and a digest
of the facts (everything except ingestion batch and time) must equal it, or
the command exits nonzero with `"status": "refused"` before any database
connection. `--publish` goes through `publish_incremental` as a standalone
`+sec_filing_document` batch under `cat-met-supplemental-results-v3`, which
is read and proved separately from CAT's Company Facts and filing-XBRL
batches (a different source and concept-map version).

Timestamps:

* **Eligibility** is SEC acceptance (`eligible_at = accepted_at`), bounded by
  the knowledge cutoff.
* **Ingestion** (`ingested_at`, bounded by the data-vintage cutoff) is when
  the publishing process read the last document, just before its
  transaction; it is never the earlier SEC capture time, because the data
  vintage describes when facts existed in our dataset. A run whose vintage
  is before the publication therefore never sees these facts.
* **SEC capture** and submissions-download times stay in the manifest as
  document provenance. The store keeps no separate publication time;
  `ingested_at` precedes the commit by the seconds the transaction takes.
* **Replays.** Publishing the same facts again inserts nothing: they stay in
  their original batch with its original `ingested_at`. The command reports
  `stored_ingestion` (the batches holding the facts and their own ingestion
  times) separately from `this_run_ingested_at`, which is only what a new
  insert would have been stamped.
* **A metadata read-back failure after a successful publish is never a
  refusal.** `--publish` calls `publish_incremental` (which commits or rolls
  back its own transaction before returning or raising) and then reads back
  the stored batch row to build `stored_ingestion`. If that read-back fails
  -- the facts are already safely stored, whether newly inserted or a
  genuine no-op replay/reuse of an earlier publication -- the command
  prints `"status": "published_unverified"` with the exact `publication`
  receipt (inserted/reused/replayed counts and batch IDs) it would have
  printed on success, a `metadata_read_error`, and a nonzero exit code. It
  never reports `"refused"` for this case, and its `message` points at a
  read-only recovery step (`--verify`, or reading
  `fundamentals_ingestion_batches` directly) rather than instructing
  another publish. A failure from `publish_incremental` itself, before any
  commit, is unaffected and still reports `"status": "refused"`.

`--verify` is read-only. It requires the stored batch named by `--batch-id`
to hold exactly the reviewed facts, all with one ingestion time equal to
that batch row's (and to `--expect-ingested-at` when given), with matching
source and versions; then no reviewed fact 1 µs before that time and all of
them at it; per filing, none 1 µs before SEC acceptance and all at it; and
the supplied margin pairs recomputed from stored facts, refusing 1 µs before
the ingestion time.

Arguments go in a shell array, which works in zsh and bash and keeps paths
with spaces intact (a scalar `$ARGS` is one argument in zsh). A saved offline
script must include `--manifest` explicitly: a package's `offline-dry-run.zsh`
that omits it parses the capture without checking it against the reviewed
package, which defeats the check.

```shell
args=(
  --capture-dir "/path/to/capture" --cik 18230
  --knowledge-cutoff 2026-09-27T18:54:12.086501Z
  --batch-id cat-segment-backfill-20260927T185412Z
  --manifest "/path/to/package/manifest.json"
)
python -m src.fundamentals.segment_publication_command "${args[@]}"            # offline check
python -m src.fundamentals.segment_publication_command "${args[@]}" --publish  # needs DATABASE_URL
python -m src.fundamentals.segment_publication_command "${args[@]}" --verify \
  --expect-ingested-at <stored ingested_at> --expectations "/path/to/package/expectations.json"
```

A fresh SEC download of the same filings cannot republish or refresh this
batch: SEC appends a different script tag to every response, so every
document hash, `raw_tag`, and stored identity changes, and the pre-commit
proof refuses the stored facts as unexpected. A refresh needs a reviewed
change to how the document is identified.
