"""
Fixed configuration, versions and the ticker allowlist for the Monte Carlo DCF.

Every number that changes a simulated result lives in `MonteCarloConfig`, so
a result's `config_hash` identifies the exact experiment: a changed bound,
spread rule or retry budget can never reuse a stale cache entry or be
mistaken for a replay of an older run. The v1 API does not accept
user-supplied configuration; the only request-level choice is the seed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from src.dcf_model.dcf import (
    MAX_DISCOUNT_RATE,
    MAX_EXPLICIT_OPERATING_MARGIN,
    MAX_REVENUE_GROWTH_RATE,
    MIN_DISCOUNT_RATE,
    MIN_EXPLICIT_OPERATING_MARGIN,
    MIN_EXPLICIT_REVENUE_GROWTH_RATE,
)

# Bump whenever sampling, the kernel, summaries or disclosures change in a way
# that can alter a result. It is part of every cache key and replay bundle.
MONTE_CARLO_MODEL_VERSION = "mc-dcf-v1"

RESULT_SCHEMA = "mc-dcf-result-v1"
SNAPSHOT_SCHEMA = "mc-dcf-snapshot-v1"
REPLAY_BUNDLE_SCHEMA = "mc-dcf-replay-bundle-v1"
PROVENANCE_SCHEMA = "mc-dcf-baseline-provenance-v1"

EXPERIMENT_NAMES: Tuple[str, ...] = ("growth", "margin", "wacc", "combined")

DEFAULT_SEED = 20261101
# JavaScript's largest exactly representable integer: the browser and the Next.js
# proxy parse JSON numbers as doubles, so a larger seed could not round-trip.
MAX_SEED = 2**53 - 1

# Candidate tickers are deliberately limited to ones expected to have four
# annual statements and a positive operating margin. None has been validated
# against live data yet; a ticker that fails its history checks returns a
# typed refusal, never a substituted spread. Override with MONTE_CARLO_TICKERS.
TICKER_ALLOWLIST_ENV_VAR = "MONTE_CARLO_TICKERS"
DEFAULT_TICKER_ALLOWLIST: Tuple[str, ...] = ("AAPL", "KO", "JNJ")
MAX_ALLOWLIST_SIZE = 5
_TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


@dataclass(frozen=True)
class MonteCarloConfig:
    """The complete, hashable description of one experiment configuration."""

    forecast_mode: str = "maturation"
    near_term_years: int = 2
    mature_growth_ceiling: float = 0.03
    terminal_growth_rate: float = 0.025
    projection_years: int = 5

    target_valid: int = 10_000

    # Truncation bounds. Growth: the explicit-slider floor and the model's
    # ceiling on historically derived growth. Margin: the explicit-slider range.
    growth_lower: float = MIN_EXPLICIT_REVENUE_GROWTH_RATE
    growth_upper: float = MAX_REVENUE_GROWTH_RATE
    margin_lower: float = MIN_EXPLICIT_OPERATING_MARGIN
    margin_upper: float = MAX_EXPLICIT_OPERATING_MARGIN

    # WACC: uniform within the applied baseline +/- half_width, intersected with
    # the model's own discount-rate bounds and kept above terminal growth.
    wacc_half_width: float = 0.01
    wacc_lower_bound: float = MIN_DISCOUNT_RATE
    wacc_upper_bound: float = MAX_DISCOUNT_RATE
    wacc_min_spread_over_terminal: float = 0.01
    wacc_min_interval_width: float = 1e-4

    # A truncated normal whose admissible probability mass is below this is
    # numerically inadequate to sample and is refused.
    min_admissible_mass: float = 1e-6

    min_growth_observations: int = 3
    min_margin_observations: int = 3
    min_fiscal_gap_days: int = 335
    max_fiscal_gap_days: int = 395

    max_retry_rounds: int = 3
    max_extra_attempts: int = 500

    histogram_bins: int = 40
    histogram_low_percentile: float = 0.5
    histogram_high_percentile: float = 99.5
    histogram_min_basis_draws: int = 100
    percentile_method: str = "linear"

    def __post_init__(self) -> None:
        if self.forecast_mode not in ("maturation", "constant"):
            raise ValueError("forecast_mode must be 'maturation' or 'constant'.")
        for name in ("near_term_years", "projection_years", "target_valid", "histogram_bins", "histogram_min_basis_draws"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if self.target_valid > 100_000:
            raise ValueError("target_valid must not exceed 100,000.")
        if self.forecast_mode == "maturation" and self.near_term_years >= self.projection_years:
            raise ValueError("maturation requires projection_years > near_term_years.")
        for name in ("max_retry_rounds", "max_extra_attempts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        floats = (
            "mature_growth_ceiling", "terminal_growth_rate", "growth_lower", "growth_upper",
            "margin_lower", "margin_upper", "wacc_half_width", "wacc_lower_bound",
            "wacc_upper_bound", "wacc_min_spread_over_terminal", "wacc_min_interval_width",
            "min_admissible_mass", "histogram_low_percentile", "histogram_high_percentile",
        )
        for name in floats:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number.")
        if not self.growth_lower < self.growth_upper:
            raise ValueError("growth bounds must satisfy lower < upper.")
        if not self.margin_lower < self.margin_upper:
            raise ValueError("margin bounds must satisfy lower < upper.")
        if not self.wacc_lower_bound < self.wacc_upper_bound:
            raise ValueError("WACC bounds must satisfy lower < upper.")
        if self.wacc_half_width < 0:
            raise ValueError("wacc_half_width must not be negative.")
        if not 0 < self.min_admissible_mass < 1:
            raise ValueError("min_admissible_mass must be in (0, 1).")
        if not 0 <= self.histogram_low_percentile < self.histogram_high_percentile <= 100:
            raise ValueError("histogram percentiles must satisfy 0 <= low < high <= 100.")
        if self.percentile_method != "linear":
            raise ValueError("only the 'linear' percentile method is supported.")
        if self.min_growth_observations < 2 or self.min_margin_observations < 2:
            raise ValueError("at least two observations are needed for a sample standard deviation.")
        if not 0 < self.min_fiscal_gap_days < self.max_fiscal_gap_days:
            raise ValueError("fiscal gap bounds are invalid.")

    def to_canonical_dict(self) -> dict:
        return {
            "forecast_mode": self.forecast_mode,
            "near_term_years": self.near_term_years,
            "mature_growth_ceiling": self.mature_growth_ceiling,
            "terminal_growth_rate": self.terminal_growth_rate,
            "projection_years": self.projection_years,
            "target_valid": self.target_valid,
            "growth_bounds": [self.growth_lower, self.growth_upper],
            "margin_bounds": [self.margin_lower, self.margin_upper],
            "wacc": {
                "half_width": self.wacc_half_width,
                "model_bounds": [self.wacc_lower_bound, self.wacc_upper_bound],
                "min_spread_over_terminal_growth": self.wacc_min_spread_over_terminal,
                "min_interval_width": self.wacc_min_interval_width,
            },
            "min_admissible_mass": self.min_admissible_mass,
            "history": {
                "min_growth_observations": self.min_growth_observations,
                "min_margin_observations": self.min_margin_observations,
                "fiscal_gap_days": [self.min_fiscal_gap_days, self.max_fiscal_gap_days],
                "sd_convention": "sample_ddof1",
            },
            "retry_policy": {
                "max_retry_rounds": self.max_retry_rounds,
                "max_extra_attempts": self.max_extra_attempts,
            },
            "histogram": {
                "bins": self.histogram_bins,
                "low_percentile": self.histogram_low_percentile,
                "high_percentile": self.histogram_high_percentile,
                "min_basis_draws": self.histogram_min_basis_draws,
            },
            "percentile_method": self.percentile_method,
            "independence": True,
            "distributions": {
                "growth": "truncated_normal_inverse_cdf",
                "margin": "truncated_normal_inverse_cdf",
                "wacc": "uniform",
            },
        }

    @classmethod
    def from_canonical_dict(cls, payload: Mapping) -> "MonteCarloConfig":
        """Rebuild a configuration from `to_canonical_dict()` output (replay)."""
        try:
            return cls(
                forecast_mode=payload["forecast_mode"],
                near_term_years=payload["near_term_years"],
                mature_growth_ceiling=payload["mature_growth_ceiling"],
                terminal_growth_rate=payload["terminal_growth_rate"],
                projection_years=payload["projection_years"],
                target_valid=payload["target_valid"],
                growth_lower=payload["growth_bounds"][0],
                growth_upper=payload["growth_bounds"][1],
                margin_lower=payload["margin_bounds"][0],
                margin_upper=payload["margin_bounds"][1],
                wacc_half_width=payload["wacc"]["half_width"],
                wacc_lower_bound=payload["wacc"]["model_bounds"][0],
                wacc_upper_bound=payload["wacc"]["model_bounds"][1],
                wacc_min_spread_over_terminal=payload["wacc"]["min_spread_over_terminal_growth"],
                wacc_min_interval_width=payload["wacc"]["min_interval_width"],
                min_admissible_mass=payload["min_admissible_mass"],
                min_growth_observations=payload["history"]["min_growth_observations"],
                min_margin_observations=payload["history"]["min_margin_observations"],
                min_fiscal_gap_days=payload["history"]["fiscal_gap_days"][0],
                max_fiscal_gap_days=payload["history"]["fiscal_gap_days"][1],
                max_retry_rounds=payload["retry_policy"]["max_retry_rounds"],
                max_extra_attempts=payload["retry_policy"]["max_extra_attempts"],
                histogram_bins=payload["histogram"]["bins"],
                histogram_low_percentile=payload["histogram"]["low_percentile"],
                histogram_high_percentile=payload["histogram"]["high_percentile"],
                histogram_min_basis_draws=payload["histogram"]["min_basis_draws"],
                percentile_method=payload["percentile_method"],
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Replay configuration is malformed: {exc!r}") from exc


DEFAULT_CONFIG = MonteCarloConfig()


def canonical_json(value) -> str:
    """Deterministic JSON: sorted keys, no whitespace, finite numbers only.

    Python float reprs round-trip exactly, so equal text means equal numbers.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_hash(config: MonteCarloConfig) -> str:
    return sha256_hex(canonical_json(config.to_canonical_dict()))


