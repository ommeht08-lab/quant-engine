# Model Change Log

Status: living document. Records **model-affecting** changes — anything that could
plausibly change a valuation, a score, a risk figure, or a trading decision — with
what changed, why, and what output it could plausibly affect. Routine refactors,
documentation-only changes, and pure security/integrity hardening with no numerical
effect are explicitly labeled as such rather than omitted, so the log stays a
complete record of *why nothing changed* as well as *why something did*.

Entries are seeded from this branch's verified commit history
(`remediation/valuation-engine-production-quality`), read from the actual diffs —
not reconstructed from commit messages alone. Only effects that are actually
supported by the diff are described; where a commit's exact model-output
consequence could not be verified from the available diff detail, that limit is
stated explicitly rather than guessed.

## How to record a future entry

Every commit that touches a file under `src/dcf_model/`, `src/risk/`,
`src/backtesting/`, `src/valuation/`, `src/trading/` (beyond safety/telemetry
plumbing), or their default constants/thresholds, should get an entry here at the
time of the commit — not reconstructed later. Use this template:

```
## <commit-hash-short> — <date> — <one-line summary>

- **What changed**: <specific functions/constants/formulas touched>
- **Why**: <the reasoning, referencing an assumptions-register or
  limitations-register ID if applicable>
- **Output effect**: <what could plausibly change as a result — a specific
  valuation, a score, a risk figure, a trading decision — or "None: security/
  infrastructure only" if genuinely no model math changed>
- **Tests**: <what test coverage was added/updated alongside this change>
```

A change with no plausible effect on any model output (a pure security fix, a
logging improvement, a comment correction) should still get an entry, explicitly
labeled `Output effect: None`, so a reader scanning this log can distinguish "this
was reviewed and confirmed to not affect model output" from "this was never
reviewed for model effect."

---

## 26221de — 2026-08-14 — "fix: harden valuation and trading safety"

- **What changed**: Broad remediation pass touching
  [`src/dcf_model/dcf.py`](../src/dcf_model/dcf.py) (+141/−? lines),
  [`src/risk/monte_carlo.py`](../src/risk/monte_carlo.py) (+266 lines — the
  `VaRResult` status-based result type, `_summarize`, `_simulate_univariate`,
  `_simulate_multivariate`, and `calculate_portfolio_var` were added/rewritten in
  this commit),
  [`src/risk/hedging.py`](../src/risk/hedging.py) (+113/−? lines, in-place
  modification of existing functions rather than new ones),
  [`src/backtesting/historical_tester.py`](../src/backtesting/historical_tester.py)
  (+360/−? lines), [`src/trading/alpaca_execution.py`](../src/trading/alpaca_execution.py)
  (+1527/−? lines — the largest single change in this commit), and
  [`src/utils/macro.py`](../src/utils/macro.py) (+63/−? lines). Also added
  authenticated API boundaries, dashboard resilience, CI gating, and non-model
  files (workflows, dashboard components) not covered by this log.
- **Why**: Per the commit message: "Correct DCF, VaR, hedging, rebalance, and
  point-in-time calculations; enforce paper-only execution and broker-order safety
  controls." Confirmed from the `dcf.py` diff specifically: added the
  `MIN_EXPLICIT_*`/`MAX_EXPLICIT_*` economic-bounds constants and
  `_validate_capital_inputs`/`DCFAssumptions.__post_init__` validation (now
  documented as current behavior in `A-002`/`A-003` and the DCF model spec's
  "Valid ranges" table), and the tax-rate override-precedence rule (`assumptions.tax_rate`
  always wins over a derivable rate).
- **Output effect**: Materially affects DCF, VaR, hedging, backtester, and live
  trading output — this commit is where most of the current model behavior
  documented in [`docs/model-specifications/`](model-specifications/) originates.
  Given the size of this change (1527 lines in `alpaca_execution.py` alone), this
  log entry does **not** claim to enumerate every individual behavioral delta from
  the pre-commit state; readers needing that level of detail should diff against
  the parent commit directly. What is verified here is the DCF-specific bounds/
  validation addition and the VaR result-type rewrite (`VaRResult` with explicit
  `"ok"`/`"insufficient_data"`/`"error"` statuses, replacing whatever the prior
  representation was).
