# Model Specification: Offline SEC Ingestion Operations

Status: implemented for operator use and an isolated scheduled publication job;
not connected to request handling or the live valuation path.

Implementation:
[`src/fundamentals/sec_pipeline_command.py`](../../src/fundamentals/sec_pipeline_command.py)

## Safety boundary

The command downloads public SEC data, validates the entire batch, and prints a
sanitized summary. It performs a dry run unless `--publish` is explicitly
present. A refused dry run never reaches the publisher.

`SEC_USER_AGENT` must be set in the command's process environment and must name
the application plus a real contact email, as required by SEC access policy.
The value is sent only as the SEC HTTP User-Agent and is not written into facts
or command output.

For `--publish`, `DATABASE_URL` must already be present in the process
environment. The command does not search parent dotenv files for it. Publication
uses the existing append-only, atomic batch writer only after every prior stage
has completed.

## Dry-run example

With `SEC_USER_AGENT` already set:

```shell
python -m src.fundamentals.sec_pipeline_command \
  --cik 320193 \
  --knowledge-cutoff 2026-09-09T14:00:00Z \
  --batch-id apple-sec-20260909
```

The required cutoff and batch ID make the run's intent explicit. Omitting
`--publish` guarantees that the command does not open a database connection or
write facts.

## Explicit publication

After reviewing a successful dry run, rerun the same coordinates with
`DATABASE_URL` already set and append `--publish`. Reusing the immutable batch
ID is safe: the store accepts exact retries and rejects conflicting batch
metadata or facts.

Every publication republishes the issuer's complete classified history. Facts
that are already stored keep their original batch; only new facts land in the
new batch (and, for issuers with filing-XBRL compositions, its
`+sec_filing_xbrl` companion). Inside the publish transaction, before commit,
`incremental_publication.publish_incremental` proves the increment exactly:

- the identities returned by `INSERT ... RETURNING` and the identities stored
  before the insert are disjoint and together equal the publication;
- every inserted row is in this publication's batch for its source;
- the issuer's stored facts at the knowledge cutoff equal the publication, and
  point-in-time selection over them is unchanged;
- every supplemental batch pairs with a primary batch of the same mapping,
  calendar, and ingestion time. The foreign key alone does not establish this;
- with `--history-frozen-through` (the recurring refresh passes the 2024-09-03
  pilot cutoff), no inserted fact is eligible at or before that cutoff. A
  concept-map change or a late SEC addition to an old filing must therefore
  go through a reviewed backfill.

Publications take one transaction-scoped advisory lock before any schema
statement, so overlapping runs serialize instead of blocking in schema DDL or
refusing each other. The lock wait is bounded at five minutes, separately from
the 15-second publish statement timeout; the backfill and refresh workflows
also share one concurrency group.

Any mismatch rolls back every batch row and fact from that transaction. The
command's JSON report gives `inserted_fact_count` (facts in this run's batches)
separately from `publication.reused_fact_count_by_earlier_batch` (facts that
were already stored and remain in earlier batches). A retry under the same batch
ID reports its own previously stored facts as `replayed_fact_count`, never as
earlier-batch reuse.

The batch table has no issuer column, so a batch row can be tied to an issuer
only through its facts, which carry the issuer. The transaction therefore reads
the issuer's stored facts first and writes a batch row only for a source that
inserts at least one fact, plus the primary row a written filing-XBRL batch
must pair with (`written_batch_ids`). A run that inserts nothing reports
`"no_op": true` and writes nothing at all. Every committed batch holds a fact
of its issuer, or is the primary of a paired filing-XBRL batch that does.

Because the store is append-only, a stored fact that SEC later stops reporting
(or re-tags) makes every later publication for that issuer and concept-map
version refuse with "unexpected" stored facts. This fails closed by design;
recovery is a reviewed concept-map version and backfill.

## Scheduled publication

[`refresh-sec-fundamentals.yml`](../../.github/workflows/refresh-sec-fundamentals.yml)
runs after each Monday-Friday SEC filing window and also supports manual dispatch.
It publishes AAPL, MSFT, and WMT. An issuer joins the schedule only after:

1. a manual [`backfill-sec-fundamentals.yml`](../../.github/workflows/backfill-sec-fundamentals.yml)
   run publishes it and verifies it read-only at the 2024-09-03 cutoff and the
   publish cutoff (backfill mode: every fact is in the supplied batch);
2. the issuer manifest records that batch and run and marks it SEC-history
   ready;
3. a separate reviewed change adds it to the matrix.

CAT is SEC-history ready from its verified v5 backfill but stays off the
schedule until refresh verification has run cleanly on the scheduled issuers.
Live SEC selection (`sec_live_approved`) is a separate gate for every issuer.

After each publish, a read-only step runs
`sec_backfill_verification --mode refresh`. It re-downloads SEC data and
checks the committed result against the pipeline at the 2024-09-03 cutoff and
the refresh's own cutoff, accepting earlier batches by lineage rather than by
name. It also requires that the refresh contributes no facts visible at
2024-09-03. Each batch row present under the refresh's ID is bound to the
issuer through facts, checked per batch across all issuers: it must hold a fact
of the requested issuer (a primary may instead pair with a filing-XBRL batch
that does), and no batch may hold another issuer's fact. Another issuer's
batch, or an empty batch committed by the older publisher, fails with that
reason. When no rows exist under the batch ID (a no-op), the report says
`"batch_rows_present": false` and only the issuer's stored result is verified;
the absence of rows alone cannot prove the publish step ran, which the
workflow's step ordering asserts instead. Verification re-downloads SEC data,
so a Company Facts update between the publish and verify steps can report a
fact missing although the publication was exact; rerun verification before
treating that as a publication fault. This check runs after commit, so it can only report a problem; the
pre-commit proof above is what prevents one.

The publication job cannot start unless its credential-free fundamentals tests
pass. The job receives only `SEC_USER_AGENT` and `DATABASE_URL`, prevents
overlapping runs, and constructs a UTC knowledge cutoff plus an immutable batch
ID from the GitHub run ID and attempt. A rerun therefore creates a new lineage
batch rather than pretending a fresh download has the prior run's ingestion
timestamp. Fact uniqueness still makes unchanged source facts idempotent.

Any refused download, extraction, fiscal classification, quarterly assembly, or
atomic database publication exits unsuccessfully and leaves the last complete
stored facts intact. The workflow does not call the valuation endpoint, install
yfinance, or switch the live statement source.
