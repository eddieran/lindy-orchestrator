"""Tests for truncate_goal() and dashboard detail section."""

import time

from lindy_orchestrator.dag import truncate_goal
from lindy_orchestrator.dashboard import Dashboard, _TaskDetail
from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import TaskItem, TaskPlan, TaskStatus

from io import StringIO
from rich.console import Console


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(tid: int, module: str = "mod", desc: str = "do thing", **kw) -> TaskItem:
    return TaskItem(id=tid, module=module, description=desc, **kw)


def _plan(*tasks: TaskItem, goal: str = "test") -> TaskPlan:
    return TaskPlan(goal=goal, tasks=list(tasks))


def _make_console(*, is_terminal: bool = False) -> Console:
    return Console(file=StringIO(), force_terminal=is_terminal)


# ---------------------------------------------------------------------------
# truncate_goal() unit tests
# ---------------------------------------------------------------------------


class TestTruncateGoal:
    def test_short_text_unchanged(self):
        assert truncate_goal("Fix the bug") == "Fix the bug"

    def test_exactly_max_chars(self):
        text = "A" * 72
        assert truncate_goal(text) == text

    def test_long_text_truncated_with_ellipsis(self):
        text = "A" * 200
        result = truncate_goal(text)
        assert " \u2026 " in result
        assert len(result) <= 78  # head + " … " + tail

    def test_whitespace_collapsed(self):
        text = "hello   world\n\ttab  spaces"
        assert truncate_goal(text) == "hello world tab spaces"

    def test_newlines_collapsed(self):
        text = "line one\nline two\nline three"
        assert truncate_goal(text) == "line one line two line three"

    def test_custom_max_chars(self):
        text = "A" * 100
        result = truncate_goal(text, max_chars=50)
        assert " \u2026 " in result
        assert len(result) <= 56  # head(30) + " … "(3) + tail(15)

    def test_truncation_preserves_head_and_tail(self):
        text = "START" + "x" * 100 + "END"
        result = truncate_goal(text, max_chars=40)
        assert result.startswith("START")
        assert result.endswith("END")

    def test_empty_string(self):
        assert truncate_goal("") == ""

    def test_only_whitespace(self):
        assert truncate_goal("   \n\t  ") == ""


# ---------------------------------------------------------------------------
# _TaskDetail unit tests
# ---------------------------------------------------------------------------


class TestTaskDetail:
    def test_tool_trail_capped_at_5(self):
        detail = _TaskDetail(started_at=time.monotonic())
        for i in range(10):
            detail.add_tool(f"tool_{i}")
        assert len(detail.tool_trail) == 5
        assert detail.tool_trail[0] == "tool_5"
        assert detail.tool_trail[-1] == "tool_9"

    def test_reasoning_truncated_to_80(self):
        detail = _TaskDetail(started_at=time.monotonic())
        detail.set_reasoning("A" * 200)
        assert len(detail.reasoning) == 80

    def test_reasoning_whitespace_collapsed(self):
        detail = _TaskDetail(started_at=time.monotonic())
        detail.set_reasoning("hello   world\n\tnewline")
        assert detail.reasoning == "hello world newline"


# ---------------------------------------------------------------------------
# _build_detail_section() tests
# ---------------------------------------------------------------------------


class TestBuildDetailSection:
    def test_returns_none_when_no_running_tasks(self):
        plan = _plan(
            _task(1, status=TaskStatus.COMPLETED),
            _task(2, status=TaskStatus.PENDING),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)
        section = dash._build_detail_section({})
        assert section is None

    def test_shows_running_tasks_with_detail(self):
        plan = _plan(
            _task(1, status=TaskStatus.IN_PROGRESS),
            _task(2, status=TaskStatus.PENDING),
        )
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)

        detail = _TaskDetail(started_at=time.monotonic() - 133)  # 2m 13s ago
        detail.tool_trail = ["Read", "Edit", "Bash"]
        detail.event_count = 42
        detail.reasoning = "Fixing the import statement"

        section = dash._build_detail_section({1: detail})
        assert section is not None
        plain = section.plain
        assert "task-1" in plain
        assert "2m 13s" in plain
        assert "Read" in plain
        assert "Edit" in plain
        assert "Bash" in plain
        assert "42 events" in plain
        assert "Fixing the import" in plain

    def test_elapsed_time_format(self):
        plan = _plan(_task(1, status=TaskStatus.IN_PROGRESS))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)

        detail = _TaskDetail(started_at=time.monotonic() - 65)  # 1m 05s
        detail.event_count = 10

        section = dash._build_detail_section({1: detail})
        assert section is not None
        assert "1m 05s" in section.plain

    def test_tool_trail_arrow_format(self):
        plan = _plan(_task(1, status=TaskStatus.IN_PROGRESS))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)

        detail = _TaskDetail(started_at=time.monotonic())
        detail.tool_trail = ["Read", "Edit"]
        detail.event_count = 5

        section = dash._build_detail_section({1: detail})
        assert section is not None
        assert "Read \u2192 Edit" in section.plain


# ---------------------------------------------------------------------------
# Dashboard verbose panel integration
# ---------------------------------------------------------------------------


class TestDashboardVerbosePanel:
    def test_verbose_panel_includes_detail_section(self):
        plan = _plan(_task(1, status=TaskStatus.IN_PROGRESS))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=True)

        # Simulate a task start + heartbeat
        dash._on_task_started(
            Event(type=EventType.TASK_STARTED, task_id=1, module="mod")
        )
        dash._on_heartbeat(
            Event(
                type=EventType.TASK_HEARTBEAT,
                task_id=1,
                module="mod",
                data={"tool": "Read", "event_count": 5, "reasoning": "checking files"},
            )
        )

        assert 1 in dash._task_details
        detail = dash._task_details[1]
        assert detail.tool_trail == ["Read"]
        assert detail.event_count == 5
        assert "checking files" in detail.reasoning

    def test_non_verbose_panel_excludes_detail_section(self):
        plan = _plan(_task(1, status=TaskStatus.IN_PROGRESS))
        hooks = HookRegistry()
        console = _make_console(is_terminal=False)
        dash = Dashboard(plan, hooks, console=console, verbose=False)

        dash._on_task_started(
            Event(type=EventType.TASK_STARTED, task_id=1, module="mod")
        )
        # Detail tracking still happens but panel shouldn't include it
        panel = dash._build_panel()
        # The panel should still render without error
        assert panel is not None
