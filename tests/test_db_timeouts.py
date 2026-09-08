"""Bounded connection behavior for trading/backtest Postgres telemetry."""

from src.utils import db


def test_telemetry_connection_sets_connect_and_statement_timeouts(monkeypatch):
    captured = {}
    sentinel = object()

    def fake_connect(database_url, **kwargs):
        captured["database_url"] = database_url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(db.psycopg2, "connect", fake_connect)

    assert db._connect("postgresql://example.invalid/test") is sentinel
    assert captured == {
        "database_url": "postgresql://example.invalid/test",
        "application_name": "valuation-engine-telemetry",
        "connect_timeout": db.DB_CONNECT_TIMEOUT_SECONDS,
        "options": f"-c statement_timeout={db.DB_STATEMENT_TIMEOUT_MS}",
    }
