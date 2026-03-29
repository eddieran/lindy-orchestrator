"""Shared E2E fixtures for CLI integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lindy_orchestrator.models import TaskSpec, TaskPlan, TaskStatus

MINIMAL_STATUS_MD = (
    "# Status\n\n"
    "## Meta\n"
    "| Key | Value |\n|-----|-------|\n"
    "| module | {name} |\n"
    "| last_updated | 2026-01-01 |\n"
    "| overall_health | GREEN |\n"
    "| agent_session | — |\n\n"
    "## Active Work\n"
    "| ID | Task | Status | BlockedBy | Started | Notes |\n"
    "|----|------|--------|-----------|---------|-------|\n\n"
    "## Completed (Recent)\n| ID | Task | Completed | Outcome |\n"
    "|----|------|-----------|--------|\n\n"
    "## Backlog\n- (none)\n\n"
    "## Cross-Module Requests\n"
    "| ID | From | To | Request | Priority | Status |\n"
    "|----|------|----|---------|----------|--------|\n\n"
    "## Cross-Module Deliverables\n"
    "| ID | From | To | Deliverable | Status | Path |\n"
    "|----|------|----|-------------|--------|------|\n\n"
    "## Key Metrics\n| Metric | Value |\n|--------|-------|\n\n"
    "## Blockers\n- (none)\n"
)


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal orchestrator project with config, modules, logs, sessions."""
    config = {
        "project": {"name": "e2e-project", "branch_prefix": "af"},
        "modules": [
            {"name": "backend", "path": "backend/"},
            {"name": "frontend", "path": "frontend/"},
        ],
    }
    orch_dir = tmp_path / ".orchestrator"
    orch_dir.mkdir(parents=True, exist_ok=True)
    (orch_dir / "config.yaml").write_text(yaml.dump(config))

    # Create new layout dirs
    (orch_dir / "claude").mkdir(parents=True, exist_ok=True)
    (orch_dir / "status").mkdir(parents=True, exist_ok=True)
    (orch_dir / "docs").mkdir(parents=True, exist_ok=True)

    for mod in ("backend", "frontend"):
        (tmp_path / mod).mkdir(exist_ok=True)
        (orch_dir / "status" / f"{mod}.md").write_text(MINIMAL_STATUS_MD.format(name=mod))

    (orch_dir / "logs").mkdir(parents=True, exist_ok=True)
    (orch_dir / "sessions").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture()
def cfg_path(project_dir: Path) -> str:
    return str(project_dir / ".orchestrator" / "config.yaml")


@pytest.fixture()
def project_with_logs(project_dir: Path) -> Path:
    """Project dir with sample JSONL log entries."""
    log_file = project_dir / ".orchestrator" / "logs" / "actions.jsonl"
    entries = [
        '{"timestamp":"2026-01-01T00:00:00","action":"session_start","result":"success","details":{"goal":"test"}}',
        '{"timestamp":"2026-01-01T00:01:00","action":"dispatch","result":"success","details":{"module":"backend"}}',
        '{"timestamp":"2026-01-01T00:02:00","action":"quality_gate","result":"fail","details":{"gate":"pytest"}}',
    ]
    log_file.write_text("\n".join(entries) + "\n")
    return project_dir


def make_plan(goal: str = "Test goal") -> TaskPlan:
    """Create a simple two-task plan for testing."""
    return TaskPlan(
        goal=goal,
        tasks=[
            TaskSpec(
                id=1,
                module="backend",
                description="Setup API",
                status=TaskStatus.COMPLETED,
                result="done",
            ),
            TaskSpec(
                id=2,
                module="frontend",
                description="Build UI",
                depends_on=[1],
                status=TaskStatus.PENDING,
            ),
        ],
    )


def mock_generate_plan(goal, cfg, on_progress=None, progress=None):
    """Mock planner.generate_plan to return a simple plan."""
    plan = make_plan(goal)
    for t in plan.tasks:
        t.status = TaskStatus.PENDING
    return plan


def mock_execute_plan(plan, cfg, logger, on_progress=None, verbose=False, hooks=None):
    """Mock scheduler.execute_plan — marks all pending tasks completed."""
    for t in plan.tasks:
        if t.status == TaskStatus.PENDING:
            t.status = TaskStatus.COMPLETED
            t.result = "mocked success"
    return plan
