# Archived evidence — Track A Phase 2B initial reconciliation (NO-GO)

This directory is a frozen, read-only snapshot of the reconciliation
artifacts produced at the end of Track A Phase 2B, preserved before Phase
2C regenerates `validation/dcf_reconciliation/`'s live artifacts. **These
files are historical evidence and must not be edited, regenerated, or
silently erased** — they are the record that the first reconciliation
attempt genuinely failed, and why.

## Provenance

- **Source HEAD**: `776e76f56e6210f1050caa74c60962ed89162496`
- **Branch**: `remediation/valuation-engine-production-quality`
- **Original independent workbook (V1) SHA-256**:
  `942b5f096cf546d63487add31eb29b1b690f0819f923f01436093f9bbc75eba1`
  (`validation/independent_dcf/independent_dcf_validation.xlsx` — unchanged
  by Phase 2B or Phase 2C; this archive does not contain a copy of it, only
  of the reconciliation *output* that compared against it)
- **Phase 2B reconciliation workbook SHA-256** (this archive's
  `dcf_reconciliation.xlsx`): `7d372f6eb063b5e120ae1c103aa21e2c118817e3d167d80e15dc60dbcd1ec135`

## Archived files and their SHA-256

| File | SHA-256 |
|---|---|
| `codebase_outputs.json` | `65f24725706f43c3dcf62d8cb8fee3fb9946b296184b73bf7f46a38080da1f60` |
| `dcf_reconciliation.xlsx` | `7d372f6eb063b5e120ae1c103aa21e2c118817e3d167d80e15dc60dbcd1ec135` |
| `reconciliation_manifest.json` | `3badf395b9e2326d0d0e6eecca432fb6372c3ec3ba26f070bd5eddcd46867370` |
| `reconciliation_report.md` | `6b4e5fcc92354363da7276c43b2b5fe141957593cea5242fd827602a34e613fb` |
| `reconciliation_results.json` | `608e64d69bfb2c0a6f6a2b5c4227b148a3fda63c952904cd02a7f52ddb13d4c1` |

## What Phase 2B found

**INTC's first divergence: Revenue CAGR.** Codebase = `-9.554417%`,
workbook V1 = `-9.566850%`, absolute difference = `0.012433` percentage
points — exceeding the documented `±0.01pp` tolerance. MSFT, CAT, and VZ
matched the workbook to full floating-point precision (zero difference) on
every required base metric.

**Root cause identified in Phase 2B**: the independent workbook's
`DCF_INTC!B5` ("Years elapsed") computed a plain period count
(`COUNT(periods)-1 = 4`), while `src/dcf_model/dcf.py`'s
`calculate_historical_revenue_cagr` computed the actual elapsed calendar
days between INTC's frozen fiscal-period-end dates, divided by 365.25
(`1463 / 365.25 = 4.005475701574264`). INTC's fiscal year-end dates are
not evenly spaced (2021-12-25, 2022-12-31, 2023-12-30, 2024-12-28,
2025-12-27), so the two "years elapsed" methods diverge for INTC even
though they coincidentally agree for MSFT/CAT/VZ (whose fiscal year-ends
recur on the same calendar day every year).

**Verdict at the end of Phase 2B: NO-GO.** `docs/independent-validation-plan.md`
did not, in its written prose, define `years_elapsed`'s exact day-count
convention — an ambiguity the independent workbook (built without reading
`src/dcf_model/dcf.py`) resolved differently, and reasonably, from the
codebase's actual implementation.

## Why Phase 2C exists

Per `docs/independent-validation-plan.md`'s own NO-GO protocol, a
discrepancy exceeding tolerance must be investigated and either fixed
(with a `docs/model-change-log.md` entry) or documented as an accepted
limitation — never silently re-run until it happens to pass, and never
"fixed" by loosening the tolerance. Phase 2C:

1. Resolves the specification ambiguity itself (governance decision,
   documented in `docs/model-specifications/dcf.md` and
   `docs/model-change-log.md`), selecting actual-elapsed-calendar-time as
   the authoritative convention — the convention the codebase already
   implements, so production required no behavioral change.
2. Builds a second-generation independent workbook (V2), from the
   corrected written specification, blind to the codebase, that applies
   the clarified convention.
3. Reruns the full reconciliation against V2, with V1 (and this archive)
   preserved unchanged as the historical record of what the ambiguity
   actually produced.

This archive is not superseded or invalidated by a later PASS — it
documents a genuine defect (a specification ambiguity that produced a
measurable, tolerance-exceeding discrepancy) that was found, not one that
was hidden or argued away.
