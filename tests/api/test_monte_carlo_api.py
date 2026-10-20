"""
`/api/monte-carlo` endpoints: authentication, validation, response shape,
caching, error mapping and the replay-bundle export. No network: the provider
fetch and the risk-free rate are replaced, and the store is in-memory.
"""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.api import monte_carlo_api
from src.dcf_model.monte_carlo_service import MonteCarloService, snapshot_lock_key
from src.dcf_model.monte_carlo_store import InMemoryStore
from tests.dcf.monte_carlo_fixtures import make_financial_data

TEST_SERVICE_TOKEN = "test-service-token-do-not-use-in-prod"  # noqa: S105 - test-only fixture value
NOW = datetime(2026, 10, 20, 15, 30, tzinfo=timezone.utc)


@pytest.fixture
def fetch_counter(monkeypatch):
    calls = {"count": 0}

    def fake_fetch(ticker):
        calls["count"] += 1
        return make_financial_data(ticker=ticker)

    monkeypatch.setattr(monte_carlo_api, "fetch_company_financials", fake_fetch)
    monkeypatch.setattr(monte_carlo_api, "get_risk_free_rate", lambda *a, **k: 0.04)
    return calls


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def client(fetch_counter, store, monkeypatch):
    service = MonteCarloService(
        monte_carlo_api._provide_snapshot_inputs,
        store_factory=lambda: store,
        allowlist=("AAPL", "KO", "JNJ"),
        clock=lambda: NOW,
    )
    api_main.app.dependency_overrides[monte_carlo_api.get_service] = lambda: service
    monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, TEST_SERVICE_TOKEN)
    test_client = TestClient(api_main.app)
    test_client.headers.update({"Authorization": f"Bearer {TEST_SERVICE_TOKEN}"})
    yield test_client
    api_main.app.dependency_overrides.pop(monte_carlo_api.get_service, None)


class TestAuthentication:
    @pytest.mark.parametrize("path", ["/api/monte-carlo", "/api/monte-carlo/AAPL", "/api/monte-carlo/snapshots/abc"])
    def test_every_route_requires_the_service_token(self, client, path):
        anonymous = TestClient(api_main.app)
        assert anonymous.get(path).status_code == 401
        assert anonymous.get(path, headers={"Authorization": "Bearer wrong-token-value"}).status_code == 401
        assert client.get(path).status_code != 401


class TestDescribe:
    def test_lists_the_allowlist_configuration_and_disclosures(self, client):
        body = client.get("/api/monte-carlo").json()
        assert body["tickers"] == ["AAPL", "KO", "JNJ"]
        assert body["experiments"] == ["growth", "margin", "wacc", "combined"]
        assert body["config"]["target_valid"] == 10_000 and len(body["config_hash"]) == 64
        assert any(d["id"] == "independence" for d in body["disclosures"])


