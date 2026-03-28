---
task: T7
title: Orchestrator
depends_on: [3, 4, 5, 6]
status: pending
---

## T7: Orchestrator

## Context & Prerequisites

**Architecture spec:** `docs/superpowers/specs/2026-03-28-pipeline-architecture-design.md` — read this first for full design context.

**Tech stack:**
- Models: Python dataclasses (`from dataclasses import dataclass, field`)
- Config: Pydantic v2 (`from pydantic import BaseModel, model_validator`)
- Testing: pytest via `uv run python -m pytest`
- Python 3.11+, type hints throughout

**Project structure:** All source in `src/lindy_orchestrator/`, tests in `tests/`.

**Prior task outputs:**
- T1: All new models in `models.py`: `TaskSpec`, `GeneratorOutput`, `EvalResult`, `EvalFeedback`, `AttemptRecord`, `TaskState` (with `to_dict()`/`from_dict()`), `ExecutionResult`, `RoleProviderConfig`
- T2: `PlannerConfig`, `GeneratorConfig`, `EvaluatorConfig` in `config.py`
- T2b: `create_provider(RoleProviderConfig)` factory
- T3: Deprecated features soft-removed (inject_* no-op, layer_check disabled, API mode disabled)
- T4: `PlannerRunner` in `planner_runner.py` — `PlannerRunner(config.planner, config).plan(goal) -> TaskPlan`
- T5: `GeneratorRunner` in `generator_runner.py` — `GeneratorRunner(config.generator, config).execute(task, worktree, branch, feedback) -> GeneratorOutput`
- T6: `EvaluatorRunner` in `evaluator_runner.py` — `EvaluatorRunner(config.evaluator, config).evaluate(task, gen_output, worktree) -> EvalResult`

**Key imports for this task:**
```python
from lindy_orchestrator.models import (TaskSpec, TaskPlan, TaskState, AttemptRecord,
    GeneratorOutput, EvalResult, EvalFeedback, ExecutionResult, TaskStatus)
from lindy_orchestrator.config import OrchestratorConfig
from lindy_orchestrator.planner_runner import PlannerRunner
from lindy_orchestrator.generator_runner import GeneratorRunner
from lindy_orchestrator.evaluator_runner import EvaluatorRunner
from lindy_orchestrator.hooks import HookRegistry, EventType
from lindy_orchestrator.worktree import create_worktree, remove_worktree, cleanup_all_worktrees
from lindy_orchestrator.logger import ActionLogger
```

**Worktree API (from `worktree.py`):**
```python
def create_worktree(project_root: Path, branch_name: str, task_id: int) -> Path:
    # Creates .worktrees/task-{task_id}, returns worktree path
def remove_worktree(project_root: Path, task_id: int) -> None:
    # Removes .worktrees/task-{task_id}
def cleanup_all_worktrees(project_root: Path) -> None:
    # Removes all .worktrees/*
```

**Hook system (from `hooks.py`):**
```python
hooks.emit(EventType.TASK_STARTED, task_id=task.spec.id, module=task.spec.module, data={...})
```
New EventTypes to add: `PHASE_CHANGED = "phase_changed"`, `EVAL_SCORED = "eval_scored"`.

**Branch name computation:** `f"{config.project.branch_prefix}/task-{task.spec.id}"` (e.g., `"af/task-3"`).

**Session ID generation:** `f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"` or reuse `SessionManager` from `session.py` if preferred.

**CommandQueue implementation detail:**
```python
class CommandQueue:
    def __init__(self):
        self._lock = threading.Lock()
        self._paused = False
        self._skip_ids: set[int] = set()
        self._force_pass_ids: set[int] = set()

    def pause(self): ...      # set _paused = True under lock
    def resume(self): ...     # set _paused = False under lock
    def skip(self, task_id): ... # add to _skip_ids under lock
    def force_pass(self, task_id): ... # add to _force_pass_ids under lock

    @property
    def is_paused(self) -> bool: ...  # read _paused under lock

    def pop_skip(self, task_id: int) -> bool:
        # Returns True and removes if task_id was in _skip_ids, else False
        with self._lock:
            if task_id in self._skip_ids:
                self._skip_ids.discard(task_id)
                return True
            return False

    def pop_force_pass(self, task_id: int) -> bool:
        # Same pattern for _force_pass_ids
```

