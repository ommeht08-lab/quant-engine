"""
Deployment-readiness contract for `src.api.main`: liveness/readiness
endpoints, structured request-ID logging, startup configuration
validation, and the global unhandled-exception backstop — the surface a
hosting platform's own health checks and log tooling depend on, none of
which is exercised by `tests/api/test_main.py` (the valuation contract)
or `tests/api/test_service_auth.py` (the auth contract).

No network: these are in-process deployment-contract tests —
`TestClient` never leaves the process, and no real deployment (Vercel or
otherwise) is built or run here. Separately-gathered local proxy
evidence (dependency footprint, import-time behavior under a genuinely
fresh subprocess) stands in for an actual deployed build, which this
environment cannot perform.
"""

import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.api.sector_median_thresholds import SectorMedianUnavailableCode
from src.api.sector_medians import LiveSectorMedianResult

VALID_TOKEN = "correct-service-token-at-least-32-chars-long"  # noqa: S105 - test fixture, not a real secret

REPO_ROOT = Path(__file__).resolve().parents[2]


@contextlib.contextmanager
def _capture_src_logs(caplog, level=logging.INFO):
    """
    pytest's `caplog` fixture only ever captures via a handler attached
    to the ROOT logger (`_pytest.logging.catching_logs` hardcodes
    `root_logger.addHandler(self.handler)`) — `caplog.at_level(level,
    logger=name)` merely adjusts THAT logger's own effective level, it
    never relocates where the capturing handler lives.

    `_configure_logging` (src/api/main.py) sets `propagate = False` on
    THIS MODULE's own logger (`logging.getLogger(__name__)`, i.e.
    `api_main.logger`) — see its docstring for why: it's what stops this
    module's own records from being emitted twice on a platform (or
    test) that has its own handler on root. Deliberately scoped that
    narrowly (not to the shared `src` logger, which an earlier version
    used and which broke unrelated tests elsewhere in the suite — see
    that docstring's bug #4) so every sibling module's logger
    (`src.trading.*`, `src.backtesting.*`, `src.dcf_model.*`, ...)
    keeps propagating to root completely unaffected. A side effect
    local to THIS module only: a plain `caplog.at_level(...)` (which
    relies on root-level capture) can no longer see `src.api.main`'s own
    records, since propagation from `api_main.logger` to root is exactly
    what got turned off for it specifically.

    This attaches `caplog`'s own handler directly to `api_main.logger`
    for the duration of the `with` block instead — records logged
    through `api_main.logger` (the access-log middleware, the lifespan
    startup check, `evaluate_ticker`'s own warnings/exceptions) are
    still generated on that exact logger object (propagate=False only
    stops propagation FROM it onward), where this handler now also sits
    alongside the module's own `_ValuationJsonHandler`, so
    `caplog.records` is populated correctly without weakening
    `propagate = False` in the application code itself. Restores
    `api_main.logger`'s handler list to exactly what it was before, in
    a `finally`, regardless of how the `with` block exits.
    """
    with caplog.at_level(level, logger=api_main.logger.name):
        api_main.logger.addHandler(caplog.handler)
        try:
            yield
        finally:
            api_main.logger.removeHandler(caplog.handler)


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
def client_with_valid_token(monkeypatch):
    monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, VALID_TOKEN)
    return TestClient(api_main.app)


@pytest.fixture
def client_without_token(monkeypatch):
    monkeypatch.delenv(api_main.VALUATION_API_TOKEN_ENV_VAR, raising=False)
    return TestClient(api_main.app)


class TestLivenessProbe:
    def test_healthz_requires_no_auth_and_returns_ok(self, client_without_token):
        """Liveness must succeed even when the service token is entirely
        unconfigured — a platform restarting an otherwise-healthy-but-
        misconfigured container on a liveness failure would just loop
        the container instead of leaving it up to report 503s."""
        response = client_without_token.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "valuation-engine-api"}

    def test_root_and_healthz_are_equivalent(self, client_with_valid_token):
        assert client_with_valid_token.get("/").json() == client_with_valid_token.get("/healthz").json()


