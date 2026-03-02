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


# ---------------------------------------------------------------------------
# Task chain resilience (issue #9)
# ---------------------------------------------------------------------------


def test_failure_skips_dependents_not_siblings():
    """When task 1 fails, task 2 (depends on 1) should be skipped,
    but task 3 (independent) should still be ready."""
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="backend", status=TaskStatus.FAILED),
            TaskItem(id=2, module="b", description="frontend", depends_on=[1]),
            TaskItem(id=3, module="c", description="docs"),
        ],
    )
    ready = plan.next_ready()
    # Task 2 should have been auto-skipped, task 3 should be ready
    assert plan.tasks[1].status == TaskStatus.SKIPPED
    assert len(ready) == 1
    assert ready[0].id == 3


def test_all_terminal():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="t1", status=TaskStatus.COMPLETED),
            TaskItem(id=2, module="b", description="t2", status=TaskStatus.FAILED),
            TaskItem(id=3, module="c", description="t3", status=TaskStatus.SKIPPED),
        ],
    )
    assert plan.all_terminal()


def test_not_all_terminal():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="a", description="t1", status=TaskStatus.COMPLETED),
            TaskItem(id=2, module="b", description="t2", status=TaskStatus.PENDING),
        ],
    )
    assert not plan.all_terminal()


def test_chain_continues_after_partial_failure():
    """Simulate a 4-task plan where task 1 fails but task 3 (no deps) runs."""
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskItem(id=1, module="backend", description="API changes"),
            TaskItem(id=2, module="frontend", description="UI update", depends_on=[1]),
            TaskItem(id=3, module="docs", description="Update docs"),
            TaskItem(id=4, module="qa", description="Integration tests", depends_on=[1, 3]),
        ],
    )

    # Iteration 1: tasks 1 and 3 are ready (no deps)
    ready = plan.next_ready()
    assert {t.id for t in ready} == {1, 3}

    # Task 1 fails, task 3 completes
    plan.tasks[0].status = TaskStatus.FAILED
    plan.tasks[2].status = TaskStatus.COMPLETED

    # Iteration 2: task 2 depends on failed 1 → skipped. Task 4 depends on 1 (failed) → skipped.
    ready = plan.next_ready()
    assert len(ready) == 0
    assert plan.tasks[1].status == TaskStatus.SKIPPED  # depends on failed 1
    assert plan.tasks[3].status == TaskStatus.SKIPPED  # depends on failed 1

    # All terminal now
    assert plan.all_terminal()
    assert plan.has_failures()
    assert not plan.is_complete()  # has failures and skips, not all completed
