"""
Security regression tests for `src.api.main`'s service-to-service
authentication and CORS posture.

Background: `/api/evaluate/{ticker}` used to be fully unauthenticated
with wildcard CORS (`allow_origins=["*"]`). It's now called
server-to-server only (by the Next.js `/api/evaluate/[ticker]` Route
Handler — see that route's docstring), gated by a `VALUATION_API_TOKEN`
bearer token compared in constant time, and there is no CORS middleware
at all (no browser origin is meant to call this API directly).
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.api.sector_median_thresholds import SectorMedianUnavailableCode
from src.api.sector_medians import LiveSectorMedianResult

VALID_TOKEN = "correct-service-token-at-least-32-chars-long"  # noqa: S105 - test fixture, not a real secret


def _synthetic_financial_data() -> dict:
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


@pytest.fixture
def app_with_valid_token(monkeypatch):
    monkeypatch.setattr(api_main, "fetch_company_financials", lambda ticker: _synthetic_financial_data())
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
    monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, VALID_TOKEN)
    return TestClient(api_main.app)


class TestServiceTokenEnforced:
    def test_missing_authorization_header_is_rejected(self, app_with_valid_token):
        response = app_with_valid_token.get("/api/evaluate/TEST")
        assert response.status_code == 401

    def test_malformed_authorization_header_is_rejected(self, app_with_valid_token):
        response = app_with_valid_token.get(
            "/api/evaluate/TEST", headers={"Authorization": VALID_TOKEN}  # missing "Bearer " prefix
        )
        assert response.status_code == 401

    def test_wrong_token_is_rejected(self, app_with_valid_token):
        response = app_with_valid_token.get(
            "/api/evaluate/TEST", headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401

    def test_empty_bearer_token_is_rejected(self, app_with_valid_token):
        response = app_with_valid_token.get("/api/evaluate/TEST", headers={"Authorization": "Bearer "})
        assert response.status_code == 401

    def test_correct_token_is_accepted(self, app_with_valid_token):
        response = app_with_valid_token.get(
            "/api/evaluate/TEST", headers={"Authorization": f"Bearer {VALID_TOKEN}"}
        )
        assert response.status_code == 200
        assert response.json()["ticker"] == "TEST"

    def test_error_responses_never_echo_the_configured_or_presented_token(self, app_with_valid_token):
        response = app_with_valid_token.get(
            "/api/evaluate/TEST", headers={"Authorization": "Bearer wrong-token"}
        )
        body_text = response.text
        assert VALID_TOKEN not in body_text
        assert "wrong-token" not in body_text


class TestServiceTokenFailsClosedWhenUnconfigured:
    def test_unconfigured_token_rejects_every_request(self, monkeypatch):
        """If VALUATION_API_TOKEN isn't set for this deployment at all, the
        endpoint must fail closed (reject every request) rather than
        silently allow unauthenticated access."""
        monkeypatch.setattr(api_main, "fetch_company_financials", lambda ticker: _synthetic_financial_data())
        monkeypatch.setattr(api_main, "get_risk_free_rate", lambda *a, **k: 0.04)
        monkeypatch.delenv(api_main.VALUATION_API_TOKEN_ENV_VAR, raising=False)
        client = TestClient(api_main.app)

        response = client.get("/api/evaluate/TEST", headers={"Authorization": "Bearer anything"})

        assert response.status_code == 503

    def test_example_placeholder_token_value_rejects_every_request(self, monkeypatch):
        """A deployment that copied `.env.example` verbatim without
        replacing VALUATION_API_TOKEN must fail closed exactly like an
        unset token — the placeholder is public (checked into git) and
        therefore not a secret at all."""
        monkeypatch.setattr(api_main, "fetch_company_financials", lambda ticker: _synthetic_financial_data())
        monkeypatch.setattr(api_main, "get_risk_free_rate", lambda *a, **k: 0.04)
        monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, "replace-with-a-long-random-value")
        client = TestClient(api_main.app)

        response = client.get(
            "/api/evaluate/TEST", headers={"Authorization": "Bearer replace-with-a-long-random-value"}
        )

        assert response.status_code == 503

    def test_too_short_token_rejects_every_request(self, monkeypatch):
        """A configured token below VALUATION_API_TOKEN_MIN_LENGTH must
        fail closed the same way, even if presented correctly — a short
        token is too easy to brute-force to be a meaningful gate."""
        monkeypatch.setattr(api_main, "fetch_company_financials", lambda ticker: _synthetic_financial_data())
        monkeypatch.setattr(api_main, "get_risk_free_rate", lambda *a, **k: 0.04)
        short_token = "too-short"
        assert len(short_token) < api_main.VALUATION_API_TOKEN_MIN_LENGTH
        monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, short_token)
        client = TestClient(api_main.app)

        response = client.get("/api/evaluate/TEST", headers={"Authorization": f"Bearer {short_token}"})

        assert response.status_code == 503

    def test_whitespace_only_token_rejects_every_request(self, monkeypatch):
        monkeypatch.setattr(api_main, "fetch_company_financials", lambda ticker: _synthetic_financial_data())
        monkeypatch.setattr(api_main, "get_risk_free_rate", lambda *a, **k: 0.04)
        blank_token = " " * 40
        monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, blank_token)
        client = TestClient(api_main.app)

        response = client.get("/api/evaluate/TEST", headers={"Authorization": f"Bearer {blank_token}"})

        assert response.status_code == 503

    def test_token_with_surrounding_whitespace_rejects_every_request(self, monkeypatch):
        """Mirrors frontend/src/lib/secret-validation.ts's identical check
        — a padded env value (a common copy-paste mistake) must not be
        silently accepted as-is."""
        monkeypatch.setattr(api_main, "fetch_company_financials", lambda ticker: _synthetic_financial_data())
        monkeypatch.setattr(api_main, "get_risk_free_rate", lambda *a, **k: 0.04)
        padded_token = "  " + "ab12cd34" * 4 + "  "
        monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, padded_token)
        client = TestClient(api_main.app)

        response = client.get("/api/evaluate/TEST", headers={"Authorization": f"Bearer {padded_token}"})

        assert response.status_code == 503

    def test_single_repeated_character_token_rejects_every_request(self, monkeypatch):
        monkeypatch.setattr(api_main, "fetch_company_financials", lambda ticker: _synthetic_financial_data())
        monkeypatch.setattr(api_main, "get_risk_free_rate", lambda *a, **k: 0.04)
        repeated_token = "a" * 40
        monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, repeated_token)
        client = TestClient(api_main.app)

        response = client.get("/api/evaluate/TEST", headers={"Authorization": f"Bearer {repeated_token}"})

        assert response.status_code == 503


class TestHealthEndpointRemainsPublic:
    def test_root_health_check_requires_no_token(self, app_with_valid_token):
        response = app_with_valid_token.get("/")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestNoWildcardCors:
    def test_no_cors_middleware_configured(self):
        """No CORSMiddleware at all — this API is server-to-server only, so
        no Access-Control-Allow-Origin header (wildcard or otherwise)
        should ever be served."""
        from starlette.middleware.cors import CORSMiddleware

        assert not any(
            getattr(middleware, "cls", None) is CORSMiddleware for middleware in api_main.app.user_middleware
        )

    def test_response_never_carries_a_wildcard_cors_header(self, app_with_valid_token):
        response = app_with_valid_token.get(
            "/api/evaluate/TEST",
            headers={"Authorization": f"Bearer {VALID_TOKEN}", "Origin": "https://evil.example.com"},
        )
        assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
