"""Core data models for lindy-orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlannerMode(str, Enum):
    CLI = "cli"
    API = "api"


# ---------------------------------------------------------------------------
# STATUS.md parsed structures
# ---------------------------------------------------------------------------


@dataclass
class ModuleMeta:
    module: str = ""
    last_updated: str = ""
    agent_session: str = ""
    overall_health: str = "GREEN"


@dataclass
class ActiveTask:
    id: str = ""
    task: str = ""
    status: str = ""
    blocked_by: str = ""
    started: str = ""
    notes: str = ""


@dataclass
class CompletedTask:
    id: str = ""
    task: str = ""
    completed: str = ""
    outcome: str = ""


@dataclass
class CrossModuleRequest:
    id: str = ""
    from_module: str = ""
    to_module: str = ""
    request: str = ""
    priority: str = ""
    status: str = ""


@dataclass
class CrossModuleDeliverable:
    id: str = ""
    from_module: str = ""
    to_module: str = ""
    deliverable: str = ""
    status: str = ""
    path: str = ""


@dataclass
class ModuleStatus:
    meta: ModuleMeta = field(default_factory=ModuleMeta)
    active_work: list[ActiveTask] = field(default_factory=list)
    completed: list[CompletedTask] = field(default_factory=list)
    backlog: list[str] = field(default_factory=list)
    requests: list[CrossModuleRequest] = field(default_factory=list)
    deliverables: list[CrossModuleDeliverable] = field(default_factory=list)
    key_metrics: dict[str, str] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    raw_text: str = ""


# ---------------------------------------------------------------------------
# Goal / TaskPlan structures
# ---------------------------------------------------------------------------


@dataclass
class QACheck:
    """A quality gate check to run after a task completes."""

    gate: str  # e.g. "ci_check", "command_check", "agent_check"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class QAResult:
    gate: str
    passed: bool
    output: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskItem:
    """A single task in a goal's execution plan."""

    id: int
    module: str
    description: str
    prompt: str = ""
    depends_on: list[int] = field(default_factory=list)
    qa_checks: list[QACheck] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    qa_results: list[QAResult] = field(default_factory=list)
    retries: int = 0


@dataclass
class TaskPlan:
    """A goal decomposed into an ordered task DAG."""

    goal: str
    tasks: list[TaskItem] = field(default_factory=list)

    def next_ready(self) -> list[TaskItem]:
        """Return all tasks whose dependencies are satisfied.

        A dependency is satisfied if it is COMPLETED.  If a dependency is
        FAILED or SKIPPED, the dependent task is automatically marked SKIPPED
        (it can never run).  Only truly ready (all deps completed) tasks are
        returned.
        """
        completed_ids = {t.id for t in self.tasks if t.status == TaskStatus.COMPLETED}
        failed_ids = {
            t.id for t in self.tasks if t.status in (TaskStatus.FAILED, TaskStatus.SKIPPED)
        }

        # First pass: skip tasks whose dependencies can never be satisfied
        for t in self.tasks:
            if t.status != TaskStatus.PENDING:
                continue
            if any(dep in failed_ids for dep in t.depends_on):
                t.status = TaskStatus.SKIPPED
                t.result = "Skipped: dependency failed"

        return [
            t
            for t in self.tasks
            if t.status == TaskStatus.PENDING and all(dep in completed_ids for dep in t.depends_on)
        ]

    def is_complete(self) -> bool:
        return all(t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for t in self.tasks)

    def all_terminal(self) -> bool:
        """True when every task is in a terminal state (no more work possible)."""
        terminal = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)
        return all(t.status in terminal for t in self.tasks)

    def has_failures(self) -> bool:
        return any(t.status == TaskStatus.FAILED for t in self.tasks)


# ---------------------------------------------------------------------------
# Dispatch result
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    module: str
    success: bool
    output: str
    exit_code: int = 0
    duration_seconds: float = 0.0
    truncated: bool = False
    error: str | None = None
    event_count: int = 0
    last_tool_use: str = ""


# ---------------------------------------------------------------------------
# Project discovery / onboarding structures
# ---------------------------------------------------------------------------


@dataclass
class ModuleProfile:
    """Deep profile of a project module gathered by static analysis."""

    name: str
    path: str
    tech_stack: list[str] = field(default_factory=list)
    dependencies: dict[str, str] = field(default_factory=dict)
    dir_tree: str = ""
    entry_points: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    build_commands: list[str] = field(default_factory=list)
    lint_commands: list[str] = field(default_factory=list)
    ci_config: str = ""
    existing_docs: str = ""
    detected_patterns: list[str] = field(default_factory=list)


@dataclass
class CrossModuleDep:
    """A dependency between two modules."""

    from_module: str
    to_module: str
    interface_type: str = ""  # "api", "file", "database", "env_var", "message_queue"
    description: str = ""


@dataclass
class ProjectProfile:
    """Auto-detected project structure from static analysis."""

    name: str
    root: str
    modules: list[ModuleProfile] = field(default_factory=list)
    cross_module_files: list[str] = field(default_factory=list)
    git_remote: str = ""
    default_branch: str = "main"
    detected_ci: str = ""
    monorepo: bool = False


@dataclass
class DiscoveryContext:
    """Complete project understanding: auto-analysis + user answers."""

    project_name: str
    project_description: str
    root: str
    modules: list[ModuleProfile] = field(default_factory=list)
    cross_deps: list[CrossModuleDep] = field(default_factory=list)
    coordination_complexity: int = 1  # 1=loose, 2=moderate, 3=tight
    branch_prefix: str = "af"
    sensitive_paths: list[str] = field(default_factory=list)
    qa_requirements: dict[str, list[str]] = field(default_factory=dict)
    git_remote: str = ""
    monorepo: bool = False
