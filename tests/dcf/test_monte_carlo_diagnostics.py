"""
Provider-request diagnostics: off by default, never in production, aggregate
counts only (no URLs, query strings, headers or cookies), and never able to
break a valuation.
"""

import json
import logging

import pytest

from src.dcf_model import monte_carlo_diagnostics as diag

SECRET_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/AAPL?crumb=SECRET-CRUMB&token=SECRET-TOKEN"


class _Response:
    def __init__(self, status_code=200, body=b"x" * 10, redirect_count=0):
        self.status_code = status_code
        self.content = body
        self.redirect_count = redirect_count


def make_session(responses):
    class FakeSession:
        calls = 0

        def request(self, method, url, *args, **kwargs):
            FakeSession.calls += 1
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    return FakeSession


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(diag.FLAG_ENV_VAR, raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    diag._last.set(None)


def test_disabled_by_default_does_not_patch_or_record():
    session = make_session([_Response()])
    with diag.count_provider_requests("AAPL", session_cls=session):
        session().request("GET", SECRET_URL)
    assert session not in diag._installed
    assert diag.take_last_summary() is None


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_only_the_exact_flag_enables_it(monkeypatch, value):
    monkeypatch.setenv(diag.FLAG_ENV_VAR, value)
    assert diag.enabled() is False


def test_never_enabled_in_production(monkeypatch):
    monkeypatch.setenv(diag.FLAG_ENV_VAR, "1")
    monkeypatch.setenv("VERCEL_ENV", "production")
    assert diag.enabled() is False
    monkeypatch.setenv("VERCEL_ENV", "preview")
    assert diag.enabled() is True


def test_counts_every_http_request_not_logical_fetches(monkeypatch):
    monkeypatch.setenv(diag.FLAG_ENV_VAR, "1")
    session = make_session([
        _Response(200, b"a" * 5),
        _Response(429, b"b" * 7),
        _Response(200, b"c" * 11, redirect_count=2),
        RuntimeError("connection reset"),
    ])
    with diag.count_provider_requests("KO", session_cls=session):
        s = session()
        s.request("GET", "https://fc.yahoo.com/")
        s.request("GET", "https://query1.finance.yahoo.com/a?x=1")
        s.request("GET", "https://query1.finance.yahoo.com/b")
        with pytest.raises(RuntimeError):
            s.request("GET", "https://query2.finance.yahoo.com/c")
    summary = diag.take_last_summary()
    assert summary["ticker"] == "KO"
    assert summary["http_requests"] == 4
    assert summary["errors"] == 1
    assert summary["redirects"] == 2
    assert summary["response_bytes"] == 23
    assert summary["by_host"] == {"fc.yahoo.com": 1, "query1.finance.yahoo.com": 2, "query2.finance.yahoo.com": 1}
    assert summary["by_status_class"] == {"2xx": 2, "4xx": 1, "error": 1}
    assert diag.take_last_summary() is None  # consumed once


def test_requests_outside_the_block_are_not_counted(monkeypatch):
    monkeypatch.setenv(diag.FLAG_ENV_VAR, "1")
    session = make_session([_Response(), _Response()])
    with diag.count_provider_requests("JNJ", session_cls=session):
        session().request("GET", "https://fc.yahoo.com/")
    session().request("GET", "https://fc.yahoo.com/")
    assert diag.take_last_summary()["http_requests"] == 1


def test_log_and_header_carry_no_url_query_or_credentials(monkeypatch, caplog):
    monkeypatch.setenv(diag.FLAG_ENV_VAR, "1")
    session = make_session([_Response()])
    with caplog.at_level(logging.INFO, logger=diag.logger.name):
        with diag.count_provider_requests("AAPL", session_cls=session):
            session().request("GET", SECRET_URL, headers={"Authorization": "Bearer SECRET-BEARER"}, cookies={"A": "SECRET-COOKIE"})
    text = caplog.text + diag.header_value(diag.take_last_summary())
    for forbidden in ("SECRET", "crumb", "token", "Bearer", "cookie", "quoteSummary", "https://"):
        assert forbidden not in text
    assert "query2.finance.yahoo.com" in text


def test_broken_diagnostics_never_break_the_valuation(monkeypatch):
    monkeypatch.setenv(diag.FLAG_ENV_VAR, "1")

    def broken():
        raise ImportError("no curl_cffi")

    monkeypatch.setattr(diag, "_default_session_class", broken)
    ran = []
    with diag.count_provider_requests("AAPL"):
        ran.append(True)
    assert ran == [True]
    assert diag.take_last_summary() is None


def test_header_value_is_compact_json(monkeypatch):
    monkeypatch.setenv(diag.FLAG_ENV_VAR, "1")
    session = make_session([_Response()])
    with diag.count_provider_requests("AAPL", session_cls=session):
        session().request("GET", "https://fc.yahoo.com/")
    value = diag.header_value(diag.take_last_summary())
    assert " " not in value and json.loads(value)["http_requests"] == 1


def test_older_yfinance_without_an_http_module_falls_back_to_curl_cffi(monkeypatch):
    import sys

    from curl_cffi import requests as curl_requests

    monkeypatch.setitem(sys.modules, "yfinance._http", None)  # `from yfinance._http import ...` raises ImportError
    assert diag._default_session_class() is curl_requests.Session
