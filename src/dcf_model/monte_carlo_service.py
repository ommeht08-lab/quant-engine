"""
Request-level orchestration for the Monte Carlo DCF: ticker allowlist, one
snapshot per request (shared across requests via the store), result caching and
duplicate-work protection.

Both expensive steps are guarded the same way:

* snapshot acquisition (provider fetch) is serialized per ticker, and
* simulation is serialized per result key,

each with an ownership-token lock (see `monte_carlo_store`). A request that
cannot take a lock re-checks the cache while waiting, takes over if the holder's
lock expired, and otherwise returns a retryable 409 after a bounded wait. The
provider is called at most once per snapshot and never inside the simulation.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence, Tuple

from src.dcf_model.monte_carlo_config import (
    DEFAULT_CONFIG,
    DEFAULT_SEED,
    EXPERIMENT_NAMES,
    MONTE_CARLO_MODEL_VERSION,
    MonteCarloConfig,
    MonteCarloRefusal,
    config_hash,
    load_ticker_allowlist,
    sha256_hex,
    validate_seed,
)
from src.dcf_model.monte_carlo import DISCLOSURES, canonical_payload
from src.dcf_model.monte_carlo_snapshot import (
    SnapshotError,
    build_replay_bundle,
    build_snapshot,
    capture_environment,
    compute_from_snapshot,
    verify_snapshot,
)
from src.dcf_model.monte_carlo_store import (
    StoreUnavailable,
    acquire_lock,
    default_store,
    release_lock,
)

logger = logging.getLogger(__name__)

KEY_PREFIX = "mcdcf:v1"
# Part of the result cache identity (not of the numerical payload). Raise it whenever the stored
# result's shape changes, so entries written by older code are never served as current ones.
# 2: results carry `baseline_provenance`; disclosure copy revised.
RESULT_FORMAT_VERSION = 2
_SNAPSHOT_ID_PATTERN = re.compile(r"^[a-f0-9]{64}$")
SNAPSHOT_TTL_SECONDS = 30 * 24 * 3600
POINTER_TTL_SECONDS = 6 * 3600
RESULT_TTL_SECONDS = 30 * 24 * 3600
# Longer than the 40 s function limit, so a still-running holder keeps its lock
# and a crashed holder's lock expires on its own.
LOCK_TTL_SECONDS = 45
SNAPSHOT_WAIT_SECONDS = 12.0
RESULT_WAIT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.5
RETRY_AFTER_SECONDS = 3

# (valuation_input, risk_free_rate) for a ticker; raises ServiceError on refusal.
SnapshotProvider = Callable[[str], Tuple[object, float]]


class ServiceError(Exception):
    """An expected failure with an HTTP status and a stable machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after


def _release(store, lock_key: str, token: str) -> None:
    """Release a lock we own. A failed release is harmless (the lock expires by TTL)
    and must never turn a finished request into an error."""
    try:
        release_lock(store, lock_key, token)
    except StoreUnavailable:
        logger.warning("Could not release a Monte Carlo lock; it will expire on its own.")


def snapshot_key(snapshot_id: str) -> str:
    return f"{KEY_PREFIX}:snapshot:{snapshot_id}"


def pointer_key(ticker: str) -> str:
    return f"{KEY_PREFIX}:latest:{ticker}"


def snapshot_lock_key(ticker: str) -> str:
    return f"{KEY_PREFIX}:lock:snapshot:{ticker}"


def result_cache_key(
    ticker: str, snapshot_id: str, seed: int, config: MonteCarloConfig
) -> str:
    """Cache identity: ticker, snapshot, seed, full configuration and model version."""
    digest = sha256_hex(
        "|".join(
            [ticker, snapshot_id, str(seed), config_hash(config), MONTE_CARLO_MODEL_VERSION, f"format{RESULT_FORMAT_VERSION}"]
        )
    )
    return f"{KEY_PREFIX}:result:{digest}"


def result_lock_key(result_key: str) -> str:
    return result_key.replace(":result:", ":lock:result:", 1)


