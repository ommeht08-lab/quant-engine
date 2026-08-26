"""
Group K: CI workflow configuration contract.

`.github/workflows/rebalance.yml` is the manual-only, dry-run-by-default
LIVE-execution workflow — a failing/reverted-isolation test suite must
not be able to still fire real orders, and real order submission must
require an explicit `workflow_dispatch` "execute" selection (there is no
automatic `schedule` trigger). This is checked via plain text/structural
parsing (no PyYAML dependency added just for this) since the file's
shape is simple and stable: two jobs (`test`, `execute_trades`), a
`needs:` edge between them, and production secrets confined to the
execution job only.
"""

from pathlib import Path

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "rebalance.yml"
HEARTBEAT_WORKFLOW_PATH = WORKFLOW_PATH.parent / "database-heartbeat.yml"
REFRESH_SECTOR_MEDIANS_WORKFLOW_PATH = WORKFLOW_PATH.parent / "refresh-sector-medians.yml"


def _read_workflow() -> str:
    return WORKFLOW_PATH.read_text()


def _read_refresh_sector_medians_workflow() -> str:
    return REFRESH_SECTOR_MEDIANS_WORKFLOW_PATH.read_text()


def _job_block(content: str, job_name: str) -> str:
    """
    Extract a top-level job's block of text (from its `  <job_name>:`
    line up to the next line at the same 2-space indent, or end of
    file) — good enough for this file's simple, stable structure
    without a full YAML parser.
    """
    lines = content.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == f"  {job_name}:":
            start = i
            break
    assert start is not None, f"Job '{job_name}' not found in {WORKFLOW_PATH}"

    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line and not line.startswith("   ") and not line.startswith("\t") and line.strip() != "":
            # A line back at (or above) the job's own 2-space indent
            # that isn't blank marks the start of the next top-level key.
            if len(line) - len(line.lstrip(" ")) <= 2:
                end = i
                break
    return "\n".join(lines[start:end])


class TestScheduleAndTriggerPreserved:
    def test_no_automatic_schedule_trigger(self):
        """Manual-only while security/governance hardening is underway
        (see the workflow file's own top-of-file comment and
        docs/security-threat-model.md) — a `schedule:`/`cron:` trigger
        previously let this workflow submit real paper orders
        automatically, with no human decision point per run."""
        content = _read_workflow()
        assert "schedule:" not in content
        assert "cron:" not in content

    def test_manual_dispatch_trigger_is_preserved(self):
        content = _read_workflow()
        assert "workflow_dispatch:" in content

    def test_manual_dispatch_defaults_to_dry_run(self):
        content = _read_workflow()
        assert "default: dry-run" in content

    def test_manual_dispatch_offers_an_explicit_execute_option(self):
        content = _read_workflow()
        assert "- execute" in content


class TestConcurrencyGuard:
    def test_concurrency_group_prevents_overlapping_runs(self):
        content = _read_workflow()
        assert "concurrency:" in content
        assert "cancel-in-progress: false" in content


class TestExecutionJobGatedOnTests:
    def test_test_job_exists(self):
        block = _job_block(_read_workflow(), "test")
        assert "pytest" in block

    def test_execute_trades_job_needs_the_test_job(self):
        block = _job_block(_read_workflow(), "execute_trades")
        assert "needs: test" in block or "needs:\n      - test" in block

    def test_test_job_installs_dev_dependencies_and_runs_pytest(self):
        block = _job_block(_read_workflow(), "test")
        assert "requirements-dev.txt" in block
        assert "python -m pytest" in block

    def test_test_job_has_no_production_secrets(self):
        block = _job_block(_read_workflow(), "test")
        assert "secrets." not in block

    def test_execute_trades_job_has_production_secrets(self):
        block = _job_block(_read_workflow(), "execute_trades")
        for secret_name in (
            "APCA_API_KEY_ID",
            "APCA_API_SECRET_KEY",
            "APCA_API_BASE_URL",
            "DATABASE_URL",
        ):
            assert f"secrets.{secret_name}" in block

    def test_execute_trades_job_runs_the_live_entry_point(self):
        block = _job_block(_read_workflow(), "execute_trades")
        assert "python -m src.trading.alpaca_execution" in block


class TestGeneralTestsWorkflowUnaffected:
    """The separate, non-scheduled `tests.yml` workflow must still exist and remain independent."""

    def test_tests_workflow_still_exists(self):
        tests_workflow = WORKFLOW_PATH.parent / "tests.yml"
        assert tests_workflow.exists()

    def test_tests_workflow_has_no_production_secrets(self):
        tests_workflow = WORKFLOW_PATH.parent / "tests.yml"
        assert "secrets." not in tests_workflow.read_text()


