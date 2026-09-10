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

## Scheduled publication

[`refresh-sec-fundamentals.yml`](../../.github/workflows/refresh-sec-fundamentals.yml)
runs after each Monday-Friday SEC filing window and also supports manual dispatch.
It currently publishes Apple only because the exact issuer-calendar catalog is
the authority for supported coverage; it does not guess unsupported issuers or
future fiscal boundaries.

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
