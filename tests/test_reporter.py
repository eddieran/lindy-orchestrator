"""Tests for reporter module — PlanProgress, print_goal_report, print_status_table."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from lindy_orchestrator.models import QAResult, TaskItem, TaskPlan, TaskStatus
from lindy_orchestrator.reporter import (
    PlanProgress,
    generate_execution_summary,
    print_goal_report,
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