class TestReadinessProbe:
    def test_ready_when_token_configured(self, client_with_valid_token):
        response = client_with_valid_token.get("/readyz")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"]["service_token_configured"] is True

    def test_not_ready_when_token_unconfigured(self, client_without_token):
        response = client_without_token.get("/readyz")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["service_token_configured"] is False

    def test_not_ready_when_token_is_placeholder(self, monkeypatch):
        """Readiness must track the exact same validity checks
        `require_service_token` enforces at request time, not merely
        "is the env var set" — a deployment that copied `.env.example`
        verbatim should show as not_ready, not ready-but-then-401ing."""
        monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, "replace-with-a-long-random-value")
        client = TestClient(api_main.app)

        response = client.get("/readyz")

        assert response.status_code == 503
        assert response.json()["checks"]["service_token_configured"] is False

    def test_readiness_response_never_contains_the_token_value(self, client_with_valid_token):
        response = client_with_valid_token.get("/readyz")
        assert VALID_TOKEN not in response.text

    def test_readyz_requires_no_auth(self, client_with_valid_token):
        """A deployment platform's readiness probe has no bearer token —
        this must never itself 401."""
        response = client_with_valid_token.get("/readyz")
        assert response.status_code != 401


class TestRequestIdPropagation:
    def test_response_carries_a_generated_request_id_header(self, client_with_valid_token):
        response = client_with_valid_token.get("/healthz")

        assert "x-request-id" in {k.lower() for k in response.headers}
        request_id = response.headers["x-request-id"]
        assert len(request_id) > 0

    def test_incoming_request_id_is_echoed_back(self, client_with_valid_token):
        response = client_with_valid_token.get("/healthz", headers={"X-Request-ID": "caller-supplied-id-123"})

        assert response.headers["x-request-id"] == "caller-supplied-id-123"

    def test_overlong_incoming_request_id_is_truncated_not_rejected(self, client_with_valid_token):
        overlong = "x" * 500
        response = client_with_valid_token.get("/healthz", headers={"X-Request-ID": overlong})

        assert response.status_code == 200
        assert len(response.headers["x-request-id"]) <= api_main._MAX_INCOMING_REQUEST_ID_LENGTH

    def test_two_requests_get_different_generated_request_ids(self, client_with_valid_token):
        first = client_with_valid_token.get("/healthz").headers["x-request-id"]
        second = client_with_valid_token.get("/healthz").headers["x-request-id"]

        assert first != second


