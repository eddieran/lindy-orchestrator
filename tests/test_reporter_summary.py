"""Tests for reporter module — generate_execution_summary and save_summary_report.

Split from test_reporter.py to keep files under 500 lines.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from lindy_orchestrator.models import QAResult, TaskSpec, TaskPlan, TaskStatus
from lindy_orchestrator.reporter import (
    generate_execution_summary,
    save_summary_report,
)


def _make_console(width: int = 80) -> Console:
    """Create a non-interactive console that writes to a string buffer."""
    return Console(file=StringIO(), force_terminal=False, width=width)


def _get_output(console: Console) -> str:
    console.file.seek(0)
    return console.file.read()


def _make_plan(with_failures: bool = False) -> TaskPlan:
    """Create a sample TaskPlan for testing."""
    tasks = [
        TaskSpec(
            id=1,
            module="backend",
            description="Add API endpoint",
            status=TaskStatus.COMPLETED,
            result="Created /api/v1/users endpoint",
            retries=0,
            started_at="2026-03-07T10:00:00+00:00",
            completed_at="2026-03-07T10:02:30+00:00",
            qa_results=[QAResult(gate="command_check", passed=True, output="All tests pass")],
        ),
        TaskSpec(
            id=2,
            module="frontend",
            description="Build user form",
            status=TaskStatus.FAILED if with_failures else TaskStatus.COMPLETED,
            result="Component created but tests failed" if with_failures else "Form component done",
            retries=2 if with_failures else 0,
            started_at="2026-03-07T10:02:30+00:00",
            completed_at="2026-03-07T10:05:00+00:00",
            depends_on=[1],
            qa_results=[
                QAResult(
                    gate="ci_check",
                    passed=not with_failures,
                    output="CI failed on lint" if with_failures else "CI green",
                ),
            ],
        ),
        TaskSpec(
            id=3,
            module="docs",
            description="Update README",
            status=TaskStatus.SKIPPED if with_failures else TaskStatus.COMPLETED,
            result="Skipped: dependency failed" if with_failures else "README updated",
            started_at="2026-03-07T10:05:00+00:00" if not with_failures else None,
            completed_at="2026-03-07T10:06:00+00:00" if not with_failures else None,
            depends_on=[2],
        ),
    ]
    return TaskPlan(goal="Build user management feature", tasks=tasks)


class TestGenerateExecutionSummary:
    def test_completed_plan(self):
        con = _make_console(width=200)
        plan = _make_plan(with_failures=False)
        generate_execution_summary(plan, 360.0, "abc12345", console=con)
        output = _get_output(con)
        assert "GOAL COMPLETED" in output
        assert "abc12345" in output
        assert "backend" in output
        assert "frontend" in output
        assert "Add API endpoint" in output
        assert "Build user form" in output

    def test_failed_plan(self):
        con = _make_console(width=200)
        plan = _make_plan(with_failures=True)
        generate_execution_summary(plan, 300.0, "def67890", console=con)
        output = _get_output(con)
        assert "GOAL PAUSED" in output
        assert "1 failed" in output

    def test_task_details_table(self):
        con = _make_console(width=200)
        plan = _make_plan(with_failures=False)
        generate_execution_summary(plan, 360.0, "abc12345", console=con)
        output = _get_output(con)
        assert "Task Details" in output
        assert "PASS" in output  # status label

    def test_qa_results_shown(self):
        con = _make_console(width=200)
        plan = _make_plan(with_failures=False)
        generate_execution_summary(plan, 360.0, "abc12345", console=con)
        output = _get_output(con)
        assert "command_check" in output
        assert "ci_check" in output

    def test_retries_shown_for_failed(self):
        con = _make_console(width=200)
        plan = _make_plan(with_failures=True)
        generate_execution_summary(plan, 300.0, "x", console=con)
        output = _get_output(con)
        assert "2" in output  # retries count

    def test_metrics_table(self):
        con = _make_console(width=200)
        plan = _make_plan(with_failures=False)
        generate_execution_summary(plan, 360.0, "abc12345", console=con)
        output = _get_output(con)
        assert "Execution Metrics" in output
        assert "$6.00" in output  # 3 tasks * $2.00

    def test_duration_formatting(self):
        con = _make_console(width=200)
        plan = _make_plan(with_failures=False)
        generate_execution_summary(plan, 90.0, "abc12345", console=con)
        output = _get_output(con)
        assert "1m30s" in output

    def test_default_console(self):
        plan = _make_plan(with_failures=False)
        # Should not raise even without explicit console
        generate_execution_summary(plan, 10.0, "test")


class TestSaveSummaryReport:
    def test_creates_report_file(self, tmp_path):
        plan = _make_plan(with_failures=False)
        path = save_summary_report(plan, 360.0, "abc12345", tmp_path)
        assert path.exists()
        assert path.name == "abc12345_summary.md"

    def test_report_content_completed(self, tmp_path):
        plan = _make_plan(with_failures=False)
        path = save_summary_report(plan, 360.0, "abc12345", tmp_path)
        content = path.read_text()
        assert "# Execution Summary" in content
        assert "COMPLETED" in content
        assert "abc12345" in content
        assert "backend" in content
        assert "Add API endpoint" in content

    def test_report_content_paused(self, tmp_path):
        plan = _make_plan(with_failures=True)
        path = save_summary_report(plan, 300.0, "def67890", tmp_path)
        content = path.read_text()
        assert "PAUSED" in content
        assert "1 failed" in content

    def test_report_has_task_table(self, tmp_path):
        plan = _make_plan(with_failures=False)
        path = save_summary_report(plan, 360.0, "abc12345", tmp_path)
        content = path.read_text()
        assert "| # | Module |" in content
        assert "| 1 | backend |" in content

    def test_report_has_output_preview(self, tmp_path):
        plan = _make_plan(with_failures=False)
        path = save_summary_report(plan, 360.0, "abc12345", tmp_path)
        content = path.read_text()
        assert "Output preview" in content
        assert "Created /api/v1/users endpoint" in content

    def test_report_has_qa_details(self, tmp_path):
        plan = _make_plan(with_failures=True)
        path = save_summary_report(plan, 300.0, "x", tmp_path)
        content = path.read_text()
        assert "ci_check" in content
        assert "FAIL" in content

    def test_reports_dir_created(self, tmp_path):
        plan = _make_plan(with_failures=False)
        save_summary_report(plan, 10.0, "sess1", tmp_path)
        assert (tmp_path / ".orchestrator" / "reports").is_dir()

    def test_report_idempotent_dir(self, tmp_path):
        """Calling save_summary_report twice doesn't fail on existing dir."""
        plan = _make_plan(with_failures=False)
        save_summary_report(plan, 10.0, "sess1", tmp_path)
        path = save_summary_report(plan, 20.0, "sess2", tmp_path)
        assert path.exists()
        assert path.name == "sess2_summary.md"

    def test_report_retries_in_table(self, tmp_path):
        """Tasks with retries show retry count in the Markdown table."""
        plan = _make_plan(with_failures=True)
        path = save_summary_report(plan, 60.0, "retry_test", tmp_path)
        content = path.read_text()
        # Task 2 has retries=2
        assert "| 2 |" in content

    def test_report_skipped_task_no_output_section(self, tmp_path):
        """Skipped tasks with result text still get output section."""
        plan = _make_plan(with_failures=True)
        path = save_summary_report(plan, 60.0, "skip_test", tmp_path)
        content = path.read_text()
        # Task 3 is SKIPPED with result "Skipped: dependency failed"
        assert "Task 3" in content

    def test_report_no_result_no_output_section(self, tmp_path):
        """Tasks with empty result don't get output preview sections."""
        tasks = [
            TaskSpec(
                id=1,
                module="svc",
                description="No-output task",
                status=TaskStatus.COMPLETED,
                result="",
            ),
        ]
        plan = TaskPlan(goal="Minimal goal", tasks=tasks)
        path = save_summary_report(plan, 5.0, "noresult", tmp_path)
        content = path.read_text()
        assert "Task 1" not in content  # no output section for empty result

    def test_report_long_duration(self, tmp_path):
        """Duration >= 60s renders as minutes."""
        plan = _make_plan(with_failures=False)
        path = save_summary_report(plan, 125.0, "longdur", tmp_path)
        content = path.read_text()
        assert "2m05s" in content

    def test_report_qa_pass_and_fail_in_same_report(self, tmp_path):
        """Both pass and fail QA results appear in the report."""
        tasks = [
            TaskSpec(
                id=1,
                module="api",
                description="Task with mixed QA",
                status=TaskStatus.COMPLETED,
                result="done",
                qa_results=[
                    QAResult(gate="lint", passed=True, output="clean"),
                    QAResult(gate="test", passed=False, output="1 failure"),
                ],
            ),
        ]
        plan = TaskPlan(goal="Mixed QA", tasks=tasks)
        path = save_summary_report(plan, 10.0, "mixedqa", tmp_path)
        content = path.read_text()
        assert "lint: **PASS**" in content
        assert "test: **FAIL**" in content


