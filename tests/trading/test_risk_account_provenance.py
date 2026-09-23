"""Risk snapshots carry paper-account provenance; old rows remain untouched."""

from src.utils import db


class FakeCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.calls.append((query, params))


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_schema_adds_only_nullable_account_columns_and_index(monkeypatch):
    conn = FakeConnection()
    monkeypatch.setattr(db, "_get_database_url", lambda: "postgresql://example.invalid/test")
    monkeypatch.setattr(db, "_connect", lambda _url: conn)

    db.ensure_schema()

    queries = [query for query, _params in conn.cursor_instance.calls]
    assert db.ALTER_TRADE_LOGS_ACCOUNT_PROVENANCE_SQL in queries
    assert db.CREATE_RISK_ACCOUNT_INDEX_SQL in queries
    assert db.ALTER_TRADE_LOGS_ACCOUNT_PROVENANCE_SQL.count("ADD COLUMN IF NOT EXISTS") == 2
    assert "UPDATE trade_logs" not in db.ALTER_TRADE_LOGS_ACCOUNT_PROVENANCE_SQL
    assert conn.committed and conn.closed


def test_risk_snapshot_inserts_epoch_and_fingerprint(monkeypatch):
    conn = FakeConnection()
    monkeypatch.setattr(db, "_get_database_url", lambda: "postgresql://example.invalid/test")
    monkeypatch.setattr(db, "_connect", lambda _url: conn)

    db.log_trade(
        ticker="PORTFOLIO", action="RISK_SNAPSHOT", quantity=0.0,
        execution_price=0.0, wacc=None, beta=None, conviction_score=None,
        altman_z_score=None, var_95=-0.05, cvar_95=-0.08,
        account_epoch="alpaca-paper-100k-v1", account_fingerprint="0123456789abcdef",
    )

    query, params = conn.cursor_instance.calls[0]
    assert query == db.INSERT_SQL
    assert len(params) == 12
    assert params[-4:] == (-0.05, -0.08, "alpaca-paper-100k-v1", "0123456789abcdef")
    assert conn.committed and conn.closed


def test_ordinary_trade_leaves_account_risk_provenance_null(monkeypatch):
    conn = FakeConnection()
    monkeypatch.setattr(db, "_get_database_url", lambda: "postgresql://example.invalid/test")
    monkeypatch.setattr(db, "_connect", lambda _url: conn)

    db.log_trade(
        ticker="AAPL", action="BUY", quantity=1.0, execution_price=100.0,
        wacc=None, beta=None, conviction_score=None, altman_z_score=None,
    )

    assert conn.cursor_instance.calls[0][1][-4:] == (None, None, None, None)
