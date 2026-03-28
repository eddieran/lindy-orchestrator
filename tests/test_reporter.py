"""Tests for reporter module — PlanProgress, print_goal_report, print_status_table,
helper functions (_task_duration, _format_duration, _qa_summary), and print_log_entries.

Execution summary tests (generate_execution_summary, save_summary_report) are in
test_reporter_summary.py.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from lindy_orchestrator.models import QAResult, TaskSpec
from lindy_orchestrator.reporter import (
    PlanProgress,
    _format_duration,
    _qa_summary,
    _task_duration,
    print_goal_report,
    print_log_entries,
    print_status_table,
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


# ---------------------------------------------------------------------------
# Helper functions: _task_duration
# ---------------------------------------------------------------------------


class TestTaskDuration:
    def test_valid_timestamps(self):
        task = TaskSpec(
            id=1,
            module="x",
            description="t",
            started_at="2026-03-07T10:00:00+00:00",
            completed_at="2026-03-07T10:02:30+00:00",
        )
        assert _task_duration(task) == 150.0

    def test_no_started_at(self):
        task = TaskSpec(id=1, module="x", description="t", completed_at="2026-03-07T10:02:30+00:00")
        assert _task_duration(task) is None

    def test_no_completed_at(self):
        task = TaskSpec(id=1, module="x", description="t", started_at="2026-03-07T10:00:00+00:00")
        assert _task_duration(task) is None

    def test_both_none(self):
        task = TaskSpec(id=1, module="x", description="t")
        assert _task_duration(task) is None

    def test_invalid_timestamp_format(self):
        task = TaskSpec(
            id=1, module="x", description="t", started_at="not-a-date", completed_at="also-not"
        )
        assert _task_duration(task) is None

    def test_zero_duration(self):
        task = TaskSpec(
            id=1,
            module="x",
            description="t",
            started_at="2026-03-07T10:00:00",
            completed_at="2026-03-07T10:00:00",
        )
        assert _task_duration(task) == 0.0

    def test_naive_timestamps(self):
        """Timestamps without timezone info still work."""
        task = TaskSpec(
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
        task = TaskSpec(id=1, module="x", description="t")
        assert _qa_summary(task) == "-"

    def test_single_pass(self):
        task = TaskSpec(
            id=1,
            module="x",
            description="t",
            qa_results=[QAResult(gate="lint", passed=True)],
        )
        assert _qa_summary(task) == "lint:PASS"

    def test_single_fail(self):
        task = TaskSpec(
            id=1,
            module="x",
            description="t",
            qa_results=[QAResult(gate="test", passed=False)],
        )
        assert _qa_summary(task) == "test:FAIL"

    def test_multiple_results(self):
        task = TaskSpec(
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