class TestDatabaseHeartbeatWorkflow:
    """The scheduled database check must remain isolated from trading."""

    def test_heartbeat_runs_three_times_daily_and_can_run_manually(self):
        content = HEARTBEAT_WORKFLOW_PATH.read_text()
        assert 'cron: "17 3,11,19 * * *"' in content
        assert "workflow_dispatch:" in content

    def test_heartbeat_receives_only_the_database_secret(self):
        content = HEARTBEAT_WORKFLOW_PATH.read_text()
        assert "secrets.DATABASE_URL" in content
        assert content.count("secrets.") == 1

    def test_heartbeat_cannot_invoke_trading_or_external_data_clients(self):
        content = HEARTBEAT_WORKFLOW_PATH.read_text()
        for forbidden_text in (
            "alpaca_execution",
            "APCA_API_KEY_ID",
            "APCA_API_SECRET_KEY",
            "UPSTASH_REDIS_REST_URL",
            "UPSTASH_REDIS_REST_TOKEN",
            "yfinance",
        ):
            assert forbidden_text not in content

    def test_heartbeat_runs_the_dedicated_read_only_module(self):
        content = HEARTBEAT_WORKFLOW_PATH.read_text()
        assert "python -m src.utils.database_heartbeat" in content
        assert "permissions:\n  contents: read" in content
        assert "cancel-in-progress: true" in content


class TestRefreshSectorMediansWorkflow:
    """
    The scheduled sector-median refresh workflow: generates and publishes
    a fresh snapshot to Supabase. Must run on a bounded, pinned
    dependency set (never the full `requirements.txt`, which pulls in
    Alpaca/scipy/uvicorn this job never needs), never contact anything
    but `DATABASE_URL`, and must exclude `tests/validation` from its own
    gating test run (that directory's "tests" actually regenerate real
    reconciliation artifacts as a side effect, not pure assertions).
    """

    def test_workflow_file_exists(self):
        assert REFRESH_SECTOR_MEDIANS_WORKFLOW_PATH.is_file()

    def test_schedule_is_weekdays_only_with_a_manual_dispatch_fallback(self):
        content = _read_refresh_sector_medians_workflow()
        assert "cron:" in content
        assert "* * 1-5" in content  # Mon-Fri only
        assert "workflow_dispatch:" in content

    def test_concurrency_guard_prevents_overlapping_runs(self):
        content = _read_refresh_sector_medians_workflow()
        assert "concurrency:" in content
        assert "group: refresh-sector-medians" in content

    def test_refresh_job_needs_the_test_job(self):
        block = _job_block(_read_refresh_sector_medians_workflow(), "refresh")
        assert "needs: test" in block

    def test_test_job_excludes_the_validation_reconciliation_tests(self):
        block = _job_block(_read_refresh_sector_medians_workflow(), "test")
        assert "--ignore=tests/validation" in block

    def test_test_job_has_no_production_secrets(self):
        block = _job_block(_read_refresh_sector_medians_workflow(), "test")
        assert "secrets." not in block

    def test_refresh_job_receives_only_the_database_secret(self):
        block = _job_block(_read_refresh_sector_medians_workflow(), "refresh")
        assert "secrets.DATABASE_URL" in block
        assert block.count("secrets.") == 1

    def test_refresh_job_does_not_install_the_full_requirements_file(self):
        """requirements.txt pulls in alpaca-py/scipy/uvicorn, none of
        which src.api.publish_sector_medians's import graph ever
        reaches — installing them here would be unreviewed bloat for a
        scheduled data-refresh job. Checked against the EXECUTABLE lines
        only (a comment explaining this choice is allowed to mention the
        file/package names by name)."""
        block = _job_block(_read_refresh_sector_medians_workflow(), "refresh")
        executable_lines = "\n".join(
            line for line in block.splitlines() if line.strip() and not line.strip().startswith("#")
        )
        assert "requirements.txt" not in executable_lines
        for forbidden_package in ("alpaca-py", "scipy", "uvicorn"):
            assert forbidden_package not in executable_lines

    def test_refresh_job_installs_pinned_versions_matching_the_vercel_deployment(self):
        """Same exact pins pyproject.toml declares for the Vercel
        deployment target, so the scheduled workflow runs against the
        same dependency versions the live API does."""
        block = _job_block(_read_refresh_sector_medians_workflow(), "refresh")
        for pinned_dependency in (
            "pandas==2.3.3",
            "numpy==2.0.2",
            "requests==2.32.5",
            "yfinance==1.2.0",
            "python-dotenv==1.2.1",
            "psycopg2-binary==2.9.12",
        ):
            assert pinned_dependency in block

    def test_refresh_job_runs_the_publish_entry_point(self):
        block = _job_block(_read_refresh_sector_medians_workflow(), "refresh")
        assert "python -m src.api.publish_sector_medians" in block
