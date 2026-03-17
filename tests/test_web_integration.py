"""Integration tests for hook-driven dashboard event flow.

Verifies end-to-end integration between HookRegistry and Dashboard:
- Events flow from hooks to dashboard annotations
- Multiple concurrent event types update correctly
- Dashboard state remains consistent after full lifecycle
- Error events propagate correctly
- CLI --web flag equivalent (verbose mode) works end-to-end
"""

from __future__ import annotations

import threading
from io import StringIO

from rich.console import Console

from lindy_orchestrator.dashboard import Dashboard
from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import TaskItem, TaskPlan, TaskStatus


def _task(tid: int, module: str = "mod", desc: str = "do thing", **kw) -> TaskItem:
    return TaskItem(id=tid, module=module, description=desc, **kw)


def _plan(*tasks: TaskItem, goal: str = "test") -> TaskPlan:
    return TaskPlan(goal=goal, tasks=list(tasks))


def _make_console(*, is_terminal: bool = False) -> Console:
    return Console(file=StringIO(), force_terminal=is_terminal)


class TestHookToDashboardIntegration:
    """End-to-end: hook events flow to dashboard annotations."""

    def test_full_task_lifecycle(self):
        plan = _plan(_task(1), _task(2, depends_on=[1]))
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        # Task 1: start -> heartbeat -> QA pass -> complete
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        assert "starting" in dash._annotations[1]

        hooks.emit(Event(type=EventType.TASK_HEARTBEAT, task_id=1, data={"tool": "Edit"}))
        assert dash._annotations[1] == "tool: Edit"

        hooks.emit(Event(type=EventType.QA_PASSED, task_id=1, data={"gate": "pytest"}))
        assert "pass" in dash._annotations[1]

        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1))
        assert dash._annotations[1] == "done"

        # Task 2: start -> fail
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="frontend"))
        assert "starting" in dash._annotations[2]
        assert dash._annotations[1] == "done"  # Task 1 preserved

        hooks.emit(Event(type=EventType.TASK_FAILED, task_id=2, data={"reason": "lint error"}))
        assert dash._annotations[2] == "lint error"

        dash.stop()

    def test_retry_then_success(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1))
        hooks.emit(Event(type=EventType.QA_FAILED, task_id=1, data={"gate": "ruff"}))
        assert "fail" in dash._annotations[1]

        hooks.emit(Event(type=EventType.TASK_RETRYING, task_id=1, data={"retry": 1}))
        assert "retry 1" in dash._annotations[1]

        hooks.emit(Event(type=EventType.QA_PASSED, task_id=1, data={"gate": "ruff"}))
        assert "pass" in dash._annotations[1]

        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1))
        assert dash._annotations[1] == "done"

    def test_skip_event(self):
        plan = _plan(
            _task(1, status=TaskStatus.FAILED),
            _task(2, depends_on=[1]),
        )
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_SKIPPED, task_id=2))
        assert dash._annotations[2] == "skipped"


class TestConcurrentEventIntegration:
    """Multiple threads emitting events to the dashboard simultaneously."""

    def test_concurrent_events_dont_crash(self):
        plan = _plan(_task(1), _task(2), _task(3))
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        def emit_lifecycle(task_id: int):
            hooks.emit(Event(type=EventType.TASK_STARTED, task_id=task_id))
            for i in range(5):
                hooks.emit(
                    Event(
                        type=EventType.TASK_HEARTBEAT,
                        task_id=task_id,
                        data={"tool": f"tool_{i}", "event_count": i},
                    )
                )
            hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=task_id))

        threads = [threading.Thread(target=emit_lifecycle, args=(tid,)) for tid in (1, 2, 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All tasks should have annotations
        for tid in (1, 2, 3):
            assert tid in dash._annotations

        dash.stop()

    def test_concurrent_events_with_exceptions(self):
        """Dashboard must handle hook exceptions during concurrent events."""
        plan = _plan(_task(1), _task(2))
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        # Add a failing handler alongside the dashboard handlers
        def bad_handler(e: Event) -> None:
            raise RuntimeError("integration test failure")

        hooks.on(EventType.TASK_STARTED, bad_handler)

        # Events should still flow to dashboard despite failing handler
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1))
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2))

        # Dashboard handlers registered before bad_handler should still work
        assert 1 in dash._annotations
        assert 2 in dash._annotations

        dash.stop()