**_attempt_task must return BOTH GeneratorOutput and EvalResult** (for AttemptRecord):
```python
def _attempt_task(self, ...) -> tuple[GeneratorOutput, EvalResult]:
    gen_output = self.generator.execute(...)
    if not gen_output.success:
        return gen_output, EvalResult(score=0, passed=False, retryable=True, ...)
    eval_result = self.evaluator.evaluate(...)
    return gen_output, eval_result
```

**Event ordering (for tests):**
SESSION_START → [per task: TASK_STARTED → PHASE_CHANGED(generating) → PHASE_CHANGED(evaluating) → EVAL_SCORED → TASK_COMPLETED|TASK_FAILED] → SESSION_END

**ID:** 7
**Depends on:** [3, 4, 5, 6]
**Module:** `src/lindy_orchestrator/orchestrator.py` (new), `src/lindy_orchestrator/hooks.py`

### Description

Create `Orchestrator` — the pipeline coordinator. Owns the DAG execution loop, wires up three runners, manages worktrees, handles signals, drives the retry loop (respecting `retryable`), emits new event types, and checks the command queue.

### Generator Prompt

1. **Add new EventTypes to `hooks.py`:**
   - `PHASE_CHANGED = "phase_changed"` — data: `{"phase": "generating"|"evaluating"}`
   - `EVAL_SCORED = "eval_scored"` — data: `{"score": int, "passed": bool, "attempt": int}`

2. **Create `src/lindy_orchestrator/orchestrator.py`:**

```python
class Orchestrator:
    def __init__(self, config: OrchestratorConfig, hooks: HookRegistry | None = None,
                 logger: ActionLogger | None = None, on_progress: Callable | None = None,
                 verbose: bool = False, command_queue: CommandQueue | None = None): ...
    def run(self, goal: str) -> ExecutionResult: ...
    def resume(self, states: list[TaskState], plan: TaskPlan) -> ExecutionResult: ...
    def _run_task(self, task_state: TaskState, branch_name: str) -> None: ...
    def _attempt_task(self, task_state: TaskState, worktree: Path,
                      branch_name: str, feedback: EvalFeedback | None) -> EvalResult: ...
```

3. **`CommandQueue`** — thread-safe command bus for dashboard controls:
   ```python
   class CommandQueue:
       def __init__(self): ...
       def pause(self): ...
       def resume(self): ...
       def skip(self, task_id: int): ...
       def force_pass(self, task_id: int): ...
       @property
       def is_paused(self) -> bool: ...
       def pop_skip(self, task_id: int) -> bool: ...  # returns True and removes if queued
       def pop_force_pass(self, task_id: int) -> bool: ...
   ```
   Uses `threading.Lock` internally. Idempotent — skip/force_pass on same task_id is harmless.

4. **`run(goal)`:**
   - Create three runners: `PlannerRunner`, `GeneratorRunner`, `EvaluatorRunner`
   - Call `planner.plan(goal)` → TaskPlan
   - Convert TaskSpec[] → TaskState[] (all pending)
   - Emit SESSION_START
   - DAG loop with ThreadPoolExecutor:
     ```python
     while not all_terminal(states):
         if command_queue and command_queue.is_paused:
             time.sleep(1); continue
         for state in next_ready(states):
             if command_queue and command_queue.pop_skip(state.spec.id):
                 state.status = SKIPPED; continue
             pool.submit(self._run_task, state, branch_name)
     ```
   - On completion: emit SESSION_END
   - Return ExecutionResult(plan, states, duration, total_cost, session_id)