- **Tests**: Added isolated regression tests and CI gating per the commit message;
  see [`tests/dcf/test_dcf.py`](../tests/dcf/test_dcf.py),
  [`tests/risk/test_monte_carlo.py`](../tests/risk/test_monte_carlo.py),
  [`tests/risk/test_hedging.py`](../tests/risk/test_hedging.py),
  [`tests/backtesting/`](../tests/backtesting/), and
  [`tests/trading/`](../tests/trading/) for the current (post-commit) coverage
  documented per-model in [`docs/model-specifications/`](model-specifications/).

## d143c80 — 2026-08-14 — "fix: normalize VaR weights after missing history"

- **What changed**: [`src/risk/monte_carlo.py`](../src/risk/monte_carlo.py) only
  (+91/−2 lines) — added the coverage-floor-then-renormalize missing-history weight
  policy documented in
  [`docs/model-specifications/risk-models.md`](model-specifications/risk-models.md)
  ("Missing-history weight policy") and `A-020` in the assumptions register:
  `MIN_PORTFOLIO_COVERAGE_FRACTION` (50%) refusal below that floor, and
  renormalizing surviving tickers' weights to the *original* total requested
  weight (not hardcoded to 1.0) above it.
- **Why**: Fixes a specific correctness bug — leaving survivors' weights at their
  original values after dropping a ticker with unusable price history would
  silently treat the dropped ticker as having *both* zero return *and* zero
  weight simultaneously, scaling the simulated portfolio return (and therefore
  VaR/CVaR) down by whatever fraction of weight vanished, understating risk
  without any signal that anything was excluded.
- **Output effect**: Changes `calculate_portfolio_var`'s output for any portfolio
  where at least one holding's price history is unavailable — previously an
  understated (less negative) VaR/CVaR for such a portfolio; now either an
  explicit `"insufficient_data"` result (if too much weight is unmodeled) or a
  correctly renormalized, non-understated VaR/CVaR.
- **Tests**: [`tests/risk/test_monte_carlo.py`](../tests/risk/test_monte_carlo.py)
  gained 316 lines in this commit, including `TestMissingHistoryWeightPolicy`.

## 39ed482 — 2026-08-15 — "fix: harden dashboard and service security"

- **What changed**: [`src/utils/cache.py`](../src/utils/cache.py) (JSON envelope
  serialization codec, replacing pickle — a security/integrity change, not a
  numerical one: the codec preserves the exact same DataFrame/scalar values it is
  given, it does not alter what any model computes), `src/api/main.py` (service-
  token authentication), `src/trading/alpaca_execution.py` (a 7-line change — a
  stale comment correction about live-trading opt-in, not a logic change), plus
  extensive frontend security additions (`frontend/src/lib/valuation-api-url.ts`,
  `secret-validation.ts`, `client-identifier.ts`, `rate-limit-policy.ts`,
  `alpaca-url.ts`, `redis.ts` atomic rate limiting) and governance documentation
  (`docs/model-development-roadmap.md`, `docs/security-threat-model.md` were added
  in this commit).
- **Why**: Security/integrity hardening — see
  [`docs/security-threat-model.md`](security-threat-model.md) for the full
  rationale.
- **Output effect**: **None.** This commit touches authentication, rate limiting,
  URL validation, secret validation, and cache *serialization format* (not cache
  *content*) — no formula, threshold, or default constant in `src/dcf_model/`,
  `src/risk/`, `src/backtesting/`, or `src/valuation/` was changed. The one change
  to `src/trading/alpaca_execution.py` in this commit is a comment-only correction.
  This entry exists specifically to record that this commit was reviewed for
  model-output effect and confirmed to have none — not to imply it was skipped.
- **Tests**: [`tests/utils/test_cache.py`](../tests/utils/test_cache.py) (new, 838
  lines), [`tests/api/test_service_auth.py`](../tests/api/test_service_auth.py)
  (new, 201 lines), plus extensive new frontend test files — all verifying the
  security/serialization behavior this commit added, not model math.

## Uncommitted working-tree changes — Track A Phase 1.5B ("Final Contract Cleanup")

