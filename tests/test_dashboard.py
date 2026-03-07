"""Tests for the live DAG dashboard."""

from io import StringIO

from rich.console import Console
from rich.panel import Panel

from lindy_orchestrator.dashboard import Dashboard, _count_statuses
from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import TaskItem, TaskPlan, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(tid: int, module: str = "mod", desc: str = "do thing", **kw) -> TaskItem:
    return TaskItem(id=tid, module=module, description=desc, **kw)


def _plan(*tasks: TaskItem, goal: str = "test") -> TaskPlan:
    return TaskPlan(goal=goal, tasks=list(tasks))


def _make_console(*, is_terminal: bool = False) -> Console:
    """Create a Console that captures output, optionally pretending to be a TTY."""
    return Console(file=StringIO(), force_terminal=is_terminal)


# ---------------------------------------------------------------------------
# Summary bar calculations
# ---------------------------------------------------------------------------


class TestCountStatuses:
    def test_all_pending(self):
        plan = _plan(_task(1), _task(2), _task(3))
        counts = _count_statuses(plan)
        assert counts["pending"] == 3
        assert counts["completed"] == 0
        assert counts["failed"] == 0
        assert counts["in_progress"] == 0

    def test_mixed(self):
        plan = _plan(
            _task(1, status=TaskStatus.COMPLETED),
            _task(2, status=TaskStatus.FAILED),
            _task(3, status=TaskStatus.IN_PROGRESS),
            _task(4, status=TaskStatus.PENDING),
            _task(5, status=TaskStatus.SKIPPED),
        )
        counts = _count_statuses(plan)
        assert counts["completed"] == 1
        assert counts["failed"] == 1
        assert counts["in_progress"] == 1
        assert counts["pending"] == 1
        assert counts["skipped"] == 1

    def test_empty_plan(self):
        plan = _plan()
        counts = _count_statuses(plan)
        assert all(v == 0 for v in counts.values())


# ---------------------------------------------------------------------------
# Dashboard rendering
# ---------------------------------------------------------------------------


class TestDashboardRender:
    def test_build_panel_returns_panel(self):
        plan = _plan(_task(1), _task(2, depends_on=[1]))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        panel = dash._build_panel()
        assert isinstance(panel, Panel)

    def test_build_panel_final_shows_complete(self):
        plan = _plan(
            _task(1, status=TaskStatus.COMPLETED),
            _task(2, status=TaskStatus.COMPLETED, depends_on=[1]),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        panel = dash._build_panel(final=True)
        assert panel.title == "Execution Complete"
        assert str(panel.border_style) == "green"

    def test_build_panel_final_with_failures(self):
        plan = _plan(
            _task(1, status=TaskStatus.COMPLETED),
            _task(2, status=TaskStatus.FAILED, depends_on=[1]),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        panel = dash._build_panel(final=True)
        assert str(panel.border_style) == "red"

    def test_summary_includes_counts(self):
        plan = _plan(
            _task(1, status=TaskStatus.COMPLETED),
            _task(2, status=TaskStatus.PENDING),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        summary = dash._build_summary()
        plain = summary.plain
        assert "1" in plain  # completed count
        assert "0:" in plain  # elapsed time


# ---------------------------------------------------------------------------
# Event-driven updates
# ---------------------------------------------------------------------------


class TestEventDrivenUpdates:
    def test_task_started_sets_active_id(self):
        plan = _plan(_task(1), _task(2, depends_on=[1]))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        assert dash._active_task_id == 1

    def test_task_completed_clears_active_id(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        assert dash._active_task_id == 1

        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="mod"))
        assert dash._active_task_id is None

    def test_task_failed_clears_active_id(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        hooks.emit(Event(type=EventType.TASK_FAILED, task_id=1, module="mod"))
        assert dash._active_task_id is None

    def test_heartbeat_update(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        dash.update_heartbeat(42, "Read")
        assert dash._hb_event_count == 42
        assert dash._hb_last_tool == "Read"

        hb_text = dash._build_heartbeat()
        assert "42 events" in hb_text.plain
        assert "Read" in hb_text.plain

    def test_heartbeat_empty_when_no_active_task(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        hb_text = dash._build_heartbeat()
        assert hb_text.plain.strip() == ""

    def test_multiple_events_update_state(self):
        plan = _plan(
            _task(1),
            _task(2, depends_on=[1]),
            _task(3, depends_on=[1]),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()

        # Start and complete task 1
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        assert dash._active_task_id == 1
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="mod"))
        assert dash._active_task_id is None

        # Start task 2
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="mod"))
        assert dash._active_task_id == 2


# ---------------------------------------------------------------------------
# Non-TTY fallback
# ---------------------------------------------------------------------------


class TestNonTTYFallback:
    def test_no_live_on_non_tty(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        assert dash._live is None

    def test_stop_prints_final_panel_on_non_tty(self):
        plan = _plan(_task(1, status=TaskStatus.COMPLETED))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        dash.stop()
        output = console.file.getvalue()
        # Should contain task info from the rendered DAG
        assert "1 mod" in output

    def test_interactive_starts_live(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=True)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        assert dash._live is not None
        dash.stop()
        assert dash._live is None