class TestStructuredAccessLogging:
    def test_access_log_line_is_valid_json_with_expected_fields(self, client_with_valid_token, caplog):
        with _capture_src_logs(caplog):
            response = client_with_valid_token.get("/healthz", headers={"X-Request-ID": "log-test-id"})

        access_log_records = [r for r in caplog.records if "Request handled" in r.getMessage()]
        assert len(access_log_records) == 1
        record = access_log_records[0]

        formatted = api_main._JsonLogFormatter().format(record)
        payload = json.loads(formatted)  # must actually be parseable JSON, not just JSON-shaped text
        assert payload["level"] == "INFO"
        assert payload["request_id"] == "log-test-id"
        assert "path=/healthz" in payload["message"]
        assert f"status_code={response.status_code}" in payload["message"]

    def test_log_output_never_contains_the_configured_token_value(self, monkeypatch, client_with_valid_token, caplog):
        """Regression guard for the exact failure mode the historical
        incidents in this codebase warn about: a structured logging
        change must not become a new way to leak a secret that was
        already proven safe from the response body."""
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

        with _capture_src_logs(caplog):
            # One successful call and one wrong-token call — the wrong-
            # token 401 path is exactly where a naive "log the header"
            # change could leak the presented value.
            client_with_valid_token.get("/api/evaluate/TEST", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
            client_with_valid_token.get("/api/evaluate/TEST", headers={"Authorization": "Bearer wrong-token"})

        for record in caplog.records:
            assert VALID_TOKEN not in record.getMessage()

    def test_route_level_failure_is_logged_and_returns_generic_500(
        self, monkeypatch, client_with_valid_token, caplog
    ):
        """`evaluate_ticker`'s own `except Exception` branch (wrapping
        `run_dcf_valuation`) converts an unexpected failure into a clean
        500 with a generic detail — this should never leak the raw
        exception message to the caller."""

        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic unhandled failure for the deployment test suite")

        monkeypatch.setattr(api_main, "fetch_company_financials", lambda ticker: _synthetic_financial_data())
        monkeypatch.setattr(api_main, "get_risk_free_rate", lambda *a, **k: 0.04)
        monkeypatch.setattr(api_main, "run_dcf_valuation", _boom)

        with _capture_src_logs(caplog):
            response = client_with_valid_token.get(
                "/api/evaluate/TEST", headers={"Authorization": f"Bearer {VALID_TOKEN}"}
            )

        assert response.status_code == 500
        assert "synthetic unhandled failure" not in response.text
        assert any("Unexpected error running DCF valuation" in r.getMessage() for r in caplog.records)

    def test_failure_outside_route_handling_is_caught_by_the_middleware_backstop(
        self, monkeypatch, client_with_valid_token, caplog
    ):
        """A failure `evaluate_ticker`'s own try/except doesn't wrap (here:
        `fetch_company_financials` raising something other than the
        `ValueError` it explicitly catches) must still be caught
        somewhere — `_request_id_and_access_log_middleware`'s own
        backstop — rather than crashing the request with no response at
        all, and must not leak the raw exception text either. The
        response must still carry the SAME request ID this test supplied
        via the incoming header, proving the fallback response is built
        while `_request_id_var` is still set (see that middleware's
        docstring for why a plain `@app.exception_handler(Exception)`
        can't do that)."""

        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic unhandled failure for the deployment test suite")

        monkeypatch.setattr(api_main, "fetch_company_financials", _boom)

        with _capture_src_logs(caplog):
            response = client_with_valid_token.get(
                "/api/evaluate/TEST",
                headers={"Authorization": f"Bearer {VALID_TOKEN}", "X-Request-ID": "backstop-test-id"},
            )

        assert response.status_code == 500
        assert "synthetic unhandled failure" not in response.text
        body = response.json()
        assert body["error"] == "Internal server error."
        assert body["request_id"] == "backstop-test-id"
        assert response.headers["x-request-id"] == "backstop-test-id"
        assert any("Unhandled exception" in r.getMessage() for r in caplog.records)


class TestStartupConfigurationValidation:
    def test_lifespan_logs_pass_when_token_configured(self, monkeypatch, caplog):
        monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, VALID_TOKEN)

        with _capture_src_logs(caplog):
            with TestClient(api_main.app):  # entering the context runs the lifespan startup phase
                pass

        assert any("Startup configuration check passed" in r.getMessage() for r in caplog.records)

    def test_lifespan_logs_warning_when_token_unconfigured(self, monkeypatch, caplog):
        monkeypatch.delenv(api_main.VALUATION_API_TOKEN_ENV_VAR, raising=False)

        with _capture_src_logs(caplog, logging.WARNING):
            with TestClient(api_main.app):
                pass

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Startup configuration check FAILED" in r.getMessage() for r in warning_records)

    def test_configured_service_token_state_matches_readiness_check(self, monkeypatch):
        """Single source of truth: the function the startup log and
        `/readyz` both call must agree with `require_service_token`'s own
        gate (`_configured_service_token_or_none`) by construction, not
        by coincidence."""
        monkeypatch.setenv(api_main.VALUATION_API_TOKEN_ENV_VAR, VALID_TOKEN)
        assert api_main._configured_service_token_state() is (
            api_main._configured_service_token_or_none() is not None
        )


