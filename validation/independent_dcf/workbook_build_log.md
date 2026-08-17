# Workbook Build Log

Chronological record of how `independent_dcf_validation.xlsx` was built, the tooling
constraints hit along the way, and every verification step actually performed.

## 1. Session checkpoint (before any work)

```
git branch --show-current  -> remediation/valuation-engine-production-quality
git rev-parse HEAD          -> 5c15540df8541207c2c2143c8d010f04090ca9ce
git status --short          -> (clean)
```
Matched the expected checkpoint exactly. Proceeded.

## 2. Specification reading

Read exactly the eight files authorized by the task instructions (and no others):
`docs/independent-validation-plan.md`, `docs/model-specifications/dcf.md`,
`docs/model-specifications/wacc-capm.md`, `docs/data-dictionary.md`,
`docs/assumptions-register.md`, `docs/limitations-register.md`,
`docs/research-overview.md`, `docs/model-development-roadmap.md`. No `src/`, `tests/`,
`frontend/`, `.github/`, diffs, commit contents, or `docs/model-change-log.md` were
read, searched, imported, or executed at any point in this session.

## 3. Tooling constraints discovered

- No `openpyxl`, `xlsxwriter`, or any `.xlsx`-writing library available in the
  project's `venv/` (Python 3.9) or system Python.
- No LibreOffice (`soffice`/`libreoffice`) installed.
- No Microsoft Excel or Apple Numbers installed (`/Applications` checked directly).
- The task instructions prohibit installing dependencies.

**Resolution:** built `xlsx_lite.py`, a small stdlib-only OOXML writer (raw
`zipfile` + hand-assembled XML strings — no third-party packages), sufficient for
this workbook's needs: multiple sheets, string/number/formula cells (each formula
carries both a real `<f>` element and a Python-computed cached `<v>`), named cell
styles (fonts/fills/borders/number formats), column widths, freeze panes, merged
cells. `pandas`, `requests`, `numpy`, and `yfinance` WERE available in the project's
`venv/` and were used only as generic third-party data-fetching libraries (not as
part of this repository's own application code) to pull SEC EDGAR and Yahoo Finance
data — this is consistent with the task's public-data authorization, not a use of the
codebase under validation.

## 4. Data retrieval

- `www.sec.gov/files/company_tickers.json` — ticker → CIK lookup.
- `data.sec.gov/api/xbrl/companyfacts/CIK{10-digit}.json` — full company facts (XBRL)
  for MSFT, CAT, IBM, VZ, and INTC (IBM was pulled, evaluated, and NOT used in the
  final four — see §5).
- yfinance (`Ticker.info` / `Ticker.fast_info`) — current price, shares outstanding,
  beta, sector, industry, for MSFT, CAT, INTC, VZ.
- User-Agent sent on every SEC request during the original data pull: a descriptive
  client identifier plus a contact string, per SEC's fair-access requirement for
  automated requests. **Correction (Phase 2A second correction pass, see §13):** the
  contact string originally used was a hard-coded personal email address that had no
  established connection to this repository's owner and should never have been
  committed to a project deliverable. It has been removed from every file in this
  directory and is not reproduced here. Any future SEC EDGAR refresh now requires the
  operator to supply their own contact string via the `SEC_USER_AGENT` environment
  variable at run time (`build_snapshots.py --refresh-sec`); the script never logs or
  prints that value. This correction does not imply the original request metadata
  never existed — the original data pull did send a User-Agent header exactly as
  described in §4 above, just with a contact address that should not have been
  hard-coded into the script.
- No authentication, no environment files read, no contact with Alpaca/Supabase/
  Redis/Vercel/GitHub/brokerages/private APIs at any point.

## 5. Company selection — IBM swapped for INTC mid-session

Initial candidate roster (from the task's candidate pool): MSFT, CAT, IBM, VZ. After
pulling IBM's SEC data, its post-Kyndryl-spinoff revenue (2021 onward) showed a
**positive** ~4.2% 4-year CAGR — it did not fit the required "negative or near-zero
recent revenue growth" profile on the actual frozen data (the task instructions are
explicit: "Confirm each profile using frozen data rather than reputation"). Re-checked
the candidate pool, which also names INTC for this slot; INTC's SEC data confirmed a
genuine -9.57% 4-year revenue CAGR (2021: $79.0B → 2025: $52.9B) and a
near-zero/negative average operating margin. Switched IBM → INTC. IBM's raw extracted
data remains in the scratch working directory but is not part of any deliverable.

## 6. Profile confirmation (from frozen data, not reputation)

| Ticker | Revenue CAGR | Avg Op Margin | Debt / (Debt+MktCap) | Confirmed profile |
|---|---|---|---|---|
| MSFT | +13.74% | 44.18% | 1.12% | Large-cap, capital-light, high-margin |
| CAT | +7.31% | 16.56% | 8.16% | Capital-intensive, moderate leverage |
| INTC | **-9.57%** | 0.46% | 7.80% | Mature, negative/near-zero revenue growth |
| VZ | +0.85% | 21.22% | **44.15%** | Meaningfully leveraged |

## 7. XBRL tag normalization decisions (documented, not silent)

- **MSFT, VZ interest expense**: both companies renamed the XBRL tag from
  `InterestExpense` to `InterestExpenseNonoperating` partway through their filing
  history (MSFT starting FY2025, VZ starting FY2024). Merged both tags into one
  continuous series by period-end.
- **CAT interest expense**: CAT does not tag `InterestExpense` (or any operating-
  income-statement interest-expense line) separately in XBRL at all. Used
  `InterestPaidNet` (the supplemental cash-flow-statement "cash paid for interest"
  disclosure) as a documented proxy, flagged as `interest_method:
  proxy_cash_paid_interest` in the snapshot and Assumptions sheet.
- **IBM EBIT** (evaluated, not used in final workbook): IBM does not tag
  `OperatingIncomeLoss` in XBRL. Would have required deriving EBIT as Pretax Income +
  Interest Expense — moot once IBM was dropped for INTC.
- **INTC total debt**: `DebtCurrent` and `LongTermDebtCurrent` both tagged the
  identical $2,499M fact in INTC's FY2025 filing (same balance-sheet line under two
  XBRL concepts). Excluded `DebtCurrent` from the sum to avoid double-counting;
  documented in the extraction script's comments and the snapshot's
  `total_debt_normalization_method` field.

## 8. Workbook construction

`build_workbook.py` generates all 18 sheets programmatically: README, Sources,
Assumptions, `Inputs_<TICKER>` × 4, `DCF_<TICKER>` × 4, `Sensitivity_<TICKER>` × 4,
Summary & Reconciliation, Research Outlook, Validation Checks. Every DCF/WACC formula
was typed from the two specification documents' prose/equations directly into
`build_workbook.py`'s formula-string construction — none was copied from, or checked
against, `src/dcf_model/dcf.py` at any point. `shadow_calc.py` is a second,
independent Python re-implementation of the same specification, used only to (a)
supply the cached `<v>` values written next to each Excel `<f>` (so the file shows
correct numbers immediately on open, given no spreadsheet engine could recalculate it
in this environment — see §9), and (b) provide the sensitivity/direction-check
numbers embedded in `Sensitivity_<TICKER>` sheets.

## 9. Verification performed (no LibreOffice/Excel/Numbers available)

The task's verification steps ask for "recalculation using an available spreadsheet
engine, preferably LibreOffice headless." **None was available in this environment**
(confirmed in §3), and installing one was out of scope. Substituted the following,
all actually run and all passing:

1. **XML well-formedness**: every part inside the `.xlsx` zip parsed cleanly with
   `xml.dom.minidom` — 0 errors across 25 parts / 18 worksheets.
2. **Zip integrity**: `unzip -t independent_dcf_validation.xlsx` — no errors detected.
3. **No macros**: no `vbaProject.bin` or macro-related content type anywhere in the
   archive.
4. **No external workbook links**: no `externalLink` parts anywhere in the archive.
5. **No error strings**: grepped every worksheet XML for `#REF!`, `#DIV/0!`,
   `#VALUE!`, `#NAME?`, `#N/A`, `#NULL!`, `#NUM!` — zero occurrences.
6. **No hidden sheets**: no `state="hidden"` attribute on any `<sheetView>` or
   `<sheet>` element; all 18 sheets are visible, and all 18 are documented in the
   README sheet's "Sheet inventory."
7. **Formula presence**: 1,620 real `<f>` formula elements confirmed across the
   4 `DCF_<TICKER>` + 4 `Sensitivity_<TICKER>` + Summary & Reconciliation sheets
   (spot-checked; every calculated cell in those sheets is a formula, never a pasted
   number — input cells, by contrast, are deliberately hardcoded and styled blue).
8. **Cached-value correctness**: every formula's cached `<v>` was independently
   spot-checked against `shadow_calc.py`'s output for the same company — confirmed
   exact match (e.g. `DCF_INTC!B41` = -6.559121211821509, matching
   `shadow_calc.run_full_dcf('INTC')['bridge']['intrinsic_value_per_share']` exactly).
9. **Base-case finiteness**: all four companies' Enterprise Value, Equity Value, and
   Intrinsic Value per Share are finite numbers (none is NaN/inf) — including INTC's
   negative intrinsic value, which is finite, just negative (a legitimate model
   output under these assumptions, not a computation error — see Research Outlook).
10. **WACC exceeds terminal growth in every base case**: MSFT 10.00%, CAT 12.10%,
    INTC 15.05%, VZ 5.00% (at the clamp floor) — all four exceed the 2.5% terminal
    growth assumption.
