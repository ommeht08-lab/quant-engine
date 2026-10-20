"""
Monte Carlo DCF: how much does uncertainty in growth, operating margin and the
discount rate change estimated equity value per share?

Four experiments (growth only, margin only, WACC only, combined) each target
`config.target_valid` valid valuations. The deterministic model is never
changed: `run_dcf_valuation` is called once to resolve the baseline (statement
inputs, tax, capex, equity bridge, applied WACC, capped growth), and the
vectorized kernel in `monte_carlo_kernel` re-values draws from those resolved
inputs. Every run first checks the kernel against the scalar pipeline at the
baseline and records the result.

`run_monte_carlo` is a pure function of (financial data, risk-free rate,
config, seed, identity): the returned payload contains no timestamps, timings
or cache state, so two runs from the same snapshot, config and seed in the same
software environment produce byte-identical canonical payloads. Volatile
request metadata is added by the service layer under `meta`, outside the digest.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple, Union

import numpy as np

from src.dcf_model.dcf import (
    DCFAssumptions,
    MultiStageForecastPolicy,
    run_dcf_valuation,
)
from src.dcf_model.monte_carlo_config import (
    DEFAULT_CONFIG,
    DEFAULT_SEED,
    EXPERIMENT_NAMES,
    MONTE_CARLO_MODEL_VERSION,
    RESULT_SCHEMA,
    MonteCarloConfig,
    MonteCarloRefusal,
    canonical_json,
    config_hash,
    sha256_hex,
    validate_seed,
)
from src.dcf_model.monte_carlo_history import HistoricalSpreads, extract_historical_spreads
from src.dcf_model.monte_carlo_provenance import build_baseline_provenance, unavailable_provenance
from src.dcf_model.monte_carlo_kernel import (
    FAILURE_CODE_NAMES,
    KernelInputs,
    evaluate_valuations,
)
from src.dcf_model.monte_carlo_sampling import (
    SeededStreams,
    TruncatedNormalSpec,
    WaccIntervalSpec,
    build_truncated_normal_spec,
    build_wacc_spec,
    draw_inputs,
)

logger = logging.getLogger(__name__)

# Relative disagreement between the kernel and the scalar pipeline at the
# baseline above which the run is refused (a real defect, not rounding).
KERNEL_MISMATCH_REFUSAL_REL_TOLERANCE = 1e-9

DISCLOSURES: List[Dict[str, str]] = [
    {
        "id": "independence",
        "text": "Growth, operating margin and WACC are sampled independently; real-world correlations "
        "between them are ignored.",
    },
    {
        "id": "persistent_rates",
        "text": "Each draw applies one growth rate (the starting rate under the maturation forecast) and one "
        "margin to the whole forecast. The growth spread is the volatility of year-to-year growth, which "
        "likely overstates uncertainty in a single persistent rate.",
    },
    {
        "id": "noisy_spreads",
        "text": "Spreads are sample standard deviations (ddof=1) of a few annual observations, so they are noisy "
        "estimates. As an illustration only: if the observations were independent draws from one normal "
        "distribution, a 90% interval for the true spread from three observations would run from about 0.58x "
        "to 4.4x the estimate. Annual growth and margins are typically autocorrelated, skewed or shifting, so "
        "this is not a distribution-free confidence statement and the true uncertainty can be larger.",
    },
    {
        "id": "truncation",
        "text": "Growth and margin are truncated normals sampled by inverse CDF (no clipping, no rejected "
        "proposals). The reported excluded mass is the probability of the untruncated normal outside the "
        "bounds, not a count of draws; a truncated distribution's mean can differ from its center.",
    },
    {
        "id": "centers_distinct",
        "text": "The growth distribution is centered on the uncapped historical revenue CAGR; the deterministic "
        "baseline applies the 25% ceiling to that CAGR; and the sampled values come from the truncated "
        "distribution. These three are reported separately.",
    },
    {
        "id": "wacc",
        "text": "Sampled WACC replaces the CAPM-derived rate in both the terminal value and discounting. It is "
        "uniform within the applied baseline +/-1 percentage point, intersected with the model's 5%-20% "
        "bounds and kept at least 1 point above terminal growth. Any clamping of the baseline and the "
        "resulting interval are reported.",
    },
    {
        "id": "fixed_inputs",
        "text": "Terminal growth, tax rate, D&A, capex, working capital, debt, cash and share count are held "
        "fixed at the deterministic model's resolved values.",
    },
    {
        "id": "failed_draw_resampling",
        "text": "A draw whose valuation fails is redrawn within a bounded retry budget. This conditions the "
        "simulated distribution on success; the counts of failures by cause are reported.",
    },
    {
        "id": "market_price",
        "text": "The market price is the quote captured with the snapshot. The data provider does not supply a "
        "quote time, so the price time is unknown; the snapshot capture time only bounds it from above. "
        "Comparisons count simulated values strictly above that price.",
    },
    {
        "id": "margin_definition",
        "text": "The v1 margin basis is the provider's (Yahoo) EBIT row, falling back to its Operating Income row only "
        "for a period with no EBIT value. Neither row is a normalized or adjusted operating-earnings figure. The "
        "EBIT row can include non-operating and special items that the Operating Income row excludes, so the margin "
        "center, the spread and the ordering of single-input widths can change materially with the row used (an "
        "offline comparison found this for JNJ). The rows and periods actually used are listed under the baseline sources.",
    },
    {
        "id": "not_prediction",
        "text": "Single-input experiments show sensitivity under the selected ranges. They are not a formal "
        "variance attribution, a probability of mispricing, or evidence of predictive accuracy.",
    },
    {
        "id": "data_scope",
        "text": "History is limited to the annual statement periods the provider returns.",
    },
]


@dataclass
class RawExperiment:
    values: np.ndarray
    inputs: Dict[str, np.ndarray]
    attempted: int
    failed_by_code: Dict[str, int]


@dataclass
class ExperimentSpecs:
    growth: Union[TruncatedNormalSpec, MonteCarloRefusal]
    margin: Union[TruncatedNormalSpec, MonteCarloRefusal]
    wacc: Union[WaccIntervalSpec, MonteCarloRefusal]


@dataclass
class Baseline:
    """Everything resolved once by the deterministic model."""

    result: Mapping
    kernel_inputs: KernelInputs
    fixed_growth: float
    fixed_margin: float
    fixed_wacc: float
    market_price: float


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------

def dcf_assumptions_for(config: MonteCarloConfig, risk_free_rate: float) -> DCFAssumptions:
    """The deterministic assumptions every experiment is anchored to."""
    policy = (
        MultiStageForecastPolicy(
            near_term_years=config.near_term_years,
            mature_growth_ceiling=config.mature_growth_ceiling,
        )
        if config.forecast_mode == "maturation"
        else None
    )
    return DCFAssumptions(
        terminal_growth_rate=config.terminal_growth_rate,
        projection_years=config.projection_years,
        risk_free_rate=risk_free_rate,
        forecast_policy=policy,
    )


def resolve_baseline(
    financial_data: Mapping, risk_free_rate: float, config: MonteCarloConfig
) -> Baseline:
    """Run the deterministic model once and capture the resolved inputs."""
    assumptions = dcf_assumptions_for(config, risk_free_rate)
    try:
        result = run_dcf_valuation(dict(financial_data), assumptions)
    except ValueError as exc:
        raise MonteCarloRefusal("input_incomplete", f"The deterministic valuation failed: {exc}") from exc

    kernel_inputs = KernelInputs(
        base_revenue=result["base_revenue"],
        tax_rate=result["tax_rate"],
        da_pct_revenue=assumptions.da_pct_revenue,
        capex_pct_revenue=result["capex_pct_revenue"],
        nwc_pct_revenue_change=assumptions.nwc_pct_revenue_change,
        terminal_growth_rate=assumptions.terminal_growth_rate,
        projection_years=assumptions.projection_years,
        total_debt=result["total_debt"] or 0.0,
        cash_and_equivalents=result["cash_and_equivalents"] or 0.0,
        shares_outstanding=result["shares_outstanding"],
        forecast_mode=config.forecast_mode,
        near_term_years=config.near_term_years,
        mature_growth_ceiling=config.mature_growth_ceiling,
    )
    return Baseline(
        result=result,
        kernel_inputs=kernel_inputs,
        fixed_growth=result["revenue_growth_rate"],
        fixed_margin=result["operating_margin"],
        fixed_wacc=result["wacc"],
        market_price=result["current_market_price"],
    )


def kernel_baseline_check(baseline: Baseline) -> dict:
    """Value the baseline triple with the kernel and compare with the scalar result."""
    values, codes = evaluate_valuations(
        baseline.kernel_inputs,
        np.array([baseline.fixed_growth]),
        np.array([baseline.fixed_margin]),
        np.array([baseline.fixed_wacc]),
    )
    scalar = float(baseline.result["intrinsic_value_per_share"])
    if codes[0] != 0:
        raise MonteCarloRefusal(
            "kernel_baseline_mismatch",
            f"The vectorized kernel could not value the deterministic baseline "
            f"({FAILURE_CODE_NAMES.get(int(codes[0]), 'unknown')}).",
        )
    kernel = float(values[0])
    difference = abs(kernel - scalar)
    relative = difference / abs(scalar) if scalar != 0 else difference
    if relative > KERNEL_MISMATCH_REFUSAL_REL_TOLERANCE:
        raise MonteCarloRefusal(
            "kernel_baseline_mismatch",
            "The vectorized kernel disagrees with the deterministic valuation at the baseline.",
        )
    return {
        "scalar_value_per_share": scalar,
        "kernel_value_per_share": kernel,
        "absolute_difference": difference,
        "exactly_equal": kernel == scalar,
    }


# --------------------------------------------------------------------------
# Specs
# --------------------------------------------------------------------------

def build_specs(
    baseline: Baseline,
    history: Union[HistoricalSpreads, MonteCarloRefusal],
    config: MonteCarloConfig,
) -> ExperimentSpecs:
    """Turn history and the baseline WACC into sampling specs (or per-input refusals)."""
    if isinstance(history, MonteCarloRefusal):
        growth: Union[TruncatedNormalSpec, MonteCarloRefusal] = history
        margin: Union[TruncatedNormalSpec, MonteCarloRefusal] = history
    else:
        growth = _try(
            build_truncated_normal_spec,
            "growth", history.growth_center_cagr, history.growth_sd,
            config.growth_lower, config.growth_upper, config.min_admissible_mass,
        )
        margin = _try(
            build_truncated_normal_spec,
            "margin", history.margin_center, history.margin_sd,
            config.margin_lower, config.margin_upper, config.min_admissible_mass,
        )
    wacc = _try(
        build_wacc_spec,
        baseline.fixed_wacc, bool(baseline.result["wacc_was_clamped"]), config,
    )
    return ExperimentSpecs(growth=growth, margin=margin, wacc=wacc)


def _try(builder, *args):
    try:
        return builder(*args)
    except MonteCarloRefusal as refusal:
        return refusal


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------

def _simulate(
    baseline: Baseline,
    config: MonteCarloConfig,
    seed: int,
    growth: Optional[TruncatedNormalSpec],
    margin: Optional[TruncatedNormalSpec],
    wacc: Optional[WaccIntervalSpec],
) -> RawExperiment:
    """Draw until `target_valid` valid valuations or the bounded retry budget is spent."""
    streams = SeededStreams(seed)
    value_chunks: List[np.ndarray] = []
    input_chunks: Dict[str, List[np.ndarray]] = {"growth": [], "margin": [], "wacc": []}
    failures: Counter = Counter()
    valid_total = 0
    attempted = 0
    extra_used = 0
    rounds = 0
    batch = config.target_valid

    while True:
        draws = draw_inputs(
            streams, batch, growth, margin, wacc,
            baseline.fixed_growth, baseline.fixed_margin, baseline.fixed_wacc,
        )
        values, codes = evaluate_valuations(
            baseline.kernel_inputs, draws["growth"], draws["margin"], draws["wacc"]
        )
        attempted += batch
        ok = codes == 0
        value_chunks.append(values[ok])
        for name in input_chunks:
            input_chunks[name].append(draws[name][ok])
        for code, count in zip(*np.unique(codes[~ok], return_counts=True)):
            failures[FAILURE_CODE_NAMES[int(code)]] += int(count)
        valid_total += int(ok.sum())

        need = config.target_valid - valid_total
        if need <= 0 or rounds >= config.max_retry_rounds:
            break
        batch = min(need, config.max_extra_attempts - extra_used)
        if batch <= 0:
            break
        extra_used += batch
        rounds += 1

    return RawExperiment(
        values=np.concatenate(value_chunks) if value_chunks else np.empty(0),
        inputs={name: np.concatenate(chunks) for name, chunks in input_chunks.items()},
        attempted=attempted,
        failed_by_code={name: int(failures.get(name, 0)) for name in FAILURE_CODE_NAMES.values()},
    )


def _experiment_plan(
    specs: ExperimentSpecs,
) -> Dict[str, Tuple[Optional[MonteCarloRefusal], Tuple[bool, bool, bool]]]:
    """For each experiment: its first blocking refusal (if any) and which inputs it samples."""
    sources = {"growth": specs.growth, "margin": specs.margin, "wacc": specs.wacc}
    plan = {}
    for name, active in (
        ("growth", (True, False, False)),
        ("margin", (False, True, False)),
        ("wacc", (False, False, True)),
        ("combined", (True, True, True)),
    ):
        refusal = None
        for flag, key in zip(active, ("growth", "margin", "wacc")):
            if flag and isinstance(sources[key], MonteCarloRefusal):
                refusal = sources[key]
                break
        plan[name] = (refusal, active)
    return plan


def _exact_mean(values: np.ndarray) -> float:
    """Correctly rounded mean, independent of NumPy's version-dependent summation order.

    `np.mean` blocks its additions differently across NumPy releases, which moved
    the last digit of a mean between environments; `math.fsum` is exact, so the
    reported means (and therefore the result digest) do not depend on that.
    """
    return math.fsum(values.tolist()) / values.size


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------

def _histogram_edges(
    raw: Mapping[str, RawExperiment], config: MonteCarloConfig
) -> Tuple[Optional[List[float]], Optional[str]]:
    """Shared bin edges: from the combined run if it is sizeable, else pooled valid values."""
    combined = raw.get("combined")
    if combined is not None and combined.values.size >= config.histogram_min_basis_draws:
        basis, label = combined.values, "combined"
    else:
        pooled = [r.values for r in raw.values() if r.values.size]
        if not pooled:
            return None, None
        basis, label = np.concatenate(pooled), "pooled"
    low, high = np.percentile(
        basis, [config.histogram_low_percentile, config.histogram_high_percentile],
        method=config.percentile_method,
    )
    low, high = float(low), float(high)
    if not (math.isfinite(low) and math.isfinite(high)):
        return None, None
    if high - low <= 0:
        pad = max(abs(low) * 0.01, 1e-6)
        low, high = low - pad, high + pad
    return [float(edge) for edge in np.linspace(low, high, config.histogram_bins + 1)], label


def _summarize_experiment(
    name: str,
    raw: Optional[RawExperiment],
    refusal: Optional[MonteCarloRefusal],
    active: Tuple[bool, bool, bool],
    specs: ExperimentSpecs,
    baseline: Baseline,
    config: MonteCarloConfig,
    edges: Optional[List[float]],
) -> dict:
    sampled = {
        key: spec
        for key, flag, spec in zip(("growth", "margin", "wacc"), active, (specs.growth, specs.margin, specs.wacc))
        if flag and not isinstance(spec, MonteCarloRefusal)
    }
    distributions = {key: spec.to_dict() for key, spec in sampled.items()}
    fixed = {
        key: value
        for key, flag, value in zip(
            ("growth", "margin", "wacc"), active,
            (baseline.fixed_growth, baseline.fixed_margin, baseline.fixed_wacc),
        )
        if not flag
    }
    out = {
        "status": "refused" if refusal is not None else None,
        "target": config.target_valid,
        "sampled_inputs": [key for key, flag in zip(("growth", "margin", "wacc"), active) if flag],
        "fixed_inputs_at_baseline": fixed,
        "distributions": distributions,
    }
    if refusal is not None or raw is None:
        out.update(
            {
                "refusal_code": refusal.code if refusal else None,
                "refusal_message": refusal.message if refusal else None,
                "valid": 0,
                "attempted": 0,
                "failed_total": 0,
                "failed_by_code": {},
                "rejected_proposals": 0,
                "above_market": None,
                "statistics": None,
                "histogram": None,
                "sampled_input_summary": None,
                "negative_value_count": 0,
            }
        )
        return out

    values = raw.values
    valid = int(values.size)
    out["status"] = "complete" if valid >= config.target_valid else "incomplete"
    out.update(
        {
            "refusal_code": None,
            "refusal_message": None,
            "valid": valid,
            "attempted": raw.attempted,
            "failed_total": int(sum(raw.failed_by_code.values())),
            "failed_by_code": raw.failed_by_code,
            # Inverse-CDF truncation maps every draw inside the bounds; nothing is proposed and rejected.
            "rejected_proposals": 0,
            "negative_value_count": int((values < 0).sum()) if valid else 0,
        }
    )
    if valid:
        p10, p50, p90 = np.percentile(values, [10, 50, 90], method=config.percentile_method)
        count_above = int((values > baseline.market_price).sum())
        out["statistics"] = {
            "mean": _exact_mean(values),
            "p10": float(p10),
            "p50": float(p50),
            "p90": float(p90),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
        }
        out["above_market"] = {
            "count": count_above,
            "denominator": valid,
            "fraction": count_above / valid,
        }
        out["sampled_input_summary"] = {
            key: {
                "min": float(np.min(raw.inputs[key])),
                "mean": _exact_mean(raw.inputs[key]),
                "max": float(np.max(raw.inputs[key])),
            }
            for key in sampled
        }
        if edges is not None:
            counts, _ = np.histogram(values, bins=np.asarray(edges))
            out["histogram"] = {
                "counts": [int(c) for c in counts],
                "underflow": int((values < edges[0]).sum()),
                "overflow": int((values > edges[-1]).sum()),
            }
        else:
            out["histogram"] = None
    else:
        out["statistics"] = None
        out["above_market"] = None
        out["sampled_input_summary"] = None
        out["histogram"] = None
    return out


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def run_experiments(baseline: Baseline, specs: ExperimentSpecs, config: MonteCarloConfig, seed: int) -> dict:
    """Run the four experiments from prepared specs; returns summaries and shared bin edges."""
    plan = _experiment_plan(specs)
    raw: Dict[str, RawExperiment] = {}
    for name in EXPERIMENT_NAMES:
        refusal, active = plan[name]
        if refusal is not None:
            continue
        raw[name] = _simulate(
            baseline, config, seed,
            specs.growth if active[0] else None,
            specs.margin if active[1] else None,
            specs.wacc if active[2] else None,
        )
    edges, edges_basis = _histogram_edges(raw, config)
    experiments = {
        name: _summarize_experiment(
            name, raw.get(name), plan[name][0], plan[name][1], specs, baseline, config, edges
        )
        for name in EXPERIMENT_NAMES
    }
    return {"experiments": experiments, "histogram_edges": edges, "histogram_edges_basis": edges_basis}


def _overall_status(experiments: Mapping[str, dict]) -> str:
    if all(e["status"] == "complete" for e in experiments.values()):
        return "complete"
    if any(e["valid"] > 0 for e in experiments.values()):
        return "partial"
    return "incomplete"


def _forecast_path(result: Mapping) -> List[dict]:
    return [
        {
            "year": step.year,
            "stage": step.stage,
            "revenue_growth_rate": step.revenue_growth_rate,
            "operating_margin": step.operating_margin,
        }
        for step in result["forecast_path"]
    ]


def run_monte_carlo(
    financial_data: Mapping,
    *,
    risk_free_rate: float,
    config: MonteCarloConfig = DEFAULT_CONFIG,
    seed: int = DEFAULT_SEED,
    identity: Optional[Mapping] = None,
    provenance_context: Optional[Mapping] = None,
) -> dict:
    """Run all four experiments on one snapshot's financial data.

    Raises `MonteCarloRefusal` only when the whole run is impossible (the
    deterministic valuation fails, or the kernel disagrees with it). Per-input
    refusals (history, truncation mass, WACC interval) are reported inside the
    affected experiments so unaffected experiments still return results.
    """
    seed = validate_seed(seed)
    baseline = resolve_baseline(financial_data, risk_free_rate, config)
    kernel_check = kernel_baseline_check(baseline)
    try:
        history: Union[HistoricalSpreads, MonteCarloRefusal] = extract_historical_spreads(
            financial_data.get("income_statement"), config
        )
    except MonteCarloRefusal as refusal:
        history = refusal
    specs = build_specs(baseline, history, config)
    ran = run_experiments(baseline, specs, config, seed)
    result = baseline.result
    try:
        provenance = build_baseline_provenance(
            financial_data, baseline=baseline, history=history, specs=specs, config=config,
            risk_free_rate=risk_free_rate, context=provenance_context,
        )
    except Exception as exc:  # noqa: BLE001 - descriptive metadata must never void a finished simulation
        logger.exception("Baseline provenance could not be built; the numerical result is unaffected.")
        provenance = unavailable_provenance(exc)

    growth_cagr = None if isinstance(history, MonteCarloRefusal) else history.growth_center_cagr
    payload = {
        "schema": RESULT_SCHEMA,
        "model_version": MONTE_CARLO_MODEL_VERSION,
        "status": _overall_status(ran["experiments"]),
        "identity": dict(identity or {}),
        "seed": seed,
        "config_hash": config_hash(config),
        "config": config.to_canonical_dict(),
        "market_price": baseline.market_price,
        "baseline": {
            "intrinsic_value_per_share": result["intrinsic_value_per_share"],
            "equity_value": result["equity_value"],
            "enterprise_value": result["enterprise_value"],
            "wacc": result["wacc"],
            "wacc_pre_clamp": result["wacc_pre_clamp"],
            "wacc_was_clamped": bool(result["wacc_was_clamped"]),
            "risk_free_rate": risk_free_rate,
            "revenue_growth_rate": result["revenue_growth_rate"],
            "revenue_growth_rate_was_capped": (
                growth_cagr is not None and growth_cagr > result["revenue_growth_rate"]
            ),
            "operating_margin": result["operating_margin"],
            "tax_rate": result["tax_rate"],
            "capex_pct_revenue": result["capex_pct_revenue"],
            "base_revenue": result["base_revenue"],
            "total_debt": result["total_debt"],
            "cash_and_equivalents": result["cash_and_equivalents"],
            "shares_outstanding": result["shares_outstanding"],
            "forecast_method": result["forecast_method"],
            "forecast_path": _forecast_path(result),
        },
        "kernel_baseline_check": kernel_check,
        "history": (
            {"status": "refused", "refusal_code": history.code, "refusal_message": history.message}
            if isinstance(history, MonteCarloRefusal)
            else {"status": "ok", **history.to_dict()}
        ),
        "experiments": ran["experiments"],
        "histogram_edges": ran["histogram_edges"],
        "histogram_edges_basis": ran["histogram_edges_basis"],
        "disclosures": DISCLOSURES,
        "baseline_provenance": provenance,
    }
    return with_digest(payload)


# Keys that are not part of the numerical digest: volatile request metadata, the
# digest itself, descriptive provenance (covered by its own `provenance_digest`)
# and disclosure copy (presentation, revised independently of the numbers).
NON_NUMERICAL_KEYS = ("meta", "result_digest", "baseline_provenance", "disclosures")


def canonical_payload(result: Mapping) -> dict:
    """The numerical payload: everything except the keys in `NON_NUMERICAL_KEYS`."""
    return {k: v for k, v in result.items() if k not in NON_NUMERICAL_KEYS}


def with_digest(payload: Mapping) -> dict:
    """Attach the digest of the numerical payload; the full payload is returned unchanged otherwise."""
    kept = {k: v for k, v in payload.items() if k not in ("meta", "result_digest")}
    return {**kept, "result_digest": sha256_hex(canonical_json(canonical_payload(payload)))}


def results_identical(a: Mapping, b: Mapping) -> bool:
    """True when two results have identical canonical numerical payloads."""
    return canonical_json(canonical_payload(a)) == canonical_json(canonical_payload(b))


# --------------------------------------------------------------------------
# Offline replay CLI
# --------------------------------------------------------------------------

_LEVEL_MEANING = {
    "exact_replay": "Integrity verified, and recomputing here reproduced the recorded result digest bit for bit.",
    "numerically_equivalent_replay": (
        "Integrity verified. The recomputed numbers are not bit-identical to the recorded ones (different CPU, math library or "
        "numpy build) but agree within the versioned numerical policy, and every count, status and key is equal."
    ),
    "numerical_mismatch": "Integrity verified, but the recomputed numbers differ from the recorded ones beyond the numerical policy.",
    "integrity_verified_only": (
        "Integrity verified, but the recomputed digest differs and the bundle carries no recorded numbers to compare with. "
        "Pass --reference-result with the original result, or replay on the platform that exported it."
    ),
    "unverified": "No integrity digest and no matching recorded result: nothing about this replay is established.",
}


def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay a Monte Carlo DCF replay bundle offline. Reports integrity, exact replay and numerical "
            "equivalence separately. Exit status: 0 exact or numerically equivalent; 1 integrity failure or "
            "numerical mismatch; 2 integrity verified but the numbers could not be confirmed."
        )
    )
    parser.add_argument("--bundle", required=True, help="Path to a replay bundle JSON file.")
    parser.add_argument("--output", help="Write the recomputed result JSON here.")
    parser.add_argument(
        "--reference-result",
        help="Original result JSON to compare against (for bundles exported without recorded numbers). "
        "It must hash to the bundle's recorded result digest.",
    )
    args = parser.parse_args(argv)

    from src.dcf_model.monte_carlo_snapshot import SnapshotError, replay_bundle

    with open(args.bundle, "r", encoding="utf-8") as handle:
        bundle = json.load(handle)
    reference = None
    if args.reference_result:
        with open(args.reference_result, "r", encoding="utf-8") as handle:
            reference = json.load(handle)
    try:
        outcome = replay_bundle(bundle, reference_result=reference)
    except SnapshotError as error:
        print(json.dumps({"verification_level": "integrity_failed", "integrity_verified": False, "reason": str(error)}, indent=2))
        return 1
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(outcome["result"], handle, sort_keys=True, allow_nan=False)
    report = {k: v for k, v in outcome.items() if k != "result"}
    report["meaning"] = _LEVEL_MEANING[outcome["verification_level"]]
    print(json.dumps(report, indent=2, sort_keys=True))
    level = outcome["verification_level"]
    if level in ("exact_replay", "numerically_equivalent_replay"):
        return 0
    return 1 if level == "numerical_mismatch" else 2


if __name__ == "__main__":
    sys.exit(_main())
