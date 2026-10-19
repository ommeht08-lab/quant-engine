"""
Monte Carlo input snapshots and replay bundles.

A snapshot freezes everything the simulation reads from the outside world, once
per request: the three annual statements, the quote fields, the risk-free rate
and the provider provenance, stamped with a capture time. Its id is the SHA-256
of its canonical content, so the same data always has the same id and any
change produces a new one. Simulations always run from the DECODED snapshot
(never from the live provider objects), so a live run and an offline replay
execute identical inputs.

A replay bundle adds what the snapshot alone cannot: the seed, the full
experiment configuration, the model version and the expected result digest.
That is enough to rerun the experiment exactly, not just to recover its
financial inputs.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime
from functools import lru_cache
from importlib import metadata
from typing import Mapping, Optional

from src.dcf_model.monte_carlo import canonical_payload, run_monte_carlo
from src.dcf_model.monte_carlo_digest_compat import legacy_v1_digest
from src.dcf_model.monte_carlo_provenance import provenance_digest
from src.dcf_model.monte_carlo_config import (
    DEFAULT_SEED,
    MONTE_CARLO_MODEL_VERSION,
    REPLAY_BUNDLE_SCHEMA,
    SNAPSHOT_SCHEMA,
    MonteCarloConfig,
    canonical_json,
    config_hash,
    sha256_hex,
    validate_seed,
)
from src.utils.cache import CacheDecodeError, decode_cache_value, encode_cache_value

_STATEMENT_KEYS = ("income_statement", "balance_sheet", "cash_flow")
_MARKET_KEYS = ("current_price", "shares_outstanding", "beta")


class SnapshotError(ValueError):
    """The snapshot is malformed, tampered with, or cannot be encoded."""


@lru_cache(maxsize=1)
def capture_environment() -> dict:
    """Software identity of this process (recorded with results; not part of any digest)."""

    def version(package: str) -> Optional[str]:
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            return None

    return {
        "model_version": MONTE_CARLO_MODEL_VERSION,
        "git_sha": os.environ.get("VERCEL_GIT_COMMIT_SHA") or None,
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "numpy": version("numpy"),
        "pandas": version("pandas"),
        "scipy": version("scipy"),
        "yfinance": version("yfinance"),
    }


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _encode_statement(frame) -> Optional[dict]:
    if frame is None:
        return None
    try:
        return json.loads(encode_cache_value(frame))
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"A financial statement could not be encoded for the snapshot: {exc}") from exc


def _decode_statement(payload):
    if payload is None:
        return None
    try:
        return decode_cache_value(json.dumps(payload, allow_nan=False))
    except (CacheDecodeError, TypeError, ValueError) as exc:
        raise SnapshotError(f"A snapshot statement could not be decoded: {exc}") from exc


def _finite_or_none(value, name: str):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SnapshotError(f"{name} must be a finite number or absent.")
    return float(value)


def snapshot_body_id(body: Mapping) -> str:
    return sha256_hex(canonical_json({k: v for k, v in body.items() if k != "snapshot_id"}))


def build_snapshot(valuation_input, risk_free_rate: float, captured_at: datetime) -> dict:
    """Freeze one `ValuationInput` (and the risk-free rate) into a snapshot dict."""
    if captured_at.tzinfo is None:
        raise SnapshotError("captured_at must be timezone-aware.")
    data = valuation_input.financial_data
    provenance = valuation_input.provenance
    captured_iso = captured_at.isoformat()
    body = {
        "schema": SNAPSHOT_SCHEMA,
        "captured_at": captured_iso,
        "ticker": valuation_input.ticker,
        "source": provenance.source.value,
        "provenance": {
            "source": provenance.source.value,
            "source_adapter": provenance.source_adapter,
            "source_selection_reason": provenance.source_selection_reason,
            "policy_version": provenance.policy_version,
            "knowledge_cutoff": _iso(provenance.knowledge_cutoff),
            "data_vintage_cutoff": _iso(provenance.data_vintage_cutoff),
            "statement_period_start": _iso(provenance.statement_period_start),
            "statement_period_end": _iso(provenance.statement_period_end),
        },
        "market": {
            **{key: _finite_or_none(data.get(key), key) for key in _MARKET_KEYS},
            "risk_free_rate": _finite_or_none(risk_free_rate, "risk_free_rate"),
            # The provider supplies no quote timestamp; this is the capture time.
            "price_observed_at": captured_iso,
            "price_observed_at_is_capture_time": True,
        },
        "statements": {key: _encode_statement(data.get(key)) for key in _STATEMENT_KEYS},
        "other_financial_data": {
            "ticker": data.get("ticker"),
            "sector": data.get("sector"),
            "reporting_currency": data.get("reporting_currency"),
        },
        "capture_environment": dict(capture_environment()),
    }
    if body["market"]["risk_free_rate"] is None:
        raise SnapshotError("A risk-free rate is required.")
    return {**body, "snapshot_id": snapshot_body_id(body)}


def verify_snapshot(snapshot: Mapping) -> None:
    """Raise `SnapshotError` unless the snapshot is well-formed and its id matches its content."""
    if not isinstance(snapshot, Mapping) or snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise SnapshotError("Unsupported or missing snapshot schema.")
    for key in ("snapshot_id", "captured_at", "ticker", "market", "statements", "provenance"):
        if key not in snapshot:
            raise SnapshotError(f"Snapshot is missing {key!r}.")
    if snapshot_body_id(snapshot) != snapshot["snapshot_id"]:
        raise SnapshotError("Snapshot content does not match its snapshot_id.")


def snapshot_financial_data(snapshot: Mapping) -> dict:
    """Rebuild the `financial_data` mapping the deterministic model consumes."""
    verify_snapshot(snapshot)
    market = snapshot["market"]
    other = snapshot.get("other_financial_data") or {}
    data = {
        "ticker": other.get("ticker") or snapshot["ticker"],
        "sector": other.get("sector") or "Unknown",
        "reporting_currency": other.get("reporting_currency"),
    }
    for key in _MARKET_KEYS:
        data[key] = market.get(key)
    for key in _STATEMENT_KEYS:
        data[key] = _decode_statement(snapshot["statements"].get(key))
    return data


def snapshot_identity(snapshot: Mapping) -> dict:
    """The identity block embedded in every result computed from this snapshot."""
    provenance = snapshot["provenance"]
    return {
        "ticker": snapshot["ticker"],
        "source": snapshot["source"],
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_captured_at": snapshot["captured_at"],
        "statement_period_end": provenance.get("statement_period_end"),
        "market_price_observed_at": snapshot["market"]["price_observed_at"],
    }


def snapshot_provenance_context(snapshot: Mapping) -> dict:
    """The timing facts a result's baseline provenance reports, read as recorded.

    `price_observed_at` is passed through with its flag; whether it is a real
    quote time is decided by the provenance builder, never by substituting the
    capture time.
    """
    provenance = snapshot["provenance"]
    market = snapshot["market"]
    return {
        "snapshot_captured_at": snapshot["captured_at"],
        "statement_period_end": provenance.get("statement_period_end"),
        "price_observed_at": market.get("price_observed_at"),
        "price_observed_at_is_capture_time": bool(market.get("price_observed_at_is_capture_time")),
    }


def compute_from_snapshot(
    snapshot: Mapping, seed: int, config: MonteCarloConfig
) -> dict:
    """The single computation path used by both live requests and offline replay."""
    financial_data = snapshot_financial_data(snapshot)
    return run_monte_carlo(
        financial_data,
        risk_free_rate=snapshot["market"]["risk_free_rate"],
        config=config,
        seed=seed,
        identity=snapshot_identity(snapshot),
        provenance_context=snapshot_provenance_context(snapshot),
    )


def build_replay_bundle(
    snapshot: Mapping,
    seed: int,
    config: MonteCarloConfig,
    expected_result_digest: Optional[str] = None,
    baseline_provenance: Optional[Mapping] = None,
) -> dict:
    """`baseline_provenance`, when given, is exported for inspection and verified on replay."""
    verify_snapshot(snapshot)
    bundle = {
        "schema": REPLAY_BUNDLE_SCHEMA,
        "snapshot": dict(snapshot),
        "replay": {
            "seed": validate_seed(seed),
            "config": config.to_canonical_dict(),
            "config_hash": config_hash(config),
            "model_version": MONTE_CARLO_MODEL_VERSION,
            "expected_result_digest": expected_result_digest,
        },
        "exported_with_environment": dict(capture_environment()),
    }
    if baseline_provenance is not None:
        bundle["baseline_provenance"] = dict(baseline_provenance)
        bundle["replay"]["expected_provenance_digest"] = baseline_provenance.get("provenance_digest")
    bundle["bundle_digest"] = bundle_integrity_digest(bundle)
    return bundle


def bundle_integrity_digest(bundle: Mapping) -> str:
    """Digest binding the snapshot id, the whole `replay` section (seed, configuration, config hash,
    model version, expected digests) and the provenance block's own digest.

    It detects accidental or partial edits to any of them. It is not an authenticity
    guarantee: it carries no secret, so someone who recomputes every digest can forge a bundle.
    """
    block = bundle.get("baseline_provenance")
    return sha256_hex(
        canonical_json(
            {
                "schema": bundle.get("schema"),
                "snapshot_id": (bundle.get("snapshot") or {}).get("snapshot_id"),
                "replay": bundle.get("replay"),
                "provenance_digest": block.get("provenance_digest") if isinstance(block, Mapping) else None,
            }
        )
    )


def verify_bundle_integrity(bundle: Mapping) -> Optional[bool]:
    """None for a bundle exported before bundle digests existed; otherwise whether it is intact.

    Raises `SnapshotError` for a provenance block that does not match its own digest or the
    digest the replay section expects.
    """
    block = bundle.get("baseline_provenance")
    if isinstance(block, Mapping):
        replay = bundle.get("replay") or {}
        if provenance_digest(block) != block.get("provenance_digest"):
            raise SnapshotError("The bundle's baseline provenance does not match its own digest.")
        expected = replay.get("expected_provenance_digest")
        if expected is not None and expected != block.get("provenance_digest"):
            raise SnapshotError("The bundle's baseline provenance does not match the digest its replay section expects.")
    stored = bundle.get("bundle_digest")
    if stored is None:
        return None
    return stored == bundle_integrity_digest(bundle)


def replay_bundle(bundle: Mapping) -> dict:
    """Recompute a bundle's experiment and compare against its expected digest."""
    if not isinstance(bundle, Mapping) or bundle.get("schema") != REPLAY_BUNDLE_SCHEMA:
        raise SnapshotError("Unsupported or missing replay bundle schema.")
    integrity = verify_bundle_integrity(bundle)
    if integrity is False:
        raise SnapshotError("Replay bundle content does not match its bundle_digest.")
    replay = bundle.get("replay") or {}
    config = MonteCarloConfig.from_canonical_dict(replay.get("config") or {})
    if replay.get("config_hash") != config_hash(config):
        raise SnapshotError("Replay configuration does not match its recorded config_hash.")
    seed = validate_seed(replay.get("seed", DEFAULT_SEED))
    result = compute_from_snapshot(bundle["snapshot"], seed, config)
    expected = replay.get("expected_result_digest")
    # Bundles exported before the digest scope changed carry the original digest,
    # which still verifies against the same numerical payload.
    legacy = legacy_v1_digest(canonical_payload(result))
    scheme = None if expected is None else (
        "current" if expected == result["result_digest"] else "legacy_v1" if expected == legacy else None
    )
    expected_provenance = replay.get("expected_provenance_digest")
    return {
        "bundle_integrity_ok": integrity,
        "matches_expected": None if expected is None else scheme is not None,
        "matched_digest_scheme": scheme,
        "expected_result_digest": expected,
        "actual_result_digest": result["result_digest"],
        "provenance_matches_expected": (
            None if expected_provenance is None
            else expected_provenance == result["baseline_provenance"]["provenance_digest"]
        ),
        "model_version_matches": replay.get("model_version") == MONTE_CARLO_MODEL_VERSION,
        "exported_with_environment": bundle.get("exported_with_environment"),
        "replayed_with_environment": dict(capture_environment()),
        "result": result,
    }
