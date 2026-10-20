"""
Static simulation metadata for the frontend: the allowlist, default seed, configuration summary and disclosures.

The Next.js app serves this without waking the Python backend, so the page's controls appear immediately.
There is one source: this module builds the description from the same constants the service uses, and
`scripts/generate_monte_carlo_metadata.py` writes it to `frontend/src/generated/`. A test fails when that
file drifts from this code.

The backend stays authoritative. It enforces the allowlist and validates every request, and it reports the
identity of the metadata it is actually running with (`metadata_id`) on every result. A browser that was
built from different metadata can therefore tell, and says so, instead of presenting stale settings as current.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

from src.dcf_model.monte_carlo import DISCLOSURES
from src.dcf_model.monte_carlo_config import (
    DEFAULT_CONFIG,
    DEFAULT_SEED,
    DEFAULT_TICKER_ALLOWLIST,
    EXPERIMENT_NAMES,
    MONTE_CARLO_MODEL_VERSION,
    MonteCarloConfig,
    canonical_json,
    config_hash,
    sha256_hex,
)

METADATA_SCHEMA = "mc-dcf-metadata-v1"


def metadata_id(description: Mapping[str, Any]) -> str:
    """Hash of everything the controls show. Any change to the list, seed, configuration or wording changes it."""
    return sha256_hex(
        canonical_json(
            {
                "schema": METADATA_SCHEMA,
                "model_version": description["model_version"],
                "tickers": list(description["tickers"]),
                "experiments": list(description["experiments"]),
                "default_seed": description["default_seed"],
                "config_hash": description["config_hash"],
                "disclosures": description["disclosures"],
            }
        )
    )


def build_description(tickers: Sequence[str], config: MonteCarloConfig = DEFAULT_CONFIG) -> Dict[str, Any]:
    description: Dict[str, Any] = {
        "model_version": MONTE_CARLO_MODEL_VERSION,
        "tickers": list(tickers),
        "experiments": list(EXPERIMENT_NAMES),
        "default_seed": DEFAULT_SEED,
        "config": config.to_canonical_dict(),
        "config_hash": config_hash(config),
        "disclosures": DISCLOSURES,
    }
    description["metadata_id"] = metadata_id(description)
    return description


def build_static_metadata() -> Dict[str, Any]:
    """The metadata the frontend ships with: the default allowlist and default configuration.

    A deployment that overrides `MONTE_CARLO_TICKERS` serves a different `metadata_id`, which the page reports.
    """
    return {"schema": METADATA_SCHEMA, **build_description(DEFAULT_TICKER_ALLOWLIST)}
