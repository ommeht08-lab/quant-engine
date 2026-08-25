"""
`/api/evaluate/{ticker}`'s `sensitivity` field: response shape, baseline-
cell/top-level-field consistency, regression coverage for the fields that
existed before this feature, and proof that adding the sensitivity grid
introduces no additional data-provider call. No network: `fetch_company_
financials` and `get_risk_free_rate` are monkeypatched, same as
tests/api/test_main.py.
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main

TEST_SERVICE_TOKEN = "test-service-token-do-not-use-in-prod"  # noqa: S105 - test-only fixture value


def _synthetic_financial_data() -> dict:
    income_stmt = pd.DataFrame(
        {
            pd.Timestamp("2022-12-31"): {"Total Revenue": 1000.0, "Pretax Income": 200.0, "Tax Provision": 50.0},
            pd.Timestamp("2023-12-31"): {"Total Revenue": 1100.0, "Pretax Income": 220.0, "Tax Provision": 55.0},
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


@pytest.fixture
def fetch_call_counter(monkeypatch):
    """Wraps `fetch_company_financials` with a call counter instead of a
    bare lambda, so a test can assert the sensitivity feature adds no
    additional data-provider call per request."""
    calls = {"count": 0}

    def _fake_fetch(ticker):
        calls["count"] += 1
        return _synthetic_financial_data()

    monkeypatch.setattr(api_main, "fetch_company_financials", _fake_fetch)
    return calls


@pytest.fixture
def client(fetch_call_counter, monkeypatch):
    monkeypatch.setattr(api_main, "get_risk_free_rate", lambda *a, **k: 0.04)
    monkeypatch.setattr(
        api_main, "get_sector_median_price_to_intrinsic", lambda sector, assumptions=None: (None, "no cache in test")
    )
    monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, TEST_SERVICE_TOKEN)
    test_client = TestClient(api_main.app)
    test_client.headers.update({"Authorization": f"Bearer {TEST_SERVICE_TOKEN}"})
    return test_client


class TestSensitivityResponseShape:
    def test_sensitivity_field_present_with_5x5_grid(self, client):
        response = client.get("/api/evaluate/TEST")

        assert response.status_code == 200
        sensitivity = response.json()["sensitivity"]

        assert set(sensitivity.keys()) == {
            "wacc_axis",
            "terminal_growth_axis",
            "cells",
            "baseline_row",
            "baseline_col",
            "baseline_wacc",
            "baseline_terminal_growth_rate",
            "baseline_intrinsic_value_per_share",
        }
        assert len(sensitivity["wacc_axis"]["values"]) == 5
        assert len(sensitivity["terminal_growth_axis"]["values"]) == 5
        assert len(sensitivity["cells"]) == 5
        assert all(len(row) == 5 for row in sensitivity["cells"])

    def test_baseline_cell_matches_top_level_intrinsic_value(self, client):
        response = client.get("/api/evaluate/TEST")
        body = response.json()
        sensitivity = body["sensitivity"]

        baseline_row = sensitivity["baseline_row"]
        baseline_col = sensitivity["baseline_col"]
        baseline_cell = sensitivity["cells"][baseline_row][baseline_col]

        assert baseline_cell == pytest.approx(body["intrinsic_value_per_share"], rel=1e-9)
        assert sensitivity["baseline_intrinsic_value_per_share"] == pytest.approx(
            body["intrinsic_value_per_share"], rel=1e-9
        )
        assert sensitivity["baseline_wacc"] == pytest.approx(body["wacc"], rel=1e-9)
        assert sensitivity["baseline_terminal_growth_rate"] == pytest.approx(
            body["assumptions"]["terminal_growth_rate"], rel=1e-9
        )

    def test_sensitivity_reflects_custom_terminal_growth_rate(self, client):
        response = client.get("/api/evaluate/TEST", params={"terminal_growth_rate": 0.03})
        body = response.json()

        assert body["sensitivity"]["baseline_terminal_growth_rate"] == pytest.approx(0.03)


class TestExistingFieldsRegression:
    """The fields that existed before the sensitivity feature must be
    completely unaffected — same values, same presence."""

    def test_all_pre_existing_fields_still_present_and_correctly_typed(self, client):
        response = client.get("/api/evaluate/TEST")
        body = response.json()

        assert body["ticker"] == "TEST"
        assert isinstance(body["current_price"], float)
        assert isinstance(body["wacc"], float)
        assert isinstance(body["enterprise_value"], float)
        assert isinstance(body["equity_value"], float)
        assert isinstance(body["intrinsic_value_per_share"], float)
        assert len(body["projected_free_cash_flows"]) == 5
        assert body["assumptions"]["projection_years"] == 5
        assert body["sector"] == "Technology"
        assert body["revenue_growth_rate_source"] == "historical"
        assert body["operating_margin_source"] == "historical"

    def test_historical_vs_custom_mode_still_works_alongside_sensitivity(self, client):
        response = client.get(
            "/api/evaluate/TEST", params={"revenue_growth_rate": 0.15, "operating_margin": 0.30}
        )
        body = response.json()

        assert body["revenue_growth_rate_source"] == "custom"
        assert body["operating_margin_source"] == "custom"
        assert body["assumptions"]["revenue_growth_rate"] == pytest.approx(0.15)
        # And the sensitivity grid still comes back shaped correctly.
        assert len(body["sensitivity"]["cells"]) == 5

    def test_economic_bounds_rejection_unaffected(self, client):
        response = client.get("/api/evaluate/TEST", params={"terminal_growth_rate": 0.5})
        assert response.status_code == 422


class TestNoExtraDataProviderCall:
    def test_sensitivity_adds_no_additional_fetch_call(self, client, fetch_call_counter):
        response = client.get("/api/evaluate/TEST")

        assert response.status_code == 200
        assert "sensitivity" in response.json()
        assert fetch_call_counter["count"] == 1