class MonteCarloService:
    def __init__(
        self,
        snapshot_provider: SnapshotProvider,
        store_factory: Callable[[], object] = default_store,
        config: MonteCarloConfig = DEFAULT_CONFIG,
        allowlist: Optional[Sequence[str]] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        snapshot_wait_seconds: float = SNAPSHOT_WAIT_SECONDS,
        result_wait_seconds: float = RESULT_WAIT_SECONDS,
    ):
        self._provider = snapshot_provider
        self._store_factory = store_factory
        self._config = config
        self._allowlist = allowlist
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._snapshot_wait = snapshot_wait_seconds
        self._result_wait = result_wait_seconds

    # ---- public ---------------------------------------------------------

    def allowlist(self) -> Tuple[str, ...]:
        try:
            return tuple(self._allowlist) if self._allowlist is not None else load_ticker_allowlist()
        except ValueError as exc:
            raise ServiceError(503, "monte_carlo_misconfigured", str(exc)) from exc

    def describe(self) -> dict:
        return {
            "model_version": MONTE_CARLO_MODEL_VERSION,
            "tickers": list(self.allowlist()),
            "experiments": list(EXPERIMENT_NAMES),
            "default_seed": DEFAULT_SEED,
            "config": self._config.to_canonical_dict(),
            "config_hash": config_hash(self._config),
            "disclosures": DISCLOSURES,
        }

    def get_result(self, ticker: str, seed: Optional[int] = None) -> dict:
        started = self._monotonic()
        ticker = self._check_ticker(ticker)
        seed = self._check_seed(seed)
        store = self._store()
        try:
            snapshot, snapshot_cache = self._acquire_snapshot(store, ticker)
            snapshot_done = self._monotonic()
            result, stored_meta, result_cache = self._acquire_result(store, ticker, snapshot, seed)
        except StoreUnavailable as exc:
            raise ServiceError(
                503, "cache_unavailable", "The simulation cache is temporarily unavailable."
            ) from exc
        finished = self._monotonic()

        meta = {
            "cache": result_cache,
            "snapshot_cache": snapshot_cache,
            "computed_at": stored_meta["computed_at"],
            "served_at": self._clock().isoformat(),
            "environment": stored_meta["environment"],
            "timings_ms": {
                "snapshot": round((snapshot_done - started) * 1000, 1),
                "result": round((finished - snapshot_done) * 1000, 1),
                "total": round((finished - started) * 1000, 1),
                "compute_when_computed": stored_meta.get("compute_ms"),
            },
        }
        return {**result, "meta": meta}

    def get_replay_bundle(self, snapshot_id: str, seed: Optional[int] = None) -> dict:
        seed = self._check_seed(seed)
        if not _SNAPSHOT_ID_PATTERN.match(snapshot_id or ""):
            raise ServiceError(404, "snapshot_not_found", "That snapshot is unknown or has expired.")
        store = self._store()
        try:
            snapshot = self._load_snapshot(store, snapshot_id)
            if snapshot is None:
                raise ServiceError(404, "snapshot_not_found", "That snapshot is unknown or has expired.")
            key = result_cache_key(snapshot["ticker"], snapshot["snapshot_id"], seed, self._config)
            cached = self._load_result(store, key)
        except StoreUnavailable as exc:
            raise ServiceError(
                503, "cache_unavailable", "The simulation cache is temporarily unavailable."
            ) from exc
        digest = None
        provenance = None
        reference = None
        if cached is not None and "result" in cached:
            digest = cached["result"]["result_digest"]
            provenance = cached["result"].get("baseline_provenance")
            reference = canonical_payload(cached["result"])
        return build_replay_bundle(
            snapshot, seed, self._config, expected_result_digest=digest, baseline_provenance=provenance,
            reference_result=reference,
        )

    # ---- validation -----------------------------------------------------

    def _check_ticker(self, ticker: str) -> str:
        normalized = ticker.strip().upper() if isinstance(ticker, str) else ""
        if normalized not in self.allowlist():
            raise ServiceError(404, "ticker_not_supported", "That ticker is not enabled for this simulation.")
        return normalized

    @staticmethod
    def _check_seed(seed: Optional[int]) -> int:
        try:
            return validate_seed(DEFAULT_SEED if seed is None else seed)
        except ValueError as exc:
            raise ServiceError(422, "invalid_seed", str(exc)) from exc

    def _store(self):
        try:
            return self._store_factory()
        except StoreUnavailable as exc:
            raise ServiceError(
                503, "cache_unavailable", "The simulation cache is temporarily unavailable."
            ) from exc

    # ---- snapshot acquisition ------------------------------------------

    def _load_snapshot(self, store, snapshot_id: str) -> Optional[dict]:
        raw = store.get(snapshot_key(snapshot_id))
        if raw is None:
            return None
        try:
            snapshot = json.loads(raw)
            verify_snapshot(snapshot)
        except (ValueError, SnapshotError):
            return None  # corrupt entry: treat as a miss and refetch
        return snapshot

    def _load_pointed_snapshot(self, store, ticker: str) -> Optional[dict]:
        pointed = store.get(pointer_key(ticker))
        return self._load_snapshot(store, pointed) if pointed else None

    def _acquire_snapshot(self, store, ticker: str) -> Tuple[dict, str]:
        deadline = self._monotonic() + self._snapshot_wait
        waited = False
        lock_key = snapshot_lock_key(ticker)
        while True:
            snapshot = self._load_pointed_snapshot(store, ticker)
            if snapshot is not None:
                return snapshot, "waited" if waited else "hit"
            token = acquire_lock(store, lock_key, LOCK_TTL_SECONDS)
            if token is not None:
                try:
                    # Re-check: another request may have finished between our read and our lock.
                    snapshot = self._load_pointed_snapshot(store, ticker)
                    if snapshot is not None:
                        return snapshot, "hit"
                    snapshot = self._capture(ticker)
                    store.set(snapshot_key(snapshot["snapshot_id"]), json.dumps(snapshot, allow_nan=False),
                              SNAPSHOT_TTL_SECONDS)
                    store.set(pointer_key(ticker), snapshot["snapshot_id"], POINTER_TTL_SECONDS)
                    return snapshot, "miss"
                finally:
                    _release(store, lock_key, token)
            if self._monotonic() >= deadline:
                raise ServiceError(
                    409, "snapshot_in_progress",
                    "Another request is loading this company's data. Retry shortly.",
                    retry_after=RETRY_AFTER_SECONDS,
                )
            waited = True
            self._sleep(POLL_INTERVAL_SECONDS)

    def _capture(self, ticker: str) -> dict:
        valuation_input, risk_free_rate = self._provider(ticker)  # exactly one provider fetch
        try:
            return build_snapshot(valuation_input, risk_free_rate, self._clock())
        except SnapshotError as exc:
            raise ServiceError(422, "input_incomplete", str(exc)) from exc

    # ---- result acquisition --------------------------------------------

    def _load_result(self, store, key: str) -> Optional[dict]:
        raw = store.get(key)
        if raw is None:
            return None
        try:
            entry = json.loads(raw)
        except ValueError:
            return None
        return entry if isinstance(entry, dict) and ("result" in entry or "refusal" in entry) else None

    def _acquire_result(self, store, ticker: str, snapshot: dict, seed: int) -> Tuple[dict, dict, str]:
        key = result_cache_key(ticker, snapshot["snapshot_id"], seed, self._config)
        lock_key = result_lock_key(key)
        deadline = self._monotonic() + self._result_wait
        while True:
            cached = self._load_result(store, key)
            if cached is not None:
                return self._unpack(cached, "hit")
            token = acquire_lock(store, lock_key, LOCK_TTL_SECONDS)
            if token is not None:
                try:
                    cached = self._load_result(store, key)
                    if cached is not None:
                        return self._unpack(cached, "hit")
                    entry = self._compute_entry(snapshot, seed)
                    store.set(key, json.dumps(entry, allow_nan=False), RESULT_TTL_SECONDS)
                    return self._unpack(entry, "miss")
                finally:
                    _release(store, lock_key, token)
            if self._monotonic() >= deadline:
                raise ServiceError(
                    409, "computation_in_progress",
                    "This simulation is already running. Retry shortly.",
                    retry_after=RETRY_AFTER_SECONDS,
                )
            self._sleep(POLL_INTERVAL_SECONDS)

    def _compute_entry(self, snapshot: dict, seed: int) -> dict:
        started = self._monotonic()
        try:
            result = compute_from_snapshot(snapshot, seed, self._config)
        except MonteCarloRefusal as refusal:
            return {"refusal": {"code": refusal.code, "message": refusal.message}}
        return {
            "result": result,
            "stored_meta": {
                "computed_at": self._clock().isoformat(),
                "environment": dict(capture_environment()),
                "compute_ms": round((self._monotonic() - started) * 1000, 1),
            },
        }

    @staticmethod
    def _unpack(entry: dict, cache: str) -> Tuple[dict, dict, str]:
        if "refusal" in entry:
            refusal = entry["refusal"]
            raise ServiceError(422, refusal["code"], refusal["message"])
        return entry["result"], entry["stored_meta"], cache
