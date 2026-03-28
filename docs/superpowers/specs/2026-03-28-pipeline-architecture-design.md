# Pipeline Architecture: Planner / Generator / Evaluator

**Date:** 2026-03-28
**Status:** Approved (rev 2 — post Codex review)

## Overview

Restructure lindy-orchestrator from a monolithic scheduler into a three-role pipeline with strict context isolation. Each role (Planner, Generator, Evaluator) runs as an independent agent with its own provider, prompt, and context boundary. Communication happens only through well-defined output interfaces — progressive disclosure.

## Goals

1. **Role separation** — Planner, Generator, Evaluator as independent runners with isolated context
2. **Feature reduction** — Remove low-value features (~950 lines): Layer Check, Tracker, Mailbox, OTel, Dual Planner Mode, Stall two-stage
3. **Configurable agents** — Each role can use a different code agent (claude_cli, codex_cli, etc.), defaulting to claude_cli
4. **Configurable prompts** — Each role's prompt is in YAML, auto-generated on init, manually editable
5. **Acceptance criteria** — Planner outputs acceptance_criteria + evaluator_prompt per task
6. **Interactive dashboard** — Web dashboard shows pipeline phases, evaluator scores, and supports pause/skip/force-pass

## Non-Goals

- Adding new providers (only claude_cli and codex_cli exist today)
- Changing the git worktree isolation model
- Changing the hook/event system fundamentally (just extend events for phases)

## Architecture

### Pipeline Data Flow

```
Planner → TaskSpec[] → Generator → GeneratorOutput → Evaluator → EvalResult
                                        ↑                            │
                                        └──── feedback (on retry) ───┘
```

### Context Isolation (Progressive Disclosure)

**Planner sees:**
- Goal (user input)
- Module list + STATUS.md per module
- ARCHITECTURE.md (if exists)
- `planner.prompt` (from YAML)

**Planner outputs (TaskSpec):**
- `generator_prompt` → Generator only
- `acceptance_criteria` → Evaluator only (human-readable)
- `evaluator_prompt` → Evaluator only (agent instructions)
- `qa_checks` → Evaluator only

**Generator sees:**
- `generator.prompt_prefix` (from YAML)
- `TaskSpec.generator_prompt` (from Planner)
- CLAUDE.md / CODEX.md instructions (selected by `generator.provider`, not global `dispatcher.provider`)
- Module STATUS.md (read-only context for current state)
- Branch delivery instructions
- On retry: `EvalResult.feedback` only (NOT acceptance_criteria)

**Evaluator sees:**
- `evaluator.prompt_prefix` (from YAML)
- `TaskSpec.acceptance_criteria` (from Planner)
- `TaskSpec.evaluator_prompt` (from Planner)
- `TaskSpec.qa_checks` (from Planner)
- `GeneratorOutput.diff` + `GeneratorOutput.output` (from Generator)
- QA gate execution results

### Retry Loop

```python
for attempt in range(max_retries + 1):
    gen_output = generator.execute(task, feedback=prev_feedback)
    eval_result = evaluator.evaluate(task, gen_output)
    if eval_result.passed:  # score >= pass_threshold
        break
    if not eval_result.retryable:  # pre-existing failures, not worth retrying
        break
    prev_feedback = eval_result.feedback  # only feedback, not criteria
```

Generator never sees acceptance_criteria — it focuses on fixing issues, not gaming the evaluator.

## YAML Configuration

```yaml
project:
  name: "my-project"
  branch_prefix: "af"

modules:
  - name: backend
    path: backend/

planner:
  provider: claude_cli
  timeout_seconds: 120
  prompt: |
    You are the Project Orchestrator for {project_name}.
    ...

generator:
  provider: claude_cli
  timeout_seconds: 1800
  stall_timeout: 600
  permission_mode: bypassPermissions
  prompt_prefix: |
    You are a code generation agent...

evaluator:
  provider: claude_cli
  timeout_seconds: 300
  pass_threshold: 80
  prompt_prefix: |
    You are a code evaluation agent...

qa_gates:
  ci_check:
    enabled: true
  structural_check:              # canonical name (not "structural")
    max_file_lines: 500
    sensitive_patterns: ["*.env", "*.key"]
  custom:
    - name: lint
      command: "ruff check {changed_files}"
      diff_only: true

safety:
  max_retries_per_task: 2
  max_parallel: 3
  dry_run: false

lifecycle_hooks:
  after_create: ""
  before_run: ""
  after_run: ""
  before_remove: ""

logging:
  dir: .orchestrator/logs
  session_dir: .orchestrator/sessions
```

