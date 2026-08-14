"""
FastAPI application exposing the DCF valuation engine over HTTP.

Run locally with:
    uvicorn src.api.main:app --reload

See the README for full setup and usage instructions.
"""

import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.api.sector_medians import get_sector_median_price_to_intrinsic
from src.data_ingestion.fetch_financials import fetch_company_financials
from src.dcf_model.dcf import DCFAssumptions, run_dcf_valuation
from src.utils.macro import get_risk_free_rate

logger = logging.getLogger(__name__)
# This module IS the application entry point (run via `uvicorn
# src.api.main:app`, which imports it directly — there's no `__main__`
# block to gate this behind the way the other two entry-point scripts do).
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Automated Corporate Finance Valuation Engine",
    description="DCF-based intrinsic valuation API.",
    version="0.1.0",
)

# Allow all origins for now. This API is intended to be consumed by a
# separate Next.js frontend, whose origin isn't known/fixed yet.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    """Basic health check / landing endpoint."""
    return {"status": "ok", "service": "valuation-engine-api"}


@app.get("/api/evaluate/{ticker}", response_model=EvaluationResponse)
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
