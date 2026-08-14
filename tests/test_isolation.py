"""
Sentinel tests proving pytest cannot reach any real external service.

These exist because of a real production incident: three pytest runs of
`tests/trading/test_rebalance.py` inserted 12 synthetic rows into the live
Supabase `trade_logs` table (IDs 5-16, since deleted) — nothing stopped
`_safe_log_trade` -> `log_trade` -> `psycopg2.connect()` from reaching the
real `DATABASE_URL` in the repo root `.env`. Every guard asserted here
(all installed in `tests/conftest.py`) is what now prevents a repeat,
independent of whether any individual test remembers to mock something.

This file must never depend on any other test's fixtures/mocks to pass —
it exercises the guards directly, as a standing proof they're active for
the whole session.
"""

import os
import socket

import psycopg2
import pytest
import yfinance as yf
from alpaca.trading.client import TradingClient
from curl_cffi import requests as curl_cffi_requests

from tests.conftest import _TEST_ISOLATION_SENTINEL


class TestEnvironmentIsPoisoned:
    """Real credentials must never be visible to a running test process."""

    def test_database_url_is_poisoned(self):
        assert _TEST_ISOLATION_SENTINEL in os.environ["DATABASE_URL"]

    def test_upstash_credentials_are_poisoned(self):
        assert _TEST_ISOLATION_SENTINEL in os.environ["UPSTASH_REDIS_REST_URL"]
        assert os.environ["UPSTASH_REDIS_REST_TOKEN"] == _TEST_ISOLATION_SENTINEL

    def test_alpaca_credentials_are_poisoned(self):
        assert os.environ["APCA_API_KEY_ID"] == _TEST_ISOLATION_SENTINEL
        assert os.environ["APCA_API_SECRET_KEY"] == _TEST_ISOLATION_SENTINEL
        assert os.environ["APCA_API_BASE_URL"] == "https://invalid.test"

    def test_load_dotenv_cannot_restore_real_values(self):
        """Calling load_dotenv() directly must not repopulate real .env values."""
        import dotenv

        dotenv.load_dotenv()
        assert _TEST_ISOLATION_SENTINEL in os.environ["DATABASE_URL"]
        assert os.environ["APCA_API_KEY_ID"] == _TEST_ISOLATION_SENTINEL


class TestExternalAccessIsBlocked:
    """Every path to a real external service must fail loudly, not silently."""

    def test_raw_socket_connect_is_blocked(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(RuntimeError, match="network"):
            s.connect(("8.8.8.8", 443))

    def test_raw_socket_connect_ex_is_blocked(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(RuntimeError, match="network"):
            s.connect_ex(("8.8.8.8", 443))

    def test_psycopg2_connect_is_blocked(self):
        with pytest.raises(RuntimeError, match="psycopg2"):
            psycopg2.connect(os.environ["DATABASE_URL"])

    def test_real_alpaca_client_construction_is_blocked(self):
        with pytest.raises(RuntimeError, match="TradingClient"):
            TradingClient(api_key="x", secret_key="y", paper=True)

    def test_redis_client_is_disabled(self):
        from src.utils.cache import _get_redis_client

        assert _get_redis_client() is None

    def test_yfinance_ticker_construction_is_blocked(self):
        """
        `yf.Ticker(...)` construction is the guard point (not `.history()`
        or another downstream call) because it's this repo's only
        construction site (`src.data_ingestion.fetch_financials.
        get_ticker_object`) and every data-fetching method/property on a
        `Ticker` requires one to exist first — blocking construction
        blocks all of them transitively without needing a real request.
        """
        with pytest.raises(RuntimeError, match="yfinance.Ticker"):
            yf.Ticker("AAPL")

    def test_yfinance_download_is_blocked(self):
        with pytest.raises(RuntimeError, match="yfinance.download"):
            yf.download("AAPL")

    def test_curl_cffi_session_request_is_blocked(self):
        """
        Defense in depth below the yfinance API surface: even if something
        reached for a `curl_cffi` session directly, `.get`/`.post`/
        `.request` all raise before any transport code runs — the
        placeholder URL below is never actually dispatched.
        """
        session = curl_cffi_requests.Session()
        with pytest.raises(RuntimeError, match="curl_cffi"):
            session.get("http://127.0.0.1:0/unreachable-placeholder-never-dispatched")
