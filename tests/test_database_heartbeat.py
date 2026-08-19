"""Regression tests for the isolated production database heartbeat."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from src.utils import database_heartbeat


@dataclass
class FakeCursor:
    events: list[object]
    result: tuple[int] = (1,)

    def __enter__(self):
        self.events.append("cursor_enter")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.events.append("cursor_exit")

    def execute(self, query: str) -> None:
        self.events.append(("execute", query))

    def fetchone(self):
        self.events.append("fetchone")
        return self.result


@dataclass
class FakeConnection:
    result: tuple[int] = (1,)
    events: list[object] = field(default_factory=list)

    def set_session(self, *, readonly: bool, autocommit: bool) -> None:
        self.events.append(("set_session", readonly, autocommit))

    def cursor(self) -> FakeCursor:
        self.events.append("cursor")
        return FakeCursor(self.events, result=self.result)

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.events.append("close")


def test_heartbeat_is_read_only_bounded_and_rolled_back(monkeypatch):
    connection = FakeConnection()
    connect_calls = []

    def fake_connect(database_url, **kwargs):
        connect_calls.append((database_url, kwargs))
        return connection

    monkeypatch.setattr(database_heartbeat.psycopg2, "connect", fake_connect)

    database_heartbeat.run_heartbeat("postgresql://synthetic.invalid/test")

    assert connect_calls == [
        (
            "postgresql://synthetic.invalid/test",
            {
                "connect_timeout": database_heartbeat.CONNECT_TIMEOUT_SECONDS,
                "application_name": database_heartbeat.APPLICATION_NAME,
            },
        )
    ]
    assert connection.events == [
        ("set_session", True, False),
        "cursor",
        "cursor_enter",
        (
            "execute",
            "SET LOCAL statement_timeout = "
            f"{database_heartbeat.STATEMENT_TIMEOUT_MILLISECONDS}",
        ),
        ("execute", "SELECT 1"),
        "fetchone",
        "cursor_exit",
        "rollback",
        "close",
    ]


def test_heartbeat_rejects_an_unexpected_result_and_still_cleans_up(monkeypatch):
    connection = FakeConnection(result=(0,))
    monkeypatch.setattr(
        database_heartbeat.psycopg2,
        "connect",
        lambda *_args, **_kwargs: connection,
    )

    with pytest.raises(RuntimeError, match="unexpected result"):
        database_heartbeat.run_heartbeat("postgresql://synthetic.invalid/test")

    assert connection.events[-2:] == ["rollback", "close"]


@pytest.mark.parametrize("database_url", ["", "   ", None])
def test_heartbeat_rejects_missing_database_url_without_connecting(
    monkeypatch, database_url
):
    connect_calls = []
    monkeypatch.setattr(
        database_heartbeat.psycopg2,
        "connect",
        lambda *_args, **_kwargs: connect_calls.append(True),
    )

    with pytest.raises(ValueError, match="non-empty"):
        database_heartbeat.run_heartbeat(database_url)

    assert connect_calls == []


def test_cli_failure_does_not_print_connection_details(monkeypatch, capsys):
    secret_url = "postgresql://private-user:private-password@private-host/db"
    monkeypatch.setenv("DATABASE_URL", secret_url)
    monkeypatch.setattr(
        database_heartbeat,
        "run_heartbeat",
        lambda _database_url: (_ for _ in ()).throw(RuntimeError(secret_url)),
    )

    assert database_heartbeat.main() == 1

    output = capsys.readouterr()
    assert "RuntimeError" in output.err
    assert secret_url not in output.err
    assert "private-password" not in output.err
