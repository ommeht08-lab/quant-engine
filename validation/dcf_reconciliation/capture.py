"""
Phase 3: run the production DCF against the frozen-snapshot adapter and
capture every intermediate output at full precision. No network access;
no call to fetch_company_financials; financial_data is built exclusively
from validation/independent_dcf/snapshots/*.json via adapter.py.

Track A Phase 2C: the reconciliation TARGET is now the V2 independent
workbook (`independent_dcf_validation_v2.xlsx`), built from the corrected
`years_elapsed` specification (see `A-028`/`L-019` and
`validation/independent_dcf/README_v2.md`). V1 (`independent_dcf_validation.xlsx`)
is preserved unchanged and its hash is still recorded on every payload for
provenance/history, but reconciliation math runs against V2.

`build_codebase_outputs()` returns a fully JSON-serializable dict. Writing
it to disk (`write_codebase_outputs_json`) is a separate step so callers
that only need the in-memory values (e.g. reconcile.py) don't pay for a
redundant round trip through JSON.
"""
import hashlib
import json
import subprocess
from pathlib import Path

from src.dcf_model.dcf import DCFAssumptions, run_dcf_valuation

from validation.dcf_reconciliation.adapter import (
    SNAPSHOT_DIR,
    TICKERS,
    load_all,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
V1_WORKBOOK_PATH = (
    REPO_ROOT / "validation" / "independent_dcf" / "independent_dcf_validation.xlsx"
)
V2_WORKBOOK_PATH = (
    REPO_ROOT / "validation" / "independent_dcf" / "independent_dcf_validation_v2.xlsx"
)
# The actual reconciliation target as of Track A Phase 2C. Kept as a separate
# name (rather than repointing V1_WORKBOOK_PATH) so V1's own identity/hash
# stays unambiguous in code, not just in comments.
INDEPENDENT_WORKBOOK_PATH = V2_WORKBOOK_PATH

EXPECTED_V1_WORKBOOK_SHA256 = "942b5f096cf546d63487add31eb29b1b690f0819f923f01436093f9bbc75eba1"
EXPECTED_V2_WORKBOOK_SHA256 = "cd2d225cfe0825ff56aa8f866c3b80b6bec5f87a8b02dc3f76724e4abdd27d6b"


def _json_default(o):
    """Handles numpy scalar types (np.bool_, np.float64, np.int64, ...) --
    never a silent data change, just a type normalization for JSON output."""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(REPO_ROOT), text=True
    ).strip()


def default_assumptions_dict(assumptions: DCFAssumptions) -> dict:
    return {
        "revenue_growth_rate": assumptions.revenue_growth_rate,
        "operating_margin": assumptions.operating_margin,
        "terminal_growth_rate": assumptions.terminal_growth_rate,
        "projection_years": assumptions.projection_years,
        "tax_rate": assumptions.tax_rate,
        "risk_free_rate": assumptions.risk_free_rate,
        "market_risk_premium": assumptions.market_risk_premium,
        "da_pct_revenue": assumptions.da_pct_revenue,
        "capex_pct_revenue": assumptions.capex_pct_revenue,
        "nwc_pct_revenue_change": assumptions.nwc_pct_revenue_change,
    }