11. **Sensitivity direction checks**: 3 of 4 companies (MSFT, CAT, VZ) pass all four
    direction checks (WACC↓→IV↑, g↑→IV↑, revenue growth↑→IV↑, margin↑→IV↑). INTC
    fails three of the four — investigated and confirmed mathematically correct given
    INTC's negative base-case FCF (higher WACC/terminal growth discount a negative
    stream LESS, not more; higher revenue growth compounds a structural per-dollar
    loss). Documented in `Sensitivity_INTC` and Research Outlook, never forced to
    PASS.
12. **Snapshot checksum verification**: recomputed SHA-256 of every file in
    `snapshots/` and confirmed an exact match against `snapshot_manifest.json` — all
    four MATCH.
13. **Error-injection test**: see `error_injection_evidence.md` — a deliberately
    corrupted Terminal Value formula was detected at 25% divergence against a 0.2%
    documented tolerance, with all four downstream intermediate values also
    correctly flagged. Corrupted temporary copy deleted immediately after.

**Not performed** (explicitly out of scope for this session, or genuinely
unavailable): visual/rendered inspection of each sheet (no spreadsheet GUI/renderer
available); actual formula recalculation inside a live spreadsheet engine (none
installed, and installing one is out of scope). Anyone opening this workbook in a
real spreadsheet application will trigger Excel's/LibreOffice's own automatic
recalculation on load (`fullCalcOnLoad="1"` is set in `workbook.xml`), which will
recompute every formula from scratch and should reproduce the cached values recorded
here if — and only if — this file's formula logic is sound; a mismatch there would
itself be informative and worth reporting.

## 10. Final state (before the Phase 2A correction pass below)

- `independent_dcf_validation.xlsx` — 18 sheets, 1,620 formulas, no errors, no
  macros, no external links, no hidden sheets.
- No file outside `validation/independent_dcf/` was created or modified.
- No `git add`, `git commit`, `git push`, or any repository-mutating command was run.

## 11. Phase 2A correction pass (external review findings)

An external spreadsheet reviewer imported and rendered all 18 sheets of the workbook
described above, traced representative formulas, and found two material defects.
Both were fixed in this same independent session, without beginning codebase
reconciliation and without reading any newly-forbidden file.

### 11.1 Defect 1 — three sensitivity tables' Terminal Value formulas referenced Revenue, not FCF

**Root cause**: in `build_sensitivity_sheet` (`build_workbook.py`), a variable named
`base_rev5` was defined as `'{DCF_SHEET}'!$G${row}` pointing at the DCF sheet's
Year-5 **Revenue** row, and was used — incorrectly — as the Gordon Growth numerator
(`TV = FCF_Y5 × (1+g) / (WACC−g)`) in three places:

1. Table 1 (WACC sensitivity) Terminal Value formula.
2. Table 2 (terminal-growth sensitivity) Terminal Value formula.
3. Table 5 (two-way WACC × terminal-growth grid) Terminal Value expression.

**Why the cached values did not reveal this**: every cached `<v>` in the workbook
was computed independently by `shadow_calc.py`, which has always used Year-5 FCF
correctly (it was never wired to the buggy `base_rev5` reference at all — that
variable only existed inside the Excel-formula-string construction in
`build_workbook.py`, not in the Python shadow-calculation path). The cached value
therefore matched what a CORRECT formula would produce, while the LIVE formula
actually stored in the file would produce a DIFFERENT number once a real
spreadsheet engine recalculated it. A cached-value comparison against
`shadow_calc.py` — which is exactly what §9 above did before this correction pass —
is structurally blind to this class of bug, because both quantities being compared
(the cache, and shadow_calc's own output) were computed the same correct way; only
the un-recalculated live formula text was wrong. This is precisely why
`formula_audit.py` (§11.3 below) was added to inspect raw `<f>` formula text instead.

**Consequence for INTC specifically**: INTC's Year-5 Revenue ($31.97B) is positive
while its Year-5 FCF (-$283.4M) is negative. A live recalculation of the buggy
formula would have used the wrong SIGN entirely for the Terminal Value numerator
input, which could have reversed the direction of INTC's already-documented inverted
sensitivity behavior (§9 point 11) once actually recalculated in a real spreadsheet
— even though the workbook's cached values and narrative disclosure already (by
coincidence, since the cache was computed correctly) described the right behavior.

**Fix**: `base_rev5` renamed to `base_fcf5`, redefined as `'{DCF_SHEET}'!$G${row}`
pointing at the DCF sheet's Year-5 **FCF** row (`dmarks['fcf_row']`, not
`dmarks['rev_row']`), and all three usages updated. Table 3 (revenue-growth
sensitivity) and Table 4 (operating-margin sensitivity) were already correct — they
recompute their own local Year-5 FCF within the sensitivity sheet itself (since
their FCF path genuinely changes per grid row, unlike Tables 1/2/5 which hold the
FCF path fixed and vary only the discount-rate side) — and were not touched.

### 11.2 Defect 2 — incomplete Total Debt provenance

**Root cause**: the original `build_snapshots.py`-equivalent snapshot-building code
(a) never populated `accession`/`source_url` for VZ's directly-reported Total Debt
figure (hardcoded `None` in the manifest-row constructor despite the underlying
extraction data having the accession available), and (b) for MSFT/CAT/INTC's
Total Debt — derived by summing two balance-sheet line items — collapsed each
component down to just its numeric value, discarding the XBRL tag, accession,
filed date, and source URL that had already been extracted for each one.

**Fix**: added `build_snapshots.py` to this directory (a corrected, reproducible,
self-contained version of the snapshot-building step, using a local
`sec_raw_cache/` of already-downloaded SEC EDGAR company facts — see its own
docstring). It re-derives every snapshot with full component-level provenance:
each debt component now carries its own XBRL tag, raw value, units, fiscal period
end, accession, filed date, and source URL; VZ's reported Total Debt now carries
its own accession and source URL; every snapshot also carries an explicit
human-readable normalization equation (e.g. `"Total Debt =
LongTermDebtNoncurrent ($44,086,000,000) + LongTermDebtCurrent ($2,499,000,000) =
$46,585,000,000"` for INTC). Market data (price/shares/beta/sector) and every
non-debt financial figure were deliberately carried forward UNCHANGED from the
existing snapshots (not re-fetched), specifically so this provenance-only fix could
not silently alter the base-case valuation as a side effect — confirmed by the
Total Debt USD values being bit-identical before and after this fix, and by every
DCF sheet's cached values remaining unchanged (§11.4).

`source_manifest.csv` was regenerated: 76 → 82 rows, adding one row per debt
component (2 components × 3 companies = 6 new rows) with full provenance, and
correcting the three existing derived-total rows (now carrying the normalization
equation instead of a bare tag string) and the VZ reported-total row (now carrying
its accession and source URL). `build_workbook.py`'s `build_sources_sheet` was
updated to display this per-component provenance (new "Source URL" column added to
the Sources sheet's table; Total Debt now shown as one row per component plus a
rollup row, instead of a single opaque row), and `build_inputs_sheet`'s Total Debt
note now points to the Sources sheet for full component provenance. `README.md`
updated to state "82 rows" and describe the new files; this document's §10 above is
retained as the historical pre-correction state rather than silently rewritten.

**Network activity for this fix**: zero new SEC EDGAR requests. `sec_raw_cache/`
was populated from already-downloaded company-facts JSON retrieved earlier in this
same session (the original Track A Phase 2A data pull) — reusing already-downloaded
public data, as explicitly authorized, rather than re-fetching. This version of
`build_snapshots.py` would fetch fresh from `data.sec.gov`/`www.sec.gov` if
`sec_raw_cache/` were absent or deleted; this run's console output confirms
`"New SEC EDGAR network requests made this run: none (used local sec_raw_cache/)"`.
**Superseded in §13**: this implicit "fetch automatically if the cache file is
missing" behavior, and this script's use of `datetime.now()` for a
"provenance_corrected_at_utc" content field, were both identified as defects in a
second review pass and replaced with an explicit three-mode command interface
(default verify-only / `--rebuild-from-cache` / `--refresh-sec`) with no implicit
network access anywhere and no wall-clock timestamp embedded in generated file
content — see §13 for the corrected design and why the implicit-fetch behavior
described in this paragraph was a problem worth fixing.

### 11.3 formula_audit.py — structural formula-text auditor

Added `formula_audit.py`, which inspects the raw `<f>` formula text inside the
generated `.xlsx` (via direct zip/XML parsing, decoding `&apos;`/`&quot;`/etc. XML
entities before comparison) — never cached `<v>` values — and asserts, for all four
companies:

1. The base `DCF_<TICKER>` Terminal Value formula references that sheet's own
   Year-5 FCF cell.
2. Every Table 1 (WACC sensitivity) row's Terminal Value formula references the
   base DCF sheet's Year-5 FCF cell.
3. Every Table 2 (terminal-growth sensitivity) row's Terminal Value formula
   references the same cell.
4. Every Table 5 (two-way grid) cell's embedded Terminal Value expression
   references the same cell.
5. None of the formulas in checks 1-4 references the Year-5 Revenue cell.
6. Table 3 (revenue-growth) and Table 4 (operating-margin) each reference their OWN
   local, same-row recomputed Year-5 FCF cell, not the base DCF sheet's fixed one.

