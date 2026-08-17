# Independent DCF Validation — Track A Phase 2A

This directory is the output of an independent-session spreadsheet validation of the
DCF/WACC model specified in `docs/model-specifications/dcf.md` and
`docs/model-specifications/wacc-capm.md`. It was built in a fresh conversation with
**no prior exposure** to this repository's `src/dcf_model/dcf.py` implementation, per
`docs/independent-validation-plan.md`'s design-principle requirement of genuine
independence.

## What this is

A second, mechanically independent calculation of intrinsic value per share for four
real public companies (MSFT, CAT, INTC, VZ), computed with real Excel formulas
entered directly from the written specification's prose and equations — never by
reading, importing, or copying from this repository's implementation code.

## What this is NOT

- **Not** a comparison against the codebase's actual output. The Summary &
  Reconciliation sheet's "Codebase Output" column is deliberately left blank — filling
  it in is the explicit scope of a **separate future session** that may read the
  implementation (this one was not allowed to).
- **Not** investment advice, and not a claim that any company is a "buy," "sell," or
  a profitable trade. See the Research Outlook sheet's prominent disclaimer.

## Files

| File | Contents |
|---|---|
| [`independent_dcf_validation.xlsx`](independent_dcf_validation.xlsx) | The workbook itself — 18 sheets, 1,620 live formulas. |
| [`build_workbook.py`](build_workbook.py) | Reproducible workbook generator script (`python3 build_workbook.py`). |
| [`build_snapshots.py`](build_snapshots.py) | Three explicit modes: default (offline, read-only verification), `--rebuild-from-cache` (offline, deterministic regeneration of `snapshots/*.json` + `source_manifest.csv` + `snapshot_manifest.json` + `independent_dcf_validation.xlsx` from `sec_raw_cache/`, staged and validated as a group before publication — see "Phase 2A correction passes" Pass 4 below for exactly what its transaction does and does not guarantee), `--refresh-sec` (the only mode that contacts SEC EDGAR; requires an operator-supplied `SEC_USER_AGENT` environment variable; not run during this session; its own staged transaction and rollback-failure reporting are covered in Pass 6 below). Market data (price/shares/beta/sector) is always carried forward frozen from the existing snapshot files, never re-fetched from Yahoo Finance/yfinance, in any mode. |
| [`formula_audit.py`](formula_audit.py) | Structural formula-text auditor — inspects raw `<f>` formulas (never cached values) to catch cell-reference defects a cached-value check cannot see. `python3 formula_audit.py` must exit 0. |
| [`xlsx_lite.py`](xlsx_lite.py) | Minimal stdlib-only `.xlsx` writer (no openpyxl — see `workbook_build_log.md` §3). |
| [`shadow_calc.py`](shadow_calc.py) | Independent Python re-implementation of the spec, used only to supply cached formula values and sensitivity/direction-check numbers. |
| [`error_injection_test.py`](error_injection_test.py) / [`error_injection_evidence.md`](error_injection_evidence.md) | The deliberate-error-injection check and its result. |
| [`workbook_build_log.md`](workbook_build_log.md) | Full chronological build + verification log, including every tooling constraint hit and the Phase 2A correction pass. |
| [`source_manifest.csv`](source_manifest.csv) | Every frozen fact's field, value, units, XBRL tag, filing accession, and source URL (82 rows — now including full per-component provenance for every derived Total Debt figure). |
| [`snapshot_manifest.json`](snapshot_manifest.json) | SHA-256 checksums for every file in `snapshots/`. |
| [`snapshots/`](snapshots/) | Frozen, normalized, per-company JSON snapshots (the sole data input to the workbook). |
| [`sec_raw_cache/`](sec_raw_cache/) | Five cached raw SEC EDGAR files (`company_tickers.json` + 4 companies' company-facts JSON) plus `cache_manifest.json` (SHA-256 + size for each). `build_snapshots.py --rebuild-from-cache` uses only this cache and never opens a network connection; only `build_snapshots.py --refresh-sec` (requires an operator-supplied `SEC_USER_AGENT` environment variable; not run during this session) may replace these files. |

## Phase 2A correction passes

Three independent review passes found and fixed defects in this workbook and its
supporting scripts. Full detail for each is in `workbook_build_log.md` §§11, 13, 15.

**Pass 1** (§11): (1) three sensitivity tables' Terminal Value formulas referenced
the DCF sheet's Year-5 Revenue cell instead of Year-5 FCF — the cached values were
unaffected (computed correctly by `shadow_calc.py`), which is exactly why only
formula-TEXT inspection, not a cached-value check, catches this class of bug; (2)
Total Debt provenance was incomplete (no source URL for any Total Debt row, no
per-component provenance for the three companies whose total is derived by
summing balance-sheet components).

**Pass 2** (§13): (1) a hard-coded personal email address in the SEC User-Agent
string, removed and replaced with an operator-supplied `SEC_USER_AGENT`
environment variable requirement; (2) `--refresh-sec` fetched implicitly and
embedded a non-deterministic timestamp — redesigned as three explicit modes
(default verify-only / `--rebuild-from-cache` offline / `--refresh-sec`, the only
network-capable mode); (3) `formula_audit.py` depended on `build_workbook.py` to
locate cells, undermining its independence — rewritten to import nothing from this
project's own code and locate every cell from the workbook's own labels/headers;
(4) derived Total Debt rollup rows had blank/duplicated provenance — fixed with
deduplicated, joined accession/URL/filed-date fields.

**Pass 3** (§15): (1) **`formula_audit.py`'s own Table 5 (two-way WACC×g grid)
check silently examined zero cells and still printed PASS** — its regex-based
parser didn't recognize plain numeric cells (Table 5's headers), so the
header-discovery loop found nothing and the per-cell loop never ran; rewritten with
`xml.etree.ElementTree` to handle every OOXML cell kind, and a PASS is now refused
unless exactly 144/144 Table 5 cells were actually examined; (2) 16 SEC filed
dates were missing from `source_manifest.csv` (Pretax Income/Tax Provision/
Interest Expense/Cash × 4 companies) — fixed, plus a new field-level manifest-
completeness verifier; (3) `--refresh-sec` could leave a mixed cache on partial
failure — redesigned as a staged transaction (validate everything in an isolated
staging directory first, publish via two atomic renames, roll back in full on any
failure); (4) the PII scanner exempted its own source file — the exact file that
had previously leaked an address — fixed with a generic email regex and no
self-exemption, extended to scan the workbook's own internal strings too; (5)
`--rebuild-from-cache`'s four outputs were written atomically as individual files,
not as one all-or-nothing group, and the workbook itself was never validated
before publishing — redesigned into a three-phase staged transaction (generate all
four outputs, including the workbook, into staging → validate the entire staged
generation, including a full formula audit of the staged workbook → publish all
four as one rollback-protected group). Every fix in this pass has a dedicated
offline fault-injection or negative-control self-test
(`python3 build_snapshots.py --self-test`, `python3 formula_audit.py --self-test`),
each proven to actually detect the defect it targets, not just constructed to pass.

