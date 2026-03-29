"""Tests for core data models and prompt parsing."""

import json

from lindy_orchestrator.models import (
    TaskSpec,
    TaskPlan,
    TaskStatus,
)
from lindy_orchestrator.planner_runner import _format_prompt, _parse_task_plan


def test_task_plan_next_ready_no_deps():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskSpec(id=1, module="a", description="task 1"),
            TaskSpec(id=2, module="b", description="task 2"),
        ],
    )
    ready = plan.next_ready()
    assert len(ready) == 2
    assert {t.id for t in ready} == {1, 2}


def test_task_plan_next_ready_with_deps():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskSpec(id=1, module="a", description="task 1"),
            TaskSpec(id=2, module="b", description="task 2", depends_on=[1]),
            TaskSpec(id=3, module="c", description="task 3", depends_on=[1, 2]),
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
            TaskSpec(id=1, module="a", description="task 1", status=TaskStatus.COMPLETED),
            TaskSpec(id=2, module="b", description="task 2", status=TaskStatus.COMPLETED),
        ],
    )
    assert plan.is_complete()


def test_task_plan_is_not_complete():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskSpec(id=1, module="a", description="task 1", status=TaskStatus.COMPLETED),
            TaskSpec(id=2, module="b", description="task 2", status=TaskStatus.PENDING),
        ],
    )
    assert not plan.is_complete()


def test_task_plan_has_failures():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskSpec(id=1, module="a", description="task 1", status=TaskStatus.COMPLETED),
            TaskSpec(id=2, module="b", description="task 2", status=TaskStatus.FAILED),
        ],
    )
    assert plan.has_failures()


def test_task_plan_no_failures():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskSpec(id=1, module="a", description="task 1", status=TaskStatus.COMPLETED),
            TaskSpec(id=2, module="b", description="task 2", status=TaskStatus.PENDING),
        ],
    )
    assert not plan.has_failures()


def test_task_plan_skipped_counts_as_complete():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskSpec(id=1, module="a", description="task 1", status=TaskStatus.COMPLETED),
            TaskSpec(id=2, module="b", description="task 2", status=TaskStatus.SKIPPED),
        ],
    )
    assert plan.is_complete()


def test_parallel_readiness():
    """Tasks 2 and 3 both depend only on 1; they should be ready in parallel."""
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskSpec(id=1, module="a", description="setup", status=TaskStatus.COMPLETED),
            TaskSpec(id=2, module="b", description="frontend", depends_on=[1]),
            TaskSpec(id=3, module="c", description="backend", depends_on=[1]),
            TaskSpec(id=4, module="d", description="integration", depends_on=[2, 3]),
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
            TaskSpec(id=1, module="a", description="backend", status=TaskStatus.FAILED),
            TaskSpec(id=2, module="b", description="frontend", depends_on=[1]),
            TaskSpec(id=3, module="c", description="docs"),
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
            TaskSpec(id=1, module="a", description="t1", status=TaskStatus.COMPLETED),
            TaskSpec(id=2, module="b", description="t2", status=TaskStatus.FAILED),
            TaskSpec(id=3, module="c", description="t3", status=TaskStatus.SKIPPED),
        ],
    )
    assert plan.all_terminal()


def test_not_all_terminal():
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskSpec(id=1, module="a", description="t1", status=TaskStatus.COMPLETED),
            TaskSpec(id=2, module="b", description="t2", status=TaskStatus.PENDING),
        ],
    )
    assert not plan.all_terminal()


# ---------------------------------------------------------------------------
# Structured prompt parsing (Change 4)
# ---------------------------------------------------------------------------


def test_format_prompt_full():
    """A structured prompt dict produces formatted instruction text."""
    prompt = {
        "objective": "Add user authentication endpoint",
        "context_files": ["backend/routes/auth.py", "backend/models/user.py"],
        "constraints": ["Do not modify existing endpoints", "Use JWT tokens"],
        "verification": ["Run pytest tests/test_auth.py", "Expected: all pass"],
    }
    result = _format_prompt(prompt)

    assert "## Objective" in result
    assert "Add user authentication endpoint" in result
    assert "## Context Files" in result
    assert "`backend/routes/auth.py`" in result
    assert "## Constraints" in result
    assert "Do not modify existing endpoints" in result
    assert "## Before committing, verify" in result
    assert "Run pytest tests/test_auth.py" in result


def test_format_prompt_partial():
    """A prompt dict with only some fields still works."""
    prompt = {"objective": "Fix the bug"}
    result = _format_prompt(prompt)

    assert "## Objective" in result
    assert "Fix the bug" in result
    assert "## Constraints" not in result


def test_format_prompt_empty():
    """An empty dict produces empty output."""
    result = _format_prompt({})
    assert result == ""


def test_parse_task_plan_with_enriched_fields():
    """_parse_task_plan handles enriched planner task fields."""
    plan_json = json.dumps(
        {
            "tasks": [
                {
                    "id": 1,
                    "module": "backend",
                    "description": "Add auth",
                    "generator_prompt": {
                        "objective": "Implement JWT auth",
                        "context_files": ["auth.py"],
                        "constraints": ["Use PyJWT"],
                        "verification": ["pytest"],
                    },
                    "acceptance_criteria": "JWT login succeeds and invalid credentials fail cleanly",
                    "evaluator_prompt": "Confirm token issuance and rejection behavior in auth.py",
                    "depends_on": [],
                    "qa_checks": [],
                }
            ]
        }
    )
    plan = _parse_task_plan("test goal", plan_json)
    assert len(plan.tasks) == 1
    assert "## Objective" in plan.tasks[0].generator_prompt
    assert "Implement JWT auth" in plan.tasks[0].generator_prompt
    assert "## Before committing, verify" in plan.tasks[0].generator_prompt
    assert plan.tasks[0].prompt == plan.tasks[0].generator_prompt
    assert (
        plan.tasks[0].acceptance_criteria
        == "JWT login succeeds and invalid credentials fail cleanly"
    )
    assert (
        plan.tasks[0].evaluator_prompt == "Confirm token issuance and rejection behavior in auth.py"
    )


def test_parse_task_plan_with_legacy_string_prompt():
    """_parse_task_plan handles string prompts (legacy format)."""
    plan_json = json.dumps(
        {
            "tasks": [
                {
                    "id": 1,
                    "module": "backend",
                    "description": "Fix bug",
                    "prompt": "Fix the login bug in auth.py",
                    "depends_on": [],
                    "qa_checks": [],
                }
            ]
        }
    )
    plan = _parse_task_plan("test goal", plan_json)
    assert plan.tasks[0].prompt == "Fix the login bug in auth.py"
    assert plan.tasks[0].generator_prompt == "Fix the login bug in auth.py"


def test_chain_continues_after_partial_failure():
    """Simulate a 4-task plan where task 1 fails but task 3 (no deps) runs."""
    plan = TaskPlan(
        goal="test",
        tasks=[
            TaskSpec(id=1, module="backend", description="API changes"),
            TaskSpec(id=2, module="frontend", description="UI update", depends_on=[1]),
            TaskSpec(id=3, module="docs", description="Update docs"),
            TaskSpec(id=4, module="qa", description="Integration tests", depends_on=[1, 3]),
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
