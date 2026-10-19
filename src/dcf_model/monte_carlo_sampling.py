"""
Samplers for the Monte Carlo DCF: truncated normal, uniform, and the seeded
random streams shared by every experiment.

Truncation is done by inverse-CDF sampling, never by clipping or by rejecting
proposals: a uniform draw is mapped into the admissible slice of the normal CDF
and then inverted, so every draw lies inside the bounds and no proposal is ever
rejected. The probability mass of the UNtruncated normal that falls outside the
bounds is reported separately; it is not a count of draws and the cap is not
something draws "hit".

`scipy.special` is imported lazily (it is the only heavy dependency here) so a
process that never simulates does not pay for it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from src.dcf_model.monte_carlo_config import MonteCarloConfig, MonteCarloRefusal

# A floating-point excursion beyond a bound larger than this is a sampler bug,
# not rounding, and raises instead of being silently clipped away.
_MAX_BOUND_EXCURSION = 1e-9
_P_MIN = float(np.finfo(np.float64).tiny)
_P_MAX = float(np.nextafter(1.0, 0.0))

STREAM_NAMES = ("growth", "margin", "wacc")


def _special():
    from scipy import special  # lazy: see module docstring

    return special


@dataclass(frozen=True)
class TruncatedNormalSpec:
    """A normal(center, sd) restricted to [lower, upper], with its excluded mass."""

    name: str
    center: float
    sd: float
    lower: float
    upper: float
    untruncated_mass_below: float
    untruncated_mass_above: float
    admissible_mass: float
    degenerate: bool

    def to_dict(self) -> dict:
        return {
            "distribution": "truncated_normal",
            "center": self.center,
            "sd": self.sd,
            "lower": self.lower,
            "upper": self.upper,
            "untruncated_mass_below_lower": self.untruncated_mass_below,
            "untruncated_mass_above_upper": self.untruncated_mass_above,
            "admissible_mass": self.admissible_mass,
            "degenerate": self.degenerate,
        }


def build_truncated_normal_spec(
    name: str, center: float, sd: float, lower: float, upper: float, min_admissible_mass: float
) -> TruncatedNormalSpec:
    """Validate and describe a truncated normal, or raise `MonteCarloRefusal`."""
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (center, sd, lower, upper)):
        raise MonteCarloRefusal("inadmissible_distribution", f"{name}: center, spread and bounds must be finite.")
    if sd < 0 or not lower < upper:
        raise MonteCarloRefusal("inadmissible_distribution", f"{name}: spread or bounds are invalid.")

    if sd == 0:
        if not lower <= center <= upper:
            raise MonteCarloRefusal(
                "degenerate_outside_bounds",
                f"{name}: the historical spread is zero and its center {center:.6g} lies outside "
                f"[{lower:.6g}, {upper:.6g}].",
            )
        return TruncatedNormalSpec(name, center, 0.0, lower, upper, 0.0, 0.0, 1.0, True)

    z_lo = (lower - center) / sd
    z_hi = (upper - center) / sd
    special = _special()
    below = float(special.ndtr(z_lo))
    above = float(special.ndtr(-z_hi))
    # Compute the slice in whichever tail keeps relative precision.
    if z_lo + z_hi > 0:
        admissible = float(special.ndtr(-z_lo) - special.ndtr(-z_hi))
    else:
        admissible = float(special.ndtr(z_hi) - special.ndtr(z_lo))
    if not math.isfinite(admissible) or admissible < min_admissible_mass:
        raise MonteCarloRefusal(
            "inadmissible_distribution",
            f"{name}: only {admissible:.3g} of the untruncated normal lies inside "
            f"[{lower:.6g}, {upper:.6g}] (minimum {min_admissible_mass:.3g}).",
        )
    return TruncatedNormalSpec(name, center, sd, lower, upper, below, above, admissible, False)


def sample_truncated_normal(spec: TruncatedNormalSpec, u: np.ndarray) -> np.ndarray:
    """Map uniform draws `u` in [0, 1) to the truncated normal by inverse CDF."""
    if spec.degenerate:
        return np.full(u.shape, spec.center, dtype=np.float64)
    special = _special()
    z_lo = (spec.lower - spec.center) / spec.sd
    z_hi = (spec.upper - spec.center) / spec.sd
    # Mirror an upper-tail slice into the lower tail, where the CDF keeps
    # relative precision, then mirror the result back.
    flip = z_lo + z_hi > 0
    a, b = (-z_hi, -z_lo) if flip else (z_lo, z_hi)
    p_lo = float(special.ndtr(a))
    p_hi = float(special.ndtr(b))
    p = np.clip(p_lo + u * (p_hi - p_lo), _P_MIN, _P_MAX)
    z = special.ndtri(p)
    if flip:
        z = -z
    x = spec.center + spec.sd * z
    return _clip_fp_excursions(x, spec.lower, spec.upper, spec.name)


def _clip_fp_excursions(x: np.ndarray, lower: float, upper: float, name: str) -> np.ndarray:
    excess = max(float(lower - x.min()), float(x.max() - upper), 0.0) if x.size else 0.0
    if not math.isfinite(excess) or excess > _MAX_BOUND_EXCURSION:
        raise RuntimeError(f"{name} sampler violated its bounds by {excess!r}.")
    return np.clip(x, lower, upper)


@dataclass(frozen=True)
class WaccIntervalSpec:
    """Uniform WACC sampling interval and what constrained it."""

    baseline: float
    baseline_was_clamped: bool
    half_width: float
    lower: float
    upper: float
    binding_constraints: List[str]
    degenerate: bool

    def to_dict(self) -> dict:
        return {
            "distribution": "uniform",
            "baseline_applied_wacc": self.baseline,
            "baseline_was_clamped_to_model_bounds": self.baseline_was_clamped,
            "requested_half_width": self.half_width,
            "lower": self.lower,
            "upper": self.upper,
            "interval_width": self.upper - self.lower,
            "binding_constraints": list(self.binding_constraints),
            "degenerate": self.degenerate,
        }


def build_wacc_spec(
    baseline_wacc: float, baseline_was_clamped: bool, config: MonteCarloConfig
) -> WaccIntervalSpec:
    """baseline +/- half_width, intersected with model bounds and kept above terminal growth."""
    if not isinstance(baseline_wacc, (int, float)) or not math.isfinite(baseline_wacc):
        raise MonteCarloRefusal("wacc_interval_empty", "The baseline WACC is not a finite number.")

    half = config.wacc_half_width
    lower_candidates = {
        "baseline_minus_half_width": baseline_wacc - half,
        "model_lower_bound": config.wacc_lower_bound,
        "terminal_growth_spread": config.terminal_growth_rate + config.wacc_min_spread_over_terminal,
    }
    upper_candidates = {
        "baseline_plus_half_width": baseline_wacc + half,
        "model_upper_bound": config.wacc_upper_bound,
    }
    lower = max(lower_candidates.values())
    upper = min(upper_candidates.values())
    binding = [
        name
        for name, value in lower_candidates.items()
        if value == lower and name != "baseline_minus_half_width"
    ] + [
        name
        for name, value in upper_candidates.items()
        if value == upper and name != "baseline_plus_half_width"
    ]

    if lower > upper:
        raise MonteCarloRefusal(
            "wacc_interval_empty",
            f"The WACC interval is empty after applying bounds: lower {lower:.6g} > upper {upper:.6g}.",
        )
    if half > 0 and upper - lower < config.wacc_min_interval_width:
        raise MonteCarloRefusal(
            "wacc_interval_empty",
            f"The admissible WACC interval is {upper - lower:.3g} wide; at least "
            f"{config.wacc_min_interval_width:.3g} is required.",
        )
    return WaccIntervalSpec(
        baseline=float(baseline_wacc),
        baseline_was_clamped=bool(baseline_was_clamped),
        half_width=half,
        lower=float(lower),
        upper=float(upper),
        binding_constraints=binding,
        degenerate=lower == upper,
    )


def sample_uniform(spec: WaccIntervalSpec, u: np.ndarray) -> np.ndarray:
    x = spec.lower + u * (spec.upper - spec.lower)
    return _clip_fp_excursions(x, spec.lower, spec.upper, "wacc")


class SeededStreams:
    """Three independent random streams (growth, margin, WACC) from one seed.

    A new instance built from the same seed replays identical draws, so every
    experiment starts its streams at the same position: the growth draws in the
    growth-only and combined experiments are the same numbers (common random
    numbers), which makes their results directly comparable. Retry batches
    simply continue the stream.
    """

    def __init__(self, seed: int):
        children = np.random.SeedSequence(seed).spawn(len(STREAM_NAMES))
        self._generators: Dict[str, np.random.Generator] = {
            name: np.random.Generator(np.random.PCG64(child))
            for name, child in zip(STREAM_NAMES, children)
        }

    def uniform(self, name: str, n: int) -> np.ndarray:
        return self._generators[name].random(n)


def draw_inputs(
    streams: SeededStreams,
    n: int,
    growth: Optional[TruncatedNormalSpec],
    margin: Optional[TruncatedNormalSpec],
    wacc: Optional[WaccIntervalSpec],
    fixed_growth: float,
    fixed_margin: float,
    fixed_wacc: float,
) -> Dict[str, np.ndarray]:
    """Draw `n` triples; an input with no spec is held at its fixed baseline value.

    Every stream the experiment uses advances by exactly `n` draws, and inputs
    held fixed do not consume their stream, so the draw positions of the active
    inputs are identical across experiments.
    """
    return {
        "growth": sample_truncated_normal(growth, streams.uniform("growth", n))
        if growth is not None
        else np.full(n, fixed_growth, dtype=np.float64),
        "margin": sample_truncated_normal(margin, streams.uniform("margin", n))
        if margin is not None
        else np.full(n, fixed_margin, dtype=np.float64),
        "wacc": sample_uniform(wacc, streams.uniform("wacc", n))
        if wacc is not None
        else np.full(n, fixed_wacc, dtype=np.float64),
    }