**Status: not yet committed.** Recorded here per this document's own rule (every
model-affecting change gets an entry at the time it's made, not reconstructed
later) — no commit hash exists yet for this work; it will be added once these
changes are committed, per the same "labeled, not omitted" discipline applied to
every other entry in this log.

- **What changed**: [`src/risk/hedging.py`](../src/risk/hedging.py) (extreme-
  but-finite `implied_vol`/`risk_free_rate` bounds, `MAX_ABS_IMPLIED_VOL`/
  `MAX_ABS_RISK_FREE_RATE`, and a defensive `try`/`except
  (ArithmeticError, OverflowError, ValueError)` around the BSM pricing calls),
  [`src/dcf_model/dcf.py`](../src/dcf_model/dcf.py) (`_require_finite_numeric`/
  `_require_finite_numeric_or_none` shared helpers, applied to `base_revenue`,
  every `project_free_cash_flows` rate parameter, every `DCFAssumptions` field,
  and `calculate_wacc`/`calculate_terminal_value`/`discount_to_present_value`/
  `calculate_intrinsic_value_per_share`/`calculate_enterprise_value`/
  `calculate_fcf_yield`), [`src/api/sector_medians.py`](../src/api/sector_medians.py)
  (top-level and nested-container JSON shape validation in `load_sector_medians`/
  `get_sector_median_price_to_intrinsic`), and
  [`src/trading/alpaca_execution.py`](../src/trading/alpaca_execution.py)
  (`POST_TRIM_NOTIONAL_TOLERANCE_DOLLARS` replacing the fixed weight-fraction
  `_POST_TRIM_WEIGHT_TOLERANCE`).
- **Why**: Four remaining contract discrepancies found by independent review,
  ahead of building the independent DCF validation workbook (this doc's own rule:
  Track B/validation work should not proceed against a specification containing
  known-invalid guarantees). See
  [`docs/independent-validation-plan.md`](independent-validation-plan.md).
- **Output effect**: For genuinely valid inputs (the only inputs any current
  caller in this codebase — `run_dcf_valuation`, the live API, the backtester, the
  trading engine — actually supplies), **none of these four fixes change any
  computed value.** Every fix narrows what was previously *accepted silently and
  computed on incorrectly* (a `bool` treated as `0`/`1`, an astronomically large
  but finite float overflowing internal BSM arithmetic, a malformed cache payload
  reaching an unguarded `.get(...)`, a post-trim tolerance worth real dollars on a
  large account) down to a clean, explicit `ValueError`/`None`/`0`/"incomplete"
  outcome instead. This is a correctness and robustness hardening pass, not a
  change to any formula.
