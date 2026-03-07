"""Tests for reporter module — PlanProgress, print_goal_report, print_status_table."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from lindy_orchestrator.reporter import PlanProgress, print_goal_report, print_status_table


def _make_console() -> Console:
    """Create a non-interactive console that writes to a string buffer."""
    return Console(file=StringIO(), force_terminal=False)


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
