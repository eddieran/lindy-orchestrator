---
task: T7
title: Orchestrator
depends_on: [3, 4, 5, 6]
status: pending
---

## T7: Orchestrator

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
