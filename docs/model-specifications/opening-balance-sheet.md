# Opening balance sheet (forecast foundation, step 1)

Status: validation only. Nothing here forecasts, values, trades, or
publishes. `src/fundamentals/opening_balance_sheet.py`, policy
`opening-balance-sheet-v1`.

## Input contract

| Field | Meaning |
| --- | --- |
| `cik` | Issuer, normalized to 10 digits |
| `period_end` | The balance-sheet instant (explicit; never "latest available") |
| `knowledge_cutoff` | A fact counts only if its filing was accepted by this instant |
| `data_vintage_cutoff` | …and only if it was ingested by this instant |
| `currency` | `USD` only (the store holds USD facts) |
| `source_adapter`, `supplemental_source_adapters`, `concept_map_version`, `fiscal_calendar_version` | Source policy; `from_policy` takes them from the issuer manifest |
| `max_staleness_days` | Default 200: `period_end` may be at most this far before the cutoff |

Each output line records concept, value, unit, currency, raw tag, accession,
form, amendment flag, filed date, SEC acceptance time, eligibility time,
source adapter and document, concept-map and calendar versions, ingestion
batch, and ingestion time. The snapshot records the policy version, the
itemization gaps and unreported line items (below), and any superseded
conflicts.

## Rules

* Required reported lines at exactly `period_end`: total assets, current
  assets, cash and cash equivalents, total liabilities, current liabilities,
  and total equity. Parent shareholders' equity stands in for total equity
  only when the issuer reports no total-equity line.
* `total assets = total liabilities + equity`, exactly. No residual line is
  ever created, so an unreported noncontrolling interest or temporary equity
  refuses rather than being absorbed.
* Consolidated USD values only; any other unit or currency, or two values
  for one line, refuses. A line reported only at another date is missing,
  never carried forward.
* Point-in-time selection (latest accepted filing wins per line) through the
  shared repository; a later amendment applies from its acceptance onward,
  and only once it is ingested by the data-vintage cutoff. A repository that
  returns a fact outside either cutoff or the source policy refuses.
* Contradictory values within one filing (two tags for one line that
  disagree), at the balance-sheet date:
  * **Unresolved** when that filing is the latest visible filing for the
    line. The shared selector then has no value for it, and the snapshot
    refuses (`conflicting_value`, naming the lines and accessions). An older
    clean value is never substituted for a newer conflicted filing.
  * **Superseded** when a strictly later filing (by acceptance time, then
    accession) reported the line cleanly and the selector chose it. The
    snapshot builds on the later value and lists the older conflict in
    `superseded_conflicts` (line, accession, tags, values, superseding
    accession). Before the correction's acceptance, or before its ingestion
    at the data-vintage cutoff, the conflict is still unresolved and refuses.
* Orderings that must hold on any nonfinancial balance sheet (for example
  current assets within total assets, PP&E within noncurrent assets); these
  are checks, not plugs.
* Sign checks apply only to these asset and liability lines, which must be
  nonnegative when reported: cash and cash equivalents, cash and restricted
  cash, accounts receivable, inventory, net PP&E, accounts payable, current
  debt, and long-term debt. Current assets and current liabilities must lie
  between zero and their totals. Equity (total or parent shareholders') and
  retained earnings may be negative; no sign is required of them (Apple's
  June 2024 retained earnings, for example, are negative).
* Cash, cash equivalents, and restricted cash (`cash_and_restricted_cash`)
  is optional. When reported, it must be nonnegative, at least cash and cash
  equivalents, and, together with receivables, inventory, and net PP&E, at
  most total assets. Restricted cash may be current or noncurrent, so it is
  not bounded by current assets.
* Itemization. A section (current/noncurrent assets and liabilities) is
  itemized only when **every** line item defined for it was reported at the
  instant and they sum exactly to the section (noncurrent sections are
  reported total less reported current):

  | Section | Defined line items |
  | --- | --- |
  | current assets | cash and cash equivalents, accounts receivable, inventory |
  | noncurrent assets | net PP&E |
  | current liabilities | accounts payable, current debt |
  | noncurrent liabilities | long-term debt |

  An explicitly reported zero counts; an unreported line is missing
  evidence and is never treated as zero. There is no reviewed absence policy,
  so no defined line may be skipped. `forecast_itemization_gaps` names each
  section that fails, and `unreported_itemization_lines` names the defined
  lines that were not reported. Nothing is filled in to close a gap.
* `is_fully_itemized` (no gaps) guarantees only that: every defined line was
  reported and each section sums exactly. It does not guarantee that the
  concept maps choose the right lines, and the lines may come from different
  filings (each the latest accepted for its line).

## What the SEC data supports (checked 2026-09-25)

This is from SEC Company Facts on data.sec.gov, the source the pipeline
ingests, at each issuer's latest balance sheet before the 2024-09-03 pilot
cutoff. It was not checked against the production store: no database access
was available.

| Issuer (date) | Assets | Liabilities | Equity line | Identity | Result |
| --- | ---: | ---: | --- | --- | --- |
| AAPL (2024-06-29, 10-Q `0000320193-24-000081`) | 331,612 | 264,904 | `StockholdersEquity` 66,708 | holds | would build |
| MSFT (2024-06-30, 10-K `0000950170-24-087843`) | 512,163 | 243,686 | `StockholdersEquity` 268,477 | holds | would build |
| CAT (2024-06-30, 10-Q `0000018230-24-000045`) | 83,336 | 66,200 | total incl. NCI 17,136 | holds | would build |
| WMT (2024-07-31) | reported | **never tagged** (`us-gaap:Liabilities` has no Company Facts series) | n/a | n/a | **refuses: `total_liabilities` missing** |

USD millions. For AAPL and MSFT the parent-equity value was checked; whether
they also tag total equity including NCI was not, and the identity holds on
parent equity either way. Cash and cash equivalents are tagged for AAPL (25,565), MSFT
(18,315), and CAT (4,341); CAT's current assets (43,096) and current
liabilities (33,564) are also tagged. MSFT's and AAPL's current totals and
WMT's other lines were not individually checked. Walmart's balance sheet has
no total-liabilities line; deriving it would be a plug, so WMT stays refused
until a reviewed issuer rule composes it from reported lines.

For every issuer, the mapped line items (cash, receivables, inventory, PP&E,
payables, current and long-term debt) do not fully itemize any section.
Goodwill, intangibles, lease right-of-use assets and liabilities, marketable
securities, deferred taxes and revenue, accrued and other liabilities,
short-term borrowings, and noncontrolling and temporary equity are unmapped.
A line-by-line forecast cannot start from this snapshot yet.

## Next step (proposal, not implemented)

Step 2 is an issuer-scoped balance-sheet line map, reviewed filing by
filing, whose reported lines sum exactly to each section (as the CAT
supplemental-table rules do for segment data), plus a reviewed composition
rule for Walmart's total liabilities. Only when `forecast_itemization_gaps`
is empty should a linked forecast use the snapshot.
