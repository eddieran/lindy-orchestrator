# Pipeline Architecture: Planner / Generator / Evaluator

**Date:** 2026-03-28
**Status:** Approved

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
- CLAUDE.md / CODEX.md instructions
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
  structural:
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
- `dispatcher` block → `generator` block
- New `evaluator` block with `pass_threshold`
- `planner.mode` removed (CLI only)
- `stall_timeout` replaces two-stage `stall_escalation`
- Removed: `mailbox`, `tracker`, `otel`, `layer_check` sections

## Data Models

```python
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

# --- Evaluator output ---
@dataclass
class EvalResult:
    score: int                   # 0-100
    passed: bool                 # score >= pass_threshold
    feedback: StructuredFeedback
    qa_results: list[QAResult]
    cost_usd: float = 0.0
    duration_seconds: float = 0.0

# --- Runtime state (Orchestrator owns) ---
@dataclass
class TaskState:
    spec: TaskSpec
    status: TaskStatus
    attempts: list[AttemptRecord] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    total_cost_usd: float = 0.0

@dataclass
class AttemptRecord:
    attempt: int
    generator_output: GeneratorOutput
    eval_result: EvalResult
    timestamp: str
```

## Core Files

### `orchestrator.py` — Pipeline coordinator

- Calls Planner → gets TaskPlan
- DAG topological sort, parallel dispatch of ready tasks
- Per-task: Generator → Evaluator → retry loop
- Signal handling (Ctrl-C graceful shutdown)
- Session checkpoint / resume
- Worktree lifecycle management
- Does NOT build prompts, run QA gates, or parse provider output

### `planner_runner.py` — Goal → TaskPlan

- Builds planner context (goal + modules + status + architecture)
- Calls provider via `dispatch_simple()`
- Parses JSON output into TaskPlan with TaskSpec[]
- Only depends on: config, provider, filesystem (STATUS.md, ARCHITECTURE.md)

### `generator_runner.py` — TaskSpec → code changes

- Builds generator prompt (prompt_prefix + CLAUDE.md + generator_prompt + feedback on retry)
- Calls provider via `dispatch()` with streaming + heartbeat
- Returns GeneratorOutput (success, output, diff, cost)
- Simple timeout monitor (single stall_timeout, kill on exceed)
- Only depends on: config, provider, worktree

### `evaluator_runner.py` — code changes → pass/fail + feedback

- Runs QA gates in parallel (ci_check, structural, command_check, custom)
- Builds evaluator prompt (prompt_prefix + acceptance_criteria + evaluator_prompt + diff + qa_results)
- Calls provider via `dispatch_simple()`
- Parses verdict (score 0-100 + structured feedback)
- Only depends on: config, provider, qa_registry, TaskSpec evaluator fields, GeneratorOutput

## Features Removed

| Feature | Files | Lines | Replacement |
|---------|-------|-------|-------------|
| Layer Check | `qa/layer_check.py` | 306 | Custom command gate |
| Tracker Integration | `trackers/`, `cli_ext.py` parts | ~200 | Manual `gh issue` |
| Mailbox | `mailbox.py`, scheduler_helpers injection | ~150 | Removed |
| OTel | `otel.py`, config `otel` block | ~100 | Removed |
| Dual Planner Mode | planner.py `_plan_via_api()` | ~60 | CLI only |
| Stall two-stage | dispatch_core.py warn/kill split | ~40 | Single timeout |
| inject_* dead code | scheduler_helpers.py | ~110 | gather_* already replaced |

Total: ~950 lines removed, net reduction ~1500+ lines after refactor.

## Visualization

### Terminal (Rich Live)

Each in_progress task shows current **phase** (Plan/Generate/Evaluate) + attempt count + latest evaluator score.

### Web Dashboard

**Three-area layout:**
- Left: DAG with clickable nodes
- Right sidebar: Task detail with pipeline phase visualization, acceptance criteria, attempt history (score + feedback per attempt), live stream, per-phase cost/duration
- Bottom bar: Interactive controls

**Interactive controls (new):**
- Pause — stop scheduling new tasks (running tasks complete)
- Skip — mark selected task as SKIPPED
- Force Pass — bypass Evaluator, mark as passed
- Resume — resume scheduling

**New SSE events:**
- `phase_changed` — task entered Generate/Evaluate phase
- `eval_scored` — Evaluator produced a score
- `task_retrying` — includes attempt number + feedback summary

### Reporter (post-execution)

- Per-task attempt history (each attempt's score + feedback)
- Per-role cost breakdown (planner / generator / evaluator)

## Testing Strategy

### Unit Tests (per runner)
- `test_planner_runner.py` — mock provider, verify prompt construction + JSON parsing
- `test_generator_runner.py` — mock provider, verify prompt injection + output parsing
- `test_evaluator_runner.py` — mock provider + mock QA gates, verify scoring + feedback

### Integration Tests (pipeline)
- `test_orchestrator.py` — mock three runners, verify DAG scheduling + retry loop + signal handling

### End-to-End Tests (critical)
- `test_e2e_pipeline.py` — fixture project + mock CLI, full plan → generate → evaluate → retry flow
- Context isolation assertion: Generator prompt must NOT contain acceptance_criteria
- Retry assertion: mock Evaluator returns score=40 then score=90, verify retry happens with correct feedback
- Dashboard SSE: verify phase events are emitted

### Migration
- Existing tests migrate by module ownership
- Tests for removed features (layer_check, mailbox, tracker, otel) are deleted
- Target: ≥1200 migrated tests + ~50 new tests