class TestImportsWithoutOptionalHeavyDependencies:
    """
    `src.api.main` must import successfully in an environment where
    `scipy`, `psycopg2`, `upstash_redis`, `alpaca`, and `uvicorn` are
    genuinely NOT INSTALLED — exactly the Vercel Python Function
    deployment target's actual dependency set (`pyproject.toml`
    deliberately excludes all five; see that file's own comment for
    why each one is safe to omit).

    A plain `monkeypatch.setitem(sys.modules, name, None)` inside the
    current pytest process would only block a *bare* `import name`, not
    reliably every `from name.submodule import X` a transitive import
    might use, and would leave those names permanently poisoned in
    `sys.modules` for every test that runs afterward in the same
    process. Instead, each check runs in a genuinely fresh subprocess
    with a `sys.meta_path` finder installed BEFORE any application code
    imports anything, so every import of a blocked name (top-level or
    a submodule) fails exactly the way it would if the package were
    never installed at all -- then the subprocess itself imports
    `src.api.main` and confirms `app` exists.

    No network, and no real .env: the isolation env vars mirror
    tests/conftest.py's own sentinel pattern; the blocked-module list
    additionally prevents any of psycopg2/upstash_redis/alpaca from
    being importable at all in the subprocess, regardless of
    credentials; and `dotenv.load_dotenv` is replaced with a no-op
    INSIDE the subprocess script itself, BEFORE `import src.api.main`
    — that module (and several of its own src.* dependencies) calls
    `load_dotenv()` at import time, which by default searches the
    current working directory upward for a real `.env` file. Without
    neutralizing it first, a subprocess whose cwd is anywhere under
    this repository would load the repository's REAL `.env` — exactly
    the failure mode `test_subprocess_cannot_load_a_real_env_file`
    below exists to catch. `cwd` is additionally set to a fresh
    directory OUTSIDE the repository entirely (belt-and-suspenders:
    `PYTHONPATH` is what makes `import src.api.main` resolve, not
    `cwd`, so moving `cwd` away from the repo costs nothing and removes
    the repository's `.env` from dotenv's upward search path even if
    the patch above were ever somehow bypassed).
    """

    BLOCKED_MODULES = ("scipy", "psycopg2", "upstash_redis", "alpaca", "uvicorn")

    # `import dotenv` + neutralizing `dotenv.load_dotenv` happens BEFORE
    # `import src.api.main` — patching the ATTRIBUTE on the `dotenv`
    # module object (not just binding a local name) means it stays
    # neutralized even for application code that does `from dotenv
    # import load_dotenv` after this point, since that import binds
    # whatever `dotenv.load_dotenv` currently is. Same technique
    # tests/conftest.py uses at the very top of the whole test session.
    _SUBPROCESS_SCRIPT = """
import sys

_BLOCKED = {blocked!r}

class _BlockingFinder:
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in _BLOCKED:
            raise ModuleNotFoundError(f"No module named {{fullname!r}} (blocked for this test)")
        return None

sys.meta_path.insert(0, _BlockingFinder())

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False

import src.api.main as m

assert hasattr(m, "app"), "src.api.main imported but has no `app` attribute"
print("IMPORT_OK")
"""

    @staticmethod
    def _isolated_env(**extra: str) -> dict:
        isolation_sentinel = "TEST-ISOLATION-INVALID-DO-NOT-USE"
        env = dict(os.environ)
        env.update(
            {
                "PYTHONPATH": str(REPO_ROOT),
                "VALUATION_API_TOKEN": "subprocess-import-test-token-not-a-real-secret-000000",
                "DATABASE_URL": f"postgresql://{isolation_sentinel}@invalid.test:5432/invalid",
                "UPSTASH_REDIS_REST_URL": f"https://{isolation_sentinel}.invalid.test",
                "UPSTASH_REDIS_REST_TOKEN": isolation_sentinel,
                "APCA_API_KEY_ID": isolation_sentinel,
                "APCA_API_SECRET_KEY": isolation_sentinel,
                "APCA_API_BASE_URL": "https://invalid.test",
            }
        )
        env.update(extra)
        return env

    def _run_script(self, script: str, cwd: str, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _run_blocked_import(self) -> subprocess.CompletedProcess:
        script = self._SUBPROCESS_SCRIPT.format(blocked=set(self.BLOCKED_MODULES))
        # cwd is a fresh directory OUTSIDE the repository (see the class
        # docstring) — never the repository root, so dotenv's own
        # upward search (even if the patch above were somehow bypassed)
        # could not reach the repository's real .env either way.
        with tempfile.TemporaryDirectory() as tmp_dir:
            return self._run_script(script, cwd=tmp_dir, env=self._isolated_env())

    def test_imports_successfully_with_all_five_packages_blocked(self):
        result = self._run_blocked_import()

        assert result.returncode == 0, (
            f"src.api.main failed to import with "
            f"{', '.join(self.BLOCKED_MODULES)} blocked.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "IMPORT_OK" in result.stdout

    def test_importing_src_api_main_does_not_touch_the_root_logger(self):
        """
        `_configure_logging` (src/api/main.py) only ever touches THIS
        module's own logger — see its docstring's bug #4 — but that
        guarantee is only as good as the rest of the import graph.
        `src/data_ingestion/fetch_financials.py` and `src/utils/cache.py`
        both used to call `logging.basicConfig(level=logging.INFO)` at
        IMPORT time, which configures the ROOT logger as a side effect
        (adds a handler if none exists, sets its level) — invisible from
        `src.api.main`'s own code, but reachable transitively through
        its import graph (`src.api.main` -> `src.api.sector_medians` ->
        `src.data_ingestion.fetch_financials` -> `src.utils.cache`).
        This test captures the ROOT logger's handler list and level in a
        genuinely fresh subprocess, before and after `import
        src.api.main`, and proves neither changed — a regression test
        for the whole import graph, not just `src.api.main` itself.

        Same isolation discipline as every other subprocess test in this
        class: `dotenv.load_dotenv` neutralized before application
        import, `cwd` a fresh directory outside the repository, no real
        `.env` file reachable.
        """
        script = """
import logging

root_logger = logging.getLogger()
handlers_before = list(root_logger.handlers)
level_before = root_logger.level

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False

import src.api.main as m

handlers_after = list(root_logger.handlers)
level_after = root_logger.level

print(f"HANDLERS_BEFORE_COUNT={len(handlers_before)}")
print(f"HANDLERS_AFTER_COUNT={len(handlers_after)}")
print(f"HANDLERS_UNCHANGED={handlers_before == handlers_after}")
print(f"LEVEL_BEFORE={level_before}")
print(f"LEVEL_AFTER={level_after}")
print(f"LEVEL_UNCHANGED={level_before == level_after}")

assert hasattr(m, "app")
print("IMPORT_OK")
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = self._run_script(script, cwd=tmp_dir, env=self._isolated_env())

        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        assert "IMPORT_OK" in result.stdout
        assert "HANDLERS_UNCHANGED=True" in result.stdout, result.stdout
        assert "LEVEL_UNCHANGED=True" in result.stdout, result.stdout

    def test_importing_src_api_main_does_not_add_a_handler_to_a_clean_root_logger(self):
        """The other half of the same guarantee, stated as a direct
        count rather than an equality check: a genuinely fresh process
        (root logger's default, empty state) must still have ZERO
        handlers on root after importing `src.api.main` — proving the
        import graph doesn't add one where none existed, not merely
        that it leaves an already-populated list alone."""
        script = """
import logging

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False

import src.api.main as m

root_logger = logging.getLogger()
print(f"ROOT_HANDLER_COUNT={len(root_logger.handlers)}")

assert hasattr(m, "app")
print("IMPORT_OK")
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = self._run_script(script, cwd=tmp_dir, env=self._isolated_env())

        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        assert "IMPORT_OK" in result.stdout
        assert "ROOT_HANDLER_COUNT=0" in result.stdout, result.stdout

    def test_handler_count_stays_one_across_a_real_module_reload(self):
        """
        Reproduces, and proves fixed, the exact defect an isinstance-
        based idempotence check has across a genuine
        `importlib.reload`: reload re-executes `class
        _ValuationJsonHandler(...)`, creating a NEW class object — an
        `isinstance` check against the freshly-rebound name would not
        recognize a handler constructed by the PREVIOUS class object as
        already present, and would add a second one. Reproduced
        directly against the pre-fix code: `HANDLERS_BEFORE_RELOAD=1`,
        `HANDLERS_AFTER_RELOAD=2`. `_configure_logging` now checks a
        stable instance attribute instead (`_VALUATION_JSON_HANDLER_
        MARKER`), which survives reload correctly.

        Runs in a subprocess (isolated dotenv, isolated credentials, cwd
        outside the repository — same discipline as every other
        subprocess test in this class) specifically so
        `importlib.reload` here cannot mutate the shared pytest process
        or the FastAPI `app` object other tests in this file depend on.

        The post-reload single-emission check counts occurrences of the
        probe message's exact text in the subprocess's raw STDERR
        (where `_ValuationJsonHandler`, a plain `StreamHandler`, writes)
        rather than adding an in-process probe handler — a duplicate
        `_ValuationJsonHandler` writes to that same stream independently
        of any handler this script might add itself, so counting the
        actual stream output is what would actually catch a
        reintroduced duplicate.
        """
        script = """
import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False

import importlib
import logging

import src.api.main as m

module_logger = logging.getLogger("src.api.main")

before = len(module_logger.handlers)
importlib.reload(m)
after = len(module_logger.handlers)

print(f"HANDLERS_BEFORE_RELOAD={before}")
print(f"HANDLERS_AFTER_RELOAD={after}")

logging.getLogger("src.api.main").info("post-reload single-emission probe message")
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = self._run_script(script, cwd=tmp_dir, env=self._isolated_env())

        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

        before_match = re.search(r"HANDLERS_BEFORE_RELOAD=(\d+)", result.stdout)
        after_match = re.search(r"HANDLERS_AFTER_RELOAD=(\d+)", result.stdout)
        assert before_match and after_match, f"stdout: {result.stdout}"

        assert int(before_match.group(1)) == 1
        assert int(after_match.group(1)) == 1
        assert result.stderr.count("post-reload single-emission probe message") == 1

    def test_blocking_confirmation_a_genuinely_required_package_does_fail(self):
        """Negative control: proves the blocking mechanism itself
        actually blocks something -- without this, a bug in the
        `_BlockingFinder` (e.g. matching nothing) would make the test
        above pass for the wrong reason. `fastapi` IS a genuine
        dependency of src.api.main, so blocking it must break the
        import."""
        script = self._SUBPROCESS_SCRIPT.format(blocked={"fastapi"})
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = self._run_script(script, cwd=tmp_dir, env=self._isolated_env())
        assert result.returncode != 0
        assert "IMPORT_OK" not in result.stdout

    def test_load_dotenv_is_disabled_before_application_import(self):
        """Direct assertion that the patch itself took effect, BEFORE
        `import src.api.main` ran — printed by the subprocess itself
        immediately after the patch line, so this can't pass merely
        because `src.api.main`'s own (unrelated) behavior happened to
        look right."""
        script = """
import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False

print("LOAD_DOTENV_IS_NOOP" if dotenv.load_dotenv() is False else "LOAD_DOTENV_STILL_REAL")

import src.api.main as m

assert hasattr(m, "app")
print("IMPORT_OK")
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = self._run_script(script, cwd=tmp_dir, env=self._isolated_env())
        assert result.returncode == 0, result.stderr
        assert "LOAD_DOTENV_IS_NOOP" in result.stdout
        assert "IMPORT_OK" in result.stdout

    def test_subprocess_cannot_load_a_real_env_file(self):
        """Functional proof, not just "the mock was called": places a
        throwaway .env file (never the repository's real one) containing
        a uniquely-named sentinel variable in a fresh directory, points
        the subprocess's cwd at that directory, and confirms the
        sentinel is ABSENT from the subprocess's own environment after
        import -- i.e. dotenv.load_dotenv really was neutralized before
        src.api.main (and therefore its own `load_dotenv()` call) ever
        ran, not merely that a mock was assigned and never exercised.
        Never reads, prints, or copies the repository's actual .env
        file -- this .env is synthetic, written fresh in a temp
        directory, and contains no real credential."""
        sentinel_var = "SUBPROCESS_ENV_LEAK_TEST_SENTINEL_NEGATIVE"
        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / ".env").write_text(f"{sentinel_var}=leaked-value-should-never-appear\n")

            script = self._SUBPROCESS_SCRIPT.format(blocked=set()) + f"""
import os
print("SENTINEL_PRESENT" if os.environ.get({sentinel_var!r}) else "SENTINEL_ABSENT")
"""
            env = self._isolated_env()
            env.pop(sentinel_var, None)
            result = self._run_script(script, cwd=tmp_dir, env=env)

        assert result.returncode == 0, result.stderr
        assert "IMPORT_OK" in result.stdout
        assert "SENTINEL_ABSENT" in result.stdout
        assert "SENTINEL_PRESENT" not in result.stdout

    def test_positive_control_dotenv_would_load_that_env_file_without_the_patch(self):
        """Proves the negative control above is actually exercising
        something real: WITHOUT the `dotenv.load_dotenv` patch, a bare
        `load_dotenv()` call run from a cwd containing the same kind of
        crafted .env DOES pick up the sentinel -- confirming the
        sentinel could genuinely have leaked if the patch were missing
        or broken, so its absence above is meaningful rather than
        vacuous. Same synthetic-only .env discipline as the test above."""
        sentinel_var = "SUBPROCESS_ENV_LEAK_TEST_SENTINEL_POSITIVE"
        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / ".env").write_text(f"{sentinel_var}=leaked-value-should-never-appear\n")

            script = f"""
import os
from dotenv import load_dotenv

load_dotenv()
print("SENTINEL_PRESENT" if os.environ.get({sentinel_var!r}) else "SENTINEL_ABSENT")
"""
            env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO_ROOT)}
            result = self._run_script(script, cwd=tmp_dir, env=env)

        assert result.returncode == 0, result.stderr
        assert "SENTINEL_PRESENT" in result.stdout


