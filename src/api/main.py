"""
FastAPI application exposing the DCF valuation engine over HTTP.

Run locally with:
    uvicorn src.api.main:app --reload

See the README for full setup and usage instructions. See the repository
root's `pyproject.toml` (dependencies and Python runtime pin) and
`vercel.json` (bundle exclusions, function duration) for how this
service is deployed as a Vercel Python Function — it is intentionally
the only part of `src/` that ships there (no `src.trading`, no Alpaca
credentials).

This service is called server-to-server only, by the Next.js app's
`/api/evaluate/[ticker]` Route Handler (never directly by a browser) —
see that route's docstring for why. Accordingly:

  - There is no CORS middleware. No browser origin is ever meant to
    fetch this API directly, so no `Access-Control-Allow-Origin` header
    (wildcard or otherwise) is served; a browser attempting a
    cross-origin request is blocked by the browser's own same-origin
    policy with no cooperation needed from this app.
  - `/api/evaluate/{ticker}` requires a `VALUATION_API_TOKEN` bearer
    token (see `require_service_token` below), known only to this
    service and the Next.js server. `/`, `/healthz`, and `/readyz` are
    the only endpoints left public — see their docstrings below for the
    liveness/readiness distinction a deployment platform's health check
    should rely on.

Every request is tagged with a request ID (from an inbound `X-Request-ID`
header if the caller supplied one, otherwise generated here) and logged
as a single structured (JSON) line via `_request_id_and_access_log_middleware`
below — `logger.info`/`.warning`/`.exception` calls anywhere in this
module automatically carry that same request ID through `_RequestIdFilter`,
without every call site having to thread it through by hand. No log line
in this module ever includes a credential/token value — see
`require_service_token`'s docstring for the specific token-handling
guarantee.
"""

import contextvars
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.sector_medians import get_sector_median_price_to_intrinsic
from src.data_ingestion.fetch_financials import fetch_company_financials
from src.dcf_model.dcf import DCFAssumptions, run_dcf_valuation
from src.utils.macro import get_risk_free_rate

# --- Structured logging -----------------------------------------------
#
# A ContextVar (not a plain module global) so concurrent in-flight
# requests each see only their own request ID — ASGI runs each request
# in its own asyncio Task, and a ContextVar's value is copied per-Task,
# never shared/overwritten across them the way a plain variable would be.
_request_id_var: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "request_id", default=None
)


class _RequestIdFilter(logging.Filter):
    """Attaches the current request's ID (if any) to every log record so
    every `logger.info`/`.warning`/`.exception` call in this module is
    automatically correlated to the request that triggered it, without
    each call site passing it explicitly."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


class _JsonLogFormatter(logging.Formatter):
    """Renders one JSON object per log line — deployed container logs are
    typically scraped/indexed by field, not read as free text. Never
    includes anything beyond the log message itself, so a log line can
    only leak a credential if a call site's message text does (none in
    this module do — see `require_service_token`'s docstring)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


class _ValuationJsonHandler(logging.StreamHandler):
    """The structured JSON handler `_configure_logging` attaches to the
    `src` logger — a distinctly-named subclass purely for readability in
    reprs/tracebacks. NOT used as an identity check anymore (see
    `_VALUATION_JSON_HANDLER_MARKER` and `_configure_logging`'s
    docstring for why an `isinstance` check against this class is
    unreliable across a module reload)."""


# A plain string sentinel set as an attribute directly on the handler
# INSTANCE, rather than relying on `isinstance(handler,
# _ValuationJsonHandler)`. The two are not equivalent: `importlib.
# reload(src.api.main)` re-executes this module's top-level `class
# _ValuationJsonHandler(...)` statement, which creates a NEW class
# object distinct from the one used to construct any handler already
# sitting on `src_logger.handlers` from before the reload — that old
# instance is `type(old_instance) is _ValuationJsonHandler_from_before`,
# which is no longer the same object as the freshly-rebound
# `_ValuationJsonHandler` name after reload, so `isinstance(old_instance,
# _ValuationJsonHandler)` evaluates to `False` even though the old
# instance is exactly the handler this function is looking for. A
# plain instance attribute has no such dependency on class identity: it
# reads back the same way regardless of which class object happened to
# construct the instance.
_VALUATION_JSON_HANDLER_MARKER = "_is_valuation_json_handler"


