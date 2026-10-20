"""
Key-value store and ownership-token locks for the Monte Carlo service.

Two implementations share one small interface: `UpstashStore` (a shared
Upstash Redis reached only through the Monte Carlo-specific credentials below) and `InMemoryStore`
(process-local; tests and local development only).

Locks are `SET key token NX EX ttl` with a random per-acquisition token and an
owner-checked release (a Lua compare-and-delete): a holder whose lock expired
and was re-acquired by someone else can never delete the new owner's lock.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Callable, Dict, Optional, Tuple

# Compare-and-delete: only the holder of the token may release.
RELEASE_LOCK_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)

REQUIRE_SHARED_STORE_ENV_VAR = "MONTE_CARLO_REQUIRE_SHARED_STORE"

# Dedicated credentials. The generic UPSTASH_REDIS_REST_* pair belongs to `src.utils.cache`, which turns on
# valuation, price, risk-free-rate and sector-median caching when it is set; this store must neither read it
# (so a generic database is never used for Monte Carlo by accident) nor, by being configured, switch it on.
URL_ENV_VAR = "MONTE_CARLO_UPSTASH_REDIS_REST_URL"
TOKEN_ENV_VAR = "MONTE_CARLO_UPSTASH_REDIS_REST_TOKEN"


class StoreUnavailable(Exception):
    """The shared store is required but missing, or an operation on it failed."""


class InMemoryStore:
    """Thread-safe, TTL-aware store with the same lock semantics as Redis."""

    shared = False

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._data: Dict[str, Tuple[str, Optional[float]]] = {}

    def _live(self, key: str) -> Optional[str]:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            del self._data[key]
            return None
        return value

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._live(key)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            self._data[key] = (value, self._clock() + ttl_seconds)

    def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        with self._lock:
            if self._live(key) is not None:
                return False
            self._data[key] = (value, self._clock() + ttl_seconds)
            return True

    def release_if_owner(self, key: str, token: str) -> bool:
        with self._lock:
            if self._live(key) == token:
                del self._data[key]
                return True
            return False


class UpstashStore:
    """Adapter over the Upstash Redis client used by `src.utils.cache`."""

    shared = True

    def __init__(self, client):
        self._client = client

    def get(self, key: str) -> Optional[str]:
        try:
            value = self._client.get(key)
        except Exception as exc:  # noqa: BLE001 - any transport failure means "unavailable"
            raise StoreUnavailable(f"store read failed: {type(exc).__name__}") from exc
        return None if value is None else str(value)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        try:
            self._client.set(key, value, ex=ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(f"store write failed: {type(exc).__name__}") from exc

    def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        try:
            return bool(self._client.set(key, value, nx=True, ex=ttl_seconds))
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(f"store lock failed: {type(exc).__name__}") from exc

    def release_if_owner(self, key: str, token: str) -> bool:
        try:
            return bool(self._client.eval(RELEASE_LOCK_SCRIPT, keys=[key], args=[token]))
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(f"store unlock failed: {type(exc).__name__}") from exc


def acquire_lock(store, key: str, ttl_seconds: int) -> Optional[str]:
    """Try to take the lock; returns the ownership token, or None if it is held."""
    token = secrets.token_hex(16)
    return token if store.set_if_absent(key, token, ttl_seconds) else None


def release_lock(store, key: str, token: str) -> bool:
    """Release only if `token` still owns the lock (never raises on a lost lock)."""
    return store.release_if_owner(key, token)


def shared_store_required(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    default = "1" if environ.get("VERCEL") else "0"
    return environ.get(REQUIRE_SHARED_STORE_ENV_VAR, default) == "1"


_local_store: Optional[InMemoryStore] = None
_local_store_lock = threading.Lock()

_client_lock = threading.Lock()
_client_checked = False
_client = None


def _shared_client():
    """The Upstash client for this process, built once, safely under concurrency.

    Deliberately not `src.utils.cache._get_redis_client()`: that helper marks itself
    initialized *before* it has built the client, so a second thread arriving
    mid-construction gets `None`. Here that would silently fall back to a
    process-local store (duplicate fetches and computations across instances) or,
    where a shared store is required, fail concurrent cold-start requests with 503.
    """
    global _client, _client_checked
    with _client_lock:
        if not _client_checked:
            url = os.environ.get(URL_ENV_VAR)
            token = os.environ.get(TOKEN_ENV_VAR)
            if url and token:
                try:
                    from upstash_redis import Redis

                    _client = Redis(url=url, token=token)
                except Exception:  # noqa: BLE001 - treated as "no shared store"; never log the URL/token
                    _client = None
            _client_checked = True
        return _client


def default_store(environ=None):
    """Upstash when configured; otherwise a process-local store unless a shared one is required."""
    client = _shared_client()
    if client is not None:
        return UpstashStore(client)
    if shared_store_required(environ):
        raise StoreUnavailable("A shared store is required but Redis is not configured.")
    global _local_store
    with _local_store_lock:
        if _local_store is None:
            _local_store = InMemoryStore()
        return _local_store