def run_one(ticker: str, snapshot: dict, financial_data: dict) -> dict:
    assumptions = DCFAssumptions()
    result = run_dcf_valuation(financial_data, assumptions)

    fcf = result["fcf_projection"]
    pv_fcf = result["pv_fcf"]

    return {
        "ticker": ticker,
        "wacc": result["wacc"],
        "revenue_growth_rate": result["revenue_growth_rate"],
        "operating_margin": result["operating_margin"],
        "fcf_projection": {
            "years": [int(y) for y in fcf.index],
            "revenue": {int(y): float(fcf.loc[y, "revenue"]) for y in fcf.index},
            "ebit": {int(y): float(fcf.loc[y, "ebit"]) for y in fcf.index},
            "nopat": {int(y): float(fcf.loc[y, "nopat"]) for y in fcf.index},
            "da": {int(y): float(fcf.loc[y, "da"]) for y in fcf.index},
            "capex": {int(y): float(fcf.loc[y, "capex"]) for y in fcf.index},
            "change_in_nwc": {int(y): float(fcf.loc[y, "change_in_nwc"]) for y in fcf.index},
            "fcf": {int(y): float(fcf.loc[y, "fcf"]) for y in fcf.index},
        },
        "terminal_value": result["terminal_value"],
        "pv_fcf": {int(y): float(pv_fcf.loc[y]) for y in pv_fcf.index},
        "pv_fcf_sum": float(pv_fcf.sum()),
        "pv_terminal_value": result["pv_terminal_value"],
        "enterprise_value": result["enterprise_value"],
        "equity_value": result["equity_value"],
        "intrinsic_value_per_share": result["intrinsic_value_per_share"],
        "current_market_price": result["current_market_price"],
        "market_enterprise_value": result["market_enterprise_value"],
        "fcf_yield": result["fcf_yield"],
        "assumptions_used": default_assumptions_dict(assumptions),
    }


def build_codebase_outputs() -> dict:
    """Returns the full deterministic payload (no wall-clock content)."""
    per_company = {}
    for ticker, (snapshot, financial_data) in load_all().items():
        per_company[ticker] = run_one(ticker, snapshot, financial_data)

    snapshot_hashes = {
        ticker: _sha256(SNAPSHOT_DIR / f"{ticker}_snapshot.json") for ticker in TICKERS
    }

    v1_hash = _sha256(V1_WORKBOOK_PATH)
    v2_hash = _sha256(V2_WORKBOOK_PATH)
    if v1_hash != EXPECTED_V1_WORKBOOK_SHA256:
        raise ValueError(
            f"V1 workbook SHA-256 changed: expected {EXPECTED_V1_WORKBOOK_SHA256}, got {v1_hash}. "
            "V1 must never be modified -- stop and investigate before proceeding."
        )
    if v2_hash != EXPECTED_V2_WORKBOOK_SHA256:
        raise ValueError(
            f"V2 workbook SHA-256 changed: expected {EXPECTED_V2_WORKBOOK_SHA256}, got {v2_hash}. "
            "V2 was frozen at the end of Track A Phase 3 -- if it was legitimately rebuilt, update "
            "EXPECTED_V2_WORKBOOK_SHA256 deliberately; do not silently reconcile against a workbook "
            "that no longer matches what was audited."
        )

    return {
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git("rev-parse", "HEAD"),
        "original_workbook_sha256": v1_hash,
        "v1_workbook_sha256": v1_hash,
        "v2_workbook_sha256": v2_hash,
        "reconciliation_target": "v2",
        "snapshot_sha256": snapshot_hashes,
        "companies": TICKERS,
        "assumptions": "DCFAssumptions() -- all production defaults, no explicit overrides",
        "assumption_values": default_assumptions_dict(DCFAssumptions()),
        "generation_command": "python3 -m validation.dcf_reconciliation.capture",
        "network_statement": (
            "Zero network requests were made to produce this file. financial_data "
            "for every company was constructed exclusively from the committed, "
            "frozen validation/independent_dcf/snapshots/*.json files via "
            "validation/dcf_reconciliation/adapter.py. "
            "src/data_ingestion/fetch_financials.py was not imported and "
            "fetch_company_financials() was not called."
        ),
        "results": per_company,
    }


def write_codebase_outputs_json(path: Path) -> dict:
    payload = build_codebase_outputs()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=_json_default)
        f.write("\n")
    return payload


if __name__ == "__main__":
    out_path = Path(__file__).resolve().parent / "codebase_outputs.json"
    payload = write_codebase_outputs_json(out_path)
    print(f"Wrote {out_path}")
    for ticker in TICKERS:
        r = payload["results"][ticker]
        print(f"  {ticker}: wacc={r['wacc']:.6f} IVPS={r['intrinsic_value_per_share']:.4f}")
