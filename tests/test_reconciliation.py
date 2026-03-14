"""Tests for TASK_SKIPPED event reconciliation in execute_plan."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import TaskItem, TaskPlan, TaskStatus


def _make_hooks_and_events() -> tuple[HookRegistry, list[Event]]:
    hooks = HookRegistry()
    events: list[Event] = []
    hooks.on_any(lambda e: events.append(e))
    return hooks, events


@patch("lindy_orchestrator.scheduler.create_provider")
def test_skipped_event_emitted_when_dep_fails(mock_provider: MagicMock) -> None:
    """When task 1 fails, task 2 (depends_on=[1]) should emit TASK_SKIPPED."""
    from lindy_orchestrator.config import OrchestratorConfig
    from lindy_orchestrator.logger import ActionLogger
    from lindy_orchestrator.scheduler import execute_plan

    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="root", description="first", status=TaskStatus.FAILED),
            TaskItem(
                id=2, module="root", description="second", depends_on=[1], status=TaskStatus.PENDING
            ),
        ],
    )

    cfg = OrchestratorConfig()
    cfg.safety.dry_run = True
    logger = MagicMock(spec=ActionLogger)
    hooks, events = _make_hooks_and_events()

    execute_plan(plan, cfg, logger, hooks=hooks)

    skipped_events = [e for e in events if e.type == EventType.TASK_SKIPPED]
    assert len(skipped_events) == 1
    assert skipped_events[0].task_id == 2
    assert plan.tasks[1].completed_at is not None


@patch("lindy_orchestrator.scheduler.create_provider")
def test_skipped_event_emitted_only_once(mock_provider: MagicMock) -> None:
    """TASK_SKIPPED should not be emitted again on subsequent next_ready calls."""
    from lindy_orchestrator.config import OrchestratorConfig
    from lindy_orchestrator.logger import ActionLogger
    from lindy_orchestrator.scheduler import execute_plan

    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="root", description="first", status=TaskStatus.FAILED),
            TaskItem(
                id=2, module="root", description="second", depends_on=[1], status=TaskStatus.PENDING
            ),
        ],
    )

    cfg = OrchestratorConfig()
    cfg.safety.dry_run = True
    logger = MagicMock(spec=ActionLogger)
    hooks, events = _make_hooks_and_events()

    execute_plan(plan, cfg, logger, hooks=hooks)

    skipped_events = [e for e in events if e.type == EventType.TASK_SKIPPED]
    assert len(skipped_events) == 1


@patch("lindy_orchestrator.scheduler.create_provider")
def test_cascading_skip_a_b_c(mock_provider: MagicMock) -> None:
    """A→B→C: if A fails, both B and C should be skipped."""
    from lindy_orchestrator.config import OrchestratorConfig
    from lindy_orchestrator.logger import ActionLogger
    from lindy_orchestrator.scheduler import execute_plan

    plan = TaskPlan(
        goal="cascade",
        tasks=[
            TaskItem(id=1, module="root", description="A", status=TaskStatus.FAILED),
            TaskItem(id=2, module="root", description="B", depends_on=[1]),
            TaskItem(id=3, module="root", description="C", depends_on=[2]),
        ],
    )

    cfg = OrchestratorConfig()
    cfg.safety.dry_run = True
    logger = MagicMock(spec=ActionLogger)
    hooks, events = _make_hooks_and_events()

    execute_plan(plan, cfg, logger, hooks=hooks)

    skipped_events = [e for e in events if e.type == EventType.TASK_SKIPPED]
    skipped_ids = {e.task_id for e in skipped_events}
    assert skipped_ids == {2, 3}
    assert plan.tasks[1].status == TaskStatus.SKIPPED
    assert plan.tasks[2].status == TaskStatus.SKIPPED


@patch("lindy_orchestrator.scheduler.create_provider")
def test_independent_task_not_skipped(mock_provider: MagicMock) -> None:
    """A task without dep on the failed task should NOT be skipped."""
    from lindy_orchestrator.config import OrchestratorConfig
    from lindy_orchestrator.logger import ActionLogger
    from lindy_orchestrator.scheduler import execute_plan

    plan = TaskPlan(
        goal="partial",
        tasks=[
            TaskItem(id=1, module="root", description="fails", status=TaskStatus.FAILED),
            TaskItem(
                id=2,
                module="root",
                description="blocked",
                depends_on=[1],
                status=TaskStatus.PENDING,
            ),
            TaskItem(id=3, module="root", description="independent", status=TaskStatus.PENDING),
        ],
    )

    cfg = OrchestratorConfig()
    cfg.safety.dry_run = True
    logger = MagicMock(spec=ActionLogger)
    hooks, events = _make_hooks_and_events()

    execute_plan(plan, cfg, logger, hooks=hooks)

    # Task 3 should be completed (dry run), not skipped
    assert plan.tasks[2].status == TaskStatus.COMPLETED
    skipped_events = [e for e in events if e.type == EventType.TASK_SKIPPED]
    skipped_ids = {e.task_id for e in skipped_events}
    assert 3 not in skipped_ids