- **Tests**: 85 additional pytest-collected cases across
  [`tests/risk/test_hedging.py`](../tests/risk/test_hedging.py) (`TestExtremeButFiniteInputsNeverRaise`),
  [`tests/dcf/test_dcf.py`](../tests/dcf/test_dcf.py)
  (`TestBooleanAndNonnumericRejectedAtProjectFCF`,
  `TestBooleanAndNonnumericRejectedAtDCFAssumptions`,
  `TestCalculateWaccBoundaryHardening`, `TestCalculateTerminalValueBoundaryHardening`,
  `TestDiscountToPresentValueBoundaryHardening`,
  `TestCalculateIntrinsicValuePerShareBoundaryHardening`,
  `TestCalculateEnterpriseValueBoundaryHardening`,
  `TestCalculateFcfYieldBoundaryHardening`),
  [`tests/api/test_sector_medians.py`](../tests/api/test_sector_medians.py)
  (`TestMalformedTopLevelShape`, `TestMalformedNestedContainers`), and
  [`tests/trading/test_rebalance.py`](../tests/trading/test_rebalance.py)
  (`TestPostFillCapNotionalTolerance`). Full pytest collection: 351 (committed
  baseline, `39ed482`) → 436 (working tree at the start of this Track A Phase
  1.5B pass) → 781 (this working tree, after this pass's four fixes) — a net
  increase of 345 collected cases from this pass alone. See the final report for
  this pass for the exact count and the unit distinction (collected cases, after
  parametrization expansion, vs. test functions as written in source).

## Uncommitted working-tree changes — Track A Phase 1.5C ("Final narrow numeric-boundary correction")

**Status: not yet committed.** Continues directly from the Phase 1.5B entry above
— same "not yet committed, no commit hash" caveat.

- **What changed**: [`src/dcf_model/dcf.py`](../src/dcf_model/dcf.py) — the
  central missing-versus-malformed policy correction. `calculate_wacc` now
  validates `risk_free_rate`/`market_risk_premium` (previously unvalidated
  entirely) and splits its `beta`/`cost_of_debt`/`tax_rate`/`total_debt` handling
  into two genuinely distinct cases: `None` (missing) still falls back to the
  documented default, but a PRESENT malformed value (`bool`, non-numeric, non-
  finite) now raises `ValueError` instead of silently falling back to that same
  default — closing the exact gap Phase 1.5B's own report had incorrectly claimed
  was already closed. The same missing-vs-malformed split was applied to
  `calculate_intrinsic_value_per_share` (`total_debt`/`cash_and_equivalents`:
  `None` → 0, present-malformed → raises) and `calculate_enterprise_value`
  (`None` → 0, present-malformed → returns `None`, this function's own
  graceful-degradation contract). Every arithmetic step across `calculate_wacc`,
  `project_free_cash_flows`, `calculate_terminal_value`,
  `discount_to_present_value` (now also validating its `fcf_projection` DataFrame
  boundary — non-empty, has an `"fcf"` column, finite index/values),
  `calculate_intrinsic_value_per_share`, `calculate_enterprise_value`, and
  `calculate_fcf_yield` is now wrapped in a narrow `try`/`except (ArithmeticError,
  OverflowError)` with an explicit finite-result check after each step, so a
  non-finite arithmetic RESULT (not just a non-finite input) is never returned as
  a legitimate value. `_validate_capital_inputs` now delegates to the shared
  `_require_finite_numeric` helper instead of a raw `math.isfinite` call.
  Additionally fixed: `_is_valid_finite_number` in
  [`src/dcf_model/dcf.py`](../src/dcf_model/dcf.py),
  [`src/risk/hedging.py`](../src/risk/hedging.py) (as `_is_finite_number`), and
  [`src/api/sector_medians.py`](../src/api/sector_medians.py) all previously called
  `math.isfinite(value)` unconditionally — which itself raises `OverflowError` for
  an astronomically large Python `int` (e.g. `10**10000`, arbitrary-precision and
  always finite by definition, but too large to convert to a C `double`). All
  three now special-case `int` (always finite, no conversion) vs. `float` (safe to
  check directly), restoring the "genuinely non-raising" contract these helpers —
  and `calculate_spy_hedge`'s own "never raises" contract, which depends on one of
  them — already claimed but did not fully satisfy. A related arithmetic-overflow
  path in [`src/api/sector_medians.py`](../src/api/sector_medians.py)'s risk-free-
  rate comparison (subtracting a huge `int` from a `float`) is now wrapped
  defensively too.
- **Why**: An independent review found the Phase 1.5B report's claim ("booleans
  and nonnumeric values are rejected at every public DCF/WACC boundary") did not
  fully hold — several fields still silently fell back to a default for a
  malformed-but-present value, which is a materially different (and worse)
  failure mode than the same field being genuinely absent: a present malformed
  value indicates a data-integrity problem the caller should be told about, not
  papered over with a plausible-looking number.
- **Output effect**: For genuinely valid inputs (the only inputs any current
  caller in this codebase actually supplies), **no change to any computed value.**
  This pass only changes behavior for adversarial/malformed inputs that no
  legitimate call site in this codebase produces — it narrows several "silently
  use a default" paths down to an explicit `ValueError`/`None`, and closes an
  overflow gap in three "non-raising" validation helpers that, until now, could
  themselves raise for an extreme adversarial input.
- **Tests**: Net effect on full pytest collection: 781 (working tree at the start
  of this Track A Phase 1.5C pass) → 879 (this working tree) — 98 additional
  collected cases, concentrated in revised/expanded
  [`tests/dcf/test_dcf.py`](../tests/dcf/test_dcf.py) classes
  (`TestCalculateWaccBoundaryHardening`,
  `TestCalculateIntrinsicValuePerShareBoundaryHardening`,
  `TestCalculateEnterpriseValueBoundaryHardening`,
  `TestCalculateTerminalValueBoundaryHardening`,
  `TestDiscountToPresentValueBoundaryHardening`, `TestCapitalInputValidation`,
  `TestNumericBoundaryHardening`), plus new huge-integer regression cases in
  [`tests/risk/test_hedging.py`](../tests/risk/test_hedging.py)
  (`test_huge_python_integer_never_leaks_a_raw_overflow_error`) and
  [`tests/api/test_sector_medians.py`](../tests/api/test_sector_medians.py)
  (`test_huge_integer_requested_rate_never_leaks_a_raw_overflow_error`,
  `test_huge_integer_cached_rate_never_leaks_a_raw_overflow_error`). Several
  Phase-1.5B tests that had encoded the now-corrected (incorrect) "malformed
  falls back to default" behavior were rewritten, not merely renamed, to assert
  the corrected contract (e.g. `test_malformed_beta_falls_back_to_default_not_raise`
  → `test_none_beta_still_falls_back_to_default` + `test_malformed_present_beta_raises`,
  and the equivalent split for `total_debt`, `cost_of_debt`, `tax_rate` on
  `calculate_wacc`, and for `total_debt`/`cash_and_equivalents` on
  `calculate_intrinsic_value_per_share`/`calculate_enterprise_value`).

## Uncommitted working-tree changes — Track A Phase 1.5D ("Semantic-invariant and specification-freeze cleanup")

**Status: not yet committed.** Continues directly from the Phase 1.5C entry above
— same "not yet committed, no commit hash" caveat.

- **What changed**: [`src/dcf_model/dcf.py`](../src/dcf_model/dcf.py) — economic
  sign/range invariants enforced directly at three public boundaries that had
  previously enforced them only indirectly (via `_validate_capital_inputs` on the
  orchestration path, which a direct caller of these functions bypasses
  entirely). `calculate_wacc` now requires `current_price`/`shares_outstanding`
  strictly `> 0`; a present `total_debt`/`cost_of_debt` must be `>= 0`; and a
  present `tax_rate` outside `[0, 1)` now **raises** `ValueError` instead of
  silently falling back to the 21% default (reversing the one Phase 1.5C
  exception that had preserved that fallback for a well-typed, finite,
  out-of-range value). `calculate_intrinsic_value_per_share` now rejects a
  present negative `total_debt`/`cash_and_equivalents`. `calculate_enterprise_value`
  now degrades to `None` for a non-positive `current_price`/`shares_outstanding`
  or a present negative `total_debt`/`cash_and_equivalents`, while deliberately
  continuing to allow a negative *final* Enterprise Value (a legitimate
  cash-rich-company case) — only malformed/nonfinite arithmetic is rejected
  there, never a merely-negative-but-valid result. Separately,
  `discount_to_present_value`'s vectorized discounting arithmetic is now scoped
  inside a narrow `numpy.errstate(over="raise", invalid="raise",
  divide="raise")` context, so the function's own deliberate-overflow test case
  (an astronomically large `terminal_value` combined with `wacc` near `-1`) now
  raises `FloatingPointError` — caught by the existing `except (ArithmeticError,
  OverflowError)` clause and re-raised as the same documented `ValueError` — the
  overflow is still detected and still raises exactly as before, but no longer
  first emits an unhandled numpy `RuntimeWarning` ("overflow encountered in
  scalar divide") on the way there. No global warning filter or numpy error
  state was changed; the `errstate` context is scoped to that one block.
- **Why**: A follow-up review found two remaining gaps before the specification
  could be considered frozen for the independent DCF workbook build: (1) the
  orchestration path's negative-data pre-check (`_validate_capital_inputs`) was
  not mirrored by the individual public functions themselves, so a caller
  invoking `calculate_wacc`/`calculate_intrinsic_value_per_share`/
  `calculate_enterprise_value` directly (as the workbook build's own
  cross-checks, or any other direct caller, would) could still pass in
  economically nonsensical values — negative debt, a zero share count, a >100%
  tax rate — and get back a plausible-looking number instead of a clean
  rejection; and (2) the full pytest suite emitted one non-third-party warning,
  which is not an acceptable steady state for a specification meant to document
  exactly what every public function does and does not raise.
- **Output effect**: For genuinely valid inputs (the only inputs any current
  caller in this codebase actually supplies), **no change to any computed
  value** and **no change to the previously-established WACC/CAPM formulas or
  the `[5%, 20%]` clamp.** This pass only changes behavior for inputs that are
  economically invalid on sign/range (negative debt, non-positive price/shares,
  an out-of-range tax rate) — none of which any current call site in this
  codebase produces — narrowing several "accept and compute anyway" paths down
  to an explicit `ValueError`/`None`, and eliminates one previously-emitted test
  warning without altering the underlying `ValueError` behavior it was already
  correctly triggering.
- **Tests**: Net effect on full pytest collection: 879 (working tree at the
  start of this Track A Phase 1.5D pass) → 901 (this working tree) — 22
  additional collected cases, concentrated in
  [`tests/dcf/test_dcf.py`](../tests/dcf/test_dcf.py) (135 test functions across
  16 test classes; 442 pytest-collected cases in this file alone), primarily in
  `TestCalculateWaccBoundaryHardening` (non-positive price/shares, negative
  total_debt/cost_of_debt, out-of-range tax_rate, and confirmation that a
  negative beta/risk-free-rate is accepted, not rejected),
  `TestCalculateIntrinsicValuePerShareBoundaryHardening` (negative present
  debt/cash), `TestCalculateEnterpriseValueBoundaryHardening` (non-positive
  price/shares, negative present debt/cash, and a dedicated
  `test_legitimate_negative_final_enterprise_value_is_allowed` case), and
  `TestDiscountToPresentValueBoundaryHardening` (a dedicated
  `test_overflow_raises_value_error_without_an_unhandled_runtime_warning` case,
  using `warnings.simplefilter("error", RuntimeWarning)` to prove the
  deliberate-overflow scenario raises `ValueError` with no `RuntimeWarning`
  escaping). One Phase 1.5C test that had encoded the now-superseded
  "out-of-range tax_rate still falls back" behavior
  (`test_out_of_range_but_genuinely_numeric_tax_rate_still_falls_back`) was
  rewritten, not merely renamed, to assert the corrected contract
  (`test_out_of_range_but_genuinely_numeric_tax_rate_now_raises`); one
  overflow-boundary test in `TestCalculateIntrinsicValuePerShareBoundaryHardening`
  that had used a negative `total_debt` to trigger arithmetic overflow was
  adjusted to use a huge non-negative `cash_and_equivalents` instead, so it
  continues to test overflow specifically rather than colliding with the new
  negative-debt sign check. Full-suite warning count: 3 → 2 (the two remaining
  warnings are the pre-existing third-party LibreSSL/`urllib3` and
  `websockets.legacy` deprecation warnings; both left untouched, as required).

## Uncommitted working-tree changes — Track A Phase 2C ("CAGR years_elapsed specification clarification")

**Status: not yet committed.** Documentation-clarification step (below)
followed by an independent workbook V2 build and a full reconciliation
rerun against it — both parts of this same change, recorded together here.

- **What changed**: Documentation only, in this step —
  [`docs/model-specifications/dcf.md`](model-specifications/dcf.md)'s
  "Historical revenue-growth and operating-margin derivation" section now
  states `years_elapsed`'s exact definition (actual elapsed calendar days
  between the earliest- and latest-dated valid revenue observations, divided
  by 365.25 — never `(number of periods − 1)`), which the written
  specification had previously left ambiguous. New entries: `A-028` in
  [`docs/assumptions-register.md`](assumptions-register.md) (formalizing the
  convention as a named assumption) and `L-019` in
  [`docs/limitations-register.md`](limitations-register.md) (recording the
  ambiguity that Track A Phase 2B's independent-validation reconciliation
  exposed, and its resolution). `L-012` updated to reflect that Phase 2A/2B
  have actually executed (not merely planned) and that Phase 2C is in
  progress. `src/dcf_model/dcf.py` was **not** modified in this step —
  `calculate_historical_revenue_cagr`'s existing implementation (actual
  elapsed calendar days / 365.25) already matched the now-clarified
  specification; the ambiguity was in the prose, not the code.
