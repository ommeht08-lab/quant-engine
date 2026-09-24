# SEC pilot run 36027972839: audit note

> **Pipeline validation, not evidence of investment performance.** Four
> companies and one formation date cannot establish an investment edge.

`record.json` in this directory is the exact, unchanged reduced public record
printed by pilot run 36027972839. It is filed beside, and does not replace,
`validation/backtest_pilot/2024-09-03/record.json` (pinned run 35809776344).

| | |
|---|---|
| Pilot run | [36027972839](https://github.com/ommeht08-lab/quant-engine/actions/runs/36027972839) |
| Code commit checked out by pilot run | `ef8d5998034742b736fa80fb6a04fd1ce50f12d2` |
| Independent archive verifier | [36028896441](https://github.com/ommeht08-lab/quant-engine/actions/runs/36028896441) |
| Decision date and knowledge cutoff | 2024-09-03, `2024-09-03T16:00:00-04:00` |
| Data-vintage cutoff | `2026-09-24T16:32:46.020337+00:00` |
| Execution | Entry 2024-09-04 open, exit 2025-09-05 close (252 sessions); $100,000; costs of 5, 10 (base), and 25 bp |
| Price snapshot | `06445c53d3771aa5db39ef0d46ac22a32663579875602e7415d69ba80f7c0b15` (private; offline replay identical) |
| Private full record | `ac6eb4be41e7251bdc2e61284ec1b82541ad8b09c26c337335a7d2b05dbdcf92` (private) |

## Independent verification

Verifier run 36028896441 ran `src/backtesting/verify_private_archive.py`, a
standalone, read-only check that imports nothing from the code that wrote the
rows. It found **zero problems**:

- The price snapshot `06445c53…0b15` was found, its checksum recomputed from the
  stored bytes matched, and it was captured in run 36027972839.
- The private full record `ac6eb4be…cf92` was found, its checksum recomputed
  from the stored bytes matched, and it references that snapshot and run
  36027972839.
- The private record holds its three daily curves (strategy, equal-weight
  control, SPY buy-and-hold), reported at **252 points each**, matching the
  252-session holding period. The verifier reports these counts as metadata;
  its pass/fail checks are the checksums, the references, and the curves'
  presence.

Neither this note nor `record.json` contains the daily curves or any price
series; those stay in the private tables.

## Result

- **Strategy:** no eligible candidates. The strategy held all capital in cash
  and ended at $100,000.00 in every cost case.
- **AAPL, MSFT, WMT:** loaded from SEC facts and rejected by the strategy's
  gates, with the same reasons as pinned run 35809776344: AAPL failed the
  sector-relative P/IV filter, MSFT the absolute fair-value gate, and WMT the
  Conviction Score eligibility test (negative FCF growth).
- **CAT:** its earlier debt-data gap is resolved. In run 35809776344 CAT was
  refused at the SEC valuation-input stage for lacking an unambiguous
  consolidated cash or term-debt balance. With its verified v5 backfill
  (`backfill-18230-35940788245-1` and its `+sec_filing_xbrl` companion) it now
  passes that stage. It was refused at the next stage instead, because its SEC
  gross-profit history was incomplete at the decision cutoff
  (`gross_profit (missing_period)`). Its 25% allocation stayed in cash in both
  the strategy and the equal-weight control, and was not redistributed.

## Audit-trail addendum

The reduced record's `audit_trail.prior_provisional_run_ids` lists only
`35809011675`. The run was dispatched with the workflow's default
`prior_run_ids` input, which is `35809011675`. The history also includes
pinned run `35809776344`, which that input omitted. The complete prior history
for this decision date is:

- `35809011675`: provisional run whose prices were not pinned.
- `35809776344`: pinned run; the reproducible reference for
  `validation/backtest_pilot/2024-09-03/record.json`.

This addendum corrects the citation in this note only. It does not modify the
immutable private full record or the reduced `record.json` beside this note:
both still read `["35809011675"]`, and the hashes above remain valid for them.