def _configure_logging() -> None:
    """
    Attaches the structured JSON handler to the `src` package logger —
    the common ancestor of every logger this codebase's own modules use
    (`src.api.main`, `src.dcf_model.dcf`, `src.data_ingestion.
    fetch_financials`, `src.utils.macro`, `src.api.sector_medians`, ...)
    — rather than to the ROOT logger, and stops `src.*` records from
    additionally propagating up to root once handled here.

    Three earlier versions of this function each had a real bug:

    1. The first called `logging.getLogger().handlers = [handler]`,
       unconditionally REPLACING whatever handlers were already attached
       to the ROOT logger — silently discarding a hosting platform's own
       logging setup (e.g. Vercel's) the moment this module was imported.
    2. The fix for that attached to `src` instead but left `propagate`
       at its default `True`, which solved the destructive-replacement
       problem but introduced a quieter one: on any platform (or test)
       that DOES have its own handler on the root logger, every `src.*`
       record would be emitted TWICE — once by this handler (JSON, with
       `request_id`) and once by whatever's on root, since the record
       would reach both.
    3. The fix for THAT added `propagate = False` and an idempotence
       guard based on `isinstance(existing, _ValuationJsonHandler)` —
       correct within a single process that never reloads this module,
       but a genuine `importlib.reload(src.api.main)` (a dev-server hot
       reload is the realistic trigger) redefines `_ValuationJsonHandler`
       as a new class object, so the `isinstance` check against a
       handler constructed by the PREVIOUS class object silently returns
       `False` — the guard fails to recognize the existing handler and
       adds a second one. Reproduced directly: a handler count of 1
       before `importlib.reload`, then 2 immediately after.

    This version fixes all three at once: root's handlers are never
    touched (added, removed, reordered, or reconfigured) — satisfying
    (1); `src_logger.propagate = False` means a record handled here
    never ALSO reaches root's handlers — satisfying (2); and the
    idempotence check now scans for the `_VALUATION_JSON_HANDLER_MARKER`
    attribute (see its own comment) instead of `isinstance` — an
    existing handler from before a reload is found and REUSED rather
    than being invisible to a freshly-rebound class object — satisfying
    (3). `src_logger.setLevel(...)`/`.propagate = False` are enforced
    unconditionally on every call, including when an existing handler is
    reused, so neither setting can ever be left stale by whatever state
    a reload's intervening code happened to leave them in.
    """
    src_logger = logging.getLogger("src")

    handler = next(
        (h for h in src_logger.handlers if getattr(h, _VALUATION_JSON_HANDLER_MARKER, False)),
        None,
    )
    if handler is None:
        handler = _ValuationJsonHandler()
        setattr(handler, _VALUATION_JSON_HANDLER_MARKER, True)
        handler.setFormatter(_JsonLogFormatter())
        handler.addFilter(_RequestIdFilter())
        src_logger.addHandler(handler)

    src_logger.setLevel(logging.INFO)
    src_logger.propagate = False


logger = logging.getLogger(__name__)
# This module IS the application entry point (run via `uvicorn
# src.api.main:app`, which imports it directly — there's no `__main__`
# block to gate this behind the way the other two entry-point scripts do).
_configure_logging()

load_dotenv()

VALUATION_API_TOKEN_ENV_VAR = "VALUATION_API_TOKEN"

# Kept in exact sync with frontend/src/lib/secret-validation.ts's
# VALUATION_API_TOKEN_REQUIREMENT — both sides validate the SAME token
# against the SAME requirements (format/placeholder/minimum-length only;
# neither side measures or claims to measure entropy — see that file's
# top-of-file comment), so a value either side would accept is never one
# the other side would reject (and vice versa). If either changes,
# change both.
VALUATION_API_TOKEN_MIN_LENGTH = 32
_KNOWN_PLACEHOLDER_TOKEN_VALUES = frozenset({"replace-with-a-long-random-value"})


