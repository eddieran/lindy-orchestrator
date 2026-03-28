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
    retryable: bool = True  # False for pre-existing violations; skip retry if all non-retryable


@dataclass
class TaskSpec:
    """A single task in a goal's execution plan."""

    id: int
    module: str
    description: str
    generator_prompt: str = ""
    acceptance_criteria: str = ""
    evaluator_prompt: str = ""
    prompt: str = ""
    depends_on: list[int] = field(default_factory=list)
    priority: int = 0  # higher = dispatched first within same dep level
    qa_checks: list[QACheck] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    qa_results: list[QAResult] = field(default_factory=list)
    retries: int = 0
    feedback_history: list[dict[str, Any]] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    skip_qa: bool = False  # skip auto-injected QA gates (for review-only tasks)
    skip_gates: list[str] = field(default_factory=list)  # exclude specific gates by name
    timeout_seconds: int | None = None  # per-task override
    stall_seconds: int | None = None  # per-task stall override
    cost_usd: float = 0.0  # actual cost from dispatch provider


@dataclass
class TaskPlan:
    """A goal decomposed into an ordered task DAG."""

    goal: str
    tasks: list[TaskSpec] = field(default_factory=list)

    def next_ready(self) -> list[TaskSpec]:
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

        ready = [
            t
            for t in self.tasks
            if t.status == TaskStatus.PENDING and all(dep in completed_ids for dep in t.depends_on)
        ]
        # Higher priority tasks dispatched first within the same dep level
        ready.sort(key=lambda t: t.priority, reverse=True)
        return ready

    def is_complete(self) -> bool:
        return all(t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for t in self.tasks)

    def all_terminal(self) -> bool:
        """True when every task is in a terminal state (no more work possible)."""
        terminal = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)
        return all(t.status in terminal for t in self.tasks)

    def has_failures(self) -> bool:
        return any(t.status == TaskStatus.FAILED for t in self.tasks)


# ---------------------------------------------------------------------------
# Plan serialization helpers
# ---------------------------------------------------------------------------


def plan_to_dict(plan: TaskPlan) -> dict:
    """Serialize a TaskPlan to a JSON-safe dict."""
    import dataclasses

    return {
        "goal": plan.goal,
        "tasks": [_task_to_dict(t, dataclasses) for t in plan.tasks],
    }


def plan_from_dict(data: dict) -> TaskPlan:
    """Deserialize a TaskPlan from a dict."""
    tasks = []
    for t in data.get("tasks", []):
        generator_prompt = t.get("generator_prompt", t.get("prompt", ""))
        qa_checks = [
            QACheck(gate=c["gate"], params=c.get("params", {})) for c in t.get("qa_checks", [])
        ]
        tasks.append(
            TaskSpec(
                id=t["id"],
                module=t["module"],
                description=t["description"],
                generator_prompt=generator_prompt,
                acceptance_criteria=t.get("acceptance_criteria", ""),
                evaluator_prompt=t.get("evaluator_prompt", ""),
                prompt=t.get("prompt", generator_prompt),
                depends_on=t.get("depends_on", []),
                priority=t.get("priority", 0),
                qa_checks=qa_checks,
                status=TaskStatus(t.get("status", "pending")),
                result=t.get("result", ""),
                retries=t.get("retries", 0),
                feedback_history=t.get("feedback_history", []),
                started_at=t.get("started_at"),
                completed_at=t.get("completed_at"),
                timeout_seconds=t.get("timeout_seconds"),
                skip_qa=t.get("skip_qa", False),
                skip_gates=t.get("skip_gates", []),
                stall_seconds=t.get("stall_seconds"),
                cost_usd=t.get("cost_usd", 0.0),
            )
        )
    return TaskPlan(goal=data["goal"], tasks=tasks)


def _task_to_dict(task: TaskSpec, dataclasses_module: Any) -> dict[str, Any]:
    """Serialize task fields while mirroring legacy prompt consumers."""
    task_dict = dataclasses_module.asdict(task)
    generator_prompt = task.generator_prompt or task.prompt
    task_dict["generator_prompt"] = generator_prompt
    task_dict["prompt"] = task.prompt or generator_prompt
    task_dict["status"] = task.status.value
    task_dict["qa_checks"] = [{"gate": q.gate, "params": q.params} for q in task.qa_checks]
    task_dict["qa_results"] = [
        {"gate": r.gate, "passed": r.passed, "output": r.output[:500]} for r in task.qa_results
    ]
    return task_dict


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
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


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