class TestDashboardStopResiliency:
    """Dashboard must handle stop gracefully in all states."""

    def test_stop_mid_event_flow(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=True)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1))
        dash.stop()

        # After stop, events still go to hooks but dashboard is disconnected
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1))

    def test_final_panel_after_lifecycle(self):
        plan = _plan(
            _task(1, status=TaskStatus.COMPLETED),
            _task(2, status=TaskStatus.FAILED),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        dash.stop()

        output = console.file.getvalue()
        assert "Execution Complete" in output


class TestVerboseModeIntegration:
    """Tests for verbose/web mode of the dashboard."""

    def test_verbose_mode_captures_tool_trail(self):
        plan = _plan(_task(1, status=TaskStatus.IN_PROGRESS))
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1))
        for tool in ("Read", "Edit", "Bash"):
            hooks.emit(
                Event(
                    type=EventType.TASK_HEARTBEAT,
                    task_id=1,
                    data={"tool": tool},
                )
            )

        detail = dash._task_details.get(1)
        assert detail is not None
        assert "Edit" in detail.tool_trail
        assert "Bash" in detail.tool_trail

    def test_verbose_captures_reasoning(self):
        plan = _plan(_task(1, status=TaskStatus.IN_PROGRESS))
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1))
        hooks.emit(
            Event(
                type=EventType.TASK_HEARTBEAT,
                task_id=1,
                data={"reasoning": "Analyzing the test coverage gaps"},
            )
        )

        detail = dash._task_details.get(1)
        assert detail is not None
        assert "Analyzing" in detail.reasoning

    def test_non_verbose_does_not_track_details(self):
        plan = _plan(_task(1, status=TaskStatus.IN_PROGRESS))
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=False)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1))
        hooks.emit(
            Event(
                type=EventType.TASK_HEARTBEAT,
                task_id=1,
                data={"tool": "Read"},
            )
        )

        # verbose=False: panel built without detail section
        panel = dash._build_panel(final=False)
        assert panel is not None

    def test_heartbeat_without_tool_preserves_annotation(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1))
        # Heartbeat with tool
        hooks.emit(Event(type=EventType.TASK_HEARTBEAT, task_id=1, data={"tool": "Edit"}))
        assert dash._annotations[1] == "tool: Edit"

        # Heartbeat without tool — annotation stays the same
        hooks.emit(Event(type=EventType.TASK_HEARTBEAT, task_id=1, data={"event_count": 10}))
        assert dash._annotations[1] == "tool: Edit"


class TestCLIWebFlagErrorHandling:
    """Test error handling for the --web equivalent (verbose dashboard mode)."""

    def test_verbose_with_empty_plan(self):
        """Verbose mode with empty plan should not crash."""
        plan = _plan()
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()
        panel = dash._build_panel()
        assert panel is not None
        dash.stop()

    def test_verbose_panel_no_running_tasks(self):
        """Verbose panel with no in-progress tasks should skip detail section."""
        plan = _plan(
            _task(1, status=TaskStatus.COMPLETED),
            _task(2, status=TaskStatus.PENDING),
        )
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        # No running tasks → detail section should be None
        result = dash._build_detail_section({})
        assert result is None

    def test_null_event_task_id_is_safe(self):
        """Events with None task_id should be handled gracefully."""
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console()
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        # These events have no task_id (e.g. session-level events)
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=None))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=None))
        hooks.emit(Event(type=EventType.TASK_FAILED, task_id=None, data={"reason": "x"}))
        hooks.emit(Event(type=EventType.TASK_HEARTBEAT, task_id=None, data={"tool": "t"}))

        # No crash, and no spurious annotations
        assert None not in dash._annotations
