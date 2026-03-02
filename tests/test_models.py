"""Tests for core data models."""

from lindy_orchestrator.models import (
    TaskItem,
    TaskPlan,
    TaskStatus,
)


def test_task_plan_next_ready_no_deps():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="task 1"),
            TaskItem(id=2, module="b", description="task 2"),
        ],
    )
    ready = plan.next_ready()
    assert len(ready) == 2
    assert {t.id for t in ready} == {1, 2}


def test_task_plan_next_ready_with_deps():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="task 1"),
            TaskItem(id=2, module="b", description="task 2", depends_on=[1]),
            TaskItem(id=3, module="c", description="task 3", depends_on=[1, 2]),
        ],
    )
    ready = plan.next_ready()
    assert len(ready) == 1
    assert ready[0].id == 1

    # Complete task 1
    plan.tasks[0].status = TaskStatus.COMPLETED
    ready = plan.next_ready()
    assert len(ready) == 1
    assert ready[0].id == 2

    # Complete task 2
    plan.tasks[1].status = TaskStatus.COMPLETED
    ready = plan.next_ready()
    assert len(ready) == 1
    assert ready[0].id == 3


def test_task_plan_is_complete():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="task 1", status=TaskStatus.COMPLETED),
            TaskItem(id=2, module="b", description="task 2", status=TaskStatus.COMPLETED),
        ],
    )
    assert plan.is_complete()


def test_task_plan_is_not_complete():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="task 1", status=TaskStatus.COMPLETED),
            TaskItem(id=2, module="b", description="task 2", status=TaskStatus.PENDING),
        ],
    )
    assert not plan.is_complete()


def test_task_plan_has_failures():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="task 1", status=TaskStatus.COMPLETED),
            TaskItem(id=2, module="b", description="task 2", status=TaskStatus.FAILED),
        ],
    )
    assert plan.has_failures()


def test_task_plan_no_failures():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="task 1", status=TaskStatus.COMPLETED),
            TaskItem(id=2, module="b", description="task 2", status=TaskStatus.PENDING),
        ],
    )
    assert not plan.has_failures()


def test_task_plan_skipped_counts_as_complete():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="task 1", status=TaskStatus.COMPLETED),
            TaskItem(id=2, module="b", description="task 2", status=TaskStatus.SKIPPED),
        ],
    )
    assert plan.is_complete()


def test_parallel_readiness():
    """Tasks 2 and 3 both depend only on 1; they should be ready in parallel."""
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="setup", status=TaskStatus.COMPLETED),
            TaskItem(id=2, module="b", description="frontend", depends_on=[1]),
            TaskItem(id=3, module="c", description="backend", depends_on=[1]),
            TaskItem(id=4, module="d", description="integration", depends_on=[2, 3]),
        ],
    )
    ready = plan.next_ready()
    assert len(ready) == 2
    assert {t.id for t in ready} == {2, 3}