class TestLoggingIsSingleOutputAndIdempotent:
    """
    Direct proof for `_configure_logging`'s correctness properties (see
    its docstring): it never touches the ROOT logger's own handlers, a
    single `src.api.main` log call is captured exactly once — never
    twice (once by `_ValuationJsonHandler`, once more by whatever a
    hosting platform has already attached to root) — never accumulating
    a duplicate `_ValuationJsonHandler` if this function runs more than
    once in the same process, and — critically, since an earlier version
    got this wrong (see the docstring's bug #4) — every SIBLING `src.*`
    logger (`src.trading.*`, `src.backtesting.*`, ...) is left completely
    unaffected; see `TestSiblingLoggersUnaffected` below for that half.
    """

    def test_preexisting_root_handlers_are_left_completely_unchanged(self):
        """Simulates "a hosting platform already configured its own
        root-level logging before this module was imported" — attaches
        a handler to root FIRST, then calls `_configure_logging`, then
        confirms root's handler list (same objects, same order, same
        count) and level are byte-for-byte unchanged."""
        root_logger = logging.getLogger()
        sentinel_handler = logging.StreamHandler()
        root_logger.addHandler(sentinel_handler)
        try:
            handlers_before = list(root_logger.handlers)
            level_before = root_logger.level
            api_main._configure_logging()
            assert root_logger.handlers == handlers_before
            assert root_logger.level == level_before
        finally:
            root_logger.removeHandler(sentinel_handler)

    def test_src_records_never_reach_a_handler_attached_to_root(self):
        """The other half of "emitted only once": a handler on ROOT
        (simulating a platform's own root-level logging setup) must
        receive ZERO records logged through `src.api.main`'s own
        logger — proving `api_main.logger.propagate = False` is
        actually in effect at runtime, not merely documented in a
        comment."""
        root_logger = logging.getLogger()
        received = []
        probe = logging.Handler()
        probe.emit = received.append
        root_logger.addHandler(probe)
        try:
            logging.getLogger("src.api.main").info("single-output probe message (root side)")
        finally:
            root_logger.removeHandler(probe)
        assert received == []

    def test_one_log_call_is_captured_exactly_once(self, caplog):
        """The other half again, from the `src`-logger side: one
        `logger.info(...)` call must result in exactly one captured
        record there — not zero (broken plumbing) and not two (a
        duplicate handler, or a stray unguarded second attachment)."""
        with _capture_src_logs(caplog):
            logging.getLogger("src.api.main").info("single-output probe message (src side)")
        matching = [r for r in caplog.records if r.getMessage() == "single-output probe message (src side)"]
        assert len(matching) == 1

    def test_calling_configure_logging_twice_does_not_add_a_duplicate_handler(self):
        """Direct idempotency proof: two calls must still leave exactly
        one valuation JSON handler on `api_main.logger`
        (`"src.api.main"`), not two (which would silently double-print
        every subsequent record — a dev-server hot-reload re-executing
        this module is the realistic trigger for a second call).
        Counted via the same `_VALUATION_JSON_HANDLER_MARKER` attribute
        `_configure_logging` itself checks — not `isinstance`, which
        `test_handler_count_stays_one_across_a_real_module_reload`
        (a separate, subprocess-based test) demonstrates is unreliable
        across a genuine `importlib.reload`."""

        def _count() -> int:
            return sum(
                1
                for h in api_main.logger.handlers
                if getattr(h, api_main._VALUATION_JSON_HANDLER_MARKER, False)
            )

        assert _count() == 1  # already configured once, at module import time
        api_main._configure_logging()
        api_main._configure_logging()
        assert _count() == 1


