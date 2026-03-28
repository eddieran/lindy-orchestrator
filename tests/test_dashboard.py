"""Tests for the live DAG dashboard."""

from io import StringIO

from rich.console import Console
from rich.panel import Panel

from lindy_orchestrator.dashboard import Dashboard, _count_statuses
from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import TaskSpec, TaskPlan, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(tid: int, module: str = "mod", desc: str = "do thing", **kw) -> TaskSpec:
    return TaskSpec(id=tid, module=module, description=desc, **kw)


def _plan(*tasks: TaskSpec, goal: str = "test") -> TaskPlan:
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
        assert "1 completed" in plain
        assert "1 pending" in plain
        assert "0:" in plain  # elapsed time

    def test_summary_shows_skipped_when_present(self):
        plan = _plan(
            _task(1, status=TaskStatus.COMPLETED),
            _task(2, status=TaskStatus.SKIPPED),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        summary = dash._build_summary()
        assert "skipped" in summary.plain


# ---------------------------------------------------------------------------
# Annotation tracking
# ---------------------------------------------------------------------------


class TestAnnotations:
    def test_update_annotation(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.update_annotation(1, "tool: Edit")
        assert dash._annotations[1] == "tool: Edit"

    def test_heartbeat_event_sets_annotation(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(
            Event(
                type=EventType.TASK_HEARTBEAT,
                task_id=1,
                module="mod",
                data={"tool": "Read"},
            )
        )
        assert dash._annotations[1] == "tool: Read"

    def test_task_started_sets_annotation(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        assert "starting" in dash._annotations[1]

    def test_task_completed_sets_annotation(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="mod"))
        assert dash._annotations[1] == "done"

    def test_task_failed_sets_annotation(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(
            Event(
                type=EventType.TASK_FAILED,
                task_id=1,
                module="mod",
                data={"reason": "timeout"},
            )
        )
        assert dash._annotations[1] == "timeout"

    def test_qa_event_sets_annotation(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(
            Event(
                type=EventType.QA_PASSED,
                task_id=1,
                module="mod",
                data={"gate": "structural_check"},
            )
        )
        assert "structural_check" in dash._annotations[1]
        assert "pass" in dash._annotations[1]


# ---------------------------------------------------------------------------
# Event-driven updates
# ---------------------------------------------------------------------------


class TestEventDrivenUpdates:
    def test_multiple_events_update_annotations(self):
        plan = _plan(
            _task(1),
            _task(2, depends_on=[1]),
            _task(3, depends_on=[1]),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        assert "starting" in dash._annotations[1]

        hooks.emit(
            Event(
                type=EventType.TASK_HEARTBEAT,
                task_id=1,
                module="mod",
                data={"tool": "Edit"},
            )
        )
        assert dash._annotations[1] == "tool: Edit"

        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="mod"))
        assert dash._annotations[1] == "done"

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="mod"))
        assert "starting" in dash._annotations[2]
        # Task 1 annotation preserved
        assert dash._annotations[1] == "done"

    def test_retrying_sets_annotation(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        dash.start()

        hooks.emit(
            Event(
                type=EventType.TASK_RETRYING,
                task_id=1,
                module="mod",
                data={"retry": 2},
            )
        )
        assert "retry 2" in dash._annotations[1]


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
        # Should contain task info from the rendered DAG tree
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
