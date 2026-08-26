"""
`/api/evaluate/{ticker}`'s `scenarios` field: response shape, Base/
top-level consistency, and proof that adding Bear/Base/Bull introduces
no additional data-provider call. No network: `fetch_company_financials`
and `get_risk_free_rate` are monkeypatched, same as tests/api/test_main.py
and tests/api/test_sensitivity_endpoint.py.
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.api.sector_median_thresholds import SectorMedianUnavailableCode
from src.api.sector_medians import LiveSectorMedianResult

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
        api_main,
        "get_live_sector_median_price_to_intrinsic",
        lambda sector, assumptions=None: LiveSectorMedianResult(
            median=None,
            unavailable_code=SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE,
            unavailable_reason="no cache in test",
            provenance=None,
        ),
    )
    monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, TEST_SERVICE_TOKEN)
    test_client = TestClient(api_main.app)
    test_client.headers.update({"Authorization": f"Bearer {TEST_SERVICE_TOKEN}"})
    return test_client


class TestScenariosResponseShape:
    def test_scenarios_field_present_with_bear_base_bull(self, client):
        response = client.get("/api/evaluate/TEST")

        assert response.status_code == 200
        scenarios = response.json()["scenarios"]

        assert set(scenarios.keys()) == {"bear", "base", "bull"}
        for name in ("bear", "base", "bull"):
            scenario = scenarios[name]
            assert scenario["name"] == name
            assert set(scenario.keys()) == {
                "name",
                "assumptions",
                "intrinsic_value_per_share",
                "is_valid",
                "invalid_reason",
            }
            assert set(scenario["assumptions"].keys()) == {
                "revenue_growth_rate",
                "operating_margin",
                "wacc",
                "terminal_growth_rate",
            }

    def test_base_matches_top_level_intrinsic_value_and_assumptions(self, client):
        response = client.get("/api/evaluate/TEST")
        body = response.json()
        base = body["scenarios"]["base"]

        assert base["is_valid"] is True
        assert base["intrinsic_value_per_share"] == pytest.approx(body["intrinsic_value_per_share"], rel=1e-9)
        assert base["assumptions"]["wacc"] == pytest.approx(body["wacc"], rel=1e-9)
        assert base["assumptions"]["revenue_growth_rate"] == pytest.approx(
            body["assumptions"]["revenue_growth_rate"], rel=1e-9
        )
        assert base["assumptions"]["operating_margin"] == pytest.approx(
            body["assumptions"]["operating_margin"], rel=1e-9
        )
        assert base["assumptions"]["terminal_growth_rate"] == pytest.approx(
            body["assumptions"]["terminal_growth_rate"], rel=1e-9
        )

    def test_bear_and_bull_differ_from_base_in_the_expected_direction(self, client):
        response = client.get("/api/evaluate/TEST")
        scenarios = response.json()["scenarios"]

        assert scenarios["bear"]["assumptions"]["wacc"] > scenarios["base"]["assumptions"]["wacc"]
        assert scenarios["bull"]["assumptions"]["wacc"] < scenarios["base"]["assumptions"]["wacc"]
        assert (
            scenarios["bear"]["assumptions"]["revenue_growth_rate"]
            < scenarios["base"]["assumptions"]["revenue_growth_rate"]
        )
        assert (
            scenarios["bull"]["assumptions"]["revenue_growth_rate"]
            > scenarios["base"]["assumptions"]["revenue_growth_rate"]
        )
        if scenarios["bear"]["is_valid"] and scenarios["bull"]["is_valid"]:
            assert scenarios["bear"]["intrinsic_value_per_share"] < scenarios["base"]["intrinsic_value_per_share"]
            assert scenarios["bull"]["intrinsic_value_per_share"] > scenarios["base"]["intrinsic_value_per_share"]


class TestExistingFieldsRegression:
    def test_all_pre_existing_fields_still_present(self, client):
        response = client.get("/api/evaluate/TEST")
        body = response.json()

        assert body["ticker"] == "TEST"
        assert isinstance(body["intrinsic_value_per_share"], float)
        assert "sensitivity" in body
        assert len(body["sensitivity"]["cells"]) == 5

    def test_historical_vs_custom_mode_still_works_alongside_scenarios(self, client):
        response = client.get(
            "/api/evaluate/TEST", params={"revenue_growth_rate": 0.15, "operating_margin": 0.30}
        )
        body = response.json()

        assert body["revenue_growth_rate_source"] == "custom"
        assert body["scenarios"]["base"]["assumptions"]["revenue_growth_rate"] == pytest.approx(0.15)
        assert body["scenarios"]["base"]["assumptions"]["operating_margin"] == pytest.approx(0.30)


class TestNoExtraDataProviderCall:
    def test_scenarios_add_no_additional_fetch_call(self, client, fetch_call_counter):
        response = client.get("/api/evaluate/TEST")

        assert response.status_code == 200
        assert "scenarios" in response.json()
        assert fetch_call_counter["count"] == 1
