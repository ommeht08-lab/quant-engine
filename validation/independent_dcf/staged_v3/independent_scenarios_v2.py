"""Independent Bear/Base/Bull scenario calculation, v2: full intermediate
coverage plus focused boundary/edge fixtures. Frozen BEFORE comparison.

Relationship to v1 (`independent_scenarios.py`, `frozen-results-scenarios.json`)
---------------------------------------------------------------------------
v1 established that an independently-coded implementation reproduces
production's Bear/Bull *per-share* value, *year-1* growth/margin, WACC, and
terminal growth exactly (50/50 comparisons, 0.0 delta) for five cases. That
result is preserved as-is -- this module does not modify, regenerate, or
supersede `frozen-results.json` or `frozen-results-scenarios.json`, and both
keep their originally-documented SHA-256 hashes.

What v1 did NOT check: years 2-5 of the growth/margin path, the resulting
per-year revenue and FCF, each year's discounted FCF, the terminal value and
its present value, enterprise value, or the debt/cash equity bridge -- and it
exercised no case designed specifically to trigger a clamp boundary or an
uncomputable (WACC <= terminal growth) scenario. This module adds all of
that, reusing v1's already fresh, production-code-free formulas
(`shift_staged_rate`, `project_fcf_path`, `terminal_value`,
`discount_and_bridge`, the pinned bound/delta constants) via a plain import
-- it does not re-derive or duplicate that math, only assembles a fuller
result from the same calls -- for the same five snapshot-backed cases, plus
nine new synthetic boundary fixtures that isolate one clamp/threshold
mechanic each.

Independence caveat (read before treating this as a blind check)
------------------------------------------------------------------
This validator's *calculation code* is independent: nothing here imports
`src/dcf_model/scenarios.py`, and every formula is typed in fresh from the
specification text now pinned in `docs/model-specifications/dcf.md` and
`docs/assumptions-register.md` (A-029/A-030). But its *policy discovery* was
not blind -- unlike the original V1/V2 Base workbooks (authored from spec
text before anyone in that effort had read `dcf.py`), the author of this
module had already read `src/dcf_model/scenarios.py` (that reading is what
produced A-030's pinned deltas and shift/clamp rule in the first place).
This check can and does catch an implementation bug in the arithmetic or in
how the shift/clamp rule is coded -- an exact-match result is genuine,
non-trivial evidence the code matches the now-pinned policy -- but it cannot
catch a scenario where the pinned policy itself was mis-transcribed from a
misreading of `scenarios.py`, the way the original blind Base workbook once
caught a genuine specification ambiguity (`L-019`/`A-028`). Treat this as a
strong code-correctness check, not a substitute for a second, independently
blind author.

Boundary fixtures
------------------
Nine synthetic single-year-repeated (flat 5-year) fixtures, none tied to a
real company snapshot, each isolating one specific mechanic in
`_shift_staged_rate`/clamp behavior:

  1. growth_clamp_in_bounds        -- Bull growth shift clamps at the 40% ceiling
  2. growth_unclamped_out_of_bounds -- baseline growth already below -10%; shift applied unclamped both directions
  3. margin_clamp_at_upper_edge    -- baseline margin exactly at the 60% ceiling; Bull shift clamps
  4. margin_clamp_at_lower_edge    -- baseline margin exactly at the 0% floor; Bear shift clamps
  5. wacc_floor_clamp              -- Bull WACC shift clamps at the 5% floor
  6. wacc_ceiling_clamp            -- Bear WACC shift clamps at the 20% ceiling
  7. terminal_growth_floor_clamp   -- Bear terminal-growth shift clamps at 0%
  8. terminal_growth_ceiling_clamp -- Bull terminal-growth shift clamps at 5%
  9. uncomputable_bull             -- Bull WACC and terminal growth both clamp to exactly 5%, so WACC <= g and the case is reported invalid; Bear (same fixture) remains computable, for contrast

Run this file directly to (re)generate `frozen-results-scenarios-v2.json`
and `frozen-manifest-v2.json` (which records the SHA-256 of both this file's
own output and of the `frozen-results.json` it depends on, for the
comparator's hash-drift check), and print both hashes. Do not edit the
generated files by hand; rebuilding them constitutes a new freeze and
invalidates any existing comparison against them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from independent_scenarios import (
    BASELINE_TERMINAL_GROWTH,
    BEAR_GROWTH_DELTA,
    BEAR_MARGIN_DELTA,
    BEAR_TG_DELTA,
    BEAR_WACC_DELTA,
    BULL_GROWTH_DELTA,
    BULL_MARGIN_DELTA,
    BULL_TG_DELTA,
    BULL_WACC_DELTA,
    CAPEX_PCT_REVENUE,
    DA_PCT_REVENUE,
    MAX_MARGIN,
    MAX_REVENUE_GROWTH,
    MAX_TERMINAL_GROWTH,
    MAX_WACC,
    MIN_MARGIN,
    MIN_REVENUE_GROWTH,
    MIN_TERMINAL_GROWTH,
    MIN_WACC,
    NWC_PCT_REVENUE_CHANGE,
    _clamp,
    discount_and_bridge,
    project_fcf_path,
    shift_staged_rate,
    terminal_value,
)

HERE = Path(__file__).resolve().parent
BASE = HERE.parent  # validation/independent_dcf


def _delta_description(name: str, bear: float, bull: float) -> str:
    def signed_pp(x: float) -> str:
        pp = x * 100
        return f"{'+' if pp >= 0 else ''}{pp:g}pp"
    return f"{name} bear={signed_pp(bear)}/bull={signed_pp(bull)}"


SCENARIO_CONVENTION_DESCRIPTION = "; ".join([
    _delta_description("growth", BEAR_GROWTH_DELTA, BULL_GROWTH_DELTA),
    _delta_description("margin", BEAR_MARGIN_DELTA, BULL_MARGIN_DELTA),
    _delta_description("wacc", BEAR_WACC_DELTA, BULL_WACC_DELTA),
    _delta_description("terminal_growth", BEAR_TG_DELTA, BULL_TG_DELTA),
])
# Generated programmatically from the actual delta constants (not hand-typed
# shorthand like "+/-1pp") specifically to avoid repeating v1's transcription
# bug, where the frozen scenarioConvention string described WACC as
# "-/+1pp" (Bear -1pp/Bull +1pp) when the actual computation -- correct in
# v1's own numeric output -- was Bear +1pp/Bull -1pp. See this repository's
# `docs/limitations-register.md` L-021 for that correction.


def compute_scenario_full(
    growth_delta: float,
    margin_delta: float,
    wacc_delta: float,
    tg_delta: float,
    base_revenue: float,
    baseline_growth_path: list[float],
    baseline_margin_path: list[float],
    baseline_wacc: float,
    baseline_terminal_growth: float,
    tax_rate: float,
    debt: float,
    cash: float,
    shares: float,
) -> dict:
    growth_path = [shift_staged_rate(g, growth_delta, MIN_REVENUE_GROWTH, MAX_REVENUE_GROWTH) for g in baseline_growth_path]
    margin_path = [shift_staged_rate(m, margin_delta, MIN_MARGIN, MAX_MARGIN) for m in baseline_margin_path]
    wacc = _clamp(baseline_wacc + wacc_delta, MIN_WACC, MAX_WACC)
    tg = _clamp(baseline_terminal_growth + tg_delta, MIN_TERMINAL_GROWTH, MAX_TERMINAL_GROWTH)
    result = {"growth": growth_path, "margin": margin_path, "wacc": wacc, "terminal_growth": tg}
    try:
        revenues, fcfs = project_fcf_path(base_revenue, growth_path, margin_path, tax_rate)
        tv = terminal_value(fcfs[-1], wacc, tg)
        bridge = discount_and_bridge(fcfs, tv, wacc, debt, cash, shares)
        result.update({
            "revenue": revenues,
            "fcf": fcfs,
            "pv_fcf": bridge["pv_fcf"],
            "terminal_value": tv,
            "pv_terminal_value": bridge["pv_terminal"],
            "enterprise_value": bridge["enterprise_value"],
            "equity_value": bridge["equity_value"],
            "per_share": bridge["per_share"],
            "invalid_reason": None,
        })
    except ValueError as exc:
        result.update({
            "revenue": None, "fcf": None, "pv_fcf": None, "terminal_value": None,
            "pv_terminal_value": None, "enterprise_value": None, "equity_value": None,
            "per_share": None, "invalid_reason": str(exc),
        })
    return result


FIXED_DA, FIXED_CAPEX, FIXED_NWC = DA_PCT_REVENUE, CAPEX_PCT_REVENUE, NWC_PCT_REVENUE_CHANGE
FIXTURE_BASE_REVENUE, FIXTURE_TAX, FIXTURE_DEBT, FIXTURE_CASH, FIXTURE_SHARES = 1000.0, 0.21, 100.0, 50.0, 100.0

# Each fixture: (growth, margin, wacc, terminal_growth), each held flat across
# all 5 years -- these fixtures test the per-year shift/clamp mechanic in
# isolation, not the maturation fade formula (already covered by the five
# snapshot-backed cases and by v1's Base-only reconciliation).
BOUNDARY_FIXTURES = {
    "growth_clamp_in_bounds": (0.39, 0.20, 0.10, 0.025),
    "growth_unclamped_out_of_bounds": (-0.15, 0.20, 0.10, 0.025),
    "margin_clamp_at_upper_edge": (0.05, 0.60, 0.10, 0.025),
    "margin_clamp_at_lower_edge": (0.05, 0.00, 0.10, 0.025),
    "wacc_floor_clamp": (0.05, 0.20, 0.054, 0.025),
    "wacc_ceiling_clamp": (0.05, 0.20, 0.195, 0.025),
    "terminal_growth_floor_clamp": (0.05, 0.20, 0.10, 0.003),
    "terminal_growth_ceiling_clamp": (0.05, 0.20, 0.10, 0.048),
    "uncomputable_bull": (0.05, 0.20, 0.054, 0.046),
}


def build() -> dict:
    frozen_base = json.loads((HERE / "frozen-results.json").read_text())

    synth_years = [(1000 + 50 * i) * 1e6 for i in range(5)]
    fixed_inputs = {"NEGATIVE": {"debt": 100e6, "cash": 50e6, "shares": 100e6, "base_revenue": synth_years[-1]}}
    for ticker in ("MSFT", "CAT", "INTC", "VZ"):
        snap = json.loads((BASE / f"snapshots/{ticker}_snapshot.json").read_text())
        fact, market = snap["latest_year_facts"], snap["market_data"]
        fixed_inputs[ticker] = {
            "debt": fact["total_debt_usd"],
            "cash": fact["cash_and_equivalents_usd"],
            "shares": market["shares_outstanding"],
            "base_revenue": snap["historical_annual_data"][-1]["revenue_raw_usd"],
        }

    companies = {}
    for ticker, entry in frozen_base["companies"].items():
        fi = fixed_inputs[ticker]
        baseline_growth_path = entry["base"]["growth"]
        baseline_margin_path = entry["base"]["margin"]
        baseline_wacc = entry["inputs"]["wacc"]
        tax_rate = entry["inputs"]["tax"]

        bear = compute_scenario_full(
            BEAR_GROWTH_DELTA, BEAR_MARGIN_DELTA, BEAR_WACC_DELTA, BEAR_TG_DELTA,
            fi["base_revenue"], baseline_growth_path, baseline_margin_path, baseline_wacc,
            BASELINE_TERMINAL_GROWTH, tax_rate, fi["debt"], fi["cash"], fi["shares"],
        )
        bull = compute_scenario_full(
            BULL_GROWTH_DELTA, BULL_MARGIN_DELTA, BULL_WACC_DELTA, BULL_TG_DELTA,
            fi["base_revenue"], baseline_growth_path, baseline_margin_path, baseline_wacc,
            BASELINE_TERMINAL_GROWTH, tax_rate, fi["debt"], fi["cash"], fi["shares"],
        )
        companies[ticker] = {
            "bear": bear, "bull": bull,
            "baseline": {
                "base_revenue": fi["base_revenue"], "growth": baseline_growth_path,
                "margin": baseline_margin_path, "wacc": baseline_wacc,
                "terminal_growth": BASELINE_TERMINAL_GROWTH, "tax": tax_rate,
                "debt": fi["debt"], "cash": fi["cash"], "shares": fi["shares"],
            },
        }

    fixtures = {}
    for name, (growth, margin, wacc, tg) in BOUNDARY_FIXTURES.items():
        baseline_growth_path = [growth] * 5
        baseline_margin_path = [margin] * 5
        bear = compute_scenario_full(
            BEAR_GROWTH_DELTA, BEAR_MARGIN_DELTA, BEAR_WACC_DELTA, BEAR_TG_DELTA,
            FIXTURE_BASE_REVENUE, baseline_growth_path, baseline_margin_path, wacc, tg,
            FIXTURE_TAX, FIXTURE_DEBT, FIXTURE_CASH, FIXTURE_SHARES,
        )
        bull = compute_scenario_full(
            BULL_GROWTH_DELTA, BULL_MARGIN_DELTA, BULL_WACC_DELTA, BULL_TG_DELTA,
            FIXTURE_BASE_REVENUE, baseline_growth_path, baseline_margin_path, wacc, tg,
            FIXTURE_TAX, FIXTURE_DEBT, FIXTURE_CASH, FIXTURE_SHARES,
        )
        fixtures[name] = {
            "bear": bear, "bull": bull,
            "baseline": {
                "base_revenue": FIXTURE_BASE_REVENUE, "growth": baseline_growth_path,
                "margin": baseline_margin_path, "wacc": wacc, "terminal_growth": tg,
                "tax": FIXTURE_TAX, "debt": FIXTURE_DEBT, "cash": FIXTURE_CASH, "shares": FIXTURE_SHARES,
            },
        }

    return {
        "version": 2,
        "sourceCommit": frozen_base["sourceCommit"],
        "created": "2026-09-13",
        "basedOn": "frozen-results.json (Base path/WACC/tax, independently validated 140/140); frozen-results-scenarios.json v1 preserved unchanged alongside this file",
        "scenarioConvention": SCENARIO_CONVENTION_DESCRIPTION,
        "independenceCaveat": (
            "Calculation code is independent (no import of src/dcf_model/scenarios.py); "
            "policy discovery was not blind (the pinned deltas/clamp rule were read from "
            "scenarios.py before this module was authored). See this file's module "
            "docstring and docs/limitations-register.md L-021."
        ),
        "companies": companies,
        "boundaryFixtures": fixtures,
    }


if __name__ == "__main__":
    result = build()
    out_path = HERE / "frozen-results-scenarios-v2.json"
    text = json.dumps(result, indent=2, sort_keys=False) + "\n"
    out_path.write_text(text)
    scenarios_v2_digest = hashlib.sha256(text.encode()).hexdigest()

    base_path = HERE / "frozen-results.json"
    base_digest = hashlib.sha256(base_path.read_bytes()).hexdigest()

    manifest = {
        "description": (
            "SHA-256 checksums recorded at v2 freeze time. The comparator "
            "(compare_scenarios_v2_after_freeze.py) recomputes both hashes "
            "before running any comparison and aborts on drift."
        ),
        "files": {
            "frozen-results.json": {"sha256": base_digest, "role": "Base input dependency (untouched, from v1/original freeze)"},
            "frozen-results-scenarios-v2.json": {"sha256": scenarios_v2_digest, "role": "This module's own frozen output"},
        },
    }
    manifest_path = HERE / "frozen-manifest-v2.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"Wrote {out_path}")
    print(f"frozen-results-scenarios-v2.json SHA-256: {scenarios_v2_digest}")
    print(f"frozen-results.json SHA-256 (dependency, unchanged): {base_digest}")
    print(f"Wrote {manifest_path}")
