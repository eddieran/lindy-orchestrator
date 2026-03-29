"""Core data models for lindy-orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar


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
    """A single task in a goal's execution plan.

    `generator_prompt` is only for the generator role. `acceptance_criteria`
    and `evaluator_prompt` are only for the evaluator role.
    """

    id: int
    module: str
    description: str
    prompt: str = ""
    generator_prompt: str = ""
    acceptance_criteria: str = ""
    evaluator_prompt: str = ""
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

    def __post_init__(self) -> None:
        """Keep legacy ``prompt`` and new ``generator_prompt`` in sync."""
        if not self.generator_prompt and self.prompt:
            self.generator_prompt = self.prompt
        elif not self.prompt and self.generator_prompt:
            self.prompt = self.generator_prompt


@dataclass
class TaskPlan:
    """A goal decomposed into an ordered task DAG."""

    goal: str
    tasks: list[TaskSpec] = field(default_factory=list)
    planner_cost_usd: float = 0.0
    tasks_v2: list[TaskSpec] = field(default_factory=list)

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
    return {
        "goal": plan.goal,
        "planner_cost_usd": plan.planner_cost_usd,
        "tasks": [_task_spec_to_dict(t) for t in plan.tasks],
        "tasks_v2": [_task_spec_to_dict(t) for t in plan.tasks_v2],
    }


def plan_from_dict(data: dict) -> TaskPlan:
    """Deserialize a TaskPlan from a dict."""
    tasks = [_task_spec_from_dict(t) for t in data.get("tasks", [])]
    tasks_v2 = [_task_spec_from_dict(t) for t in data.get("tasks_v2", [])]
    return TaskPlan(
        goal=data["goal"],
        tasks=tasks,
        planner_cost_usd=data.get("planner_cost_usd", 0.0),
        tasks_v2=tasks_v2,
    )


# ---------------------------------------------------------------------------
# Dispatch result
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    module: str
    success: bool
    output: str
    raw_output: str = ""
    exit_code: int = 0
    duration_seconds: float = 0.0
    truncated: bool = False
    error: str | None = None
    event_count: int = 0
    last_tool_use: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class RoleProviderConfig:
    provider: str = "claude_cli"
    timeout_seconds: int = 300


@dataclass
class GeneratorOutput:
    """Generator execution output captured per attempt."""

    success: bool
    output: str = ""
    prompt: str = ""
    diff: str = ""
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    event_count: int = 0
    last_tool: str = ""


@dataclass
class EvalFeedback:
    """Evaluator feedback for retries and debugging."""

    summary: str = ""
    specific_errors: list[str] = field(default_factory=list)
    files_to_check: list[str] = field(default_factory=list)
    remediation_steps: list[str] = field(default_factory=list)
    failed_criteria: list[str] = field(default_factory=list)
    evidence: str = ""
    missing_behaviors: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    """Evaluator verdict for a single attempt."""

    score: int = 0
    passed: bool = False
    retryable: bool = True
    criteria_results: list[dict[str, Any]] = field(default_factory=list)
    raw_output: str = ""
    feedback: EvalFeedback = field(default_factory=EvalFeedback)
    qa_results: list[QAResult] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_seconds: float = 0.0


@dataclass
class AttemptRecord:
    """One generator/evaluator cycle for a task."""

    attempt: int
    generator_output: GeneratorOutput = field(default_factory=lambda: GeneratorOutput(False))
    eval_result: EvalResult = field(default_factory=EvalResult)
    timestamp: str = ""


@dataclass
class TaskState:
    """Runtime execution state for a task."""

    _checkpoint_version: ClassVar[int] = 2

    spec: TaskSpec
    status: TaskStatus = TaskStatus.PENDING
    phase: str = "pending"
    attempts: list[AttemptRecord] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    total_cost_usd: float = 0.0

    @property
    def id(self) -> int:
        return self.spec.id

    @property
    def module(self) -> str:
        return self.spec.module

    @property
    def description(self) -> str:
        return self.spec.description

    @property
    def depends_on(self) -> list[int]:
        return self.spec.depends_on

    @property
    def acceptance_criteria(self) -> str:
        return self.spec.acceptance_criteria

    @property
    def retries(self) -> int:
        if self.attempts:
            return max(len(self.attempts) - 1, 0)
        return self.spec.retries

    @property
    def result(self) -> str:
        if not self.attempts:
            return self.spec.result
        return self.attempts[-1].generator_output.output

    @property
    def qa_results(self) -> list[QAResult]:
        if not self.attempts:
            return self.spec.qa_results
        return self.attempts[-1].eval_result.qa_results

    @property
    def cost_usd(self) -> float:
        if self.total_cost_usd > 0:
            return self.total_cost_usd
        return sum(
            record.generator_output.cost_usd + record.eval_result.cost_usd
            for record in self.attempts
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "_checkpoint_version": self._checkpoint_version,
            "spec": _task_spec_to_dict(self.spec),
            "status": self.status.value,
            "phase": self.phase,
            "attempts": [
                {
                    "attempt": attempt.attempt,
                    "generator_output": {
                        "success": attempt.generator_output.success,
                        "output": attempt.generator_output.output,
                        "diff": attempt.generator_output.diff,
                        "cost_usd": attempt.generator_output.cost_usd,
                        "duration_seconds": attempt.generator_output.duration_seconds,
                        "event_count": attempt.generator_output.event_count,
                        "last_tool": attempt.generator_output.last_tool,
                    },
                    "eval_result": {
                        "score": attempt.eval_result.score,
                        "passed": attempt.eval_result.passed,
                        "retryable": attempt.eval_result.retryable,
                        "feedback": {
                            "summary": attempt.eval_result.feedback.summary,
                            "specific_errors": list(attempt.eval_result.feedback.specific_errors),
                            "files_to_check": list(attempt.eval_result.feedback.files_to_check),
                            "remediation_steps": list(
                                attempt.eval_result.feedback.remediation_steps
                            ),
                            "failed_criteria": list(attempt.eval_result.feedback.failed_criteria),
                            "evidence": attempt.eval_result.feedback.evidence,
                            "missing_behaviors": list(
                                attempt.eval_result.feedback.missing_behaviors
                            ),
                        },
                        "qa_results": [
                            {
                                "gate": qa.gate,
                                "passed": qa.passed,
                                "output": qa.output,
                                "details": qa.details,
                                "retryable": qa.retryable,
                            }
                            for qa in attempt.eval_result.qa_results
                        ],
                        "cost_usd": attempt.eval_result.cost_usd,
                        "duration_seconds": attempt.eval_result.duration_seconds,
                    },
                    "timestamp": attempt.timestamp,
                }
                for attempt in self.attempts
            ],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_cost_usd": self.total_cost_usd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskState:
        attempts = []
        for attempt in data.get("attempts", []):
            feedback_data = attempt.get("eval_result", {}).get("feedback", {})
            qa_results = [
                QAResult(
                    gate=qa.get("gate", ""),
                    passed=qa.get("passed", False),
                    output=qa.get("output", ""),
                    details=qa.get("details", {}),
                    retryable=qa.get("retryable", True),
                )
                for qa in attempt.get("eval_result", {}).get("qa_results", [])
            ]
            attempts.append(
                AttemptRecord(
                    attempt=attempt.get("attempt", 0),
                    generator_output=GeneratorOutput(
                        success=attempt.get("generator_output", {}).get("success", False),
                        output=attempt.get("generator_output", {}).get("output", ""),
                        diff=attempt.get("generator_output", {}).get("diff", ""),
                        cost_usd=attempt.get("generator_output", {}).get("cost_usd", 0.0),
                        duration_seconds=attempt.get("generator_output", {}).get(
                            "duration_seconds", 0.0
                        ),
                        event_count=attempt.get("generator_output", {}).get("event_count", 0),
                        last_tool=attempt.get("generator_output", {}).get("last_tool", ""),
                    ),
                    eval_result=EvalResult(
                        score=attempt.get("eval_result", {}).get("score", 0),
                        passed=attempt.get("eval_result", {}).get("passed", False),
                        retryable=attempt.get("eval_result", {}).get("retryable", True),
                        feedback=EvalFeedback(
                            summary=feedback_data.get("summary", ""),
                            specific_errors=feedback_data.get("specific_errors", []),
                            files_to_check=feedback_data.get("files_to_check", []),
                            remediation_steps=feedback_data.get("remediation_steps", []),
                            failed_criteria=feedback_data.get("failed_criteria", []),
                            evidence=feedback_data.get("evidence", ""),
                            missing_behaviors=feedback_data.get("missing_behaviors", []),
                        ),
                        qa_results=qa_results,
                        cost_usd=attempt.get("eval_result", {}).get("cost_usd", 0.0),
                        duration_seconds=attempt.get("eval_result", {}).get(
                            "duration_seconds", 0.0
                        ),
                    ),
                    timestamp=attempt.get("timestamp", ""),
                )
            )

        return cls(
            spec=_task_spec_from_dict(data.get("spec", {})),
            status=TaskStatus(data.get("status", TaskStatus.PENDING.value)),
            phase=data.get("phase", "pending"),
            attempts=attempts,
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            total_cost_usd=data.get("total_cost_usd", 0.0),
        )

    @classmethod
    def from_task(cls, task: TaskSpec) -> TaskState:
        if task.status == TaskStatus.IN_PROGRESS:
            phase = "generating"
        elif task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED):
            phase = "done"
        else:
            phase = "pending"
        return cls(
            spec=task,
            status=task.status,
            phase=phase,
            started_at=task.started_at or "",
            completed_at=task.completed_at or "",
            total_cost_usd=task.cost_usd,
        )


@dataclass
class ExecutionResult:
    """Complete execution state consumed by dashboards and reporters."""

    plan: TaskPlan | None = None
    states: list[TaskState] = field(default_factory=list)
    goal: str = ""
    duration_seconds: float = 0.0
    total_cost_usd: float = 0.0
    session_id: str = ""
    checkpoint_version: int = 2

    @property
    def resolved_goal(self) -> str:
        return self.goal or (self.plan.goal if self.plan else "")


def coerce_execution_result(
    source: TaskPlan | ExecutionResult | list[TaskState],
    *,
    goal: str | None = None,
    duration_seconds: float | None = None,
    session_id: str | None = None,
) -> ExecutionResult:
    """Normalize legacy and pipeline execution shapes."""

    if isinstance(source, ExecutionResult):
        if goal and not source.goal:
            source.goal = goal
        if duration_seconds is not None and not source.duration_seconds:
            source.duration_seconds = duration_seconds
        if session_id and not source.session_id:
            source.session_id = session_id
        if source.total_cost_usd <= 0:
            source.total_cost_usd = sum(state.cost_usd for state in source.states)
        return source

    if isinstance(source, TaskPlan):
        states = [TaskState.from_task(task) for task in source.tasks]
        total_cost = sum(task.cost_usd for task in source.tasks)
        return ExecutionResult(
            plan=source,
            states=states,
            goal=goal or source.goal,
            duration_seconds=duration_seconds or 0.0,
            total_cost_usd=total_cost,
            session_id=session_id or "",
        )

    states = list(source)
    total_cost = sum(state.cost_usd for state in states)
    derived_plan = TaskPlan(goal=goal or "", tasks=[state.spec for state in states])
    return ExecutionResult(
        plan=derived_plan,
        states=states,
        goal=goal or "",
        duration_seconds=duration_seconds or 0.0,
        total_cost_usd=total_cost,
        session_id=session_id or "",
    )


def _task_spec_to_dict(task: TaskSpec) -> dict[str, Any]:
    return {
        "id": task.id,
        "module": task.module,
        "description": task.description,
        "generator_prompt": task.generator_prompt,
        "acceptance_criteria": task.acceptance_criteria,
        "evaluator_prompt": task.evaluator_prompt,
        "prompt": task.prompt or task.generator_prompt,
        "depends_on": list(task.depends_on),
        "priority": task.priority,
        "qa_checks": [{"gate": q.gate, "params": q.params} for q in task.qa_checks],
        "status": task.status.value,
        "result": task.result,
        "qa_results": [
            {
                "gate": r.gate,
                "passed": r.passed,
                "output": r.output,
                "details": r.details,
                "retryable": r.retryable,
            }
            for r in task.qa_results
        ],
        "retries": task.retries,
        "feedback_history": list(task.feedback_history),
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "skip_qa": task.skip_qa,
        "skip_gates": list(task.skip_gates),
        "timeout_seconds": task.timeout_seconds,
        "stall_seconds": task.stall_seconds,
        "cost_usd": task.cost_usd,
    }


def _coerce_str(value: object) -> str:
    """Coerce a value to a string, handling list/dict from JSON."""
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item)
    if isinstance(value, dict):
        import json as _json

        return _json.dumps(value, indent=2)
    if value is None:
        return ""
    return str(value)


def _task_spec_from_dict(data: dict[str, Any]) -> TaskSpec:
    qa_checks = [
        QACheck(gate=c.get("gate", ""), params=c.get("params", {}))
        for c in data.get("qa_checks", [])
    ]
    qa_results = [
        QAResult(
            gate=r.get("gate", ""),
            passed=r.get("passed", False),
            output=r.get("output", ""),
            details=r.get("details", {}),
            retryable=r.get("retryable", True),
        )
        for r in data.get("qa_results", [])
    ]
    raw_gen = data.get("generator_prompt", data.get("prompt", ""))
    generator_prompt = _coerce_str(raw_gen)
    return TaskSpec(
        id=data["id"],
        module=data["module"],
        description=data["description"],
        generator_prompt=generator_prompt,
        acceptance_criteria=_coerce_str(data.get("acceptance_criteria", "")),
        evaluator_prompt=_coerce_str(data.get("evaluator_prompt", "")),
        prompt=_coerce_str(data.get("prompt", raw_gen)),
        depends_on=data.get("depends_on", []),
        priority=data.get("priority", 0),
        qa_checks=qa_checks,
        status=TaskStatus(data.get("status", TaskStatus.PENDING.value)),
        result=data.get("result", ""),
        qa_results=qa_results,
        retries=data.get("retries", 0),
        feedback_history=data.get("feedback_history", []),
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
        skip_qa=data.get("skip_qa", False),
        skip_gates=data.get("skip_gates", []),
        timeout_seconds=data.get("timeout_seconds"),
        stall_seconds=data.get("stall_seconds"),
        cost_usd=data.get("cost_usd", 0.0),
    )


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