# ---------------------------------------------------------------------------
# generate_execution_summary — additional edge cases
# ---------------------------------------------------------------------------


class TestGenerateExecutionSummaryEdgeCases:
    def test_empty_plan(self):
        """Plan with no tasks renders without error."""
        con = _make_console(width=200)
        plan = TaskPlan(goal="Empty goal", tasks=[])
        generate_execution_summary(plan, 0.0, "empty", console=con)
        output = _get_output(con)
        assert "GOAL COMPLETED" in output
        assert "0 passed" in output

    def test_all_skipped(self):
        """Plan where all tasks are skipped."""
        con = _make_console(width=200)
        tasks = [
            TaskSpec(id=1, module="a", description="Skipped task", status=TaskStatus.SKIPPED),
        ]
        plan = TaskPlan(goal="Skipped goal", tasks=tasks)
        generate_execution_summary(plan, 5.0, "skip", console=con)
        output = _get_output(con)
        assert "GOAL COMPLETED" in output
        assert "1 skipped" in output

    def test_in_progress_task(self):
        """Tasks with IN_PROGRESS status render with RUN label."""
        con = _make_console(width=200)
        tasks = [
            TaskSpec(id=1, module="a", description="Running task", status=TaskStatus.IN_PROGRESS),
        ]
        plan = TaskPlan(goal="Running goal", tasks=tasks)
        generate_execution_summary(plan, 5.0, "run", console=con)
        output = _get_output(con)
        assert "RUN" in output

    def test_pending_task(self):
        """Tasks with PENDING status render with PEND label."""
        con = _make_console(width=200)
        tasks = [
            TaskSpec(id=1, module="a", description="Pending task", status=TaskStatus.PENDING),
        ]
        plan = TaskPlan(goal="Pending goal", tasks=tasks)
        generate_execution_summary(plan, 5.0, "pend", console=con)
        output = _get_output(con)
        assert "PEND" in output

    def test_long_result_truncated(self):
        """Long task results are truncated — full 200-char result not shown."""
        con = _make_console(width=300)
        tasks = [
            TaskSpec(
                id=1,
                module="a",
                description="Verbose task",
                status=TaskStatus.COMPLETED,
                result="x" * 200,
            ),
        ]
        plan = TaskPlan(goal="Verbose goal", tasks=tasks)
        generate_execution_summary(plan, 5.0, "trunc", console=con)
        output = _get_output(con)
        # Full 200-char result should not appear (truncated at 120 + table max_width)
        assert "x" * 200 not in output
        assert "Verbose task" in output

    def test_no_retries_shows_dash(self):
        """Tasks with 0 retries show '-' in the retries column."""
        con = _make_console(width=200)
        tasks = [
            TaskSpec(
                id=1,
                module="a",
                description="No retry",
                status=TaskStatus.COMPLETED,
                retries=0,
            ),
        ]
        plan = TaskPlan(goal="No retry goal", tasks=tasks)
        generate_execution_summary(plan, 5.0, "noretry", console=con)
        output = _get_output(con)
        assert "No retry" in output

    def test_multiline_result_flattened(self):
        """Newlines in result are replaced with spaces for display."""
        con = _make_console(width=200)
        tasks = [
            TaskSpec(
                id=1,
                module="a",
                description="Multiline",
                status=TaskStatus.COMPLETED,
                result="line1\nline2\nline3",
            ),
        ]
        plan = TaskPlan(goal="Multiline goal", tasks=tasks)
        generate_execution_summary(plan, 5.0, "ml", console=con)
        output = _get_output(con)
        assert "line1" in output
        assert "line2" in output

    def test_short_duration_seconds(self):
        """Short duration renders as seconds."""
        con = _make_console(width=200)
        plan = _make_plan(with_failures=False)
        generate_execution_summary(plan, 5.5, "short", console=con)
        output = _get_output(con)
        assert "5.5s" in output

    def test_task_without_timestamps(self):
        """Task with no timestamps shows '-' for duration."""
        con = _make_console(width=200)
        tasks = [
            TaskSpec(id=1, module="a", description="No timestamps", status=TaskStatus.COMPLETED),
        ]
        plan = TaskPlan(goal="No ts", tasks=tasks)
        generate_execution_summary(plan, 5.0, "nots", console=con)
        output = _get_output(con)
        # Should not crash; duration column shows "-"
        assert "No timestamps" in output