**Changes from current:**
- `dispatcher` block → `generator` block (backward-compat validator maps old → new)
- New `evaluator` block with `pass_threshold`
- `planner.mode` removed (CLI only)
- `stall_timeout` replaces two-stage `stall_escalation`
- Gate names canonicalized (`structural_check` not `structural`)
- Removed: `mailbox`, `tracker`, `otel`, `layer_check` sections

## Data Models

```python
# --- Provider config (role-agnostic) ---
@dataclass
class RoleProviderConfig:
    """Common provider config extracted from any role config."""
    provider: str = "claude_cli"
    timeout_seconds: int = 300

# --- Planner output ---
@dataclass
class TaskSpec:
    id: int
    module: str
    description: str
    depends_on: list[int]
    generator_prompt: str
    acceptance_criteria: str
    evaluator_prompt: str
    qa_checks: list[QACheck]
    skip_qa: bool = False
    skip_gates: list[str] = field(default_factory=list)
    timeout_seconds: int | None = None
    stall_seconds: int | None = None

@dataclass
class TaskPlan:
    goal: str
    tasks: list[TaskSpec]
    planner_cost_usd: float = 0.0

# --- Generator output ---
@dataclass
class GeneratorOutput:
    success: bool
    output: str
    diff: str
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    event_count: int = 0
    last_tool: str = ""

# --- Evaluator feedback (extended beyond QA-only) ---
@dataclass
class EvalFeedback:
    """Rich feedback for retry — covers both QA failures and semantic gaps."""
    summary: str
    specific_errors: list[str] = field(default_factory=list)
    files_to_check: list[str] = field(default_factory=list)
    remediation_steps: list[str] = field(default_factory=list)
    failed_criteria: list[str] = field(default_factory=list)   # which acceptance criteria failed
    evidence: str = ""                                          # evaluator's evidence/reasoning
    missing_behaviors: list[str] = field(default_factory=list)  # behaviors not implemented

# --- Evaluator output ---
@dataclass
class EvalResult:
    score: int                   # 0-100
    passed: bool                 # computed in code: score >= pass_threshold
    retryable: bool = True       # False for pre-existing failures
    feedback: EvalFeedback = field(default_factory=EvalFeedback)
    qa_results: list[QAResult] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_seconds: float = 0.0

# --- Runtime state (Orchestrator owns) ---
@dataclass
class TaskState:
    spec: TaskSpec
    status: TaskStatus
    phase: str = "pending"       # pending/planning/generating/evaluating/done
    attempts: list[AttemptRecord] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    total_cost_usd: float = 0.0

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "TaskState": ...

@dataclass
class AttemptRecord:
    attempt: int
    generator_output: GeneratorOutput
    eval_result: EvalResult
    timestamp: str

# --- Execution result (what dashboard/reporter consume) ---
@dataclass
class ExecutionResult:
    """Complete execution state — replaces direct TaskPlan mutation."""
    plan: TaskPlan
    states: list[TaskState]
    duration_seconds: float = 0.0
    total_cost_usd: float = 0.0
    session_id: str = ""
    checkpoint_version: int = 2  # schema version for forward compat
```

### Scoring Rubric (anchored, computed in code)

The evaluator prompt includes rubric anchors. `passed` is always computed in code from `score >= threshold`, never trusted from the model:

- **90-100:** All acceptance criteria met, code is clean, tests pass
- **70-89:** Most criteria met, minor issues that don't block functionality
- **50-69:** Some criteria met, notable gaps or quality issues
- **30-49:** Significant gaps, multiple failing criteria
- **0-29:** Fundamental issues, wrong approach, or broken code

## Core Files

### `orchestrator.py` — Pipeline coordinator

- Calls Planner → gets TaskPlan
- DAG topological sort, parallel dispatch of ready tasks
- Per-task: Generator → Evaluator → retry loop (respects `retryable`)
- Signal handling (Ctrl-C graceful shutdown)
- Session checkpoint / resume (uses TaskState.to_dict/from_dict)
- Worktree lifecycle management
- Checks command queue from dashboard (pause/skip/force-pass)
- Does NOT build prompts, run QA gates, or parse provider output

### `planner_runner.py` — Goal → TaskPlan

- Builds planner context (goal + modules + status + architecture)
- Calls provider via `dispatch_simple()` — provider created from `PlannerConfig`
- Parses JSON output into TaskPlan with TaskSpec[]
- Only depends on: config, provider, filesystem (STATUS.md, ARCHITECTURE.md)

### `generator_runner.py` — TaskSpec → code changes

- Builds generator prompt (prompt_prefix + CLAUDE.md/CODEX.md per `generator.provider` + STATUS.md + generator_prompt + feedback on retry)
- Calls provider via `dispatch()` with streaming — provider created from `GeneratorConfig`
- Single stall_timeout (dispatch_core simplified, no two-stage)
- Returns GeneratorOutput (success, output, diff, cost)
- Only depends on: config, provider, worktree