- **Why**: Track A Phase 2B's independent-validation workbook (V1, built
  without reading `dcf.py`, per `docs/independent-validation-plan.md`'s
  genuine-independence requirement) computed `years_elapsed` as a plain
  period count. For INTC — whose frozen fiscal-period-end dates are not
  evenly spaced (2021-12-25, 2022-12-31, 2023-12-30, 2024-12-28,
  2025-12-27) — this diverged from the codebase's actual-elapsed-calendar-
  days convention by enough (`4.0` vs. `4.005475701574264`) to move Revenue
  CAGR by ~0.0124 percentage points, exceeding the documented `±0.01`pp
  reconciliation tolerance and producing a Phase 2B NO-GO (archived at
  `validation/dcf_reconciliation/history/phase2b_initial_no_go/`). Per
  `docs/independent-validation-plan.md`'s own NO-GO protocol, the ambiguity
  had to be resolved and documented — not silently re-run, and not "fixed"
  by loosening the tolerance or by editing either calculation path to force
  agreement.
- **Output effect**: None from this step alone (documentation only; no
  `src/` file changed). The clarified convention matches
  `src/dcf_model/dcf.py`'s pre-existing behavior exactly, so no DCF output
  for any company changes as a result of this documentation update.
- **Tests**: None added in this documentation-only step. Regression tests
  covering the clarified convention (an irregular-fiscal-date CAGR test
  using INTC's actual dates/revenues, among others) are added in the
  following step of this same Phase 2C change, tracked in
  `tests/validation/test_dcf_reconciliation.py`.

### Phase 2C, continued: independent workbook V2 and reconciliation rerun

- **What changed**: A second independent workbook, V2
  (`validation/independent_dcf/independent_dcf_validation_v2.xlsx`,
  built by new scripts `build_workbook_v2.py`/`shadow_calc_v2.py`), was
  constructed from the clarified specification above — blind to `src/`
  and to the reconciliation implementation until V2 was fully built,
  audited, and hashed (see `validation/independent_dcf/README_v2.md`).
  V1 (`independent_dcf_validation.xlsx`) is preserved byte-for-byte
  unchanged; its Phase 2B evidence is archived at
  `validation/dcf_reconciliation/history/phase2b_initial_no_go/`, never
  edited. `validation/dcf_reconciliation/`'s reconciliation tooling
  (`capture.py`, `reconcile.py`) was then retargeted at V2 (with a hash
  guard on both V1 and V2 before every run), and three defects found in
  the Phase 2B reconciliation *tooling itself* (not the DCF model) were
  fixed: (1) `xlsx_writer.py`'s number-format-ID allocator could silently
  collide two distinct custom formats onto the same ID (e.g. a dollar
  amount rendering as a raw percentage) — rewritten to assign every
  distinct format a unique ID generically, with no per-format special
  casing; (2) sensitivity coverage was mislabeled as a single ambiguous
  "cells" count — now reported as both "Scenario Rows" and actual "Scalar
  Comparisons" (28+28+72+63+36 = 227 per company, 908 total), via a new
  shared `coverage.py` module; (3) the reconciliation workbook only showed
  full formula-driven detail for Sensitivity Tables 1-2 — a new "All
  Sensitivity Comparisons" sheet now carries all 908 scalar comparisons
  as their own live-formula rows.
- **Why**: Per `docs/independent-validation-plan.md`'s NO-GO protocol —
  the Phase 2B discrepancy is now resolved (specification clarified,
  V2 built from it) and the reconciliation re-run to confirm the fix
  actually closes the gap, not merely asserted to.
- **Output effect**: None on any production model output — `src/dcf_model/dcf.py`
  was not modified anywhere in Phase 2C (Phase 2B had already established
  its `years_elapsed` computation matched the clarified specification).
  The effect is entirely on the independent-validation artifacts: V2
  (new), and `validation/dcf_reconciliation/`'s reconciliation outputs
  (regenerated against V2). Result: all four companies (MSFT, CAT, INTC,
  VZ) now pass base-case reconciliation and all 908 sensitivity scalar
  comparisons against V2 — **GO** verdict, pending separate second-reviewer
  sign-off (still not performed). See
  `validation/dcf_reconciliation/reconciliation_report.md`.
- **Tests**: `tests/validation/test_dcf_reconciliation.py` covers both the
  Phase 2B/2C findings (irregular-fiscal-date CAGR correctness, V1/V2 hash
  guards, V2 date-subtraction-not-COUNT verification, OOXML format-ID
  collision checks, exact scalar-comparison counts, full Table 1-5
  workbook coverage, formula-tied PASS/FAIL, formula-error scans) and the
  pre-existing negative controls (missing-cell / perturbed-value
  detection, deterministic regeneration, archived-evidence immutability).

### Phase 2C, continued: independent second-reviewer audit remediation (H-1, M-1)

An independent, read-only second-reviewer audit of the above work (per
`docs/independent-validation-plan.md`'s still-pending sign-off requirement)
found two confirmed defects, neither in the DCF model itself. Both are
remediated here; `src/dcf_model/dcf.py` was not touched.

- **H-1 — durable evidence excluded by `.gitignore`**: the repository's
  blanket `*.xlsx` rule (meant for raw/interim financial data pulls)
  incidentally also excluded `dcf_reconciliation.xlsx`, its archived
  `history/phase2b_initial_no_go/` copy, and V2
  (`independent_dcf_validation_v2.xlsx`) from version control entirely --
  none would have survived a `git clean` or been present after a fresh
  clone. **Fix**: three narrow, exact-path `!`-exceptions added to
  `.gitignore` (see its own comment block) for exactly these three files;
  the general `*.xlsx` rule is unchanged for everything else. V1
  (`independent_dcf_validation.xlsx`) needed no change -- already tracked.
  Nothing was staged or committed as part of this fix. See
  `validation/dcf_reconciliation/README.md`'s "Durable evidence and
  `.gitignore`" section.
- **M-1 — empty cached PASS/FAIL values on formula cells**: `workbook_builder.py`'s
  `_comparison_row` wrote a correctly-referenced live `IF(...,"PASS","FAIL")`
  formula for every comparison cell, but cached an empty string alongside
  it -- structurally correct, functionally blank to any reader that does
  not run an actual spreadsheet recalculation (none is available anywhere
  in this repository's tooling). **Fix**: `_comparison_row` now also
  computes the verdict in Python, via `compare.py`'s
  `compare_rate`/`compare_monetary` (the same functions
  `reconciliation_results.json`'s own verdicts come from) applied to the
  exact codebase/workbook/tolerance values written into that row, and
  caches that as the cell's value. The formula text is unchanged. `n/a`
  (invalid-combination) rows were and remain handled separately, as
  literal text, never through this formula path. See
  `validation/dcf_reconciliation/README.md`'s "What 'PASS/FAIL' means at
  each layer" section for the resulting three-tier distinction
  (structurally verified / Python-computed cached verdict / actual
  spreadsheet recalculation -- the last of which remains unverified, by
  design, since no spreadsheet engine was used).
- **Output effect**: `dcf_reconciliation.xlsx` (this directory's own
  workbook) was regenerated to incorporate the M-1 fix. V1, V2, and the
  archived Phase 2B evidence were not regenerated and their SHA-256 hashes
  are unchanged -- `capture.py`'s existing hash guard would have raised
  before writing anything if either had drifted.
- **Tests**: new negative controls in `tests/validation/test_dcf_reconciliation.py`
  assert every formula-based PASS/FAIL cell has a non-empty cached value,
  independently recompute each cached verdict from the row's own values
  and tolerance and require an exact match, detect a deliberately
  perturbed comparison value as FAIL, detect a deliberately wrong cached
  verdict as a defect, and confirm the H-1 `.gitignore` exceptions are
  both present and narrowly scoped.

## Pre-remediation-branch history

Commits before `26221de` (e.g. `9927a26` "DevOps: Fix Tailwind CSS compilation and
force Vercel build context", `ed616be` "fix: force vercel env var injection") are
infrastructure/deployment changes, not model-affecting, and are not individually
logged here. If this log is ever extended further back, apply the same "labeled,
not omitted" rule established above.