class TestRun:
    def test_response_shape(self, client):
        response = client.get("/api/monte-carlo/AAPL")
        assert response.status_code == 200
        body = response.json()
        assert body["schema"] == "mc-dcf-result-v1" and "schema_" not in body
        assert body["status"] == "complete" and body["seed"] == 20261101
        assert body["identity"]["ticker"] == "AAPL" and body["identity"]["source"] == "yahoo"
        assert body["market_price"] == 150.0
        assert set(body["experiments"]) == {"growth", "margin", "wacc", "combined"}
        combined = body["experiments"]["combined"]
        assert combined["valid"] == 10_000 and combined["above_market"]["denominator"] == 10_000
        assert len(body["histogram_edges"]) == 41 and len(combined["histogram"]["counts"]) == 40
        assert set(combined["statistics"]) == {"mean", "p10", "p50", "p90", "min", "max"}
        assert body["meta"]["cache"] == "miss" and body["meta"]["snapshot_cache"] == "miss"
        assert body["kernel_baseline_check"]["exactly_equal"] is True

    def test_response_exposes_baseline_provenance(self, client):
        body = client.get("/api/monte-carlo/AAPL").json()
        block = body["baseline_provenance"]
        assert block["schema"] == "mc-dcf-baseline-provenance-v1" and block["status"] == "ok"
        assert block["margin"]["rows_used"] == ["EBIT"] and block["margin"]["periods"]
        assert block["cost_of_debt"]["is_fallback"] is False and block["tax_rate"]["source"] == "statement_derived"
        assert block["capex"]["fixed"] is True and block["wacc"]["baseline_was_clamped"] is False
        market_price = block["timing"]["market_price"]
        assert market_price["observed_at"] is None and market_price["status"] == "not_provided_by_provider"
        assert block["timing"]["snapshot_captured_at"] == body["identity"]["snapshot_captured_at"]
        assert len(body["result_digest"]) == 64

    def test_second_call_is_a_cache_hit_with_one_provider_fetch(self, client, fetch_counter):
        first = client.get("/api/monte-carlo/AAPL").json()
        second = client.get("/api/monte-carlo/aapl").json()
        assert fetch_counter["count"] == 1
        assert second["meta"]["cache"] == "hit"
        assert {k: v for k, v in first.items() if k != "meta"} == {k: v for k, v in second.items() if k != "meta"}

    def test_seed_query_parameter_is_honored(self, client):
        a = client.get("/api/monte-carlo/AAPL?seed=7").json()
        b = client.get("/api/monte-carlo/AAPL?seed=8").json()
        assert a["seed"] == 7 and b["seed"] == 8 and a["result_digest"] != b["result_digest"]

    def test_partial_results_return_200_with_per_experiment_refusals(self, client, monkeypatch):
        short = make_financial_data(revenues=(110.0, 120.0, 130.0), ebits=(20.0, 22.0, 25.0),
                                    periods=("2022-12-31", "2023-12-31", "2024-12-31"))
        monkeypatch.setattr(monte_carlo_api, "fetch_company_financials", lambda t: short)
        body = client.get("/api/monte-carlo/KO").json()
        assert body["status"] == "partial"
        assert body["history"]["refusal_code"] == "insufficient_history"
        assert body["experiments"]["growth"]["status"] == "refused"
        assert body["experiments"]["growth"]["refusal_code"] == "insufficient_history"
        assert body["experiments"]["wacc"]["valid"] == 10_000
        assert body["histogram_edges"] is not None and body["histogram_edges_basis"] == "pooled"

    def test_unsupported_ticker_is_404_and_nothing_is_fetched(self, client, fetch_counter):
        response = client.get("/api/monte-carlo/MSFT")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "ticker_not_supported"
        assert fetch_counter["count"] == 0

    @pytest.mark.parametrize("query", ["seed=-1", f"seed={2**63}"])
    def test_out_of_range_seed_is_422_with_a_code(self, client, query):
        response = client.get(f"/api/monte-carlo/AAPL?{query}")
        assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_seed"

    def test_non_integer_seed_is_rejected_by_request_validation(self, client):
        assert client.get("/api/monte-carlo/AAPL?seed=abc").status_code == 422
        assert client.get("/api/monte-carlo/AAPL?seed=1.5").status_code == 422

    def test_incomplete_provider_data_is_a_422(self, client, monkeypatch):
        monkeypatch.setattr(monte_carlo_api, "fetch_company_financials",
                            lambda t: {**make_financial_data(), "beta": None})
        response = client.get("/api/monte-carlo/AAPL")
        assert response.status_code == 422 and response.json()["detail"]["code"] == "input_incomplete"
        assert "beta" in response.json()["detail"]["message"]

    def test_in_progress_work_is_a_retryable_409(self, client, store):
        store.set(snapshot_lock_key("AAPL"), "someone-else", 100)
        service = api_main.app.dependency_overrides[monte_carlo_api.get_service]()
        service._snapshot_wait = 0.01
        response = client.get("/api/monte-carlo/AAPL")
        assert response.status_code == 409 and response.headers["retry-after"] == "3"
        assert response.json()["detail"]["code"] == "snapshot_in_progress"

    def test_unavailable_cache_is_a_503(self, fetch_counter, monkeypatch):
        from src.dcf_model.monte_carlo_store import StoreUnavailable

        def unavailable():
            raise StoreUnavailable("redis down")

        service = MonteCarloService(monte_carlo_api._provide_snapshot_inputs, store_factory=unavailable,
                                    allowlist=("AAPL",))
        api_main.app.dependency_overrides[monte_carlo_api.get_service] = lambda: service
        monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, TEST_SERVICE_TOKEN)
        try:
            response = TestClient(api_main.app).get(
                "/api/monte-carlo/AAPL", headers={"Authorization": f"Bearer {TEST_SERVICE_TOKEN}"}
            )
        finally:
            api_main.app.dependency_overrides.pop(monte_carlo_api.get_service, None)
        assert response.status_code == 503 and response.json()["detail"]["code"] == "cache_unavailable"
        assert fetch_counter["count"] == 0


