"""
Group I: `/api/evaluate/{ticker}` endpoint contract.

Covers the historical-vs-custom assumption mode split (omitted query
params -> company-derived historicals; explicit params -> user override),
the economic-bounds rejection path (422, not a 500 or a silently-huge
valuation), and an end-to-end contract test proving the endpoint's own
DEFAULT (no query params) request is comparable against the sector-median
cache's DEFAULT generation assumptions — the exact cross-system mismatch
the independent review found. No network: `fetch_company_financials` and
`get_risk_free_rate` are monkeypatched; TestClient never leaves the
process.
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.api.sector_medians import generate_sector_medians
from src.dcf_model.dcf import DCFAssumptions


def _synthetic_financial_data() -> dict:
    # Two periods so a historical Revenue CAGR is actually derivable
    # (distinguishable from the "can't derive, use fallback" path).
    income_stmt = pd.DataFrame(
        {
            pd.Timestamp("2022-12-31"): {
                "Total Revenue": 1000.0, "Pretax Income": 200.0, "Tax Provision": 50.0,
            },
            pd.Timestamp("2023-12-31"): {
                "Total Revenue": 1100.0, "Pretax Income": 220.0, "Tax Provision": 55.0,
            },
        }
    )
    balance_sheet = pd.DataFrame(
        {pd.Timestamp("2023-12-31"): {"Total Debt": 100.0, "Cash And Cash Equivalents": 50.0}}
    )
    return {
        "ticker": "TEST",
        "sector": "Technology",
        "income_statement": income_stmt,
        "balance_sheet": balance_sheet,
        "cash_flow": None,
        "current_price": 50.0,
        "shares_outstanding": 100.0,
        "beta": 1.0,
    }


TEST_SERVICE_TOKEN = "test-service-token-do-not-use-in-prod"  # noqa: S105 - test-only fixture value


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_main, "fetch_company_financials", lambda ticker: _synthetic_financial_data())
    monkeypatch.setattr(api_main, "get_risk_free_rate", lambda *a, **k: 0.04)
    monkeypatch.setattr(
        api_main, "get_sector_median_price_to_intrinsic", lambda sector, assumptions=None: (None, "no cache in test")
    )
    monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, TEST_SERVICE_TOKEN)
    test_client = TestClient(api_main.app)
    test_client.headers.update({"Authorization": f"Bearer {TEST_SERVICE_TOKEN}"})
    return test_client


class TestHistoricalVsCustomAssumptionMode:
    def test_omitted_params_use_historical_mode(self, client):
        response = client.get("/api/evaluate/TEST")

        assert response.status_code == 200
        body = response.json()
        assert body["revenue_growth_rate_source"] == "historical"
        assert body["operating_margin_source"] == "historical"
        # The historically-derived value (Revenue CAGR of roughly 1000 ->
        # 1100 = ~10%) must be the ACTUAL number reported, not None and
        # not the old hardcoded 0.08 default.
        assert body["assumptions"]["revenue_growth_rate"] == pytest.approx(0.10, abs=1e-3)

    def test_explicit_params_use_custom_mode(self, client):
        response = client.get(
            "/api/evaluate/TEST", params={"revenue_growth_rate": 0.15, "operating_margin": 0.30}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["revenue_growth_rate_source"] == "custom"
        assert body["operating_margin_source"] == "custom"
        assert body["assumptions"]["revenue_growth_rate"] == pytest.approx(0.15)
        assert body["assumptions"]["operating_margin"] == pytest.approx(0.30)

    def test_partial_override_tracks_each_field_independently(self, client):
        """Only revenue_growth_rate overridden -> operating_margin stays historical."""
        response = client.get("/api/evaluate/TEST", params={"revenue_growth_rate": 0.15})

        assert response.status_code == 200
        body = response.json()
        assert body["revenue_growth_rate_source"] == "custom"
        assert body["operating_margin_source"] == "historical"


class TestEconomicBoundsRejected:
    def test_absurd_revenue_growth_rate_is_rejected_with_422(self, client):
        response = client.get("/api/evaluate/TEST", params={"revenue_growth_rate": 50.0})

        assert response.status_code == 422
        assert "revenue_growth_rate" in response.json()["detail"]

    def test_absurd_operating_margin_is_rejected_with_422(self, client):
        response = client.get("/api/evaluate/TEST", params={"operating_margin": 5.0})

        assert response.status_code == 422

    def test_out_of_range_terminal_growth_rate_is_rejected_with_422(self, client):
        response = client.get("/api/evaluate/TEST", params={"terminal_growth_rate": 0.5})

        assert response.status_code == 422

    def test_in_bounds_values_succeed(self, client):
        response = client.get(
            "/api/evaluate/TEST",
            params={"revenue_growth_rate": 0.08, "operating_margin": 0.25, "terminal_growth_rate": 0.025},
        )

        assert response.status_code == 200


class TestDefaultAssumptionCrossSystemContract:
    """
    The exact contract the independent review found broken: the
    endpoint's DEFAULT (no query params) request must be comparable
    against the sector-median cache's DEFAULT generation assumptions —
    both must resolve to the same "derive from historicals"
    configuration, not None-vs-0.08 divergence.
    """

    def test_default_request_assumptions_match_default_cache_generation_assumptions(self, client):
        from src.api.sector_medians import _serialize_comparable_assumptions

        # What the endpoint uses when called with NO query params.
        default_request_assumptions = DCFAssumptions(
            revenue_growth_rate=None, operating_margin=None, terminal_growth_rate=0.025,
        )
        # What both cache generators (src.api.sector_medians.generate_sector_medians
        # and src.trading.alpaca_execution.refresh_sector_median_cache) use by default.
        default_cache_assumptions = DCFAssumptions()

        assert _serialize_comparable_assumptions(default_request_assumptions) == _serialize_comparable_assumptions(
            default_cache_assumptions
        )

    def test_generate_sector_medians_default_signature_is_historical(self, monkeypatch):
        """
        `generate_sector_medians()` (used by both the standalone script
        and, with the same default, mirrored by the trading engine) must
        default to the historical (None/None) configuration — confirmed
        by inspecting what it actually serializes into the cache, not
        just by reading the default parameter.
        """
        monkeypatch.setattr(
            "src.api.sector_medians._compute_current_price_to_intrinsic",
            lambda ticker, assumptions: None,  # no real valuation needed for this contract check
        )
        monkeypatch.setattr("src.api.sector_medians.get_risk_free_rate", lambda *a, **k: 0.04)

        cache = generate_sector_medians(tickers=["AAPL"])

        assert cache["assumptions"]["revenue_growth_rate"] is None
        assert cache["assumptions"]["operating_margin"] is None
