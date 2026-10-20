"""
HTTP surface for the Monte Carlo DCF.

Mounted by `src.api.main` with the shared service-token dependency, so every
route here requires the same bearer token as `/api/evaluate`. The router only
translates between HTTP and `MonteCarloService`; all modeling, caching and
locking lives in `src.dcf_model.monte_carlo_*`.

    GET /api/monte-carlo                     allowlist, configuration, disclosures
    GET /api/monte-carlo/{ticker}?seed=N     the four experiments for one snapshot
    GET /api/monte-carlo/snapshots/{id}?seed=N   replay bundle (snapshot + seed + config)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from src.data_ingestion.fetch_financials import fetch_company_financials
from src.dcf_model import monte_carlo_diagnostics as diagnostics
from src.dcf_model.monte_carlo_service import MonteCarloService, ServiceError
from src.utils.macro import get_risk_free_rate
from src.valuation_input import (
    ValuationInputLoader,
    ValuationInputSource,
    YahooValuationInputAdapter,
)

router = APIRouter(prefix="/api/monte-carlo", tags=["monte-carlo"])


# --------------------------------------------------------------------------
# Response models
# --------------------------------------------------------------------------

class _Model(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), populate_by_name=True)


class MonteCarloAboveMarket(_Model):
    count: int
    denominator: int
    fraction: float


class MonteCarloHistogram(_Model):
    counts: List[int]
    underflow: int
    overflow: int


class MonteCarloExperiment(_Model):
    status: Literal["complete", "incomplete", "refused"]
    target: int
    sampled_inputs: List[str]
    fixed_inputs_at_baseline: Dict[str, float]
    distributions: Dict[str, Dict[str, Any]]
    refusal_code: Optional[str]
    refusal_message: Optional[str]
    valid: int
    attempted: int
    failed_total: int
    failed_by_code: Dict[str, int]
    rejected_proposals: int
    negative_value_count: int
    statistics: Optional[Dict[str, float]]
    above_market: Optional[MonteCarloAboveMarket]
    sampled_input_summary: Optional[Dict[str, Dict[str, float]]]
    histogram: Optional[MonteCarloHistogram]


class MonteCarloMeta(_Model):
    cache: Literal["hit", "miss"]
    snapshot_cache: Literal["hit", "miss", "waited"]
    computed_at: str
    served_at: str
    environment: Dict[str, Optional[str]]
    timings_ms: Dict[str, Optional[float]]


class MonteCarloResponse(_Model):
    schema_: str = Field(alias="schema")
    model_version: str
    status: Literal["complete", "partial", "incomplete"]
    identity: Dict[str, Any]
    seed: int
    config_hash: str
    config: Dict[str, Any]
    market_price: float
    baseline: Dict[str, Any]
    kernel_baseline_check: Dict[str, Any]
    history: Dict[str, Any]
    experiments: Dict[str, MonteCarloExperiment]
    histogram_edges: Optional[List[float]]
    histogram_edges_basis: Optional[Literal["combined", "pooled"]]
    disclosures: List[Dict[str, str]]
    baseline_provenance: Optional[Dict[str, Any]] = None
    result_digest: str
    meta: MonteCarloMeta


class MonteCarloDescription(_Model):
    model_version: str
    tickers: List[str]
    experiments: List[str]
    default_seed: int
    config: Dict[str, Any]
    config_hash: str
    disclosures: List[Dict[str, str]]


# --------------------------------------------------------------------------
# Service wiring
# --------------------------------------------------------------------------

def _provide_snapshot_inputs(ticker: str) -> Tuple[object, float]:
    """One provider fetch for a snapshot: Yahoo statements plus the risk-free rate."""
    now = datetime.now(timezone.utc)
    loader = ValuationInputLoader(
        yahoo_adapter=YahooValuationInputAdapter(fetcher=lambda t: fetch_company_financials(t))
    )
    with diagnostics.count_provider_requests(ticker):  # no-op unless explicitly enabled in a preview
        outcome = loader.load(ticker, now, now, ValuationInputSource.YAHOO)
        if not outcome.is_complete:
            issue = outcome.issues[0]
            raise ServiceError(422, "input_incomplete", issue.message)
        return outcome.valuation_input, get_risk_free_rate()


_service: Optional[MonteCarloService] = None


def get_service() -> MonteCarloService:
    """Dependency; overridable in tests via `app.dependency_overrides`."""
    global _service
    if _service is None:
        _service = MonteCarloService(snapshot_provider=_provide_snapshot_inputs)
    return _service


def _raise_http(error: ServiceError) -> None:
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
        headers=headers,
    )


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@router.get("", response_model=MonteCarloDescription)
def describe_monte_carlo(service: MonteCarloService = Depends(get_service)) -> dict:
    try:
        return service.describe()
    except ServiceError as error:
        _raise_http(error)


@router.post("/diagnostics/replay")
async def replay_exported_bundle(request: Request, full: bool = False) -> JSONResponse:
    """Preview-only: replay a posted bundle on this runtime. 404 unless diagnostics are enabled."""
    if not diagnostics.enabled():
        raise HTTPException(status_code=404, detail="Not found")
    from src.dcf_model.monte_carlo_snapshot import SnapshotError, replay_bundle

    raw = await request.body()
    if len(raw) > 1_000_000:
        raise HTTPException(status_code=413, detail="Bundle too large")
    try:
        outcome = replay_bundle(json.loads(raw))
    except (ValueError, SnapshotError) as error:
        raise HTTPException(status_code=422, detail={"code": "bundle_invalid", "message": str(error)[:200]})
    result = outcome.pop("result")
    if full:  # the whole recomputed result, for cross-platform comparison
        outcome["recomputed_result"] = result
    outcome["recomputed"] = {
        "status": result.get("status"),
        "baseline": result.get("baseline"),
        "statistics": {name: exp.get("statistics") for name, exp in result.get("experiments", {}).items()},
    }
    return JSONResponse(outcome)


# Registered before "/{ticker}" so "snapshots" is never read as a ticker.
@router.get("/snapshots/{snapshot_id}")
def export_snapshot(
    snapshot_id: str,
    seed: Optional[int] = Query(None, description="Seed to embed in the replay bundle."),
    service: MonteCarloService = Depends(get_service),
) -> JSONResponse:
    try:
        bundle = service.get_replay_bundle(snapshot_id, seed)
    except ServiceError as error:
        _raise_http(error)
    return JSONResponse(
        bundle,
        headers={"Content-Disposition": f'attachment; filename="mc-dcf-{snapshot_id[:12]}.json"'},
    )


@router.get("/{ticker}", response_model=MonteCarloResponse, response_model_by_alias=True)
def run_monte_carlo_for_ticker(
    ticker: str,
    response: Response,
    seed: Optional[int] = Query(None, description="Non-negative integer seed (defaults to the fixed v1 seed)."),
    service: MonteCarloService = Depends(get_service),
) -> dict:
    try:
        result = service.get_result(ticker, seed)
    except ServiceError as error:
        _raise_http(error)
    summary = diagnostics.take_last_summary()
    if summary is not None:  # only set when diagnostics are enabled and this call fetched from the provider
        response.headers[diagnostics.HEADER_NAME] = diagnostics.header_value(summary)
    return result
