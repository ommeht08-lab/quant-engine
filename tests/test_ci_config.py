"""
Group K: CI workflow configuration contract.

`.github/workflows/rebalance.yml` is the scheduled LIVE-execution
workflow — a failing/reverted-isolation test suite must not be able to
still fire real orders. This is checked via plain text/structural
parsing (no PyYAML dependency added just for this) since the file's
shape is simple and stable: two jobs (`test`, `execute_trades`), a
`needs:` edge between them, and production secrets confined to the
execution job only.
"""

from pathlib import Path

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "rebalance.yml"


def _read_workflow() -> str:
    return WORKFLOW_PATH.read_text()


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
    def test_cron_schedule_is_preserved(self):
        content = _read_workflow()
        assert "cron: '45 19 * * 1-5'" in content

    def test_manual_dispatch_trigger_is_preserved(self):
        content = _read_workflow()
        assert "workflow_dispatch:" in content


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