def validate_seed(seed) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
        raise ValueError(f"seed must be an integer in [0, {MAX_SEED}].")
    return seed


def load_ticker_allowlist(environ: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """The configured allowlist (1 to MAX_ALLOWLIST_SIZE tickers, order kept)."""
    environ = os.environ if environ is None else environ
    raw = environ.get(TICKER_ALLOWLIST_ENV_VAR)
    if raw is None or not raw.strip():
        return DEFAULT_TICKER_ALLOWLIST
    tickers = []
    for item in raw.split(","):
        ticker = item.strip().upper()
        if not _TICKER_PATTERN.match(ticker):
            raise ValueError(f"{TICKER_ALLOWLIST_ENV_VAR} contains an invalid ticker: {item!r}.")
        if ticker not in tickers:
            tickers.append(ticker)
    if not 1 <= len(tickers) <= MAX_ALLOWLIST_SIZE:
        raise ValueError(f"{TICKER_ALLOWLIST_ENV_VAR} must list 1 to {MAX_ALLOWLIST_SIZE} tickers.")
    return tuple(tickers)


REFUSAL_CODES = frozenset(
    {
        "input_incomplete",
        "insufficient_history",
        "irregular_fiscal_spacing",
        "history_alignment_mismatch",
        "nonpositive_revenue",
        "inadmissible_distribution",
        "degenerate_outside_bounds",
        "wacc_interval_empty",
        "kernel_baseline_mismatch",
    }
)


class MonteCarloRefusal(Exception):
    """A typed, expected refusal to simulate (never a crash or a silent default).

    `code` is a stable vocabulary member of REFUSAL_CODES; `message` is a
    short explanation safe to show to a user.
    """

    def __init__(self, code: str, message: str):
        if code not in REFUSAL_CODES:
            raise ValueError(f"Unknown refusal code: {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
