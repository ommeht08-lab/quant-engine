"""
Group K: CI workflow configuration contract.

`.github/workflows/rebalance.yml` runs the paper strategy autonomously on
weekday schedules and remains dry-run-by-default when started manually.
A failing/reverted-isolation test suite must not be able to fire orders,
and an unscheduled real paper-order run must require an explicit
`workflow_dispatch` "execute" selection. This is checked via plain text/structural
parsing (no PyYAML dependency added just for this) since the file's
shape is simple and stable: two jobs (`test`, `execute_trades`), a
`needs:` edge between them, and production secrets confined to the
execution job only.
"""

import re
from pathlib import Path

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "rebalance.yml"
HEARTBEAT_WORKFLOW_PATH = WORKFLOW_PATH.parent / "database-heartbeat.yml"
REFRESH_SECTOR_MEDIANS_WORKFLOW_PATH = WORKFLOW_PATH.parent / "refresh-sector-medians.yml"
REFRESH_SEC_FUNDAMENTALS_WORKFLOW_PATH = (
    WORKFLOW_PATH.parent / "refresh-sec-fundamentals.yml"
)
WORKFLOWS_DIR = WORKFLOW_PATH.parent
TESTS_WORKFLOW_PATH = WORKFLOWS_DIR / "tests.yml"


def _read_workflow() -> str:
    return WORKFLOW_PATH.read_text()


def _read_refresh_sector_medians_workflow() -> str:
    return REFRESH_SECTOR_MEDIANS_WORKFLOW_PATH.read_text()


def _read_refresh_sec_fundamentals_workflow() -> str:
    return REFRESH_SEC_FUNDAMENTALS_WORKFLOW_PATH.read_text()


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
    def test_weekday_schedule_runs_during_us_market_hours_across_dst(self):
        content = _read_workflow()
        assert "schedule:" in content
        assert 'cron: "15 17 * * 1-5"' in content

    def test_manual_dispatch_trigger_is_preserved(self):
        content = _read_workflow()
        assert "workflow_dispatch:" in content

    def test_manual_dispatch_defaults_to_dry_run(self):
        content = _read_workflow()
        assert "default: dry-run" in content

    def test_manual_dispatch_offers_an_explicit_execute_option(self):
        content = _read_workflow()
        assert "- execute" in content

    def test_schedule_executes_while_manual_default_remains_dry_run(self):
        block = _job_block(_read_workflow(), "execute_trades")
        assert 'github.event_name }}" = "schedule"' in block
        assert 'github.event.inputs.execute }}" = "execute"' in block
        assert "python -m src.trading.alpaca_execution --dry-run" in block

    def test_workflow_requires_terminal_completion_receipt(self):
        block = _job_block(_read_workflow(), "execute_trades")
        assert "set -o pipefail" in block
        assert "tee rebalance-report.txt" in block
        assert "grep -q '^ALPACA_PIPELINE_COMPLETED ' rebalance-report.txt" in block


class TestConcurrencyGuard:
    def test_concurrency_group_prevents_overlapping_runs(self):
        content = _read_workflow()
        assert "concurrency:" in content
        assert "cancel-in-progress: false" in content


class TestExecutionJobGatedOnTests:
    def test_workflow_uses_read_only_github_token_permissions(self):
        assert "permissions:\n  contents: read" in _read_workflow()

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
        assert "secrets." not in TESTS_WORKFLOW_PATH.read_text()

    def test_tests_workflow_uses_read_only_github_token_permissions(self):
        assert "permissions:\n  contents: read" in TESTS_WORKFLOW_PATH.read_text()


class TestFundamentalsPostgresIntegration:
    """The only live socket in tests is one explicit synthetic CI database."""

    def test_uses_postgres_16_with_a_fixed_synthetic_database(self):
        content = TESTS_WORKFLOW_PATH.read_text()
        assert "image: postgres:16" in content
        assert "POSTGRES_DB: valuation_engine_test" in content
        assert "POSTGRES_USER: postgres" in content
        assert "POSTGRES_PASSWORD: postgres" in content

    def test_integration_step_enables_only_the_loopback_test_database(self):
        content = TESTS_WORKFLOW_PATH.read_text()
        assert "ALLOW_TEST_POSTGRES: '1'" in content
        assert (
            "FUNDAMENTALS_TEST_DATABASE_URL: "
            "postgresql://postgres:postgres@127.0.0.1:5432/valuation_engine_test"
        ) in content
        assert "python -m pytest -q tests/fundamentals/test_store_postgres.py" in content

    def test_workflow_still_has_no_repository_secret_reference(self):
        assert "secrets." not in TESTS_WORKFLOW_PATH.read_text()


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