### `evaluator_runner.py` — code changes → pass/fail + feedback

- Runs QA gates in parallel (ci_check, structural_check, command_check, custom)
- Builds evaluator prompt with scoring rubric + acceptance_criteria + evaluator_prompt + diff + qa_results
- Calls provider via `dispatch_simple()` — provider created from `EvaluatorConfig`
- Parses JSON verdict → computes `passed` from `score >= threshold` in code
- On provider timeout/error: return EvalResult(score=0, retryable=True, feedback=timeout_feedback)
- Only depends on: config, provider, qa_registry, TaskSpec evaluator fields, GeneratorOutput

## Provider Factory

`create_provider()` is refactored to accept a `RoleProviderConfig` (just `provider` name + `timeout`), not the full `DispatcherConfig`. This decouples providers from the old dispatcher config and makes them role-agnostic. The actual `dispatcher.py` and `codex_dispatcher.py` wrapper modules are inlined into the provider implementations during the refactor, then deleted.

## Features Removed

| Feature | Files | Lines | Replacement | Additional cleanup |
|---------|-------|-------|-------------|-------------------|
| Layer Check | `qa/layer_check.py` | 306 | Custom command gate | scheduler_helpers injection, discovery templates, qa/__init__.py import |
| Tracker Integration | `trackers/`, `cli_ext.py` parts | ~200 | Manual `gh issue` | — |
| Mailbox | `mailbox.py`, scheduler_helpers injection | ~150 | Removed | cli_status.py, discovery/generator.py, hooks.py event type |
| OTel | `otel.py`, config `otel` block | ~100 | Removed | scheduler.py setup block |
| Dual Planner Mode | planner.py `_plan_via_api()` | ~60 | CLI only | — |
| Stall two-stage | dispatch_core.py warn/kill split | ~40 | Single timeout | — |
| inject_* dead code | scheduler_helpers.py | ~110 | gather_* already replaced | — |

**Removal strategy:** Soft deprecation first (T3 — config warns, code paths no-op'd), hard deletion last (T12 — files removed, imports cleaned).

## Visualization

### Terminal (Rich Live)

Each in_progress task shows current **phase** (Plan/Generate/Evaluate) + attempt count + latest evaluator score. Consumes `ExecutionResult.states` not `TaskPlan.tasks`.

### Web Dashboard

**Three-area layout:**
- Left: DAG with clickable nodes
- Right sidebar: Task detail with pipeline phase visualization, acceptance criteria, attempt history (score + feedback per attempt), live stream, per-phase cost/duration
- Bottom bar: Interactive controls

**Interactive controls via locked command queue:**
- `CommandQueue` (thread-safe, idempotent transitions):
  - `pause()` / `resume()` — toggle scheduling
  - `skip(task_id)` — legal only if task is PENDING or IN_PROGRESS
  - `force_pass(task_id)` — legal only if task is in evaluate phase
- Orchestrator polls queue each DAG loop iteration
- POST handlers enqueue commands, don't mutate state directly

**New EventTypes (added to hooks.py):**
- `PHASE_CHANGED` — task entered Generate/Evaluate phase
- `EVAL_SCORED` — Evaluator produced a score
- (existing `TASK_RETRYING` — enhanced with attempt number + feedback summary)

### Reporter (post-execution)

- Consumes `ExecutionResult` (not TaskPlan)
- Per-task attempt history (each attempt's score + feedback)
- Per-role cost breakdown (planner / generator / evaluator)

## Testing Strategy

### Unit Tests (per runner)
- `test_planner_runner.py` — mock provider, verify prompt construction + JSON parsing
- `test_generator_runner.py` — mock provider, verify prompt injection + output parsing + context isolation
- `test_evaluator_runner.py` — mock provider + mock QA gates, verify scoring + feedback + timeout handling

### Integration Tests (pipeline)
- `test_orchestrator.py` — mock three runners, verify DAG scheduling + retry loop + signal handling + command queue
- Parallel execution tested with barriers/latches, not wall-clock timing

### End-to-End Tests (critical)
- `test_e2e_pipeline.py` — fixture project + mock CLI, full plan → generate → evaluate → retry flow
- Context isolation assertion: Generator prompt must NOT contain acceptance_criteria
- Retry assertion: mock Evaluator returns score=40 then score=90, verify retry happens with correct feedback
- Dashboard SSE: verify PHASE_CHANGED and EVAL_SCORED events emitted
- Checkpoint resume: verify TaskState serialization roundtrip

### Migration
- Existing tests migrate by module ownership
- Tests for removed features deleted
- Each task gated by domain-specific test suites + rolling full-suite count
- Final full-suite target is a minimum, not the primary guardrail