def _configured_service_token_state() -> bool:
    """`True` iff `VALUATION_API_TOKEN` currently passes every check in
    `_configured_service_token_or_none` — the single source of truth
    `/readyz` and the startup log line below both read, so they can never
    disagree about whether this deployment is usable."""
    return _configured_service_token_or_none() is not None


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001 - FastAPI's lifespan signature requires this parameter
    """Runs once at process startup (before the first request is
    accepted) and once at shutdown. The startup half is this service's
    explicit configuration validation: it logs whether
    `VALUATION_API_TOKEN` is usable so a misconfigured deployment is
    obvious in the very first log lines after boot, not just on the
    first rejected request — and never logs the token's value either way."""
    if _configured_service_token_state():
        logger.info("Startup configuration check passed: VALUATION_API_TOKEN is configured.")
    else:
        logger.warning(
            "Startup configuration check FAILED: VALUATION_API_TOKEN is not usable (unset, "
            "placeholder, too short, whitespace-padded, or a single repeated character). Every "
            "request to a protected route will be rejected with 503 until this is fixed. See "
            "/readyz."
        )
    yield


app = FastAPI(
    title="Automated Corporate Finance Valuation Engine",
    description="DCF-based intrinsic valuation API.",
    version="0.1.0",
    lifespan=_lifespan,
)

REQUEST_ID_HEADER = "X-Request-ID"
# Bounds how much of a caller-supplied X-Request-ID this service will
# echo back/log verbatim — generous enough for any real correlation ID
# scheme, small enough that a request can't use this header to smuggle
# an unbounded string into structured logs.
_MAX_INCOMING_REQUEST_ID_LENGTH = 128


