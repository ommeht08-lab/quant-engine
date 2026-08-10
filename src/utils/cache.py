"""
Redis caching layer (Upstash) for expensive external calls — primarily
`yfinance` fetches, which are both slow and subject to Yahoo Finance rate
limiting. Read-through cache: on a hit, the wrapped function is never
called at all.

Configured via the `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`
environment variables (from `.env` — never hardcoded here, same as every
other credential in this project). If either is missing, or Redis is
unreachable for any reason, the cache degrades to a transparent
passthrough: callers still get a working (just uncached) function rather
than a hard failure, since caching here is a performance optimization,
not a correctness requirement.

Values are pickled and base64-encoded before storage so arbitrary Python
objects — in particular, the pandas DataFrames returned by
`src.data_ingestion.fetch_financials` — can be cached transparently
without bespoke per-type serialization. The cache is private to this
application (nothing else ever writes to it), so deserializing what we
ourselves wrote is not a security concern the way deserializing
untrusted input would be.
"""

import base64
import functools
import hashlib
import logging
import os
import pickle
from typing import Callable

from dotenv import load_dotenv

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_redis_client = None
_client_initialized = False


def _get_redis_client():
    """Lazily construct (once) the Upstash Redis client, or None if unconfigured/unavailable."""
    global _redis_client, _client_initialized
    if _client_initialized:
        return _redis_client
    _client_initialized = True

    load_dotenv()
    url = os.getenv("UPSTASH_REDIS_REST_URL")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        logger.warning(
            "UPSTASH_REDIS_REST_URL/UPSTASH_REDIS_REST_TOKEN not set; caching is disabled (passthrough)."
        )
        return None

    try:
        from upstash_redis import Redis

        _redis_client = Redis(url=url, token=token)
    except Exception as exc:  # noqa: BLE001 - caching must never block the caller
        logger.warning("Failed to initialize Upstash Redis client; caching is disabled: %s", exc)
        _redis_client = None
    return _redis_client


def _build_cache_key(prefix: str, func: Callable, args: tuple, kwargs: dict) -> str:
    """Deterministic cache key: prefix + function name + a hash of its arguments."""
    raw = repr((args, sorted(kwargs.items())))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{func.__name__}:{digest}"


def cached(ttl_seconds: int, prefix: str) -> Callable:
    """
    Decorator: cache a function's return value in Upstash Redis for
    `ttl_seconds`, keyed by `prefix` + function name + a hash of its
    positional/keyword arguments (so e.g. `get_balance_sheet(aapl_ticker)`
    and `get_balance_sheet(msft_ticker)` never collide).

    On any Redis failure (unreachable, misconfigured, a (de)serialization
    error) the wrapped function is simply called directly — caching must
    never become a source of failures for the caller. A `None` result is
    never cached, so a transient fetch failure doesn't get "frozen" as a
    cache hit for the full TTL.

    Args:
        ttl_seconds: How long a cached result stays valid, e.g. 86400
            for a 24-hour TTL on relatively static financial statements.
        prefix: Cache key namespace, e.g. "balance_sheet".

    Returns:
        A decorator to apply to the function whose result should be cached.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            client = _get_redis_client()
            if client is None:
                return func(*args, **kwargs)

            cache_key = _build_cache_key(prefix, func, args, kwargs)

            try:
                cached_value = client.get(cache_key)
                if cached_value is not None:
                    logger.info("Cache hit: %s", cache_key)
                    return pickle.loads(base64.b64decode(cached_value))
            except Exception as exc:  # noqa: BLE001 - a cache-read failure must fall through
                logger.warning(
                    "Cache read failed for %s; calling %s directly: %s", cache_key, func.__name__, exc
                )

            result = func(*args, **kwargs)

            if result is not None:
                try:
                    encoded = base64.b64encode(pickle.dumps(result)).decode("ascii")
                    client.set(cache_key, encoded, ex=ttl_seconds)
                except Exception as exc:  # noqa: BLE001 - a cache-write failure must not propagate
                    logger.warning("Cache write failed for %s: %s", cache_key, exc)

            return result

        return wrapper

    return decorator
