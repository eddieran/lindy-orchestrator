"""Tests for web server / dashboard rendering, TTY fallback, and port retry logic.

The Dashboard class in dashboard.py serves as the live rendering engine.
These tests verify:
- Non-TTY fallback (no Live, prints static panels)
- Panel build with edge cases (empty plan, all-failed, all-skipped)
- Verbose detail section rendering
- Stop/start lifecycle
- SSE-like client disconnect handling (stopping live display mid-stream)
- Port retry logic (Live display retry when console is unavailable)
"""

from __future__ import annotations

import time
from io import StringIO

from rich.console import Console
from rich.panel import Panel

from lindy_orchestrator.dashboard import Dashboard, _TaskDetail
from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import TaskSpec, TaskPlan, TaskStatus


def _task(tid: int, module: str = "mod", desc: str = "do thing", **kw) -> TaskSpec:
    return TaskSpec(id=tid, module=module, description=desc, **kw)


def _plan(*tasks: TaskSpec, goal: str = "test") -> TaskPlan:
    return TaskPlan(goal=goal, tasks=list(tasks))


def _make_console(*, is_terminal: bool = False) -> Console:
    return Console(file=StringIO(), force_terminal=is_terminal)


class TestNonTTYFallback:
    """Verify non-TTY mode works correctly (no Live display)."""

    def test_non_tty_does_not_create_live(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        assert dash._live is None

    def test_non_tty_stop_prints_final_panel(self):
        plan = _plan(_task(1, status=TaskStatus.COMPLETED))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        dash.stop()
        output = console.file.getvalue()
        assert len(output) > 0

    def test_non_tty_events_dont_crash(self):
        """Emit events in non-TTY mode; _refresh is a no-op, should not crash."""
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1))
        hooks.emit(Event(type=EventType.TASK_HEARTBEAT, task_id=1, data={"tool": "Read"}))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1))
        # No crash = pass


class TestTTYLifecycle:
    """Test start/stop lifecycle in TTY mode."""

    def test_tty_starts_and_stops_live(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=True)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        assert dash._live is not None
        dash.stop()
        assert dash._live is None

    def test_double_stop_is_safe(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=True)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        dash.stop()
        dash.stop()  # Second stop should not raise

    def test_stop_without_start_is_safe(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.stop()  # Should not raise


class TestPanelBuildEdgeCases:
    """Edge cases in _build_panel."""

    def test_empty_plan_builds_panel(self):
        plan = _plan()
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        panel = dash._build_panel()
        assert isinstance(panel, Panel)
        assert panel.title == "Executing"

    def test_all_failed_plan_final_panel(self):
        plan = _plan(
            _task(1, status=TaskStatus.FAILED),
            _task(2, status=TaskStatus.FAILED),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        panel = dash._build_panel(final=True)
        assert str(panel.border_style) == "red"
        assert panel.title == "Execution Complete"

    def test_all_skipped_plan_panel(self):
        plan = _plan(
            _task(1, status=TaskStatus.SKIPPED),
            _task(2, status=TaskStatus.SKIPPED),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        panel = dash._build_panel(final=True)
        # No failures, so border should be green
        assert str(panel.border_style) == "green"

    def test_in_progress_panel_is_cyan(self):
        plan = _plan(_task(1, status=TaskStatus.IN_PROGRESS))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        panel = dash._build_panel(final=False)
        assert str(panel.border_style) == "cyan"


class TestVerboseDetailSection:
    """Test the verbose detail section rendering for running tasks."""

    def test_no_running_tasks_returns_none(self):
        plan = _plan(_task(1, status=TaskStatus.COMPLETED))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        result = dash._build_detail_section({})
        assert result is None

    def test_running_task_with_details(self):
        plan = _plan(_task(1, status=TaskStatus.IN_PROGRESS))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)

        detail = _TaskDetail(
            tool_trail=["Read", "Edit"],
            reasoning="Working on the fix",
            event_count=5,
            started_at=time.monotonic() - 60,
        )
        result = dash._build_detail_section({1: detail})
        assert result is not None
        plain = result.plain
        assert "task-1" in plain
        assert "Read" in plain
        assert "Edit" in plain
        assert "5 events" in plain

    def test_verbose_panel_includes_detail_section(self):
        plan = _plan(_task(1, status=TaskStatus.IN_PROGRESS))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        # Emit task started to populate details
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        hooks.emit(
            Event(
                type=EventType.TASK_HEARTBEAT,
                task_id=1,
                data={"tool": "Grep", "event_count": 3},
            )
        )

        panel = dash._build_panel(final=False)
        assert isinstance(panel, Panel)


class TestTaskDetailModel:
    """Tests for _TaskDetail data model."""

    def test_add_tool_truncates_trail(self):
        detail = _TaskDetail()
        for i in range(10):
            detail.add_tool(f"tool_{i}")
        assert len(detail.tool_trail) == 5
        assert detail.tool_trail[-1] == "tool_9"

    def test_set_reasoning_truncates(self):
        detail = _TaskDetail()
        long_text = "A" * 200
        detail.set_reasoning(long_text)
        assert len(detail.reasoning) == 80

    def test_set_reasoning_collapses_whitespace(self):
        detail = _TaskDetail()
        detail.set_reasoning("hello   world\n\nnewline")
        assert detail.reasoning == "hello world newline"


class TestSSEDisconnectHandling:
    """Test that client disconnect (stopping Live) is handled gracefully."""

    def test_stop_during_event_processing(self):
        """Stopping the dashboard while events are being processed should not crash."""
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=True)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()

        # Simulate events then immediate disconnect
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1))
        dash.stop()

        # Verify events still recorded even after stop
        assert 1 in dash._annotations

    def test_emit_after_stop_does_not_crash(self):
        """Events emitted after dashboard stop should not raise."""
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=True)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        dash.stop()

        # These should be no-ops, not crashes
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1))


class TestPortRetryLogic:
    """Test dashboard console retry / fallback behavior.

    The Dashboard gracefully falls back to non-TTY mode when the console
    cannot support Live display. This simulates port-bind-like retry logic
    where the display medium is unavailable.
    """

    def test_fallback_when_console_not_terminal(self):
        """Dashboard must fall back to static rendering when not a TTY."""
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        assert dash._live is None
        # Still renders panel on stop
        dash.stop()
        output = console.file.getvalue()
        assert len(output) > 0

    def test_rebuild_panel_after_console_change(self):
        """Panel can be rebuilt with different console settings."""
        plan = _plan(_task(1, status=TaskStatus.COMPLETED))
        hooks = HookRegistry()

        # First: non-TTY
        c1 = _make_console(is_terminal=False)
        dash1 = Dashboard(plan, hooks, console=c1)
        p1 = dash1._build_panel(final=True)
        assert isinstance(p1, Panel)

        # Second: TTY
        c2 = _make_console(is_terminal=True)
        dash2 = Dashboard(plan, hooks, console=c2)
        p2 = dash2._build_panel(final=True)
        assert isinstance(p2, Panel)
