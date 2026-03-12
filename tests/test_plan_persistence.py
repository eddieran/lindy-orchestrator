"""Tests for plan serialization, persistence, and resume helpers."""

from pathlib import Path

from lindy_orchestrator.cli_helpers import persist_plan, plan_from_dict, plan_to_dict
from lindy_orchestrator.models import QACheck, TaskItem, TaskPlan, TaskStatus


def _make_plan() -> TaskPlan:
    return TaskPlan(
        goal="Build feature X",
        tasks=[
            TaskItem(
                id=1,
                module="backend",
                description="Add API endpoint",
                prompt="Create /api/x endpoint",
                qa_checks=[QACheck(gate="command_check", params={"command": "pytest"})],
                status=TaskStatus.COMPLETED,
                result="Done",
            ),
            TaskItem(
                id=2,
                module="frontend",
                description="Add UI page",
                prompt="Create page for X",
                depends_on=[1],
                status=TaskStatus.PENDING,
            ),
        ],
    )


def test_plan_roundtrip():
    """Plan → dict → Plan preserves all fields."""
    plan = _make_plan()
    data = plan_to_dict(plan)
    restored = plan_from_dict(data)

    assert restored.goal == plan.goal
    assert len(restored.tasks) == 2

    t1 = restored.tasks[0]
    assert t1.id == 1
    assert t1.module == "backend"
    assert t1.status == TaskStatus.COMPLETED
    assert t1.result == "Done"
    assert len(t1.qa_checks) == 1
    assert t1.qa_checks[0].gate == "command_check"
    assert t1.qa_checks[0].params == {"command": "pytest"}

    t2 = restored.tasks[1]
    assert t2.id == 2
    assert t2.depends_on == [1]
    assert t2.status == TaskStatus.PENDING


def test_persist_plan_creates_files(tmp_path: Path):
    """_persist_plan writes JSON and latest.md."""
    plan = _make_plan()
    persist_plan(tmp_path, plan)

    plans_dir = tmp_path / ".orchestrator" / "plans"
    assert plans_dir.exists()

    json_files = list(plans_dir.glob("*.json"))
    assert len(json_files) == 1
    assert "build-feature-x" in json_files[0].name

    latest_md = plans_dir / "latest.md"
    assert latest_md.exists()
    content = latest_md.read_text()
    assert "Build feature X" in content
    assert "backend" in content
    assert "frontend" in content


def test_resume_skips_completed():
    """When restoring a plan, completed tasks keep their status."""
    plan = _make_plan()
    data = plan_to_dict(plan)

    # Simulate resume: task 1 completed, task 2 still pending
    restored = plan_from_dict(data)
    assert restored.tasks[0].status == TaskStatus.COMPLETED
    assert restored.tasks[1].status == TaskStatus.PENDING

    # next_ready should return task 2 (its dep is completed)
    ready = restored.next_ready()
    assert len(ready) == 1
    assert ready[0].id == 2


def test_resume_resets_failed_to_pending():
    """On resume, failed tasks should be reset to pending for retry."""
    plan = _make_plan()
    plan.tasks[1].status = TaskStatus.FAILED
    plan.tasks[1].retries = 2
    data = plan_to_dict(plan)

    restored = plan_from_dict(data)
    # Simulate resume logic: reset failed to pending
    for t in restored.tasks:
        if t.status == TaskStatus.FAILED:
            t.status = TaskStatus.PENDING
            t.retries = 0

    ready = restored.next_ready()
    assert len(ready) == 1
    assert ready[0].id == 2
    assert ready[0].retries == 0