**Negative-control verification that the audit script actually works** (not just
"looks like it should"): the original bug (`base_fcf5` pointed back at `rev_row`)
was deliberately reintroduced into a scratch copy of `build_workbook.py`, the
workbook was rebuilt from that buggy copy, and `formula_audit.py` was re-run against
it — it correctly reported 400 formula-text defects (60 base checks × multiple
failure reasons each, across all 4 companies × Tables 1/2/5), each explicitly
tagged `"DEFECT 1 REGRESSION"` where the Revenue cell was found. The correct
`build_workbook.py` was then restored from a backup and the real workbook rebuilt
again; `formula_audit.py` now exits 0 against it. This confirms the audit script
is a genuine detector of this defect class, not a check that would trivially pass
regardless of the underlying formulas.

### 11.4 Post-fix verification (full suite re-run)

All of §9's verification steps were re-run against the corrected, rebuilt workbook,
plus the two new checks below. Every check passed:

- XML well-formedness (0 errors, 18 sheets), zip integrity, no macros, no external
  links, no hidden sheets, no error-string tokens (`#REF!` etc. — 0 occurrences),
  1,620 formulas (unchanged count — the fix corrected existing cell references, it
  did not add or remove formulas).
- Every DCF sheet's cached Intrinsic Value/Share, Enterprise Value, and WACC still
  matches `shadow_calc.py` exactly for all 4 companies (bit-identical to before this
  correction pass, confirming the fix did not change the base-case valuation).
- `formula_audit.py` exits 0 (§11.3).
- Snapshot SHA-256 checksums verified against the regenerated `snapshot_manifest.json`
  — all 4 match (checksums necessarily changed from the pre-fix version, since the
  snapshot files themselves now contain additional provenance fields; this is
  expected and is what "regenerated" means).
- `source_manifest.csv` audit: 0 "reported" rows missing a `source_url`; MSFT/CAT/
  INTC's derived Total Debt values tie EXACTLY (bit-for-bit) to the sum of their
  own component rows in the manifest; VZ's reported Total Debt row carries both an
  accession and a source URL.
- `error_injection_test.py` re-run against the corrected workbook: still detects the
  same injected Terminal Value error at the same 25% divergence (the DCF sheet's own
  row layout was not touched by either fix, so this result is expected to be
  identical — confirmed identical), and still deletes its temporary copy.
- INTC's live Table 1 (WACC sensitivity) formulas were read directly out of the
  corrected file (not re-derived from shadow_calc) and hand-verified to still
  produce the previously-disclosed inverted direction (IVPS rises from -$8.13 at
  WACC=5% to -$6.45 at WACC=20%), now confirmed to be what the ACTUAL stored
  formula computes, not merely what an independently-correct cache happened to show.

**Still not performed, unchanged from §9**: no LibreOffice/Excel/Numbers is
available in this environment (unchanged from the original build), and installing
one remains out of scope. This correction pass does not claim any live spreadsheet
engine recalculated the workbook — every check above is either direct inspection of
the stored `<f>` formula text, or an independent Python re-derivation
(`shadow_calc.py`), never a claim of actual spreadsheet-engine recalculation.

## 12. Final state (after the Phase 2A correction pass)

- `independent_dcf_validation.xlsx` — 18 sheets, 1,620 formulas, no errors, no
  macros, no external links, no hidden sheets, `formula_audit.py`-clean.
- New files added this pass: `build_snapshots.py`, `formula_audit.py`,
  `sec_raw_cache/` (five files, ~19MB total: `company_tickers.json` plus four
  companies' cached SEC EDGAR company-facts JSON — corrected from an earlier
  "four cached JSON files" undercount that omitted `company_tickers.json`; see §13).
- Modified files this pass: `build_workbook.py` (both defect fixes),
  `snapshots/*.json` (enriched debt provenance, market data unchanged),
  `source_manifest.csv`, `snapshot_manifest.json`, `README.md`, this file,
  `error_injection_evidence.md` (re-verification note),
  `independent_dcf_validation.xlsx` (rebuilt).
- No file outside `validation/independent_dcf/` was created or modified.
- No `git add`, `git commit`, `git push`, or any repository-mutating command was run.

## 13. Second correction pass (independent review findings)

A second independent review of the workbook described in §12 confirmed the §11 fixes
held (FCF references correct, all 18 sheets render, checksums match, debt totals tie
to components), then found four further integrity issues, unrelated to the DCF
formula logic itself. All four were fixed in this same session, without beginning
codebase reconciliation.

### 13.1 Issue 1 — unverified personal email hard-coded in the SEC User-Agent

A personal email address had been hard-coded into `build_snapshots.py`'s SEC
User-Agent string and repeated in `workbook_build_log.md` §4. It has been removed
from both files and is not reproduced anywhere in this directory (verified — see
§13.5). No replacement address was guessed or substituted. Any future SEC EDGAR
refresh now requires the operator to supply their own contact string via the
`SEC_USER_AGENT` environment variable at run time; the script refuses to run
`--refresh-sec` if that variable is unset or empty, and never logs or prints its
value (confirmed — see §13.5).

### 13.2 Issue 2 — implicit network access and non-deterministic rebuild

**Root cause**: the `build_snapshots.py` version described in §11.2 fetched from
SEC EDGAR automatically whenever a cache file was absent (no explicit operator
action required), and stamped a `"provenance_corrected_at_utc": datetime.now(...)`
value into every generated snapshot's content — meaning two runs against identical
inputs never produced byte-identical output, defeating any later attempt to verify
"this is exactly what running the script produces."