class TestSiblingLoggersUnaffected:
    """
    Regression tests for the exact bug `_configure_logging`'s docstring
    documents as bug #4: an earlier version scoped `propagate = False`
    to the shared `src` logger, which silenced propagation for every
    module under `src.*` — not just `src.api.main` — breaking
    `caplog`-based tests completely unrelated to the valuation API
    (`tests/trading/test_rebalance.py::TestPostFillCapNotionalTolerance::
    test_sector_total_uses_proven_remaining_weight_not_assumed_restored_weight`
    in particular) the moment anything in the same pytest process
    imported `src.api.main` first. Both tests here restore every piece
    of logger state they touch, in a `finally`, regardless of outcome.
    """

    def test_shared_src_logger_propagate_is_left_untouched(self):
        """`_configure_logging` must never read or write
        `logging.getLogger("src").propagate` (or its level/handlers) at
        all — confirms the fix is scoped to `src.api.main`'s own
        logger, not merely that the shared logger's CURRENT value
        happens to still be `True` (a weaker check that wouldn't catch
        a version that explicitly sets it to `True`, which would be
        just as much an overreach as setting it to `False`)."""
        src_logger = logging.getLogger("src")
        original_propagate = src_logger.propagate
        original_level = src_logger.level
        original_handlers = list(src_logger.handlers)
        try:
            api_main._configure_logging()
            assert src_logger.propagate == original_propagate
            assert src_logger.propagate is True  # Python's own default, never touched
            assert src_logger.level == original_level
            assert src_logger.handlers == original_handlers
        finally:
            src_logger.propagate = original_propagate
            src_logger.setLevel(original_level)
            src_logger.handlers = original_handlers

    def test_sibling_logger_still_reaches_a_root_attached_capture_handler(self):
        """The other half: a SIBLING module's logger
        (`src.trading.alpaca_execution`, chosen because it's exactly
        the module whose own test file broke under the old bug) must
        still propagate all the way to a handler attached to ROOT,
        completely unaffected by `src.api.main` having been imported
        and configured earlier in this same process — this is the
        actual property `tests/trading/test_rebalance.py`'s own
        `caplog`-based assertions depend on."""
        import src.trading.alpaca_execution as trading_module

        # WARNING, not INFO: neither this logger nor any of its
        # ancestors (src.trading, src, root) has its level explicitly
        # set to INFO by anything in this test file, so an INFO record
        # would be filtered by the default WARNING level before ever
        # reaching a handler regardless of propagation — WARNING is
        # also the actual severity `src.trading.alpaca_execution` uses
        # for the exact cap-breach messages
        # `tests/trading/test_rebalance.py`'s own `caplog` assertions
        # depend on, so this exercises the real scenario, not a level
        # this logger wouldn't normally emit at.
        root_logger = logging.getLogger()
        received = []
        probe = logging.Handler()
        probe.emit = received.append
        root_logger.addHandler(probe)
        try:
            trading_module.logger.warning("sibling-logger reachability probe message")
        finally:
            root_logger.removeHandler(probe)

        matching = [
            r for r in received if r.getMessage() == "sibling-logger reachability probe message"
        ]
        assert len(matching) == 1
