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
BACKFILL_SEC_FUNDAMENTALS_WORKFLOW_PATH = (
    WORKFLOW_PATH.parent / "backfill-sec-fundamentals.yml"
)
WORKFLOWS_DIR = WORKFLOW_PATH.parent
TESTS_WORKFLOW_PATH = WORKFLOWS_DIR / "tests.yml"
REQUIREMENTS_PATH = WORKFLOW_PATH.parents[2] / "requirements.txt"


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
        assert 'cron: "37 14 * * 1-5"' in content
        assert 'cron: "15 17 * * 1-5"' not in content

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

    def test_every_execution_mode_receives_database_url_for_run_health(self):
        block = _job_block(_read_workflow(), "execute_trades")
        assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in block


class TestTradingDependencyLock:
    def test_every_direct_runtime_dependency_is_exactly_pinned(self):
        dependencies = [
            line.strip()
            for line in REQUIREMENTS_PATH.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert dependencies
        assert all("==" in dependency for dependency in dependencies)


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





class TestVerifySecPilotArchiveWorkflow:
    """The archive check is manual, read-only, and prints no price data."""

    def _content(self):
        return (WORKFLOWS_DIR / "verify-sec-pilot-archive.yml").read_text()

    def test_is_manual_read_only_and_gated_on_tests(self):
        content = self._content()
        assert "workflow_dispatch:" in content
        assert "schedule:" not in content
        assert "permissions:\n  contents: read" in content
        assert "secrets." not in _job_block(content, "test")
        assert "needs: test" in _job_block(content, "verify")

    def test_verify_job_uses_only_the_driver_and_database_secret(self):
        verify = _job_block(self._content(), "verify")
        assert '"psycopg2-binary==2.9.12"' in verify
        assert "requirements" not in verify
        assert verify.count("secrets.") == 1
        assert "secrets.DATABASE_URL" in verify
        assert "python -m src.backtesting.verify_private_archive" in verify
        for forbidden in ("--publish", "sec_pilot --output", "replay"):
            assert forbidden not in verify

class TestSecBacktestPilotWorkflow:
    """The pilot is manual, read-only, test-gated, and labelled pipeline validation."""

    def _content(self):
        return (WORKFLOWS_DIR / "sec-backtest-pilot.yml").read_text()

    def test_is_manual_only_with_read_only_permissions(self):
        content = self._content()
        assert "workflow_dispatch:" in content
        assert "schedule:" not in content
        assert "permissions:\n  contents: read" in content

    def test_run_is_gated_on_tests_and_receives_only_the_database_secret(self):
        content = self._content()
        assert "secrets." not in _job_block(content, "test")
        run = _job_block(content, "run")
        assert "needs: test" in run
        assert run.count("secrets.") == 1
        assert "secrets.DATABASE_URL" in run
        for forbidden in ("APCA_", "SEC_USER_AGENT", "--publish", "UPSTASH"):
            assert forbidden not in run

    def test_pins_prices_privately_and_cites_the_provisional_run(self):
        content = self._content()
        run = _job_block(content, "run")
        assert 'default: "35809011675"' in content
        assert '--github-run-id "${GITHUB_RUN_ID}"' in run
        assert "^[0-9]+$" in run
        assert "price_snapshot" in run
        for forbidden in ("upload-artifact", "adjusted_open_close"):
            assert forbidden not in content

    def test_archive_mode_inputs_are_validated_and_logs_carry_only_the_reduced_record(self):
        run = _job_block(self._content(), "run")
        assert "^[0-9a-f]{64}$" in run
        assert "--replay-snapshot" in run
        assert "--replay-of-run" in run
        assert "curves_base_cost" not in self._content()
        assert "Reduced public record only" in run

    def test_runs_the_pilot_and_publishes_the_record_to_the_run(self):
        run = _job_block(self._content(), "run")
        assert "python -m src.backtesting.sec_pilot --output" in run
        assert "BEGIN SEC PILOT RECORD" in run
        assert "GITHUB_STEP_SUMMARY" in run
        assert "pipeline validation" in self._content()

class TestBackfillSecFundamentalsWorkflow:
    """Manual pilot backfills, AAPL included, publish one verified batch without touching the schedule."""

    def _content(self):
        return BACKFILL_SEC_FUNDAMENTALS_WORKFLOW_PATH.read_text()

    def _dispatch_ciks(self):
        cik_input = self._content().split("      cik:\n", 1)[1].split("      verify_batch_id:", 1)[0]
        return re.findall(r'^\s+- "(\d+)"$', cik_input, flags=re.MULTILINE)

    def _publish_guard_ciks(self):
        guard = re.search(r"^\s+([0-9|]+)\) ;;$", _job_block(self._content(), "publish"), flags=re.MULTILINE)
        assert guard, "the publish step must allow-list issuers before publishing"
        return guard.group(1).split("|")

    def test_is_manual_only_and_limited_to_the_four_pilot_issuers(self):
        content = self._content()
        assert "workflow_dispatch:" in content
        assert "schedule:" not in content
        assert "cron:" not in content
        assert sorted(self._dispatch_ciks()) == ["104169", "18230", "320193", "789019"]

    def test_the_dispatch_choices_and_the_publish_guard_allow_exactly_the_same_issuers(self):
        # A CIK offered in the form but refused by the guard, or the reverse,
        # would let the two allow-lists drift apart silently.
        assert sorted(self._publish_guard_ciks()) == sorted(self._dispatch_ciks())

    def test_aapl_uses_the_same_publish_and_verify_path_as_every_other_issuer(self):
        # No issuer-specific branches: AAPL gets the run-scoped batch, the
        # complete-or-refuse publish, and the issuer-bound verify-only check
        # exactly as MSFT, WMT, and CAT do.
        content = self._content()
        for job in ("publish", "verify"):
            block = _job_block(content, job)
            assert "ISSUER_CIK: ${{ inputs.cik }}" in block
            script = block.split("run: |", 1)[1].replace("|".join(self._publish_guard_ciks()) + ") ;;", "")
            for cik in self._dispatch_ciks():
                assert cik not in script
        assert "^backfill-${ISSUER_CIK}-[0-9]+-[0-9]+$" in _job_block(content, "verify")

    def test_every_allowed_issuer_has_a_pinned_policy_but_no_live_approval(self):
        from src.fundamentals.calendar_catalog import SEC_FISCAL_CALENDAR_CATALOG_V1
        from src.fundamentals.concept_map import concept_map_for_issuer
        from src.fundamentals.issuer_manifest import SEC_ISSUER_MANIFEST_V1

        manifest = {policy.cik.lstrip("0"): policy for policy in SEC_ISSUER_MANIFEST_V1}
        for cik in self._dispatch_ciks():
            assert SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(cik).version
            assert concept_map_for_issuer(cik).version
            # Backfill access neither approves live SEC use nor schedules the issuer.
            assert manifest[cik].sec_live_approved is False

    def test_adding_aapl_to_backfills_leaves_the_recurring_schedule_unchanged(self):
        block = _job_block(_read_refresh_sec_fundamentals_workflow(), "publish")
        assert sorted(re.findall(r'^\s+cik: "(\d+)"$', block, flags=re.MULTILINE)) == ["104169", "320193", "789019"]
        assert 'cron: "23 3 * * 2-6"' in _read_refresh_sec_fundamentals_workflow()

    def test_shares_the_recurring_publication_concurrency_group(self):
        content = self._content()
        assert "group: refresh-sec-fundamentals" in content
        assert "cancel-in-progress: false" in content
        assert "permissions:\n  contents: read" in content

    def test_publishes_only_after_isolated_tests_with_a_run_scoped_batch(self):
        content = self._content()
        assert "secrets." not in _job_block(content, "test")
        publish = _job_block(content, "publish")
        assert "needs: test" in publish
        assert 'batch_id="backfill-${ISSUER_CIK}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in publish
        assert "--publish" in publish
        assert "320193|789019|104169|18230) ;;" in publish

    def test_verifies_both_cutoffs_read_only_after_publishing(self):
        verify = _job_block(self._content(), "verify")
        assert "needs: [test, publish]" in verify
        assert "needs.test.result == 'success'" in verify
        assert "python -m src.fundamentals.sec_backfill_verification" in verify
        assert '--cutoff "2024-09-03T16:00:00-04:00"' in verify
        assert '--cutoff "${PUBLISH_CUTOFF}"' in verify
        assert "--publish" not in verify

    def test_verify_only_mode_skips_publication_and_validates_its_inputs(self):
        content = self._content()
        assert "verify_batch_id:" in content
        assert "verify_publish_cutoff:" in content
        assert "if: inputs.verify_batch_id == ''" in _job_block(content, "publish")
        verify = _job_block(content, "verify")
        assert "needs.publish.result == 'skipped' && inputs.verify_batch_id != ''" in verify
        assert "^backfill-${ISSUER_CIK}-[0-9]+-[0-9]+$" in verify
        assert "${{ inputs.verify_batch_id" not in verify.split("run: |", 1)[1]

    def test_jobs_install_only_pinned_publication_dependencies_and_two_secrets(self):
        content = self._content()
        for job in ("publish", "verify"):
            block = _job_block(content, job)
            assert "requests==2.32.5" in block
            assert "psycopg2-binary==2.9.12" in block
            assert "requirements" not in block
            assert block.count("secrets.") == 2
            assert "secrets.DATABASE_URL" in block
            assert "secrets.SEC_USER_AGENT" in block

class TestRebalanceRunDiagnostics:
    """Scheduled runs carry the inputs needed to diagnose missed windows."""

    def test_execution_job_passes_schedule_and_account_epoch_diagnostics(self):
        block = _job_block(_read_workflow(), "execute_trades")
        assert "SCHEDULED_CRON: ${{ github.event.schedule }}" in block
        assert "ALPACA_ACCOUNT_EPOCH: ${{ vars.ALPACA_ACCOUNT_EPOCH }}" in block
        assert "secrets.ALPACA_ACCOUNT_EPOCH" not in block


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
        assert '--cik "${ISSUER_CIK}"' in block
        assert 'batch_id="${ISSUER}-sec-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in block
        assert "--knowledge-cutoff" in block
        assert "--batch-id" in block
        assert "--publish" in block

    def test_publishes_the_ready_pilot_issuers_serially(self):
        block = _job_block(_read_refresh_sec_fundamentals_workflow(), "publish")
        assert "max-parallel: 1" in block
        assert "fail-fast: false" in block
        for issuer, cik in (
            ("apple", "320193"),
            ("msft", "789019"),
            ("wmt", "104169"),
        ):
            assert f'- issuer: {issuer}\n            cik: "{cik}"' in block

    def test_schedule_excludes_cat_until_incremental_refresh_verification_exists(self):
        content = _read_refresh_sec_fundamentals_workflow()
        code_lines = [line for line in content.splitlines() if not line.strip().startswith("#")]
        # CAT (CIK 18230) appears nowhere in the workflow's executable content.
        assert not any("18230" in line or "issuer: cat" in line for line in code_lines)
        # The only CIKs the schedule can publish are the matrix entries, and the
        # publish command takes its CIK solely from the matrix.
        block = _job_block(content, "publish")
        matrix_ciks = re.findall(r'^\s+cik: "(\d+)"$', block, flags=re.MULTILINE)
        assert sorted(matrix_ciks) == ["104169", "320193", "789019"]
        assert "ISSUER_CIK: ${{ matrix.cik }}" in block
        assert '--cik "${ISSUER_CIK}"' in block
        assert block.count("--cik") == 1
        # SEC-history readiness does not approve the schedule or live use.
        from src.fundamentals.issuer_manifest import issuer_policy_for

        assert issuer_policy_for("CAT").sec_live_approved is False


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
