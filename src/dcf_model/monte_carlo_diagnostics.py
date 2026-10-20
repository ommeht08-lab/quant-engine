"""
Preview-only counter for the HTTP requests the Yahoo client actually makes.

One logical provider fetch is not one HTTP request: the client may add cookie
and crumb requests, retries and one request per statement. This records what
really went out so a preview benchmark can bound and report it.

Off by default. It runs only when `MONTE_CARLO_PROVIDER_DIAGNOSTICS=1` and the
deployment is not production. Only aggregate counts and timings are kept: the
host, status class and elapsed time of each request. URLs, query strings,
headers, cookies and bodies are never read into the summary or the log.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

FLAG_ENV_VAR = "MONTE_CARLO_PROVIDER_DIAGNOSTICS"
HEADER_NAME = "X-MC-Provider-Diagnostics"

_active: contextvars.ContextVar[Optional["_Collector"]] = contextvars.ContextVar("mc_provider_collector", default=None)
_last: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("mc_provider_last", default=None)
_install_lock = threading.Lock()
_installed: set = set()


def enabled() -> bool:
    return os.environ.get(FLAG_ENV_VAR) == "1" and os.environ.get("VERCEL_ENV") != "production"


class _Collector:
    def __init__(self) -> None:
        self.requests = 0
        self.errors = 0
        self.by_host: Dict[str, int] = {}
        self.by_status_class: Dict[str, int] = {}
        self.redirects = 0
        self.response_bytes = 0
        self.total_ms = 0.0
        self.max_ms = 0.0

    def record(self, url: Any, status: Optional[int], elapsed_ms: float, size: int, redirects: int) -> None:
        try:
            host = urlsplit(str(url)).hostname or "unknown"
        except ValueError:
            host = "unknown"
        self.requests += 1
        self.by_host[host] = self.by_host.get(host, 0) + 1
        cls = "error" if status is None else f"{status // 100}xx"
        self.by_status_class[cls] = self.by_status_class.get(cls, 0) + 1
        if status is None:
            self.errors += 1
        self.redirects += redirects
        self.response_bytes += size
        self.total_ms += elapsed_ms
        self.max_ms = max(self.max_ms, elapsed_ms)

    def summary(self, ticker: str, wall_ms: float) -> Dict[str, Any]:
        return {
            "ticker": ticker,
            "http_requests": self.requests,
            "errors": self.errors,
            "redirects": self.redirects,
            "by_host": dict(sorted(self.by_host.items())),
            "by_status_class": dict(sorted(self.by_status_class.items())),
            "response_bytes": self.response_bytes,
            "request_ms_total": round(self.total_ms, 1),
            "request_ms_max": round(self.max_ms, 1),
            "wall_ms": round(wall_ms, 1),
        }


def install(session_cls: Any) -> None:
    """Wrap `session_cls.request` once. Without an active collector it only calls through."""
    with _install_lock:
        if session_cls in _installed:
            return
        original = session_cls.request

        def counted(self, method, url, *args, **kwargs):  # noqa: ANN001
            collector = _active.get()
            if collector is None:
                return original(self, method, url, *args, **kwargs)
            started = time.perf_counter()
            try:
                response = original(self, method, url, *args, **kwargs)
            except Exception:
                collector.record(url, None, (time.perf_counter() - started) * 1000, 0, 0)
                raise
            try:
                size = len(response.content or b"")
            except Exception:  # noqa: BLE001 - a streaming body must not break the call
                size = 0
            collector.record(
                url,
                getattr(response, "status_code", None),
                (time.perf_counter() - started) * 1000,
                size,
                int(getattr(response, "redirect_count", 0) or 0),
            )
            return response

        session_cls.request = counted
        _installed.add(session_cls)


def _default_session_class() -> Any:
    # The HTTP backend yfinance actually selected (curl_cffi, or plain requests when that is unavailable).
    from yfinance._http import requests as yahoo_backend  # imported lazily: only needed when enabled

    return yahoo_backend.Session


@contextmanager
def count_provider_requests(ticker: str, session_cls: Any = None) -> Iterator[None]:
    """Count the HTTP requests made inside the block; a no-op unless `enabled()`."""
    if not enabled():
        yield
        return
    try:
        install(session_cls or _default_session_class())
    except Exception:  # noqa: BLE001 - diagnostics must never break a valuation
        logger.warning("provider diagnostics unavailable")
        yield
        return
    collector = _Collector()
    token = _active.set(collector)
    started = time.perf_counter()
    try:
        yield
    finally:
        _active.reset(token)
        summary = collector.summary(ticker, (time.perf_counter() - started) * 1000)
        _last.set(summary)
        logger.info("monte_carlo_provider_diagnostics %s", json.dumps(summary, sort_keys=True))


def take_last_summary() -> Optional[Dict[str, Any]]:
    """The summary recorded on this request's thread, once; None when nothing was fetched."""
    summary = _last.get()
    _last.set(None)
    return summary


def header_value(summary: Dict[str, Any]) -> str:
    return json.dumps(summary, sort_keys=True, separators=(",", ":"))
