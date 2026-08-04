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

from src.data_ingestion.fetch_financials import fetch_company_financials
from src.dcf_model.dcf import DCFAssumptions, run_dcf_valuation

logger = logging.getLogger(__name__)
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


@app.get("/")
def read_root() -> dict:
    """Basic health check / landing endpoint."""
    return {"status": "ok", "service": "valuation-engine-api"}


@app.get("/api/evaluate/{ticker}", response_model=EvaluationResponse)
def evaluate_ticker(
    ticker: str,
    revenue_growth_rate: float = Query(
        0.08,
        description="Annual revenue growth rate assumption, as a decimal (e.g. 0.08 = 8%).",
    ),
    operating_margin: float = Query(
        0.25,
        description="EBIT margin assumption applied to projected revenue, as a decimal.",
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
        revenue_growth_rate: Configurable annual revenue growth assumption.
        operating_margin: Configurable EBIT margin assumption.
        terminal_growth_rate: Configurable terminal (perpetuity) growth rate.

    Returns:
        EvaluationResponse containing intrinsic value per share, current
        market price, WACC, enterprise value, equity value, and the 5-year
        FCF projection.

    Raises:
        HTTPException(400): If the ticker symbol itself is invalid.
        HTTPException(422): If required financial data (e.g. revenue,
            shares outstanding) could not be retrieved for the ticker, so
            the DCF cannot be run.
        HTTPException(500): For any other unexpected failure.
    """
    try:
        financial_data = fetch_company_financials(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    assumptions = DCFAssumptions(
        revenue_growth_rate=revenue_growth_rate,
        operating_margin=operating_margin,
        terminal_growth_rate=terminal_growth_rate,
    )

    try:
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

    return EvaluationResponse(
        ticker=financial_data["ticker"],
        current_price=result["current_market_price"],
        wacc=result["wacc"],
        enterprise_value=result["enterprise_value"],
        equity_value=result["equity_value"],
        intrinsic_value_per_share=result["intrinsic_value_per_share"],
        projected_free_cash_flows=projected_fcf,
        assumptions={
            "revenue_growth_rate": assumptions.revenue_growth_rate,
            "operating_margin": assumptions.operating_margin,
            "terminal_growth_rate": assumptions.terminal_growth_rate,
            "projection_years": assumptions.projection_years,
        },
    )