**Fix**: `build_snapshots.py` was redesigned around three explicit, mutually
exclusive modes (see the file's own module docstring for full detail):

- **Default (no flag)** — read-only verification: checks `sec_raw_cache/` against
  a new SHA-256+size checksum manifest (`sec_raw_cache/cache_manifest.json`,
  covering all five raw files), checks every snapshot parses and its checksum
  matches `snapshot_manifest.json`, checks every derived Total Debt ties to its
  components, and scans for hard-coded personal contact strings. Writes nothing.
  Opens no network connection.
- **`--rebuild-from-cache`** — offline, deterministic regeneration of
  `snapshots/*.json`, `source_manifest.csv`, and `snapshot_manifest.json` from
  `sec_raw_cache/` only. Refuses to run if the cache does not match
  `cache_manifest.json`, or if any of the four existing per-company snapshot files
  (needed to carry forward already-frozen market data) is missing. Writes are
  atomic (temp file in the same directory + `os.replace`). No wall-clock value is
  embedded in any generated file's content — the design choice made here is that a
  "when was this regenerated" timestamp is fundamentally incompatible with
  byte-identical repeated output, so it is printed to the console only, never
  written into a file (see the module docstring's "Determinism design" section).
- **`--refresh-sec`** — the only mode that calls `urllib.request.urlopen`. Refuses
  before any network access if `SEC_USER_AGENT` is unset/empty. Prints which five
  cache files it would replace before doing so. Downloads to a temp file, validates
  the JSON parses, and only then atomically replaces the cache file; on any failure
  the existing cache file is left untouched, and `cache_manifest.json` is only
  regenerated once every file has been refreshed successfully. **Not invoked at any
  point during this correction pass** — confirmed by zero network requests in every
  command run (see §13.5).

**Determinism proof** (this session's actual run, not a claim): SHA-256 of all four
snapshots + `source_manifest.csv` + `snapshot_manifest.json` was recorded before any
change, `--rebuild-from-cache` was run, hashes recorded again, `--rebuild-from-cache`
was run a second time, hashes recorded a third time. The second and third hash sets
were byte-for-byte identical for all six files; the first (pre-fix) hash set
legitimately differs from the other two, because the file CONTENT genuinely changed
(the new joined debt-rollup provenance fields — §13.4 — plus switching JSON
serialization to `sort_keys=True` for reproducibility). This is the expected,
intended difference, not a determinism failure.

**Documentation corrections** (Issue 2, points 5-6): the "four cached JSON files"
undercount in §12 above is corrected there directly (it always should have said
five — `company_tickers.json` was cached alongside the four company-facts files
from the start; this was a documentation error, not a change in what was cached).
`README.md`'s description of `sec_raw_cache/` and `build_snapshots.py` was rewritten
to state the three-mode design precisely, and to state explicitly that market data
is always carried forward frozen from the existing snapshots and never re-fetched
from Yahoo Finance/yfinance in any mode — this boundary was already accurate in the
code (§11.2 already documented it correctly) but is now stated with the same
precision in `README.md`.

### 13.3 Issue 3 — formula_audit.py was not mechanically independent

**Root cause**: the §11.3 version of `formula_audit.py` imported `build_workbook`,
`shadow_calc`, and `xlsx_lite`, and called `build_workbook`'s sheet-builder
functions to obtain the exact row numbers to check. This meant the audit and the
workbook it was checking were produced by the same code path — a defect shared by
both (e.g. in how `build_dcf_sheet` computes row positions) could in principle
affect the workbook and the audit's idea of "where to look" identically, which
undermines the point of an independent check.

**Fix**: `formula_audit.py` was rewritten from scratch to import nothing from this
project's own code — only `os`, `re`, `shutil`, `sys`, `tempfile`, `zipfile` (all
standard library). It resolves sheet names via `xl/_rels/workbook.xml.rels` (rather
than assuming relationship-ID order matches `sheetN.xml` numbering — a real
distinction, since OOXML relationship IDs are not required to correspond 1:1 with
worksheet part numbers, even though this particular writer happens to allocate them
that way), and locates every cell it checks by reading the workbook's own printed
row labels ("Revenue = Revenue_(t-1)...", "Unlevered FCF = NOPAT...", "Terminal
Value = FCF_Y5...") and column headers ("Year 5", "Terminal Value", "FCF Y5") —
the same information a human reviewer opening the file would use, not markers
supplied by the generating code. It accepts an optional workbook path argument
(`python3 formula_audit.py /path/to/other.xlsx`) so a temporary negative-control
copy can be audited without touching the real file.

**Self-test** (`python3 formula_audit.py --self-test`): copies the real workbook to
a temp file inside a fresh `tempfile.mkdtemp()` directory, locates one Table 1 (WACC
sensitivity) Terminal Value formula's Year-5 FCF reference purely by re-deriving it
from labels (the same discovery logic the audit itself uses), replaces it with the
Year-5 Revenue reference (reintroducing exactly the class of defect fixed in §11.1),
re-audits the corrupted copy, and asserts both that it fails AND that it specifically
identifies the corrupted cell (`Sensitivity_MSFT!B6` in this run). Cleanup runs in a
`finally` block so the temp file is removed even if an assertion or exception fires
partway through. This session's actual run: the real workbook audited PASS, the
corrupted copy audited FAIL with `Sensitivity_MSFT!B6` specifically identified, and
the temp file was confirmed deleted afterward. The real, unmodified workbook was
never touched by this test.

### 13.4 Issue 4 — derived Total Debt rollup rows had blank/repeated provenance

**Root cause**: `source_manifest.csv`'s per-component debt rows (added in §11.2)
were complete, but the rollup row summarizing the derived total for MSFT/CAT/INTC
left `accession_number` and `filed_or_retrieved_date` blank and repeated the
identical `source_url` once per component (both components of a given company's
debt come from the same 10-K filing, so the join was literally duplicating one URL).

**Fix**: `build_snapshots.py`'s `extract_company()` now computes
`debt_rollup_accession_joined`, `debt_rollup_source_url_joined`, and
`debt_rollup_filed_date_joined` — each built by de-duplicating the relevant field
across all of that company's debt components (preserving first-seen order) and
joining with `"; "`. Because every component in this dataset happens to share a
single filing, each joined field currently collapses to exactly one value per
company (verified — see §13.5); the join logic itself handles the general case of
components drawn from different filings correctly, should that ever occur for a
future company. `build_workbook.py`'s `build_sources_sheet` was updated to display
these joined values (and the filed date, in the Provenance-column text) instead of
the previous literal placeholder text `"see components above"`.

### 13.5 Post-fix verification (this session's actual command output)

- `git branch --show-current` / `git rev-parse HEAD` / `git status --short`:
  matched the expected checkpoint exactly before starting, and again at the end
  (only `validation/` untracked; nothing staged).
- A case-insensitive scan for the removed personal-contact string (both its exact
  form and its email domain) across every `.py`/`.md`/`.csv`/`.json` file in
  `validation/independent_dcf/` (excluding `sec_raw_cache/`, which is unmodified raw
  third-party SEC data): zero matches. (This bullet deliberately does not spell out
  the search pattern itself, to avoid the file self-matching a literal reproduction
  of it — the same reasoning applied to `build_snapshots.py`'s own leak-scan code.)
- `python3 build_snapshots.py` (default, verify-only): confirmed PASS on all five
  checks after the fixes, confirmed it writes no file (checksums identical before
  and after running it) and opens no network connection.
- `python3 build_snapshots.py --rebuild-from-cache` run twice in sequence: SHA-256 of
  all four snapshots + `source_manifest.csv` + `snapshot_manifest.json` identical
  between the two runs (byte-for-byte).
- `sec_raw_cache/cache_manifest.json` verified against the five cache files on disk:
  all five SHA-256 + byte-size pairs match.
- `python3 formula_audit.py` (real workbook): PASS, 0 defects, all four companies.
- `python3 formula_audit.py --self-test` (negative control): real workbook PASS;
  corrupted temp copy FAIL, `Sensitivity_MSFT!B6` specifically identified; temp file
  confirmed deleted.
- `python3 error_injection_test.py`: unchanged result from §9/§11.4 (25% Terminal
  Value divergence detected against a 0.2% tolerance, all downstream values
  flagged), temp copy deleted and confirmed gone.
- Workbook rebuilt after the Sources-sheet fix (§13.4); re-ran the full structural
  suite: 0 XML errors, 18 sheets, 1,620 formulas (unchanged — this pass corrected
  text/reference content, it did not add or remove formula cells), 0 macros, 0
  external links, 0 hidden sheets, 0 error-string tokens, zip integrity clean.
- Snapshot SHA-256 checksums re-verified against the regenerated
  `snapshot_manifest.json`: all four match.
- `source_manifest.csv` rollup-provenance audit: MSFT/CAT/INTC's derived Total Debt
  rollup rows now carry a non-empty `accession_number`, `source_url`, and
  `filed_or_retrieved_date`, and their component sums still tie exactly to the
  rollup value; VZ's reported Total Debt row remains complete.
- External hostnames contacted this pass: **none**. Every command run was either
  pure computation, filesystem I/O within `validation/independent_dcf/`, or explicit
  offline verification against `sec_raw_cache/`; `--refresh-sec` was never invoked.
- Nothing was staged or committed at any point.

## 14. Final state (after the second correction pass)

- `independent_dcf_validation.xlsx` — 18 sheets, 1,620 formulas, no errors, no
  macros, no external links, no hidden sheets, both `formula_audit.py` (real +
  self-test) and `error_injection_test.py` clean.
- New files this pass: `sec_raw_cache/cache_manifest.json`.
- Rewritten files this pass: `build_snapshots.py` (three-mode redesign, email
  removed), `formula_audit.py` (mechanically independent rewrite).
- Modified files this pass: `build_workbook.py` (Sources-sheet rollup-provenance
  display), `snapshots/*.json` and `source_manifest.csv` and
  `snapshot_manifest.json` (regenerated via `--rebuild-from-cache`, joined rollup
  provenance populated), `README.md`, this file, `independent_dcf_validation.xlsx`
  (rebuilt).
- `sec_raw_cache/` (the raw SEC EDGAR cache) was preserved, not deleted, per this
  pass's explicit instructions.
- No file outside `validation/independent_dcf/` was created or modified.
- No `git add`, `git commit`, `git push`, or any repository-mutating command was run.

## 15. Third correction pass — Table 5 audit blindness, provenance/atomicity/PII hardening

A third independent review confirmed the §13 fixes held, then found five further
issues: (1) the formula audit's own Table 5 check silently examined zero cells and
still printed PASS; (2) 16 filed dates were missing from `source_manifest.csv`;
(3) `--refresh-sec` could leave a mixed/partially-replaced cache on a mid-run
failure; (4) the PII scanner exempted its own source file, the exact file that had
previously contained the leaked address; (5) `--rebuild-from-cache`'s per-file
atomic writes were not a genuine all-or-nothing group publish. All five were fixed.

### 15.1 Issue 1 — the Table 5 audit examined nothing and still reported success

**Root cause**: `formula_audit.py`'s regex-based cell parser only populated a
`text` field from inline-string cells. Table 5's row/column headers are plain
NUMERIC cells (`<v>0.05</v>`, no `<f>`, no `<is>`) — parsed as entirely empty by
that reader. The header-discovery loop therefore found zero g-columns and zero
WACC-rows, the per-cell verification loop never executed, and — because the
script only ever *appended* to a `failures` list and printed PASS when that list
was empty — it printed PASS having examined 0 of the 144 Table 5 cells that
actually needed checking, for every company.

**Fix**: `formula_audit.py` was rewritten to parse cells with `xml.etree.ElementTree`
instead of regular expressions, explicitly handling every OOXML cell kind:
numeric (`<v>` with no `t=` or `t="n"`), shared string (`t="s"`, resolved via
`xl/sharedStrings.xml`), inline string (`t="inlineStr"`), formula (`<f>` present,
with its cached `<v>` also captured), and boolean (`t="b"`). Sheet-name resolution
now reads `xl/workbook.xml` + `xl/_rels/workbook.xml.rels` as XML trees (no
positional rId-equals-sheetN assumption). The Table 5 check
(`check_table5()`) now explicitly REQUIRES exactly 6 numeric g-headers, exactly 6
numeric WACC-headers, and exactly 36 formula cells at their intersection for each
company (144 total), verifies each cell's WACC/g references match its own
row/column header cell (not some other row/column's), and **prints the exact
count of cells examined for every check category** — a PASS is refused unless
Table 5 coverage is exactly 144/144, so this specific defect class (examining
nothing, reporting success) cannot recur silently. Verified this session:
`Table 5 (two-way grid) formulas checked: 144 (expected 144 = 36 per company x 4
companies)`.

**Self-test extended**: `--self-test` now runs two Table 5 negative controls
against temporary copies (never the real file): (a) corrupts `Sensitivity_MSFT!B55`
(the first Table 5 grid cell)'s FCF reference back to the Revenue reference —
audit correctly fails and identifies `B55` specifically; (b) deletes the `<f>`
element from `Sensitivity_MSFT!B55` entirely, leaving only its cached value —
audit correctly fails, reports the cell as `(missing)`, and reports Table 5
coverage as `143/144` (proving a missing formula is treated as a failure, not
silently skipped, and that the coverage count itself degrades correctly). Both
temp copies are deleted in a `finally` block; the real workbook is never touched
by either test.

### 15.2 Issue 2 — 16 missing SEC filed dates

**Root cause**: `generate_snapshot()` never copied the `filed` field (already
present in every fact extracted from SEC EDGAR) into `latest_year_facts` for
Pretax Income, Tax Provision, Interest Expense, or Cash & Equivalents, and
`build_manifest_rows()` hardcoded `None` for those four rows' filed date — for
all 4 companies (4 fields x 4 companies = 16 rows).

**Fix**: added `pretax_income_filed_date`, `tax_provision_filed_date`,
`interest_expense_filed_date`, `cash_filed_date` to the snapshot's
`latest_year_facts`, sourced from the same already-extracted SEC data (no
refetch), and wired them into the manifest-row builder. Verified this session:
0 of 82 `source_manifest.csv` rows now have a blank `filed_or_retrieved_date`.

**New manifest-completeness verifier** (`verify_manifest_completeness()`),
required by this same issue: classifies every row as `reported_fact` (a directly
reported SEC fact or debt component — requires nonempty accession, source URL,
filed date, XBRL tag, and provenance classification), `derived_rollup` (a summed
Total Debt total — requires nonempty deduplicated-joined accession/URL/filed-date
lists and a provenance string that explicitly says "derived"), or `market`
(price/shares/beta/sector — requires a nonempty retrieval timestamp, source URL,
and a source field starting with `"yfinance"`). Failures report the exact
`(ticker, field, column)` that is empty, never a generic message. Wired into
`verify()` as step `[5/6]`. A negative-control self-test
(`run_manifest_completeness_self_test()`) blanks one real field
(`[MSFT] "Pretax Income (latest)"`'s filed date) in a temporary CSV copy and
confirms the verifier identifies that exact row/column; the real
`source_manifest.csv` is confirmed byte-identical before and after.

**Sources sheet**: `build_workbook.py`'s `build_sources_sheet` gained a visible
"Filed / Retrieved Date" column (between Filing Accession and Provenance) so a
reviewer does not need to open the CSV to see when each fact was filed or
retrieved.

### 15.3 Issue 3 — `--refresh-sec` could leave a mixed cache on partial failure

**Root cause**: the §13 version replaced cache files one at a time
(`safe_replace_one` per file); if a later download failed, earlier files were
already live while `cache_manifest.json` still described the OLD generation —
an inconsistent, unverifiable mixed state, and the earlier known-good cache for
the failed file(s) was already gone.

**Fix**: replaced with `staged_refresh()` — every download is fetched AND
validated (JSON-parseable; for `companyfacts_<TICKER>.json`, its `cik` must match
the CIK `company_tickers.json` itself reports for that ticker, `entityName` must
be non-empty, and `facts.us-gaap` must be a non-empty object) into an isolated
staging directory on the same filesystem; nothing under the live `sec_raw_cache/`
is touched until every download and validation has succeeded. The publish step is
exactly two `os.rename()` calls (live → backup, staging → live); a failure at any
point restores the backup before returning. This module's docstring now states
precisely what is and is not guaranteed: process-level failures (exceptions) are
provably recovered; a true OS-level crash mid-rename is not, and that limitation
is stated explicitly rather than overclaimed. `write_cache_manifest()`'s CLI
bypass (`--write-cache-manifest`) now refuses once `cache_manifest.json` already
exists — bootstrap is complete, and only a successful `--refresh-sec` may replace
the trusted manifest going forward.

**Fault-injection self-test** (`run_refresh_fault_injection_self_test()`, entirely
offline, stubbed fetch functions, operating on a temporary COPY of the cache —
the real `sec_raw_cache/` is only ever read from, never passed to
`staged_refresh` as its target directory): 11 negative scenarios (a network
failure at each of the 5 download positions; malformed JSON for 2 of them; a CIK
mismatch; a missing `entityName`; empty `facts.us-gaap`; a failure injected
between the two publish renames) plus 1 positive control, each verified by
hashing the entire temp cache directory before and after — unchanged on every
failure, changed only on the positive control. Verified this session: all 11 + 1
passed; the real `sec_raw_cache/`'s own hash was also confirmed unchanged across
the entire test run. `--refresh-sec` itself was never invoked against the real
cache during this correction pass.

### 15.4 Issue 4 — the PII scanner exempted the one file that had leaked PII

**Root cause**: the §13 scanner searched only for the literal substring
`"gmail.com"` and explicitly skipped its own source file
(`if os.path.abspath(path) == this_file: continue`) — meaning a recurrence of a
hard-coded email specifically in `build_snapshots.py` (the exact file that had
contained one) would never be caught.

**Fix**: replaced with a generic email-address regex
(`[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`), no self-exemption for any
file, and extended to inspect the generated `.xlsx`'s own internal strings
(`xl/sharedStrings.xml`, every `xl/worksheets/sheetN.xml`'s cell text,
`docProps/core.xml`, `docProps/app.xml`) in addition to every `.py`/`.md`/`.csv`/
`.json` file under `validation/independent_dcf/`. `sec_raw_cache/` (unmodified
third-party SEC data) is explicitly excluded and that exclusion is stated in the
scan's own output (`scope_note`), never silently applied or described as "the
entire package." The only allowlisted domains are `example.com`/`.net`/`.org`
(IANA/RFC 2606-reserved for documentation and testing) — narrowly scoped to this
scanner's own self-test fixture, not gmail.com or any other real domain.

**Regression self-test** (`run_pii_regression_self_test()`): copies
`build_snapshots.py` into a temporary directory, appends a synthetic injected
address (built from string parts at runtime so the REAL source file never
contains the contiguous pattern and doesn't poison its own "real tree is clean"
baseline check — the same self-referential trap this fix is closing), re-scans
the temp copy, confirms the scanner fails and identifies `build_snapshots.py`
specifically, deletes the temp copy, and confirms the real file is byte-for-byte
unchanged afterward. Verified this session: real tree scans clean (0 matches,
including this file's own source); temp copy correctly flagged; real file
confirmed unchanged.

### 15.5 Issue 5 — `--rebuild-from-cache`'s per-file atomicity was not group atomicity

**Root cause**: each of the 4 outputs (4 snapshots as a set, `source_manifest.csv`,
`snapshot_manifest.json`) was written atomically INDIVIDUALLY (temp file +
`os.replace`), and the workbook was rebuilt as a separate step entirely outside
this pipeline. A failure between any two of those individual writes — or between
the manifest write and the (separate, unvalidated) workbook rebuild — could leave
a live output set that was internally inconsistent, and nothing validated the
generated workbook before it became the delivered artifact.

**Fix**: `rebuild_from_cache()` now runs three explicit phases. **Phase 1**
generates all four outputs — including building `independent_dcf_validation.xlsx`
itself, via `build_workbook.build_workbook(staged_xlsx_path, snap_dir=staged_snap_dir)`
(both now accept an explicit snapshot-directory override for exactly this
purpose) — entirely inside an isolated staging directory; nothing live is
touched. **Phase 2** validates the ENTIRE staged generation before anything is
published: snapshot schema/invariants (required top-level and `latest_year_facts`
keys present), `source_manifest.csv` completeness (the same
`verify_manifest_completeness()` from §15.2), cross-artifact checksum consistency
(staged `snapshot_manifest.json` vs. the staged snapshot files it describes), a
full independent formula audit of the STAGED workbook (via `formula_audit.py`,
imported and run against the staged `.xlsx` path — requiring, among everything
else, the full 144/144 Table 5 coverage from §15.1), and an Excel error-token
scan of the staged workbook. Any failure aborts before touching a single live
file. **Phase 3** publishes all four outputs as one group: every live slot is
backed up first, then every staged slot is promoted in order; a failure at ANY
point rolls back ALL four slots to their exact pre-publish state.

This module now states its atomicity guarantee precisely rather than overclaiming
it (see the `ATOMICITY / RECOVERY GUARANTEES` comment directly above
`OUTPUT_SLOTS` in `build_snapshots.py`): a single `os.rename()` of one directory
is OS-level atomic (used for `sec_raw_cache/` in §15.3); publishing four
independent filesystem objects together has no equivalent single OS primitive, so
this code implements an ordered rename-with-software-rollback sequence instead,
proven to recover from any process-level failure (a raised exception at any of
the four promotion positions) but explicitly NOT claimed to survive a hard OS
crash mid-sequence — that would require filesystem journaling this script does
not implement, and the limitation is stated rather than left implicit.

**Fault-injection self-test** (`run_publish_fault_injection_self_test()`,
entirely inside a temporary directory with four SYNTHETIC output slots — the
real live outputs are never referenced): injects a failure at each of the 4
promotion positions and, for each, hashes the synthetic "live" generation before
and after, confirming it is byte-for-byte unchanged and that no
`.rollback_backup` artifact is left behind; a positive control with no injected
fault confirms the synthetic live generation DOES change to the new content when
nothing fails (proving the negative scenarios fail because of the injected
fault, not because the machinery never works). Verified this session: all 4
negative scenarios + 1 positive control passed.

**A genuine determinism gap found and fixed while proving this end-to-end**:
`xlsx_lite.py`'s `Workbook.save()` embedded `datetime.now(timezone.utc)` into
`docProps/core.xml`'s `dcterms:created`/`dcterms:modified` fields, and
`zipfile.ZipFile.writestr(name_as_str, data)` stamps each zip entry with the
current wall-clock time by default — both would have made the generated `.xlsx`'s
raw bytes differ between two runs even given byte-identical XML content on every
part, which was never exercised by the pre-existing determinism proof (§13.2's
proof only covered the 3 non-xlsx outputs). Fixed by removing the
`dcterms:created`/`modified` fields entirely (optional in OOXML) and by writing
every zip entry via an explicit `ZipInfo` with a fixed `date_time=(1980,1,1,0,0,0)`.

### 15.6 Post-fix determinism proof (extended to all four outputs, including the workbook)

- `python3 build_snapshots.py` (verify, default): PASS on all 6 checks, 0 writes,
  0 network connections.
- `python3 build_snapshots.py --rebuild-from-cache` run twice in the live
  directory: SHA-256 of all 4 snapshots + `source_manifest.csv` +
  `snapshot_manifest.json` + `independent_dcf_validation.xlsx` — **byte-for-byte
  identical**, including the workbook, for the first time this pass (the prior
  determinism proof in §13.2 did not cover the `.xlsx`, which would have failed
  it before the fix described above).
- A THIRD rebuild, run inside a completely separate temporary copy of the whole
  `validation/independent_dcf/` directory (a fresh `cp -R`, its own filesystem
  location, its own process): produced output byte-for-byte identical to the live
  directory's own rebuilt output, for all 7 files. This directly satisfies the
  "second offline rebuild in a separate temporary copy" requirement — determinism
  holds across genuinely independent locations, not just repeated runs in place.
- A visible layout defect was found and fixed during this pass's "visually
  inspect all sheets" step (no spreadsheet GUI is available, so this was an
  automated proxy: declared column width vs. actual cell-text length, accounting
  for merged-cell spans and Excel's overflow-into-blank-neighbor rule, cross-
  checked against each cell's `wrapText` style): the four "Direction check: ..."
  labels in every `Sensitivity_<TICKER>` sheet (rows preceding each sensitivity
  table's summary) sat in an unmerged, non-wrapped, 14-character-wide column A
  immediately next to a non-empty PASS/FAIL cell in column B — Excel's normal
  text-overflow-into-a-blank-neighbor rendering could not apply, so these labels
  (44-69 characters) would have visibly clipped. Fixed by switching these four
  cells to the `wrap` style and setting an explicit 45pt row height; re-verified
  by both the automated proxy check (0 issues remaining) and direct inspection of
  the generated XML (`wrapText="1"` confirmed on the applicable style,
  `customHeight="1"` confirmed on the row).

## 16. Final state (after the third correction pass)

- `independent_dcf_validation.xlsx` — 18 sheets, 1,620 formulas, 0 XML errors, 0
  macros, 0 external links, 0 hidden sheets, 0 error-string tokens, zip-integrity
  clean, formula-audit-clean including full 144/144 Table 5 coverage, 0 layout
  issues per the automated visual-inspection proxy.
- New files this pass: none (all changes were to existing files' logic/content).
- Rewritten (substantially) this pass: `formula_audit.py` (ElementTree parser,
  Table 5 fix, extended self-test), `build_snapshots.py` (manifest-completeness
  verifier, staged `--refresh-sec`, staged group-publish `--rebuild-from-cache`,
  rewritten PII scanner, four new self-test suites), `xlsx_lite.py`
  (determinism fix).
- Modified this pass: `build_workbook.py` (Sources-sheet Filed/Retrieved Date
  column, Direction-check label layout fix, `load_snapshots`/`build_workbook`
  snapshot-directory override), `snapshots/*.json`, `source_manifest.csv`,
  `snapshot_manifest.json`, `independent_dcf_validation.xlsx` (all four
  regenerated via the new staged transaction), this file.
- `sec_raw_cache/` preserved; `--refresh-sec` was never invoked against it this
  pass; all refresh/publish testing used stubbed fetchers and/or temporary
  directories, never the real live cache or live output set.
- No file outside `validation/independent_dcf/` was created or modified.
- No `git add`, `git commit`, `git push`, or any repository-mutating command was run.

## 17. Fourth correction pass — the P0 rollback defect and expanded transaction/validation hardening

An independent review reproduced a real, destructive bug in Pass 3's own
`_publish_staged_generation()` and identified several gaps in the staged-
validation coverage and documentation. Everything below was fixed and
independently re-verified in this pass; nothing here changed any DCF/WACC
financial formula.

### 17.1 The P0 defect — a backup-phase failure deleted unbacked live outputs

**Reproduction:** injecting a failure while backing up the *second* of the
four output slots (`source_manifest.csv`) caused `_publish_staged_generation()`
to return `False` having restored only the first slot — the second, third, and
fourth slots' ORIGINAL live content was deleted outright, with nothing left to
restore them from.

**Root cause:** the old rollback path was:
```python
for name, live_path, is_dir in slots:
    if os.path.exists(live_path):
        <delete it>
    if name in backups and os.path.exists(backups[name]):
        <restore from backup>
```
This deletes every live path that *still exists*, regardless of whether this
function ever backed it up. When the backup loop fails partway through, every
slot after the failure point still has its ORIGINAL content sitting at
`live_path` (untouched, because its backup rename never happened) — and this
loop deletes it anyway, then finds no backup to restore because there isn't
one.

**Fix:** `_publish_staged_generation()` was rewritten around an explicit
per-slot state machine tracked in a Python dict (`pending → backed_up →
promoted`), never inferred from what happens to exist on disk. Rollback now
restores only slots in state `backed_up`/`promoted` and leaves every `pending`
slot completely untouched. A unique transaction-specific backup directory
(`.publish_txn_<random>/`) replaces the old fixed `.rollback_backup` suffix,
and a preflight check refuses to start — without deleting anything — if a
leftover transaction directory from a prior interrupted run is found. Once
every slot reaches `promoted` the transaction is treated as committed: a
failure removing the now-obsolete backup directory is reported as success
with a warning, never as "publish failed," since the new generation is
already live and correct. The identical design was applied to
`staged_refresh()`'s single-directory case (`_publish_refresh_transaction()`),
replacing its own blind `if os.path.isdir(backup_dir): shutil.rmtree(backup_dir)`
pre-existing-backup deletion with the same residue-refusal preflight check.

**Verification:** an 11-scenario fault-injection matrix for the 4-slot
publish (4 backup-phase positions + 4 promotion-phase positions + 1
post-commit cleanup failure + 1 pre-existing-residue refusal + 1 positive
control) and a 16-scenario matrix for the single-directory refresh (the
original 11 fetch/validation scenarios + backup-failure + cleanup-failure +
residue-refusal + positive control + real-cache-untouched check), each
asserting the live output set's hash before and after. All scenarios PASS —
see the verification output reproduced in §17.4 below. Every fault-injection
run operates against synthetic temp directories or a temp copy of the cache;
the real live outputs and the real `sec_raw_cache/` were never passed as the
mutated target during any test.

### 17.2 Staged-validation vacuous-pass bug and structural coverage gaps

**Bug found:** `snapshot_manifest.json`'s checksum check only iterated
whatever entries were present in its `files` mapping:
```python
for relpath, info in staged_manifest["files"].items():
    ...
```
An *empty* `files` mapping iterates zero times and reports zero problems —
this check would PASS a manifest that lists nothing at all. Replaced with
`verify_snapshot_manifest()`, which first requires exactly the 4 expected
`snapshots/<TICKER>_snapshot.json` entries (no more, no fewer) before
checking any checksum, and explicitly fails an empty mapping by name.

**New structural checks added** (complementary to, not a replacement for, the
existing field-level `verify_manifest_completeness()`):
- `verify_snapshots_directory()` — exactly the 4 required ticker files present
  (no missing/extra/duplicate), each snapshot's internal `ticker` field
  matches its filename, and a deeper schema/invariant check (not just "does
  it parse") covering required latest-fact provenance fields and required
  market-data fields.
- `verify_source_manifest_structure()` — the exact expected ROW SET per
  company (history-row counts matching `historical_annual_data` length,
  exactly one of each latest-fact row, exactly one of each market field,
  exactly one total-debt row derived XOR reported), a recheck of the derived
  Total Debt total against its component rows, reconciliation of key scalar
  values back to the staged snapshots, and — for derived rollups — dedup/
  no-blank-element checks on the joined accession/URL/filed-date lists that
  correctly account for components legitimately sharing one filing (the
  existing `dedupe_preserve_order()` already collapses identical values from
  components filed together; the first version of this check wrongly
  required "one joined element per component row" and failed the REAL,
  correct manifest — caught and fixed before this pass's checks were trusted,
  by replacing the count check with a set-correspondence check against each
  component row's own provenance values).

**Negative controls** (all in-memory against temp copies or in-memory row
lists; the real `snapshot_manifest.json`/`source_manifest.csv` on disk are
never modified): empty snapshot-manifest `files` mapping, one missing
required manifest entry, one unexpected extra manifest entry, a broken
debt-component total, a duplicate required source-manifest row, a missing
required source-manifest row, and a duplicated provenance element in a
derived rollup — seven scenarios, each asserting the exact defect is named,
not just that the check fails. All PASS.

### 17.3 Documentation accuracy and a genuine layout defect

- `build_snapshots.py`'s module docstring and `README.md`'s "Phase 2A
  correction passes" section were corrected: `--rebuild-from-cache` now
  explicitly documents that it also builds and publishes the workbook as
  part of the group transaction (the docstring previously described only
  snapshots/manifests with "atomic writes," which was stale after Pass 3);
  every atomicity/crash-recovery claim was re-stated to distinguish
  validation-before-publication, controlled-exception rollback, post-commit
  cleanup, and (explicitly, as NOT guaranteed) crash recovery against
  `kill -9` or power loss, which would require a durable journal this code
  does not implement.
- A genuine visual-layout defect was found in the README sheet's "Companies
  validated" table: the "Profile" column (20-char declared width) clipped
  against its non-empty "Revenue CAGR" neighbor for the longer profile
  strings (up to 49 characters). Fixed by widening the column to 32,
  applying the wrap style to both the header and data cells, and setting an
  explicit 30pt row height — verified by direct inspection of the rebuilt
  workbook's raw XML (`wrapText="1"` confirmed on the resolved style index
  for both the header cell and all four data rows; column width confirmed as
  `32` in `<cols>`). No spreadsheet-rendering engine (LibreOffice/Excel) is
  available in this environment, so this remains XML-level verification, not
  a rendered screenshot — stated explicitly, not overclaimed as a rendered
  visual check.

### 17.4 Post-fix verification (this session's actual command output)

In the required order: read-only checkpoint (branch/HEAD/clean tree
confirmed unchanged before any edit) → implementation → the expanded 11-
scenario publish fault matrix (`ALL 11 SCENARIOS PASSED`) → the expanded
16-scenario refresh fault matrix (`ALL SCENARIOS PASSED`) → the 7 new
staged-validation negative controls, both categories (`ALL SCENARIOS PASSED`
for snapshot-manifest-structure and source-manifest-structure) — all run and
passing BEFORE the real `--rebuild-from-cache` was invoked. Only then:
`--rebuild-from-cache` (staged, validated, published — `Rebuild published
successfully`) → default `verify()` (`=== Overall: PASS ===`, all 6 checks)
→ `formula_audit.py` (`PASS`, 144/144 Table 5 cells, per-table counts:
base TV=4, Table1=28, Table2=28, Table3=32, Table4=28, Table5=144) →
`formula_audit.py --self-test` (both Table 5 negative controls PASS,
B55 identified exactly each time) → `build_snapshots.py --self-test`
(`manifest-completeness=PASS, PII-scanner=PASS, publish-fault-injection=PASS,
refresh-fault-injection=PASS, snapshot-manifest-structure=PASS,
source-manifest-structure=PASS`) → `error_injection_test.py` (unchanged: 25%
Terminal Value divergence detected, all 4 downstream values flagged FAIL) →
determinism re-proved across two in-place `--rebuild-from-cache` runs AND a
third rebuild in a genuinely separate `cp -R` temporary copy of the whole
directory (all byte-identical, including the xlsx) → structural checks (18
sheets, 1,620 formulas, 0 error tokens, 0 macros, 0 external links, zip
integrity clean) → Sources sheet's "Filed / Retrieved Date" column confirmed
present in the rebuilt workbook's XML → visual-layout proxy re-run (0 issues)
→ zero external network requests at any point (all refresh/publish testing
used stubs, temp copies, or the read-only cache-manifest check) → final `git
status` (only `validation/` untracked, nothing staged, branch/HEAD unchanged
from the checkpoint).

## 18. Final state (after the fourth correction pass)

- `independent_dcf_validation.xlsx` — 18 sheets, 1,620 formulas, 0 XML errors,
  0 macros, 0 external links, 0 hidden sheets, 0 error-string tokens,
  zip-integrity clean, formula-audit-clean including full 144/144 Table 5
  coverage, README company-table layout defect fixed and XML-verified.
- New files this pass: none.
- Rewritten (substantially) this pass: `build_snapshots.py`
  (`_publish_staged_generation()` and `staged_refresh()`'s publish phase
  around an explicit per-slot state machine and unique transaction
  directories; `verify_snapshot_manifest()`, `verify_snapshots_directory()`,
  `verify_source_manifest_structure()`; two new negative-control self-test
  suites; expanded fault-injection matrices; corrected module docstring).
- Modified this pass: `build_workbook.py` (README company-table wrap/width/
  height fix, new `header_wrap` style), `README.md` (Pass 4 summary,
  corrected `--rebuild-from-cache` description), `snapshots/*.json`,
  `source_manifest.csv`, `snapshot_manifest.json`,
  `independent_dcf_validation.xlsx` (all four regenerated via the corrected
  staged transaction), this file.
- `sec_raw_cache/` preserved; `--refresh-sec` was never invoked this pass;
  all refresh/publish fault-injection testing used stubbed fetchers and/or
  temporary/synthetic directories, never the real live cache or live output
  set (confirmed by hashing the real cache before and after the full
  self-test run).
- No file outside `validation/independent_dcf/` was created or modified.
- No `git add`, `git commit`, `git push`, or any repository-mutating command
  was run.

## 19. Fifth correction pass — the missing-original KeyError and reporting/documentation hardening

An independent review found that Pass 4's own fix to `_publish_staged_generation()`
still had a bug: it did not handle a slot that had NO live original before the
transaction. Fixed and independently re-verified in this pass; nothing here
changed any DCF/WACC financial formula, and the rebuilt workbook this pass is
byte-for-byte identical to the one at the start of this pass.

### 19.1 Root cause and state-model fix

**Reproduction:** a slot with no live original is never backed up (correctly
-- there is nothing to back up), so it never gets an entry in `backup_paths`.
If that slot's staged content is later promoted, and a DIFFERENT, later slot's
promotion then fails, `restore_all_touched()` unconditionally evaluated
`backup_paths[name]` for every `'promoted'` slot -- raising `KeyError` for the
one with no original. That exception escaped `restore_all_touched()` mid-loop,
aborting rollback for every slot processed after it and leaving both
transaction residue and unrestored live outputs behind.

**Fix:** added a second, independent per-slot fact, `had_original[name]`,
recorded once at backup time (`True` only if a live-to-backup rename actually
happened) and never confused with `state[name]`'s `pending -> backed_up ->
promoted` lifecycle. `restore_all_touched()` now branches explicitly:
- `promoted` + `had_original`: remove the promoted output, restore its backup.
- `promoted` + NOT `had_original`: remove the promoted output, leave the slot
  absent -- `backup_paths` is never indexed for this case.
- `backed_up` (not promoted): restore its backup.
- `pending`: left untouched.

Preflight was deliberately NOT changed to refuse a missing live slot --
rebuilding a not-yet-existing generated artifact is a valid recovery
operation, so the publisher supports absent originals rather than rejecting
them.

### 19.2 Missing-original fault matrix

A new `run_missing_original_fault_injection_self_test()` runs 33 scenarios,
entirely against synthetic temp directories: each of the 4 slots individually
absent crossed with a promotion failure at each of the 4 positions (16), two-
and three-slot combinations absent (3), all 4 slots absent (2), an absent slot
promoted successfully followed by a later failure (2), backup-stage failures
with an original absent (4), and successful publishes with some/all/no
originals absent (3, positive controls). Every scenario asserts: the call
never raises (wrapped in `try/except Exception`, itself asserted); the return
type is `(bool, str)`; every originally-present slot matches its original
content/hash after rollback; every originally-absent slot remains absent
after rollback; no transaction residue remains after a successful rollback.
All 33 PASS.

### 19.3 Rollback-operation failure semantics

A separate `run_rollback_operation_failure_self_test()` injects a filesystem
failure INSIDE rollback itself -- via two new TEST-ONLY hooks,
`fail_removing_promoted` and `fail_restoring_backup` (both by slot name, never
set by the real CLI path) -- covering (a) a failure removing a promoted
output and (b) a failure renaming a backup back into place. In both cases the
function is confirmed to: not raise; return a controlled `(False, message)`;
name the unresolved slot in the message; preserve (not delete) the
transaction directory; and leave the affected slot's backup genuinely
recoverable inside it. A third scenario confirms the next publish attempt
against the same `base_dir` refuses safely while that residue remains
unresolved. All 3 scenarios PASS. Docstrings for `_publish_staged_generation`,
`staged_refresh`, and `RefreshTransactionError` were narrowed to state
precisely what is proven by self-test (a specific, named set of failure
classes) versus what would require catching an arbitrary, untested filesystem
failure -- "raises nothing," "fully restored," and "live outputs untouched"
are no longer claimed unconditionally.

### 19.4 Cleanup-warning reporting through the public rebuild path

`rebuild_from_cache()` previously checked only the success boolean returned
by `_publish_staged_generation()` and then always printed a generic "Rebuild
published successfully" report -- a committed generation with a failed
post-commit cleanup would have looked identical to a completely clean run.
Fixed by extracting a shared `_report_publish_result()` helper that
`rebuild_from_cache()` now calls: the returned message is always surfaced,
and a cleanup-warning result (the message now begins with the literal marker
`"COMMITTED WITH CLEANUP WARNING"`) is called out with its own explicit
banner, never folded into a silent-looking success. A new
`run_cleanup_warning_self_test()` verifies, against a synthetic output set:
all 4 live outputs hold the complete new generation with the expected
content; the retained backups for all 4 previously-existing slots remain
recoverable inside the transaction directory; `_report_publish_result` --
the exact function the public rebuild path calls -- surfaces the warning
banner; and the next publish attempt refuses safely while that residue is
unresolved. All 4 checks PASS.

### 19.5 Small consistency fixes

- `_snapshot_schema_ok()` now checks that every required provenance field
  holds a valid, nonempty value of the expected basic type (nonempty string
  for accession/URL/filed-date/entity fields, a finite number for USD/share/
  beta fields) -- a present key holding `None`, `""`, or the wrong type now
  fails exactly like a missing key. Re-verified PASS against all 4 real
  snapshots.
- `verify_source_manifest_structure()`'s docstring previously claimed the
  joined provenance lists "correspond 1:1 with the component rows actually
  present" -- corrected to state the actual, implemented invariant: they
  correspond to the DISTINCT SET of the components' own provenance values,
  since `dedupe_preserve_order()` legitimately collapses identical values
  shared by components filed together in the same accession (this was
  caught and fixed as a bug in Pass 4's own new check before it shipped;
  this pass only corrected the docstring to match the already-correct code).

### 19.6 Post-fix verification (this session's actual command output)

Read-only checkpoint (branch/HEAD/clean tree confirmed unchanged) →
implementation → `python3 build_snapshots.py --self-test` (9 categories, all
PASS: manifest-completeness, PII-scanner, publish-fault-injection,
refresh-fault-injection, snapshot-manifest-structure, source-manifest-
structure, missing-original-fault-injection, rollback-operation-failure,
cleanup-warning) → `python3 build_snapshots.py` (default verify, `=== Overall:
PASS ===`, all 6 checks) → `python3 formula_audit.py` (`PASS`, 144/144 Table 5
cells) → `python3 formula_audit.py --self-test` (both negative controls PASS)
→ a real (non-synthetic) `--rebuild-from-cache` run confirmed byte-identical
to the pre-pass workbook/snapshots/manifests, then a second real rebuild
confirmed byte-identical to the first (determinism intact; no financial
content changed this pass) → structural checks re-confirmed (18 sheets, 1,620
formulas, 0 error tokens, zip-integrity clean) → no transaction residue left
in the live directory by any test or rebuild run → zero external network
requests at any point → `git status` (only `validation/` untracked, nothing
staged, branch/HEAD unchanged) → `git diff --check` clean.

## 20. Final state (after the fifth correction pass)

- `independent_dcf_validation.xlsx` — 18 sheets, 1,620 formulas, 0 XML errors,
  0 macros, 0 external links, 0 hidden sheets, 0 error-string tokens,
  zip-integrity clean, formula-audit-clean including full 144/144 Table 5
  coverage. Byte-for-byte identical to the workbook at the start of this
  pass -- no financial formula or value changed.
- New files this pass: none.
- Modified this pass: `build_snapshots.py` only (`_publish_staged_generation`'s
  state model and rollback logic; new `had_original` tracking; new
  `fail_removing_promoted`/`fail_restoring_backup` test hooks; new
  `_report_publish_result` helper wired into `rebuild_from_cache`; three new
  self-test functions; strengthened `_snapshot_schema_ok`; corrected
  docstrings). `snapshots/*.json`, `source_manifest.csv`,
  `snapshot_manifest.json`, `independent_dcf_validation.xlsx` were
  regenerated via a real `--rebuild-from-cache` run for verification and are
  confirmed byte-identical to their pre-pass content. This file.
- `sec_raw_cache/` preserved; `--refresh-sec` was never invoked this pass;
  all fault-injection testing used synthetic temp directories, never the
  real live cache or live output set.
- No file outside `validation/independent_dcf/` was created or modified.
- No `git add`, `git commit`, `git push`, or any repository-mutating command
  was run.

## 21. Sixth correction pass — the refresh rollback-restore misreporting defect

An independent review reproduced a real defect in the SEPARATE SEC-cache
refresh transaction (`_publish_refresh_transaction`, `staged_refresh`,
`refresh_sec`) — distinct from the rebuild-generation transaction fixed in
Pass 5. Fixed and independently re-verified in this pass; nothing here
touched the DCF workbook, formulas, snapshots, source data, or the
generation publisher (`_publish_staged_generation`). The rebuilt-nothing
proof: every snapshot, manifest, cached SEC input, and the workbook itself
are byte-for-byte identical before and after this pass.

### 21.1 Root cause and fix

**Reproduction:** a synthetic test backed up the live cache successfully,
then failed promoting the staged cache (triggering rollback), then ALSO
failed restoring the backup during rollback itself. The restore-rename
(`os.rename(backup_dir, cache_dir)` inside `_publish_refresh_transaction`'s
own exception handler) was not wrapped in its own `try`/`except` — the
resulting `OSError` escaped `_publish_refresh_transaction` entirely,
propagated up through `staged_refresh`'s inner `try`, and was caught by an
UNRELATED outer handler:
```python
except OSError as e:
    log(f"[staged_refresh] FAILED (I/O) -- live cache untouched: {e}")
```
That handler's "live cache untouched" claim was FALSE: the live cache
directory was actually ABSENT (it had been renamed to backup_dir, and the
attempt to rename it back had just failed). `refresh_sec()` then compounded
this with its own unconditional `"Refresh FAILED, live cache unchanged"`.
Neither message named the retained backup or gave any recovery path.

**Fix:** the restore-rename step is now wrapped in its own `try`/`except
OSError`. On failure, the exception is caught (never escapes), and the
function returns a controlled result whose message begins with the literal
string `REFRESH_ROLLBACK_INCOMPLETE_PREFIX` ("REFRESH FAILED AND ROLLBACK
INCOMPLETE"), states the live cache directory may be ABSENT or incomplete,
and gives the exact retained transaction directory and backup path for
manual recovery. The transaction directory is preserved (not deleted, not
overwritten), so the next refresh attempt's residue preflight check refuses
safely. A new `fail_restoring_backup` TEST-ONLY hook (never set by the real
CLI path) was added to `_publish_refresh_transaction`/`staged_refresh` to
inject this specific failure.

### 21.2 Reporting fixes

`staged_refresh`'s two outer exception handlers (which now ONLY fire for
genuine Phase 1/2 fetch/validation/staging-I/O failures, since Phase 3 no
longer lets an exception escape) were reworded from an unconditional "live
cache untouched" to state precisely which phase failed. `refresh_sec()`'s
final report was rewritten around a new shared helper,
`_report_refresh_result()`, that distinguishes five outcomes rather than
collapsing them into "succeeded"/"unchanged": (1) failure before the live
cache was touched, (2) failure fully rolled back, (3) failure with an
INCOMPLETE rollback (its own explicit banner, manual recovery required),
(4) successful commit, (5) successful commit with a cleanup warning (reusing
the existing `CLEANUP_WARNING_PREFIX` marker from Pass 5's rebuild-path
fix). The unconditional "Refresh FAILED, live cache unchanged" message no
longer exists in this code path.

### 21.3 Fault-injection scenario and assertions

A new `run_refresh_rollback_incomplete_self_test()` reproduces exactly the
scenario above (backup succeeds, promotion fails, restore-rename ALSO
fails) against a temporary copy of the cache and asserts: no unexpected
exception escapes `staged_refresh` (the call is wrapped and checked, not
assumed); the result is `False`; the message begins with
`REFRESH_ROLLBACK_INCOMPLETE_PREFIX`; the message states the live cache may
be ABSENT; the message includes both the retained transaction directory and
backup path; the live cache directory is confirmed absent on disk; the
original cache contents are byte-for-byte recoverable from the retained
backup (`_hash_directory_tree` comparison); transaction residue remains; a
subsequent refresh attempt refuses because of that residue; and — passed
through `_report_refresh_result`, the exact function `refresh_sec()` calls —
public reporting never makes an unqualified "untouched"/"unchanged" claim
(a negated warning, "do NOT assume ... unchanged", is the correct behavior
and is not flagged; only the ORIGINAL bug's exact affirmative phrases are
checked for). All checks PASS. The existing `run_refresh_fault_injection_self_test()`'s
positive controls (an ordinary promotion failure restores the original
cache; a backup-stage failure leaves it untouched; a successful refresh
still commits; a cleanup failure remains committed-with-warning) were
re-run and continue to PASS unchanged.

### 21.4 Post-fix verification (this session's actual command output)

Read-only checkpoint (branch/HEAD/clean tree confirmed unchanged) →
implementation → `python3 build_snapshots.py` (default verify, `=== Overall:
PASS ===`, all 6 checks) → `python3 build_snapshots.py --self-test` (10
categories, all PASS, including the new refresh-rollback-incomplete
category) → `python3 formula_audit.py` (`PASS`, 144/144 Table 5 cells) →
`python3 formula_audit.py --self-test` (both negative controls PASS) →
`independent_dcf_validation.xlsx`, `source_manifest.csv`,
`snapshot_manifest.json`, `snapshots/*.json`, and every `sec_raw_cache/*.json`
file confirmed byte-for-byte identical (SHA-256) before and after this pass
-- this pass changed `build_snapshots.py` only → no transaction/staging
residue remains in the live directory → zero external network requests at
any point → `git status` (only `validation/` untracked, nothing staged,
branch/HEAD unchanged) → `git diff --check` clean → no file outside
`validation/independent_dcf/` changed.

## 22. Final state (after the sixth correction pass)

- `independent_dcf_validation.xlsx` — unchanged, byte-for-byte identical to
  the workbook at the start of this pass (SHA-256 verified). 18 sheets,
  1,620 formulas, formula-audit-clean including full 144/144 Table 5
  coverage.
- New files this pass: none.
- Modified this pass: `build_snapshots.py` only (`_publish_refresh_transaction`'s
  rollback-restore handling; new `fail_restoring_backup` test hook;
  `REFRESH_ROLLBACK_INCOMPLETE_PREFIX`; new `_report_refresh_result` helper
  wired into `refresh_sec`; reworded `staged_refresh` outer exception
  handlers; new `run_refresh_rollback_incomplete_self_test`; corrected
  docstrings and the module-level ATOMICITY / RECOVERY GUARANTEES comment
  for the refresh transaction). `README.md`, this file. No snapshot,
  manifest, cached SEC input, or workbook file changed.
- `sec_raw_cache/` preserved (byte-identical, SHA-256 verified);
  `--refresh-sec` was never invoked this pass; all fault-injection testing
  used temporary copies of the cache, never the real live cache.
- No file outside `validation/independent_dcf/` was created or modified.
- No `git add`, `git commit`, `git push`, or any repository-mutating command
  was run.