class TestRefreshSecFundamentalsWorkflow:
    """The scheduled SEC job remains isolated, bounded, and fail-closed."""

    def test_workflow_file_exists(self):
        assert REFRESH_SEC_FUNDAMENTALS_WORKFLOW_PATH.is_file()

    def test_runs_after_each_weekday_filing_window_and_can_run_manually(self):
        content = _read_refresh_sec_fundamentals_workflow()
        assert 'cron: "23 3 * * 2-6"' in content
        assert "workflow_dispatch:" in content

    def test_prevents_overlapping_publication_runs(self):
        content = _read_refresh_sec_fundamentals_workflow()
        assert "group: refresh-sec-fundamentals" in content
        assert "cancel-in-progress: false" in content

    def test_uses_read_only_github_permissions(self):
        assert "permissions:\n  contents: read" in _read_refresh_sec_fundamentals_workflow()

    def test_publish_job_is_gated_on_isolated_tests(self):
        content = _read_refresh_sec_fundamentals_workflow()
        test_block = _job_block(content, "test")
        publish_block = _job_block(content, "publish")
        assert "secrets." not in test_block
        assert "tests/fundamentals tests/test_ci_config.py" in test_block
        assert "needs: test" in publish_block

    def test_publish_job_receives_only_its_two_required_secrets(self):
        block = _job_block(_read_refresh_sec_fundamentals_workflow(), "publish")
        assert "secrets.DATABASE_URL" in block
        assert "secrets.SEC_USER_AGENT" in block
        assert block.count("secrets.") == 2
        for forbidden in (
            "APCA_API_KEY_ID",
            "APCA_API_SECRET_KEY",
            "UPSTASH_REDIS_REST_URL",
            "UPSTASH_REDIS_REST_TOKEN",
        ):
            assert forbidden not in block

    def test_publish_job_installs_only_pinned_required_dependencies(self):
        block = _job_block(_read_refresh_sec_fundamentals_workflow(), "publish")
        assert "requests==2.32.5" in block
        assert "psycopg2-binary==2.9.12" in block
        for forbidden in (
            "requirements.txt",
            "requirements-dev.txt",
            "yfinance",
            "alpaca-py",
            "pandas",
            "numpy",
            "scipy",
        ):
            assert forbidden not in block

    def test_publish_job_uses_explicit_reproducible_run_coordinates(self):
        block = _job_block(_read_refresh_sec_fundamentals_workflow(), "publish")
        assert "GITHUB_RUN_ID" in block
        assert "GITHUB_RUN_ATTEMPT" in block
        assert "date -u" in block
        assert "python -m src.fundamentals.sec_pipeline_command" in block
        assert "--cik 320193" in block
        assert "--knowledge-cutoff" in block
        assert "--batch-id" in block
        assert "--publish" in block


# GitHub deprecated the Node 20 runtime these action majors still ran
# on; every workflow must use the Node24-runtime major instead. Kept as
# module-level maps (not hardcoded per-test) so there is exactly one
# place to update if a future bump changes the required major again.
DEPRECATED_NODE20_ACTION_VERSIONS = {
    "actions/checkout": "v4",
    "actions/setup-python": "v5",
    "actions/setup-node": "v4",
}
REQUIRED_ACTION_VERSIONS = {
    "actions/checkout": "v7",
    "actions/setup-python": "v7",
    "actions/setup-node": "v7",
}
_USES_LINE_PATTERN = re.compile(r"uses:\s*(actions/[\w-]+)@(v\d+)")


def _all_workflow_files():
    return sorted(WORKFLOWS_DIR.glob("*.yml"))


class TestActionVersionsAreNotDeprecatedNode20Majors:
    """
    `actions/checkout@v4`, `actions/setup-python@v5`, and
    `actions/setup-node@v4` all run on GitHub's deprecated Node 20
    runtime — every workflow must use the Node24-runtime v7 major of
    each instead. Scans every `.github/workflows/*.yml` file's raw text
    (same plain-text-parsing convention as the rest of this file — no
    PyYAML dependency), so a newly added workflow, or a future revert of
    this bump, is caught immediately rather than only the four files
    reviewed when this test was written.
    """

    def test_at_least_the_four_known_workflows_are_scanned(self):
        # Guards against this test silently checking zero files if the
        # workflows directory ever moves or empties.
        names = {p.name for p in _all_workflow_files()}
        assert {
            "tests.yml",
            "rebalance.yml",
            "database-heartbeat.yml",
            "refresh-sector-medians.yml",
            "refresh-sec-fundamentals.yml",
        } <= names

    def test_no_workflow_uses_a_deprecated_node20_action_major(self):
        for workflow_path in _all_workflow_files():
            content = workflow_path.read_text()
            for action, deprecated_version in DEPRECATED_NODE20_ACTION_VERSIONS.items():
                assert f"{action}@{deprecated_version}" not in content, (
                    f"{workflow_path.name} still uses the deprecated {action}@{deprecated_version} "
                    f"(Node 20 runtime) — bump to {action}@{REQUIRED_ACTION_VERSIONS[action]}."
                )

    def test_every_tracked_action_is_pinned_to_its_required_major(self):
        for workflow_path in _all_workflow_files():
            content = workflow_path.read_text()
            for match in _USES_LINE_PATTERN.finditer(content):
                action, version = match.group(1), match.group(2)
                if action not in REQUIRED_ACTION_VERSIONS:
                    continue
                assert version == REQUIRED_ACTION_VERSIONS[action], (
                    f"{workflow_path.name} uses {action}@{version}, expected "
                    f"{action}@{REQUIRED_ACTION_VERSIONS[action]}."
                )