5. **`_run_task(task_state)`:**
   - Set status = IN_PROGRESS, emit TASK_STARTED
   - Create worktree, compute branch name
   - Run lifecycle hooks (before_run, after_run)
   - Retry loop:
     ```python
     feedback = None
     for attempt in range(max_retries + 1):
         eval_result = self._attempt_task(state, worktree, branch, feedback)
         state.attempts.append(AttemptRecord(attempt, gen_output, eval_result, now()))
         if eval_result.passed:
             state.status = COMPLETED; break
         if not eval_result.retryable:
             state.status = FAILED; break  # pre-existing, don't retry
         if command_queue and command_queue.pop_force_pass(state.spec.id):
             state.status = COMPLETED; break
         feedback = eval_result.feedback
         emit TASK_RETRYING
     else:
         state.status = FAILED
     ```
   - Cleanup worktree
   - Emit TASK_COMPLETED or TASK_FAILED
   - Save checkpoint (TaskState.to_dict for all states)

6. **`_attempt_task()`:**
   - state.phase = "generating"; emit PHASE_CHANGED(phase="generating")
   - `gen_output = generator.execute(task.spec, worktree, branch, feedback, on_progress)`
   - If not gen_output.success: return EvalResult(score=0, retryable=True, ...)
   - state.phase = "evaluating"; emit PHASE_CHANGED(phase="evaluating")
   - `eval_result = evaluator.evaluate(task.spec, gen_output, worktree)`
   - Emit EVAL_SCORED(score, passed, attempt)
   - Return eval_result

7. **`resume(states, plan)`:**
   - Filter states to non-terminal only
   - Re-enter DAG loop from current state
   - Use TaskState.from_dict for deserialization

8. **Session checkpoint:** After each task completes, save all TaskState[].to_dict() to `{session_dir}/{session_id}.json`. Include `_checkpoint_version`.

9. **Signal handling:** SIGINT → set `_shutdown` flag. DAG loop checks flag each iteration. Running tasks complete. New tasks don't start.

10. **Config hot-reload:** Check config mtime each DAG loop iteration (reuse existing `check_reload` logic on safety, qa_gates sections).

11. Keep `scheduler.py` intact — it will be removed in T12.

12. Write `tests/test_orchestrator.py`:
    - DAG ordering: T1→T2 dependency respected
    - Parallel execution: T1,T2 independent → both started (use barrier/latch, NOT wall-clock)
    - Retry loop: evaluator fails → generator retried with feedback
    - retryable=False → no retry, immediate FAILED
    - Signal handling: SIGINT → graceful shutdown
    - Command queue: pause stops scheduling, skip marks SKIPPED, force_pass marks COMPLETED
    - Session checkpoint written and loadable
    - All event types emitted in correct order

### Acceptance Criteria

- `Orchestrator.run(goal)` executes full pipeline: plan → generate → evaluate → retry
- DAG scheduling correct: dependencies respected, independent tasks parallel
- Retry loop: on eval score < threshold AND retryable=True, generator re-runs with feedback
- retryable=False → task FAILED immediately, no retry
- Signal handling: SIGINT sets flag, running tasks complete, new tasks don't start
- CommandQueue: pause/skip/force_pass work correctly, thread-safe
- Events emitted: SESSION_START, TASK_STARTED, PHASE_CHANGED, EVAL_SCORED, TASK_COMPLETED/FAILED, SESSION_END
- Session checkpoint written with _checkpoint_version
- PHASE_CHANGED and EVAL_SCORED added to hooks.py EventType
- ≥22 unit tests

### Evaluator Prompt

Verify: (1) mock planner returns 3-task DAG with T2 depending on T1, T3 independent — assert T1 runs before T2, T3 runs concurrently with T1 (use barrier), (2) mock evaluator returns score=40/retryable=True then score=90 — assert generator called twice, (3) mock evaluator returns retryable=False — assert generator called once, task FAILED, (4) SIGINT → running task completes but pending don't start, (5) CommandQueue.skip → task marked SKIPPED without execution, (6) all events emitted in correct order, (7) checkpoint file written to disk.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_orchestrator.py tests/ -x -q --tb=short"
  timeout: 180