@app.middleware("http")
async def _request_id_and_access_log_middleware(request: Request, call_next):
    """Assigns/propagates a request ID (via `_request_id_var`, so every
    log line emitted while handling this request — including from deep
    inside `evaluate_ticker` — is tagged with it automatically), logs
    exactly one structured access-log line per request (method, path,
    status code, duration), and echoes the request ID back as a response
    header so the Next.js proxy or an operator reading its own logs could
    correlate the two sides of a call, though neither side depends on
    that today.

    Also this service's ONLY backstop for an exception that reaches
    neither `evaluate_ticker`'s own try/except (which already converts
    its failure modes into 400/422/500 `HTTPException`s) nor any other
    route's handling: this middleware sits OUTSIDE FastAPI's routing/
    exception-handling layer (Starlette's `ExceptionMiddleware`) but
    INSIDE the outermost `ServerErrorMiddleware` that a plain
    `@app.exception_handler(Exception)` registration would be wired into
    — building the fallback response here, in the same scope that still
    has `_request_id_var` set, is what lets that response actually carry
    the request ID; a handler registered on `ServerErrorMiddleware` runs
    only after this middleware's own `finally` has already reset it.
    Never returns a stack trace or exception message to the caller —
    only a generic message and the request ID, so an operator can find
    the matching structured log line without the response body leaking
    internals."""
    incoming = request.headers.get(REQUEST_ID_HEADER)
    request_id = incoming[:_MAX_INCOMING_REQUEST_ID_LENGTH] if incoming else uuid.uuid4().hex
    token = _request_id_var.set(request_id)
    start = time.monotonic()
    try:
        # Both the success and failure responses are built inside this
        # try (rather than after it) so the `finally` below — which
        # resets `_request_id_var` — always runs last, after the
        # response (and its request_id-tagged log line) already exist.
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.monotonic() - start) * 1000
            logger.exception(
                "Unhandled exception. method=%s path=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                duration_ms,
            )
            response = JSONResponse(
                status_code=500,
                content={"error": "Internal server error.", "request_id": request_id},
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response

        duration_ms = (time.monotonic() - start) * 1000
        logger.info(
            "Request handled. method=%s path=%s status_code=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
    finally:
        _request_id_var.reset(token)


def _configured_service_token_or_none() -> Optional[str]:
    """
    The configured `VALUATION_API_TOKEN`, or `None` if it fails any of
    this codebase's baseline secret-format checks (mirrored exactly on
    the frontend side — see the module-level comment above): unset,
    whitespace-only, surrounded by leading/trailing whitespace, still
    the literal example placeholder from `.env.example`, a single
    character repeated to reach the length requirement, or shorter than
    `VALUATION_API_TOKEN_MIN_LENGTH`. Any of these means this deployment
    does not actually have a usable service token configured, and every
    protected request must be refused the same way a genuinely-unset
    token would be (see `require_service_token`).
    """
    configured_token = os.getenv(VALUATION_API_TOKEN_ENV_VAR)
    if not configured_token:
        return None
    if configured_token.strip() == "":
        return None
    if configured_token != configured_token.strip():
        return None
    if configured_token in _KNOWN_PLACEHOLDER_TOKEN_VALUES:
        return None
    if len(set(configured_token)) == 1:
        return None
    if len(configured_token) < VALUATION_API_TOKEN_MIN_LENGTH:
        return None
    return configured_token


async def require_service_token(authorization: Optional[str] = Header(default=None)) -> None:
    """
    FastAPI dependency enforcing the service-to-service bearer token on
    every route it's attached to. The token travels as `Authorization:
    Bearer <token>` and is compared with `hmac.compare_digest` (constant
    time — a naive `==` would leak how many leading bytes matched via
    response-time differences).

    Fails closed: if `VALUATION_API_TOKEN` fails any check in
    `_configured_service_token_or_none` (unset, blank/whitespace-padded,
    still the `.env.example` placeholder, a single repeated character,
    or shorter than `VALUATION_API_TOKEN_MIN_LENGTH`), every request to
    a protected route is rejected (503, "not configured") rather than
    silently allowing unauthenticated access or accepting an obviously-
    not-a-real-secret token. Error details never include the configured
    or presented token value.
    """
    configured_token = _configured_service_token_or_none()
    if configured_token is None:
        raise HTTPException(
            status_code=503, detail="Valuation API authentication is not configured on this deployment."
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")

    presented_token = authorization[len("Bearer "):]
    if not hmac.compare_digest(presented_token, configured_token):
        raise HTTPException(status_code=401, detail="Invalid service token.")


class FreeCashFlowYear(BaseModel):
    """A single projected year in the 5-year FCF forecast."""

    year: int
    revenue: float
    ebit: float
    nopat: float
    da: float
    capex: float
    change_in_nwc: float
    fcf: float


class EvaluationResponse(BaseModel):
    """Response payload for GET /api/evaluate/{ticker}."""

    ticker: str
    current_price: Optional[float]
    wacc: float
    enterprise_value: float
    equity_value: float
    intrinsic_value_per_share: float
    projected_free_cash_flows: List[FreeCashFlowYear]
    assumptions: dict
    sector: str
    price_to_intrinsic_value: Optional[float]
    sector_median_p_iv: Optional[float]
    sector_median_unavailable_reason: Optional[str] = None
    revenue_growth_rate_source: str
    operating_margin_source: str


@app.get("/")
def read_root() -> dict:
    """Basic health check / landing endpoint. Equivalent to `/healthz`,
    kept for backward compatibility with any existing external health
    check already pointed at `/`."""
    return {"status": "ok", "service": "valuation-engine-api"}


@app.get("/healthz")
def liveness() -> dict:
    """
    Liveness probe: confirms only that this process is up and able to
    handle an HTTP request, nothing about configuration or external data
    providers — a deployment platform should restart the container if
    this ever fails to respond, but should NOT use it to decide whether
    to route real traffic here (see `/readyz` for that). No auth, no
    dependency checks, no outbound calls.
    """
    return {"status": "ok", "service": "valuation-engine-api"}


class ReadinessChecks(BaseModel):
    """Individual readiness checks. Booleans only — never a value that
    could leak a credential or its shape."""

    service_token_configured: bool


class ReadinessResponse(BaseModel):
    """Response payload for `GET /readyz`."""

    status: str
    checks: ReadinessChecks


@app.get("/readyz", response_model=ReadinessResponse)
def readiness(response: Response) -> ReadinessResponse:
    """
    Readiness probe: distinct from `/healthz` above — this reports
    whether the service is actually USABLE, not just alive. Today that
    means exactly one thing: whether `VALUATION_API_TOKEN` passes
    `_configured_service_token_or_none`'s checks, using the same
    single source of truth the startup log line and `require_service_token`
    itself both read, so this can never disagree with what a real request
    would experience.

    Deliberately does NOT make a live call to yfinance, the Treasury
    data source, or anything else external on every readiness check —
    this endpoint may be polled every few seconds by the hosting
    platform, and doing so would mean silently hammering a third-party
    provider from an infrastructure health check no operator asked for.
    Data-provider reachability is instead observed indirectly, through
    real `/api/evaluate/{ticker}` responses and their own logged outcomes.

    Returns HTTP 503 (not 200) when not ready, so a platform's own
    readiness-gate tooling can rely on the status code alone without
    parsing the body.
    """
    checks = ReadinessChecks(service_token_configured=_configured_service_token_state())
    is_ready = checks.service_token_configured
    if not is_ready:
        response.status_code = 503
    return ReadinessResponse(status="ready" if is_ready else "not_ready", checks=checks)


@app.get("/api/evaluate/{ticker}", response_model=EvaluationResponse, dependencies=[Depends(require_service_token)])
def evaluate_ticker(
    ticker: str,
    revenue_growth_rate: Optional[float] = Query(
        None,
        description=(
            "Annual revenue growth rate assumption, as a decimal (e.g. 0.08 = 8%). "
            "Omit this parameter entirely to use the company's own historical Revenue "
            "CAGR instead (the default dashboard mode) — this is NOT the same as "
            "passing 0."
        ),
    ),
    operating_margin: Optional[float] = Query(
        None,
        description=(
            "EBIT margin assumption applied to projected revenue, as a decimal. Omit "
            "this parameter entirely to use the company's own historical average "
            "operating margin instead (the default dashboard mode) — this is NOT the "
            "same as passing 0."
        ),
    ),
    terminal_growth_rate: float = Query(
        0.025,
        description="Perpetual growth rate used in the terminal value calculation, as a decimal.",
    ),
) -> EvaluationResponse:
    """
    Run a full DCF valuation for a given ticker.

    Fetches live financial statements, current price, shares outstanding,
    and beta via yfinance, then projects Free Cash Flow, WACC, terminal
    value, and intrinsic value per share using the supplied (or default)
    assumptions.

    Args:
        ticker: Stock ticker symbol, e.g. "AAPL".
        revenue_growth_rate: Explicit annual revenue growth override. When
            omitted (the default), the company's own historical Revenue
            CAGR is used instead — this is also the same default the
            sector-median cache (`src.api.sector_medians`) and the live
            trading engine (`src.trading.alpaca_execution`) are generated
            with, so the default (no query params) response is comparable
            against the default cached sector median out of the box.
        operating_margin: Explicit EBIT margin override, same
            historical-by-default behavior as `revenue_growth_rate`.
        terminal_growth_rate: Configurable terminal (perpetuity) growth
            rate — always an explicit policy assumption (no per-company
            historical equivalent exists), so it has no omit-to-derive
            mode.

    The discount rate's CAPM risk-free leg uses a live 10-Year Treasury
    yield (`src.utils.macro.get_risk_free_rate`) rather than a static
    assumption, keeping WACC in sync with the same macro environment used
    by the backtester and the sector-median cache generator.

    Returns:
        EvaluationResponse containing intrinsic value per share, current
        market price, WACC, enterprise value, equity value, the 5-year
        FCF projection, the company's sector, its Price / Intrinsic Value
        (P/IV) ratio, that sector's median P/IV (from a precomputed
        cache — see `src.api.sector_medians`), and
        `revenue_growth_rate_source`/`operating_margin_source` ("historical"
        or "custom") so a caller can distinguish "the dashboard is
        showing the company's own historical growth" from "the dashboard
        is showing a user-chosen slider value" rather than guessing from
        the numeric value alone. `sector_median_p_iv` is `null` whenever
        the comparison is refused — cache missing/stale/unhealthy,
        generated with different assumptions than this request, or too
        few samples for the sector — with `sector_median_unavailable_reason`
        explaining why, rather than silently comparing against a
        cache that isn't actually comparable to this valuation.

    Raises:
        HTTPException(400): If the ticker symbol itself is invalid.
        HTTPException(422): If required financial data (e.g. revenue,
            shares outstanding) could not be retrieved for the ticker, or
            an explicitly-supplied assumption is outside its documented
            economic range (see `src.dcf_model.dcf.DCFAssumptions`), so
            the DCF cannot be run.
        HTTPException(500): For any other unexpected failure.
    """
    try:
        financial_data = fetch_company_financials(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    revenue_growth_rate_source = "custom" if revenue_growth_rate is not None else "historical"
    operating_margin_source = "custom" if operating_margin is not None else "historical"

    try:
        assumptions = DCFAssumptions(
            revenue_growth_rate=revenue_growth_rate,
            operating_margin=operating_margin,
            terminal_growth_rate=terminal_growth_rate,
            risk_free_rate=get_risk_free_rate(),
        )
        result = run_dcf_valuation(financial_data, assumptions)
    except ValueError as exc:
        logger.warning("DCF valuation failed for %s: %s", ticker, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface unexpected failures as 500s
        logger.exception("Unexpected error running DCF valuation for %s", ticker)
        raise HTTPException(status_code=500, detail="Unexpected error running valuation.") from exc

    projected_fcf = [
        FreeCashFlowYear(
            year=int(year),
            revenue=row["revenue"],
            ebit=row["ebit"],
            nopat=row["nopat"],
            da=row["da"],
            capex=row["capex"],
            change_in_nwc=row["change_in_nwc"],
            fcf=row["fcf"],
        )
        for year, row in result["fcf_projection"].iterrows()
    ]

    current_price = result["current_market_price"]
    intrinsic_value = result["intrinsic_value_per_share"]
    price_to_intrinsic_value = (
        current_price / intrinsic_value if current_price and intrinsic_value and intrinsic_value > 0 else None
    )

    sector = financial_data.get("sector", "Unknown")
    sector_median_p_iv, sector_median_unavailable_reason = get_sector_median_price_to_intrinsic(
        sector, assumptions=assumptions
    )

    return EvaluationResponse(
        ticker=financial_data["ticker"],
        current_price=current_price,
        wacc=result["wacc"],
        enterprise_value=result["enterprise_value"],
        equity_value=result["equity_value"],
        intrinsic_value_per_share=intrinsic_value,
        projected_free_cash_flows=projected_fcf,
        assumptions={
            # The ACTUAL values used for the projection — never the raw
            # request params, which are `None` in historical mode. See
            # `run_dcf_valuation`'s docstring: `result["revenue_growth_rate"]`/
            # `result["operating_margin"]` reflect whatever was actually
            # used (explicit override, historical derivation, or fallback).
            "revenue_growth_rate": result["revenue_growth_rate"],
            "operating_margin": result["operating_margin"],
            "terminal_growth_rate": assumptions.terminal_growth_rate,
            "projection_years": assumptions.projection_years,
            "risk_free_rate": assumptions.risk_free_rate,
        },
        sector=sector,
        price_to_intrinsic_value=price_to_intrinsic_value,
        sector_median_p_iv=sector_median_p_iv,
        sector_median_unavailable_reason=sector_median_unavailable_reason,
        revenue_growth_rate_source=revenue_growth_rate_source,
        operating_margin_source=operating_margin_source,
    )