**Pass 4** (§16): an independent review reproduced a real destructive bug in Pass
3's own `_publish_staged_generation()`: injecting a failure while backing up the
*second* of four output slots caused the rollback path to delete slots 2–4
outright, because it deleted every live path that still existed rather than only
the ones it had actually backed up — leaving no way to restore them. (1) Rewrote
`_publish_staged_generation()` around an explicit per-slot state machine
(`pending → backed_up → promoted`), a unique transaction-specific backup
directory (never a fixed `.rollback_backup` suffix, and never silently deleted
if one is found pre-existing — a leftover from an interrupted run now causes a
clean refusal, not silent deletion or overwrite), and a hard distinction between
a pre-commit failure (full rollback to the prior generation) and a post-commit
cleanup failure (the new generation stays live; only the old backup is
retained, reported, and left for the next run to detect). Applied the identical
design to `staged_refresh()`'s single-directory case. Both are now proven by an
11/16-scenario fault-injection matrix covering every backup-phase and
promotion-phase position, cleanup failure, and pre-existing residue. (2) Fixed a
real vacuous-pass bug in the staged-validation code: the snapshot-manifest
checksum check only iterated whatever entries were present, so an *empty*
`files` mapping reported zero problems — replaced with `verify_snapshot_manifest()`,
which requires exactly the 4 expected entries. (3) Added
`verify_snapshots_directory()` and `verify_source_manifest_structure()` —
structural checks (exact required row/entry sets, no vacuous pass, dedup/no-blank
rollup provenance reconciled against the actual component rows, reconciliation
of manifest values back to the staged snapshots) that are complementary to, not
a replacement for, the existing field-level completeness checks. Six negative
controls added (empty snapshot-manifest mapping, one missing entry, one extra
entry, a broken debt-component total, a duplicate required row, a missing
required row, and a duplicated provenance element in a derived rollup — seven
in total). (4) Fixed a genuine visual-layout defect: the README's company
table clipped the "Profile" column against its non-empty "Revenue CAGR"
neighbor — fixed with a wider wrapped column and taller rows, verified by
direct inspection of the generated XML's `wrapText`/`customHeight` attributes
(no spreadsheet-rendering engine is available in this environment; see §16 for
what that limitation does and does not mean for this verification). (5)
Corrected this file's and `build_snapshots.py`'s documentation to state exactly
what the publish transaction does and does not guarantee — a killed process is
NOT automatically recovered, and the 4-file group publish is NOT a single
OS-atomic operation, only a software-level rollback protocol proven against
process-level (not OS-crash-level) failures.

