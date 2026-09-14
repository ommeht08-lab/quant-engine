"""Independent Bear/Base/Bull scenario calculation, frozen BEFORE comparison.

Purpose
-------
`frozen-results.json` (this directory) froze an independently-built Base
valuation for five cases and reconciled it against production
(140/140 intermediate comparisons, largest difference 1.39e-17) -- but its
own Bear/Bull cells used a *guessed* scenario convention (growth +/-2pp,
margin +/-1pp), because the written specification was silent on the actual
production deltas at freeze time. That gap is now closed in
`docs/model-specifications/dcf.md` ("Bear / Base / Bull scenarios") and
`docs/assumptions-register.md` (A-029, A-030) -- see `L-021` in the
limitations register.

This module is the independent (re-)calculation of Bear/Bull built FROM that
now-pinned specification, not from `src/dcf_model/scenarios.py`. It imports
nothing from `src.dcf_model` -- every formula below is written fresh from the
spec text, exactly like the original workbook's Excel formulas were. It
reuses the already-independently-validated Base per-year growth/margin path,
WACC, and tax rate frozen in `frozen-results.json` (see that file's own
freeze/reconciliation evidence) as the baseline three inputs a Bear/Bull
scenario shifts -- re-deriving WACC/CAGR/margin a second time here would not
add evidence, since that arithmetic already has an independent, passing
reconciliation.

Run this file directly to (re)generate `frozen-results-scenarios.json` and
print its SHA-256. Do not edit the generated file by hand; rebuilding it
constitutes a new freeze and invalidates any existing comparison against it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent  # validation/independent_dcf

# Pinned bounds (docs/model-specifications/dcf.md "Bear / Base / Bull
# scenarios"; docs/assumptions-register.md A-029/A-030). Hardcoded here,
# independently of src/dcf_model/dcf.py's own MIN_EXPLICIT_*/MAX_EXPLICIT_*
# constants, on purpose -- an independent check must not import the module
# it is meant to catch bugs in.
MIN_REVENUE_GROWTH, MAX_REVENUE_GROWTH = -0.10, 0.40
MIN_MARGIN, MAX_MARGIN = 0.0, 0.60
MIN_WACC, MAX_WACC = 0.05, 0.20
MIN_TERMINAL_GROWTH, MAX_TERMINAL_GROWTH = 0.0, 0.05

# Pinned scenario deltas (same source).
BEAR_GROWTH_DELTA, BEAR_MARGIN_DELTA, BEAR_WACC_DELTA, BEAR_TG_DELTA = -0.03, -0.02, 0.01, -0.005
BULL_GROWTH_DELTA, BULL_MARGIN_DELTA, BULL_WACC_DELTA, BULL_TG_DELTA = 0.03, 0.02, -0.01, 0.005

DA_PCT_REVENUE = 0.03
CAPEX_PCT_REVENUE = 0.04
NWC_PCT_REVENUE_CHANGE = 0.01
BASELINE_TERMINAL_GROWTH = 0.025


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def shift_staged_rate(baseline_year_rate: float, delta: float, low: float, high: float) -> float:
    """Pinned shift/clamp rule (A-030): clamp only if the baseline year's own
    rate is already within the explicit-override bounds; otherwise shift
    unclamped, so an out-of-bounds historical rate's Bear case is never
    silently pulled back toward Base."""
    shifted = baseline_year_rate + delta
    if low <= baseline_year_rate <= high:
        return _clamp(shifted, low, high)
    return shifted


def project_fcf_path(base_revenue: float, growth: list[float], margin: list[float], tax_rate: float) -> tuple[list[float], list[float]]:
    """Independent re-implementation of the unlevered FCF projection
    (docs/model-specifications/dcf.md 'Free Cash Flow projection'). Returns
    (revenue_path, fcf_path), five years each."""
    revenues: list[float] = []
    fcfs: list[float] = []
    prior_revenue = base_revenue
    for g, m in zip(growth, margin):
        revenue = prior_revenue * (1 + g)
        ebit = revenue * m
        nopat = ebit * (1 - tax_rate)
        da = revenue * DA_PCT_REVENUE
        capex = revenue * CAPEX_PCT_REVENUE
        delta_nwc = NWC_PCT_REVENUE_CHANGE * (revenue - prior_revenue)
        fcf = nopat + da - capex - delta_nwc
        revenues.append(revenue)
        fcfs.append(fcf)
        prior_revenue = revenue
    return revenues, fcfs


def terminal_value(final_year_fcf: float, wacc: float, terminal_growth_rate: float) -> float:
    if wacc <= terminal_growth_rate:
        raise ValueError("WACC must exceed terminal growth rate for the perpetuity to converge.")
    return final_year_fcf * (1 + terminal_growth_rate) / (wacc - terminal_growth_rate)


def discount_and_bridge(fcfs: list[float], tv: float, wacc: float, debt: float, cash: float, shares: float) -> dict:
    pv_fcf = [fcf / (1 + wacc) ** (t + 1) for t, fcf in enumerate(fcfs)]
    pv_terminal = tv / (1 + wacc) ** len(fcfs)
    enterprise_value = sum(pv_fcf) + pv_terminal
    equity_value = enterprise_value - debt + cash
    per_share = equity_value / shares
    return {
        "pv_fcf": pv_fcf,
        "pv_terminal": pv_terminal,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "per_share": per_share,
    }


def compute_scenario(
    growth_delta: float,
    margin_delta: float,
    wacc_delta: float,
    tg_delta: float,
    base_revenue: float,
    baseline_growth_path: list[float],
    baseline_margin_path: list[float],
    baseline_wacc: float,
    tax_rate: float,
    debt: float,
    cash: float,
    shares: float,
) -> dict:
    growth_path = [shift_staged_rate(g, growth_delta, MIN_REVENUE_GROWTH, MAX_REVENUE_GROWTH) for g in baseline_growth_path]
    margin_path = [shift_staged_rate(m, margin_delta, MIN_MARGIN, MAX_MARGIN) for m in baseline_margin_path]
    wacc = _clamp(baseline_wacc + wacc_delta, MIN_WACC, MAX_WACC)
    tg = _clamp(BASELINE_TERMINAL_GROWTH + tg_delta, MIN_TERMINAL_GROWTH, MAX_TERMINAL_GROWTH)
    try:
        revenues, fcfs = project_fcf_path(base_revenue, growth_path, margin_path, tax_rate)
        tv = terminal_value(fcfs[-1], wacc, tg)
        bridge = discount_and_bridge(fcfs, tv, wacc, debt, cash, shares)
        return {
            "growth": growth_path,
            "margin": margin_path,
            "wacc": wacc,
            "terminal_growth": tg,
            "per_share": bridge["per_share"],
            "invalid_reason": None,
        }
    except ValueError as exc:
        return {
            "growth": growth_path,
            "margin": margin_path,
            "wacc": wacc,
            "terminal_growth": tg,
            "per_share": None,
            "invalid_reason": str(exc),
        }


def build() -> dict:
    frozen_base = json.loads((HERE / "frozen-results.json").read_text())

    # Synthetic negative-margin case: same construction as build.mjs / compare_after_freeze.py.
    synth_years = [(1000 + 50 * i) * 1e6 for i in range(5)]
    fixed_inputs = {
        "MSFT": None, "CAT": None, "INTC": None, "VZ": None,  # filled from snapshots below
        "NEGATIVE": {"debt": 100e6, "cash": 50e6, "shares": 100e6, "base_revenue": synth_years[-1]},
    }
    for ticker in ("MSFT", "CAT", "INTC", "VZ"):
        snap = json.loads((BASE / f"snapshots/{ticker}_snapshot.json").read_text())
        fact, market = snap["latest_year_facts"], snap["market_data"]
        base_revenue = snap["historical_annual_data"][-1]["revenue_raw_usd"]
        fixed_inputs[ticker] = {
            "debt": fact["total_debt_usd"],
            "cash": fact["cash_and_equivalents_usd"],
            "shares": market["shares_outstanding"],
            "base_revenue": base_revenue,
        }

    companies = {}
    for ticker, entry in frozen_base["companies"].items():
        fi = fixed_inputs[ticker]
        baseline_growth_path = entry["base"]["growth"]
        baseline_margin_path = entry["base"]["margin"]
        baseline_wacc = entry["inputs"]["wacc"]
        tax_rate = entry["inputs"]["tax"]

        bear = compute_scenario(
            BEAR_GROWTH_DELTA, BEAR_MARGIN_DELTA, BEAR_WACC_DELTA, BEAR_TG_DELTA,
            fi["base_revenue"], baseline_growth_path, baseline_margin_path, baseline_wacc,
            tax_rate, fi["debt"], fi["cash"], fi["shares"],
        )
        bull = compute_scenario(
            BULL_GROWTH_DELTA, BULL_MARGIN_DELTA, BULL_WACC_DELTA, BULL_TG_DELTA,
            fi["base_revenue"], baseline_growth_path, baseline_margin_path, baseline_wacc,
            tax_rate, fi["debt"], fi["cash"], fi["shares"],
        )
        companies[ticker] = {"bear": bear, "bull": bull}

    return {
        "sourceCommit": frozen_base["sourceCommit"],
        "created": "2026-09-13",
        "basedOn": "frozen-results.json (Base path/WACC/tax, independently validated 140/140)",
        "scenarioConvention": (
            "Pinned production convention per docs/model-specifications/dcf.md "
            "'Bear / Base / Bull scenarios' and docs/assumptions-register.md A-030: "
            "growth +/-3pp, margin +/-2pp, WACC -/+1pp, terminal growth -/+0.5pp "
            "(Bear/Bull respectively), staged per-year conditional shift/clamp."
        ),
        "companies": companies,
    }


if __name__ == "__main__":
    result = build()
    out_path = HERE / "frozen-results-scenarios.json"
    text = json.dumps(result, indent=2, sort_keys=False) + "\n"
    out_path.write_text(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    print(f"Wrote {out_path}")
    print(f"SHA-256: {digest}")