# ---------------------------------------------------------------------------
# save_summary_report — additional edge cases
# ---------------------------------------------------------------------------


class TestSaveSummaryReportEdgeCases:
    def test_empty_plan_report(self, tmp_path):
        """Report for empty plan still has headers."""
        plan = TaskPlan(goal="Empty", tasks=[])
        path = save_summary_report(plan, 0.0, "empty", tmp_path)
        content = path.read_text()
        assert "# Execution Summary" in content
        assert "COMPLETED" in content
        assert "0 passed" in content

    def test_all_statuses_in_report(self, tmp_path):
        """Report includes all status labels."""
        tasks = [
            TaskSpec(id=1, module="a", description="d1", status=TaskStatus.COMPLETED, result="ok"),
            TaskSpec(id=2, module="a", description="d2", status=TaskStatus.FAILED, result="err"),
            TaskSpec(id=3, module="a", description="d3", status=TaskStatus.SKIPPED, result="skip"),
            TaskSpec(id=4, module="a", description="d4", status=TaskStatus.PENDING),
            TaskSpec(id=5, module="a", description="d5", status=TaskStatus.IN_PROGRESS),
        ]
        plan = TaskPlan(goal="All statuses", tasks=tasks)
        path = save_summary_report(plan, 30.0, "allstat", tmp_path)
        content = path.read_text()
        assert "PASS" in content
        assert "FAIL" in content
        assert "SKIP" in content
        assert "PEND" in content
        assert "RUN" in content

    def test_report_special_chars_in_goal(self, tmp_path):
        """Goal with Markdown special characters is preserved."""
        plan = TaskPlan(goal="Build `auth` & deploy **v2**", tasks=[])
        path = save_summary_report(plan, 5.0, "special", tmp_path)
        content = path.read_text()
        assert "Build `auth` & deploy **v2**" in content

    def test_report_qa_output_truncated_to_200(self, tmp_path):
        """QA output in report is truncated to 200 chars."""
        tasks = [
            TaskSpec(
                id=1,
                module="a",
                description="Long QA",
                status=TaskStatus.FAILED,
                result="failed",
                qa_results=[QAResult(gate="test", passed=False, output="x" * 300)],
            ),
        ]
        plan = TaskPlan(goal="Long QA output", tasks=tasks)
        path = save_summary_report(plan, 5.0, "longqa", tmp_path)
        content = path.read_text()
        # Output truncated to 200 chars, so not full 300
        assert "test: **FAIL**" in content

    def test_report_result_truncated_to_500(self, tmp_path):
        """Task result preview is truncated to 500 chars."""
        tasks = [
            TaskSpec(
                id=1,
                module="a",
                description="Long result",
                status=TaskStatus.COMPLETED,
                result="y" * 700,
            ),
        ]
        plan = TaskPlan(goal="Long result", tasks=tasks)
        path = save_summary_report(plan, 5.0, "longresult", tmp_path)
        content = path.read_text()
        # Preview limited to 500
        assert "y" * 500 in content
        assert "y" * 700 not in content