**Pass 5** (§19): Pass 4's own rollback fix still had a bug — a slot with no
live original before the transaction (e.g. rebuilding a not-yet-existing
generated artifact) could be promoted and then, if a later slot's promotion
failed, rollback unconditionally indexed a backup that was never created for
it, raising `KeyError` mid-rollback and aborting restoration of every slot
after it. Fixed by tracking a second, independent per-slot fact
(`had_original`) alongside the existing state machine, so a promoted slot
with no original is removed and correctly left absent instead of triggering
a backup lookup that can't exist — proven by a new 33-scenario missing-
original fault matrix. Also added: fault injection for failures INSIDE
rollback itself (removing a promoted output, restoring a backup), which are
now reported by slot name with a preserved recovery path rather than risking
a silent or crashing failure; a `COMMITTED WITH CLEANUP WARNING` marker so a
post-commit cleanup failure is always surfaced through the public
`rebuild_from_cache()` path, never folded into an apparently-clean success
report; a strengthened `_snapshot_schema_ok()` that checks provenance fields
hold valid nonempty values, not just present keys; and a docstring
correction (a "1:1" provenance-correspondence claim was replaced with the
actual, implemented "distinct-set" invariant). No financial formula or value
changed — the rebuilt workbook is byte-for-byte identical to the one before
this pass.

**Pass 6** (§21): a separate defect in the SEC-cache `--refresh-sec`
transaction (distinct from Pass 5's rebuild-generation fix) — when
restoring the cache backup during rollback itself failed, that exception
escaped the publisher's own handling, was caught by an unrelated handler,
and was misreported as `"live cache untouched"` even though the live cache
directory was actually absent. Fixed by wrapping the restore-rename in its
own error handling: a failure there now returns a `REFRESH FAILED AND
ROLLBACK INCOMPLETE` result naming the retained transaction directory and
backup path for manual recovery, never claiming the cache is restored,
untouched, or unchanged. `staged_refresh()` and `refresh_sec()`'s reporting
now distinguish five outcomes (untouched, fully rolled back, incomplete
rollback, committed, committed-with-cleanup-warning) instead of one
unconditional claim. No snapshot, manifest, cached SEC input, or workbook
content changed this pass — verified byte-for-byte (SHA-256) identical
before and after.

## Companies validated (profile confirmed from frozen data, not reputation)

| Ticker | Entity | Profile | Revenue CAGR | Avg Op Margin |
|---|---|---|---|---|
| MSFT | Microsoft Corporation | Large-cap, capital-light, high-margin | +13.74% | 44.18% |
| CAT | Caterpillar Inc. | Capital-intensive, moderate leverage | +7.31% | 16.56% |
| INTC | Intel Corporation | Mature, negative/near-zero revenue growth | **-9.57%** | 0.46% |
| VZ | Verizon Communications Inc. | Meaningfully leveraged | +0.85% | 21.22% (44% debt/(debt+mktcap)) |

(IBM was initially considered per the task's candidate pool but its SEC data showed
positive post-Kyndryl-spinoff revenue growth, so it did not fit the required
negative/near-zero-growth profile on actual data — see `workbook_build_log.md` §5.)

## Data sources

SEC EDGAR (`data.sec.gov`, `www.sec.gov`) for all financial-statement facts; Yahoo
Finance via the `yfinance` package for current price, shares outstanding, beta, and
sector. Every value is frozen with a UTC retrieval timestamp, XBRL tag or source
field, and (where applicable) SEC filing accession number — see
`source_manifest.csv` and the per-company files in `snapshots/`.

## Status

- Independent workbook construction: **COMPLETE**
- Codebase-to-workbook reconciliation: **PENDING** (separate future session)
- Second-reviewer sign-off: **PENDING**
- Profitability evidence: **NOT ESTABLISHED** (see Research Outlook sheet)

See the chat transcript's final report for the complete 17-point summary, or the
workbook's own README/Validation Checks/Research Outlook sheets for the same
information in spreadsheet form.
