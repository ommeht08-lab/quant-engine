"""The frontend's static simulation metadata: one source, a stable identity, and drift detection."""

import subprocess
import sys
from pathlib import Path

from src.dcf_model.monte_carlo_config import DEFAULT_TICKER_ALLOWLIST
from src.dcf_model.monte_carlo_metadata import build_description, build_static_metadata, metadata_id
from src.dcf_model.monte_carlo_service import MonteCarloService

ROOT = Path(__file__).resolve().parents[2]


def _no_provider(ticker):
    raise AssertionError("metadata must never fetch provider data")


def test_committed_frontend_metadata_matches_the_code():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_monte_carlo_metadata.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_static_metadata_is_the_services_description_for_the_default_allowlist(monkeypatch):
    monkeypatch.delenv("MONTE_CARLO_TICKERS", raising=False)
    described = MonteCarloService(snapshot_provider=_no_provider).describe()
    static = build_static_metadata()
    assert static.pop("schema") == "mc-dcf-metadata-v1"
    assert described == static
    assert tuple(static["tickers"]) == DEFAULT_TICKER_ALLOWLIST


def test_identity_changes_with_the_allowlist_seed_config_or_wording():
    base = build_description(("AAPL", "KO"))
    assert metadata_id(base) == base["metadata_id"]
    assert build_description(("AAPL", "KO", "JNJ"))["metadata_id"] != base["metadata_id"]
    assert build_description(("KO", "AAPL"))["metadata_id"] != base["metadata_id"]
    assert metadata_id({**base, "default_seed": base["default_seed"] + 1}) != base["metadata_id"]
    assert metadata_id({**base, "config_hash": "0" * 64}) != base["metadata_id"]
    assert metadata_id({**base, "disclosures": [{"id": "x", "text": "changed"}]}) != base["metadata_id"]


def test_env_override_of_the_allowlist_changes_the_services_identity(monkeypatch):
    monkeypatch.setenv("MONTE_CARLO_TICKERS", "AAPL,KO")
    service = MonteCarloService(snapshot_provider=_no_provider)
    assert service.describe()["tickers"] == ["AAPL", "KO"]
    assert service.describe()["metadata_id"] != build_static_metadata()["metadata_id"]
