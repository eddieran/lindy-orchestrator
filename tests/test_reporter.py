"""Tests for reporter module — PlanProgress, print_goal_report, print_status_table,
execution summary helpers, and print_log_entries.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from lindy_orchestrator.models import QAResult, TaskItem, TaskPlan, TaskStatus
from lindy_orchestrator.reporter import (
    PlanProgress,
    _format_duration,
    _qa_summary,
    _task_duration,
    generate_execution_summary,
    print_goal_report,
    print_log_entries,
    print_status_table,
    save_summary_report,
)


def _make_console(width: int = 80) -> Console:
    """Create a non-interactive console that writes to a string buffer."""
    return Console(file=StringIO(), force_terminal=False, width=width)


def _get_output(console: Console) -> str:
    console.file.seek(0)
    return console.file.read()


class TestPlanProgress:
    def test_initial_state(self):
        con = _make_console()
        pp = PlanProgress(console=con)
        assert pp.event_count == 0
        assert pp.phase == "Initializing..."
        assert pp.elapsed >= 0

    def test_tick_event_increments(self):
        con = _make_console()
        pp = PlanProgress(console=con)
        pp.tick_event()
        pp.tick_event()
        pp.tick_event()
        assert pp.event_count == 3

    def test_set_phase(self):
        con = _make_console()
        pp = PlanProgress(console=con)
        pp.set_phase("Planning tasks...")
        assert pp.phase == "Planning tasks..."

    def test_set_phase_prints_in_non_interactive(self):
        con = _make_console()
        pp = PlanProgress(console=con)
        pp.set_phase("Analyzing")
        output = _get_output(con)
        assert "Analyzing" in output

    def test_start_non_interactive(self):
        con = _make_console()
        pp = PlanProgress(console=con)
        pp.start()
        output = _get_output(con)
        assert "Initializing" in output

    def test_stop_prints_final_message(self):
        con = _make_console()
        pp = PlanProgress(console=con)
        pp.start()
        pp.stop("Done in 5s")
        output = _get_output(con)
        assert "Done in 5s" in output

    def test_stop_default_message(self):
        con = _make_console()
        pp = PlanProgress(console=con)
        pp.start()
        pp.tick_event()
        pp.stop()
        output = _get_output(con)
        assert "Plan generated" in output
        assert "1 events" in output

    def test_rich_protocol(self):
        con = _make_console()
        pp = PlanProgress(console=con)
        renderable = pp.__rich__()
        # Should return a Text object
        assert renderable is not None


class TestPrintGoalReport:
    def test_outputs_report_text(self):
        con = _make_console()
        print_goal_report("All tasks done!", dispatches=3, duration=12.5, console=con)
        output = _get_output(con)
        assert "All tasks done!" in output

    def test_execution_summary_table(self):
        con = _make_console()
        print_goal_report("Report", dispatches=5, duration=100.0, console=con)
        output = _get_output(con)
        assert "5" in output  # dispatches
        assert "100.0s" in output  # duration
        assert "$10.00" in output  # 5 * $2.00

    def test_zero_dispatches(self):
        con = _make_console()
        print_goal_report("Dry run", dispatches=0, duration=0.0, console=con)
        output = _get_output(con)
        assert "$0.00" in output

    def test_default_console(self):
        # Should not raise even without explicit console
        print_goal_report("test", dispatches=1, duration=1.0)


class TestPrintStatusTable:
    def test_with_modules(self):
        con = _make_console()
        modules = [
            {
                "name": "backend",
                "health": "GREEN",
                "last_updated": "2026-01-01",
                "active_count": 2,
                "open_requests": 1,
                "blocker_count": 0,
            },
            {
                "name": "frontend",
                "health": "YELLOW",
                "last_updated": "2026-01-02",
                "active_count": 1,
                "open_requests": 0,
                "blocker_count": 1,
            },
        ]
        print_status_table(modules, console=con)
        output = _get_output(con)
        assert "backend" in output
        assert "frontend" in output
        assert "GREEN" in output
        assert "YELLOW" in output

    def test_empty_modules(self):
        con = _make_console()
        print_status_table([], console=con)
        output = _get_output(con)
        assert "Module Status Overview" in output

    def test_missing_fields_fallback(self):
        con = _make_console()
        modules = [{"name": "minimal"}]
        print_status_table(modules, console=con)
        output = _get_output(con)
        assert "minimal" in output
        assert "?" in output  # fallback for missing health

    def test_red_health_module(self):
        con = _make_console()
        modules = [{"name": "broken", "health": "RED"}]
        print_status_table(modules, console=con)
        output = _get_output(con)
        assert "RED" in output

    def test_unknown_health_style(self):
        con = _make_console()
        modules = [{"name": "weird", "health": "BLUE"}]
        print_status_table(modules, console=con)
        output = _get_output(con)
        assert "BLUE" in output


def _make_plan(with_failures: bool = False) -> TaskPlan:
    """Create a sample TaskPlan for testing."""
    tasks = [
        TaskItem(
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
        TaskItem(
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
        TaskItem(
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
            TaskItem(
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
            TaskItem(
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
# Helper functions: _task_duration
# ---------------------------------------------------------------------------


class TestTaskDuration:
    def test_valid_timestamps(self):
        task = TaskItem(
            id=1,
            module="x",
            description="t",
            started_at="2026-03-07T10:00:00+00:00",
            completed_at="2026-03-07T10:02:30+00:00",
        )
        assert _task_duration(task) == 150.0

    def test_no_started_at(self):
        task = TaskItem(id=1, module="x", description="t", completed_at="2026-03-07T10:02:30+00:00")
        assert _task_duration(task) is None

    def test_no_completed_at(self):
        task = TaskItem(id=1, module="x", description="t", started_at="2026-03-07T10:00:00+00:00")
        assert _task_duration(task) is None

    def test_both_none(self):
        task = TaskItem(id=1, module="x", description="t")
        assert _task_duration(task) is None

    def test_invalid_timestamp_format(self):
        task = TaskItem(
            id=1, module="x", description="t", started_at="not-a-date", completed_at="also-not"
        )
        assert _task_duration(task) is None

    def test_zero_duration(self):
        task = TaskItem(
            id=1,
            module="x",
            description="t",
            started_at="2026-03-07T10:00:00",
            completed_at="2026-03-07T10:00:00",
        )
        assert _task_duration(task) == 0.0

    def test_naive_timestamps(self):
        """Timestamps without timezone info still work."""
        task = TaskItem(
            id=1,
            module="x",
            description="t",
            started_at="2026-03-07T10:00:00",
            completed_at="2026-03-07T10:01:00",
        )
        assert _task_duration(task) == 60.0


# ---------------------------------------------------------------------------
# Helper functions: _format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_none(self):
        assert _format_duration(None) == "-"

    def test_sub_minute(self):
        assert _format_duration(42.3) == "42.3s"

    def test_exactly_60(self):
        assert _format_duration(60.0) == "1m00s"

    def test_over_minute(self):
        assert _format_duration(90.0) == "1m30s"

    def test_zero(self):
        assert _format_duration(0.0) == "0.0s"

    def test_large_duration(self):
        assert _format_duration(3661.0) == "61m01s"

    def test_just_under_minute(self):
        assert _format_duration(59.9) == "59.9s"


# ---------------------------------------------------------------------------
# Helper functions: _qa_summary
# ---------------------------------------------------------------------------


class TestQaSummary:
    def test_no_qa_results(self):
        task = TaskItem(id=1, module="x", description="t")
        assert _qa_summary(task) == "-"

    def test_single_pass(self):
        task = TaskItem(
            id=1,
            module="x",
            description="t",
            qa_results=[QAResult(gate="lint", passed=True)],
        )
        assert _qa_summary(task) == "lint:PASS"

    def test_single_fail(self):
        task = TaskItem(
            id=1,
            module="x",
            description="t",
            qa_results=[QAResult(gate="test", passed=False)],
        )
        assert _qa_summary(task) == "test:FAIL"

    def test_multiple_results(self):
        task = TaskItem(
            id=1,
            module="x",
            description="t",
            qa_results=[
                QAResult(gate="lint", passed=True),
                QAResult(gate="test", passed=False),
                QAResult(gate="build", passed=True),
            ],
        )
        result = _qa_summary(task)
        assert result == "lint:PASS, test:FAIL, build:PASS"


# ---------------------------------------------------------------------------
# print_log_entries
# ---------------------------------------------------------------------------


class TestPrintLogEntries:
    def test_empty_lines(self):
        con = _make_console()
        print_log_entries([], console=con)
        output = _get_output(con)
        assert "No log entries" in output

    def test_valid_json_entry(self):
        con = _make_console()
        lines = [
            '{"timestamp":"2026-01-01T00:00:00","action":"session_start","result":"success","details":{"goal":"test"}}'
        ]
        print_log_entries(lines, console=con)
        output = _get_output(con)
        assert "session_start" in output
        assert "success" in output

    def test_multiple_entries(self):
        con = _make_console()
        lines = [
            '{"timestamp":"2026-01-01T00:00:00","action":"start","result":"success","details":{}}',
            '{"timestamp":"2026-01-01T00:01:00","action":"dispatch","result":"error","details":{}}',
        ]
        print_log_entries(lines, console=con)
        output = _get_output(con)
        assert "start" in output
        assert "dispatch" in output
        assert "error" in output

    def test_invalid_json_fallback(self):
        con = _make_console()
        lines = ["this is not json at all"]
        print_log_entries(lines, console=con)
        output = _get_output(con)
        assert "this is not json at all" in output

    def test_mixed_valid_and_invalid(self):
        con = _make_console()
        lines = [
            '{"timestamp":"2026-01-01T00:00:00","action":"test","result":"pass","details":{}}',
            "broken line",
        ]
        print_log_entries(lines, console=con)
        output = _get_output(con)
        assert "test" in output
        assert "broken line" in output

    def test_entry_with_details(self):
        con = _make_console()
        lines = [
            '{"timestamp":"2026-01-01T00:00:00","action":"dispatch","result":"success","details":{"module":"backend","task_id":1}}'
        ]
        print_log_entries(lines, console=con)
        output = _get_output(con)
        assert "module" in output
        assert "backend" in output

    def test_result_colors(self):
        """Different result values use different color codes — we just check they render."""
        con = _make_console()
        lines = [
            '{"timestamp":"T","action":"a","result":"success","details":{}}',
            '{"timestamp":"T","action":"b","result":"error","details":{}}',
            '{"timestamp":"T","action":"c","result":"fail","details":{}}',
            '{"timestamp":"T","action":"d","result":"pass","details":{}}',
            '{"timestamp":"T","action":"e","result":"unknown","details":{}}',
        ]
        print_log_entries(lines, console=con)
        output = _get_output(con)
        # All actions should appear
        for action in ("a", "b", "c", "d", "e"):
            assert action in output

    def test_default_console(self):
        # Should not raise even without explicit console
        print_log_entries([])

    def test_details_truncated_to_3(self):
        """Only first 3 detail keys are shown."""
        con = _make_console()
        lines = [
            '{"timestamp":"T","action":"a","result":"success","details":{"k1":"v1","k2":"v2","k3":"v3","k4":"v4"}}'
        ]
        print_log_entries(lines, console=con)
        output = _get_output(con)
        assert "k1" in output
        assert "k2" in output
        assert "k3" in output
        # k4 should not appear (truncated to first 3)
        assert "k4" not in output


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
            TaskItem(id=1, module="a", description="Skipped task", status=TaskStatus.SKIPPED),
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
            TaskItem(id=1, module="a", description="Running task", status=TaskStatus.IN_PROGRESS),
        ]
        plan = TaskPlan(goal="Running goal", tasks=tasks)
        generate_execution_summary(plan, 5.0, "run", console=con)
        output = _get_output(con)
        assert "RUN" in output

    def test_pending_task(self):
        """Tasks with PENDING status render with PEND label."""
        con = _make_console(width=200)
        tasks = [
            TaskItem(id=1, module="a", description="Pending task", status=TaskStatus.PENDING),
        ]
        plan = TaskPlan(goal="Pending goal", tasks=tasks)
        generate_execution_summary(plan, 5.0, "pend", console=con)
        output = _get_output(con)
        assert "PEND" in output

    def test_long_result_truncated(self):
        """Long task results are truncated — full 200-char result not shown."""
        con = _make_console(width=300)
        tasks = [
            TaskItem(
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
            TaskItem(
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
            TaskItem(
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
            TaskItem(id=1, module="a", description="No timestamps", status=TaskStatus.COMPLETED),
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
            TaskItem(id=1, module="a", description="d1", status=TaskStatus.COMPLETED, result="ok"),
            TaskItem(id=2, module="a", description="d2", status=TaskStatus.FAILED, result="err"),
            TaskItem(id=3, module="a", description="d3", status=TaskStatus.SKIPPED, result="skip"),
            TaskItem(id=4, module="a", description="d4", status=TaskStatus.PENDING),
            TaskItem(id=5, module="a", description="d5", status=TaskStatus.IN_PROGRESS),
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
            TaskItem(
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
            TaskItem(
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