class TestSnapshotExport:
    def test_export_returns_a_replayable_bundle_with_the_expected_digest(self, client):
        run = client.get("/api/monte-carlo/AAPL?seed=42").json()
        response = client.get(f"/api/monte-carlo/snapshots/{run['identity']['snapshot_id']}?seed=42")
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        bundle = response.json()
        assert bundle["schema"] == "mc-dcf-replay-bundle-v1"
        assert bundle["replay"]["seed"] == 42 and bundle["replay"]["expected_result_digest"] == run["result_digest"]
        assert bundle["replay"]["config"] == run["config"]

        from src.dcf_model.monte_carlo_snapshot import replay_bundle

        outcome = replay_bundle(json.loads(response.text))
        assert outcome["matches_expected"] is True
        assert bundle["baseline_provenance"] == run["baseline_provenance"]
        assert outcome["provenance_matches_expected"] is True

    def test_unknown_snapshot_is_404_and_is_not_mistaken_for_a_ticker(self, client):
        response = client.get("/api/monte-carlo/snapshots/not-a-snapshot")
        assert response.status_code == 404 and response.json()["detail"]["code"] == "snapshot_not_found"

    def test_invalid_seed_on_export_is_422(self, client):
        assert client.get("/api/monte-carlo/snapshots/abc?seed=-5").status_code == 422


class TestExistingRoutesAreUntouched:
    def test_evaluate_route_still_registered_and_health_checks_unchanged(self, client):
        # OpenAPI is stable across FastAPI versions; `app.routes` exposes included routers lazily.
        paths = set(api_main.app.openapi()["paths"])
        assert "/api/evaluate/{ticker}" in paths and "/healthz" in paths
        assert {"/api/monte-carlo", "/api/monte-carlo/snapshots/{snapshot_id}", "/api/monte-carlo/{ticker}"} <= paths
        assert TestClient(api_main.app).get("/healthz").status_code == 200

    def test_default_service_is_built_lazily_and_wired_to_the_real_provider(self, monkeypatch):
        monkeypatch.setattr(monte_carlo_api, "_service", None)
        service = monte_carlo_api.get_service()
        assert isinstance(service, MonteCarloService) and monte_carlo_api.get_service() is service


class TestProviderDiagnosticsHeader:
    def test_absent_by_default(self, client):
        response = client.get("/api/monte-carlo/AAPL")
        assert response.status_code == 200
        assert "x-mc-provider-diagnostics" not in response.headers

    def test_present_only_on_the_fetching_call_when_enabled(self, client, monkeypatch):
        from src.dcf_model import monte_carlo_diagnostics as diag

        monkeypatch.setenv(diag.FLAG_ENV_VAR, "1")
        monkeypatch.setattr(diag, "_default_session_class", lambda: type("Unused", (), {"request": lambda *a, **k: None}))
        first = client.get("/api/monte-carlo/AAPL")
        assert first.status_code == 200
        assert json.loads(first.headers["x-mc-provider-diagnostics"])["http_requests"] == 0
        second = client.get("/api/monte-carlo/AAPL")  # snapshot cached: no provider call
        assert "x-mc-provider-diagnostics" not in second.headers


class TestPreviewReplayEndpoint:
    def _bundle_text(self, client):
        result = client.get("/api/monte-carlo/AAPL").json()
        return client.get(f"/api/monte-carlo/snapshots/{result['identity']['snapshot_id']}?seed={result['seed']}").text

    def test_hidden_unless_diagnostics_are_enabled(self, client):
        text = self._bundle_text(client)
        assert client.post("/api/monte-carlo/diagnostics/replay", content=text).status_code == 404

    def test_replays_an_exported_bundle_and_requires_the_token(self, client, monkeypatch):
        from src.dcf_model import monte_carlo_diagnostics as diag

        text = self._bundle_text(client)
        monkeypatch.setenv(diag.FLAG_ENV_VAR, "1")
        assert TestClient(api_main.app).post("/api/monte-carlo/diagnostics/replay", content=text).status_code == 401
        response = client.post("/api/monte-carlo/diagnostics/replay", content=text)
        assert response.status_code == 200
        body = response.json()
        assert body["bundle_integrity_ok"] is True and body["matches_expected"] is True
        assert "result" not in body and set(body["recomputed"]["statistics"]) == {"growth", "margin", "wacc", "combined"}

    def test_tampered_or_malformed_bundles_are_refused(self, client, monkeypatch):
        from src.dcf_model import monte_carlo_diagnostics as diag

        text = self._bundle_text(client)
        monkeypatch.setenv(diag.FLAG_ENV_VAR, "1")
        assert client.post("/api/monte-carlo/diagnostics/replay", content="not json").status_code == 422
        bundle = json.loads(text)
        bundle["replay"]["seed"] += 1  # edited after export: the bundle digest must refuse it
        response = client.post("/api/monte-carlo/diagnostics/replay", content=json.dumps(bundle))
        assert response.status_code == 422 and response.json()["detail"]["code"] == "bundle_invalid"
