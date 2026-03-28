# Pipeline Architecture — Task DAG

**Spec:** `2026-03-28-pipeline-architecture-design.md`
**Execution:** Each task is dispatched to a subagent. Tasks are orthogonal within each level. Each task's output is the next level's context.

## DAG Overview

```
Level 0:  T1 (models)
              │
Level 1:  T2 (config)
              │
         ┌────┼────┬────┐
Level 2:  T3  T4   T5   T6        ← all depend on T2, orthogonal to each other
         (rm) (plan)(gen)(eval)
         └────┼────┴────┘
              │
Level 3:  T7 (orchestrator)        ← depends on T3,T4,T5,T6
              │
         ┌────┴────┐
Level 4:  T8       T9              ← both depend on T7, orthogonal
         (viz)    (CLI)
              │
Level 5:  T10 (integration tests)  ← depends on T8,T9
              │
Level 6:  T11 (e2e tests)          ← depends on T10
              │
Level 7:  T12 (cleanup + PR)       ← depends on T11
```

**Parallelism:** T3/T4/T5/T6 run in parallel. T8/T9 run in parallel. All others sequential.

---

## T1: Data Models

**ID:** 1
**Depends on:** none
**Module:** `src/lindy_orchestrator/models.py`

### Description

Define new data models for the three-role pipeline. Keep existing `TaskStatus`, `QACheck`, `QAResult`, `StructuredFeedback` — they still work. Add new models alongside existing ones (don't break imports yet).

### Generator Prompt

Add the following dataclasses to `models.py`:

1. `TaskSpec` — Planner output per task. Fields: `id`, `module`, `description`, `depends_on`, `generator_prompt`, `acceptance_criteria`, `evaluator_prompt`, `qa_checks`, `skip_qa`, `timeout_seconds`, `stall_seconds`.

2. `GeneratorOutput` — Generator result. Fields: `success`, `output`, `diff`, `cost_usd`, `duration_seconds`, `event_count`, `last_tool`.

3. `EvalResult` — Evaluator verdict. Fields: `score` (int 0-100), `passed` (bool), `feedback` (StructuredFeedback), `qa_results` (list[QAResult]), `cost_usd`, `duration_seconds`.

4. `AttemptRecord` — One generate→evaluate cycle. Fields: `attempt` (int), `generator_output` (GeneratorOutput), `eval_result` (EvalResult), `timestamp` (str).

5. `TaskState` — Runtime state for orchestrator. Fields: `spec` (TaskSpec), `status` (TaskStatus), `attempts` (list[AttemptRecord]), `started_at`, `completed_at`, `total_cost_usd`.

6. Update `TaskPlan` to hold `list[TaskSpec]` alongside existing `list[TaskItem]` (backward compat during migration). Add `planner_cost_usd` field.

Keep `TaskItem`, `DispatchResult`, and all existing models intact — they'll be removed in T12 after migration.

### Acceptance Criteria

- All new dataclasses importable from `lindy_orchestrator.models`
- Existing tests still pass (no existing model changed)
- Type annotations complete, no `Any` types
- `TaskSpec` has clear docstring explaining context isolation (which fields go to Generator vs Evaluator)

### Evaluator Prompt

Verify: (1) all dataclasses defined per spec, (2) existing `TaskItem`/`DispatchResult` untouched, (3) `from lindy_orchestrator.models import TaskSpec, GeneratorOutput, EvalResult, AttemptRecord, TaskState` works, (4) all existing tests pass unchanged.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/ -x -q --tb=short"
  timeout: 120

---

## T2: Configuration Schema

**ID:** 2
**Depends on:** [1]
**Module:** `src/lindy_orchestrator/config.py`

### Description

Update `OrchestratorConfig` to support three-role configuration. Add `PlannerConfig`, `GeneratorConfig`, `EvaluatorConfig` dataclasses. Maintain backward compatibility — old `dispatcher` key maps to `generator`.

### Generator Prompt

1. Add `PlannerConfig` pydantic model:
   - `provider`: str = "claude_cli"
   - `timeout_seconds`: int = 120
   - `prompt`: str = "" (empty = use default template)

2. Add `GeneratorConfig` pydantic model:
   - `provider`: str = "claude_cli"
   - `timeout_seconds`: int = 1800
   - `stall_timeout`: int = 600
   - `permission_mode`: str = "bypassPermissions"
   - `max_output_chars`: int = 200_000
   - `prompt_prefix`: str = ""

3. Add `EvaluatorConfig` pydantic model:
   - `provider`: str = "claude_cli"
   - `timeout_seconds`: int = 300
   - `pass_threshold`: int = 80
   - `prompt_prefix`: str = ""

4. In `OrchestratorConfig`:
   - Add `planner: PlannerConfig`
   - Add `generator: GeneratorConfig`
   - Add `evaluator: EvaluatorConfig`
   - Keep `dispatcher: DispatcherConfig` for backward compat
   - Add `model_validator` that maps old `dispatcher` to `generator` if `generator` not set
   - Remove `mailbox`, `tracker`, `otel` fields (add validator that warns if present in YAML)
   - Remove `layer_check` from `QAGatesConfig`
   - Simplify `StallEscalationConfig` to single `stall_timeout` on GeneratorConfig

5. Remove `MailboxConfig`, `TrackerConfig`, `OTelConfig`, `LayerCheckConfig` classes.

### Acceptance Criteria

- `OrchestratorConfig` loads both new format (planner/generator/evaluator) and old format (dispatcher)
- Old YAML with `dispatcher:` still loads with deprecation warning in log
- New YAML with `planner:/generator:/evaluator:` loads cleanly
- Removed config sections (mailbox, tracker, otel) log a warning if present, don't error
- All existing config tests pass or are updated

### Evaluator Prompt

Verify: (1) load a new-format YAML — all three role configs populated, (2) load an old-format YAML — `generator` populated from `dispatcher`, (3) removed sections don't crash, (4) `config.evaluator.pass_threshold` defaults to 80, (5) existing tests pass.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_config*.py tests/test_schema*.py -x -q --tb=short"
  timeout: 120

---

## T3: Remove Dead Features

**ID:** 3
**Depends on:** [2]
**Module:** multiple files

### Description

Delete code for removed features: Layer Check, Tracker Integration, Mailbox, OTel, Dual Planner Mode, Stall two-stage escalation, inject_* dead code. Delete associated test files.

### Generator Prompt

Delete the following files entirely:
- `src/lindy_orchestrator/qa/layer_check.py`
- `src/lindy_orchestrator/trackers/` (entire directory)
- `src/lindy_orchestrator/mailbox.py`
- `src/lindy_orchestrator/otel.py` (if exists)

Remove from `src/lindy_orchestrator/qa/__init__.py`:
- The `from . import layer_check` import at bottom

Remove from `src/lindy_orchestrator/scheduler_helpers.py`:
- `inject_mailbox_messages()`, `inject_claude_md()`, `inject_status_content()`, `inject_branch_delivery()` functions
- Their entries in `__all__`
- Mailbox-related imports (`from .mailbox import ...`)

Remove from `src/lindy_orchestrator/planner.py`:
- `_plan_via_api()` function and the `if mode == "api"` branch
- Keep only CLI mode

Remove from `src/lindy_orchestrator/scheduler.py`:
- OTel import and setup block
- Stall escalation two-stage logic — will be replaced in T5

Remove from CLI files (`cli.py`, `cli_ext.py`):
- `issues` command
- `run-issue` command
- `mailbox` command
- Tracker-related imports and logic

Delete test files:
- `tests/test_layer_check*.py`
- `tests/test_mailbox*.py`
- `tests/test_tracker*.py`
- `tests/test_otel*.py`
- Tests in `test_inject_claude_md.py` that test `inject_*` functions (keep tests for `gather_*` functions if they exist in same file)

Update `__init__.py` files to remove deleted module re-exports.

### Acceptance Criteria

- All deleted files gone from tree
- No import of deleted modules anywhere in codebase (`grep -r "layer_check\|mailbox\|otel\|trackers" src/`)
- `inject_mailbox_messages`, `inject_claude_md`, `inject_status_content`, `inject_branch_delivery` no longer exist
- Planner only has CLI path
- Remaining tests pass (count will drop — that's expected)
- `lindy run --help` no longer shows `issues`, `run-issue`, `mailbox` commands

### Evaluator Prompt

Verify: (1) `grep -rn "from.*layer_check\|from.*mailbox\|from.*otel\|from.*trackers" src/` returns nothing, (2) `grep -rn "inject_mailbox\|inject_claude_md\|inject_status_content\|inject_branch_delivery" src/` returns nothing, (3) `grep -rn "_plan_via_api" src/` returns nothing, (4) remaining tests pass, (5) no `ImportError` when importing `lindy_orchestrator`.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/ -x -q --tb=short"
  timeout: 180

---

## T4: Planner Runner

**ID:** 4
**Depends on:** [2]
**Module:** `src/lindy_orchestrator/planner_runner.py` (new)

### Description

Create `PlannerRunner` — extracts planning logic from `planner.py` into the new role-based runner. Uses `PlannerConfig` for provider selection and prompt. Outputs `TaskPlan` with `TaskSpec[]`.

### Generator Prompt

Create `src/lindy_orchestrator/planner_runner.py`:

```python
class PlannerRunner:
    def __init__(self, config: PlannerConfig, project_config: OrchestratorConfig): ...
    def plan(self, goal: str) -> TaskPlan: ...
    def _build_context(self, goal: str) -> str: ...
    def _parse_plan(self, raw_output: str) -> TaskPlan: ...
```

Key behaviors:
1. `_build_context()`: Assemble planner prompt from `config.prompt` (or default template from `prompts.py`). Inject: module list, STATUS.md summaries (truncated 1500 chars), ARCHITECTURE.md (truncated 5000 chars), available QA gates, date.

2. `plan()`: Create provider via `create_provider()` using `config.provider`. Call `provider.dispatch_simple()`. Parse output into `TaskPlan`.

3. `_parse_plan()`: Parse JSON into `TaskSpec[]`. Each task MUST have `generator_prompt`, `acceptance_criteria`, `evaluator_prompt`. Validate: IDs unique, depends_on references valid, no cycles.

4. Update `prompts.py` `PLAN_PROMPT_TEMPLATE` to instruct the LLM to output the three fields per task:
   - `prompt` → renamed to `generator_prompt`
   - NEW: `acceptance_criteria` (human-readable success criteria)
   - NEW: `evaluator_prompt` (specific verification instructions for the evaluator agent)

5. Write `tests/test_planner_runner.py` — mock provider, test prompt construction, JSON parsing, validation, error handling.

Do NOT modify `planner.py` — it stays for backward compat until T7 wires up the new orchestrator.

### Acceptance Criteria

- `PlannerRunner.plan(goal)` returns `TaskPlan` with `TaskSpec[]`
- Each `TaskSpec` has non-empty `generator_prompt`, `acceptance_criteria`, `evaluator_prompt`
- Provider is created from `PlannerConfig.provider` (not hardcoded)
- Custom `config.prompt` overrides default template
- JSON parse errors produce a meaningful single-task error plan (like current behavior)
- ≥10 unit tests covering: prompt construction, JSON parsing, validation errors, cycle detection

### Evaluator Prompt

Verify: (1) `PlannerRunner` creates provider from config, not hardcoded, (2) default prompt template includes instructions for all three output fields, (3) `_parse_plan` validates TaskSpec fields, (4) unit tests cover happy path + error cases, (5) existing tests unaffected.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_planner_runner.py tests/ -x -q --tb=short"
  timeout: 120

---

## T5: Generator Runner

**ID:** 5
**Depends on:** [2]
**Module:** `src/lindy_orchestrator/generator_runner.py` (new)

### Description

Create `GeneratorRunner` — extracts dispatch + prompt building logic from `scheduler.py` and `scheduler_helpers.py` into the generator role. Strict context isolation: sees only `generator_prompt`, CLAUDE.md, branch instructions, and feedback on retry. Never sees `acceptance_criteria` or `evaluator_prompt`.

### Generator Prompt

Create `src/lindy_orchestrator/generator_runner.py`:

```python
class GeneratorRunner:
    def __init__(self, config: GeneratorConfig, project_config: OrchestratorConfig): ...
    def execute(self, task: TaskSpec, worktree: Path, branch_name: str,
                feedback: StructuredFeedback | None = None,
                on_progress: Callable | None = None) -> GeneratorOutput: ...
    def _build_prompt(self, task: TaskSpec, worktree: Path, branch_name: str,
                      feedback: StructuredFeedback | None) -> str: ...
```

Key behaviors:
1. `_build_prompt()`:
   - Start with `config.prompt_prefix` (from YAML)
   - Append CLAUDE.md / CODEX.md instructions (reuse `gather_claude_md` from scheduler_helpers)
   - Append `task.generator_prompt` (from Planner)
   - Append branch delivery instructions (reuse `gather_branch_delivery`)
   - If retry: append `feedback` as structured retry guidance (reuse `build_retry_prompt` from qa/feedback.py, adapted)
   - MUST NOT include `task.acceptance_criteria` or `task.evaluator_prompt`

2. `execute()`:
   - Create provider via `create_provider()` using `config.provider`
   - Dispatch with streaming, using `SimpleTimeoutMonitor` (single `stall_timeout`)
   - Collect output, compute diff via `git diff` in worktree
   - Return `GeneratorOutput`

3. `SimpleTimeoutMonitor`:
   - Replace `_HeartbeatTracker` complexity
   - Single `stall_timeout` — if no events for this duration, kill
   - Track `last_event_time`, expose `is_stalled()` method
   - Still emit progress updates (tool name, event count) via callback

4. Write `tests/test_generator_runner.py` — mock provider, test:
   - Prompt construction includes generator_prompt but NOT acceptance_criteria
   - Retry prompt includes feedback
   - Timeout monitor kills on stall
   - diff collection from worktree

### Acceptance Criteria

- `GeneratorRunner.execute()` returns `GeneratorOutput` with success, output, diff, cost
- Prompt NEVER contains acceptance_criteria or evaluator_prompt (test assertion)
- Provider created from `GeneratorConfig.provider`
- Custom `prompt_prefix` prepended to all prompts
- Retry includes structured feedback from previous EvalResult
- SimpleTimeoutMonitor replaces two-stage stall escalation
- ≥12 unit tests covering: prompt isolation, retry, timeout, diff collection

### Evaluator Prompt

Verify: (1) build a prompt for a TaskSpec with acceptance_criteria="must pass all tests" — assert that string does NOT appear in the built prompt, (2) provider created from config, (3) SimpleTimeoutMonitor works (mock time), (4) existing tests unaffected.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_generator_runner.py tests/ -x -q --tb=short"
  timeout: 120

---

## T6: Evaluator Runner

**ID:** 6
**Depends on:** [2]
**Module:** `src/lindy_orchestrator/evaluator_runner.py` (new)

### Description

Create `EvaluatorRunner` — extracts QA gate execution from scheduler + adds evaluator agent call. Two-phase evaluation: mechanical QA gates (parallel), then agent-based judgment with scoring.

### Generator Prompt

Create `src/lindy_orchestrator/evaluator_runner.py`:

```python
class EvaluatorRunner:
    def __init__(self, config: EvaluatorConfig, project_config: OrchestratorConfig): ...
    def evaluate(self, task: TaskSpec, gen_output: GeneratorOutput,
                 worktree: Path) -> EvalResult: ...
    def _run_qa_gates(self, checks: list[QACheck], worktree: Path,
                      project_root: Path, module_name: str) -> list[QAResult]: ...
    def _run_eval_agent(self, task: TaskSpec, gen_output: GeneratorOutput,
                        qa_results: list[QAResult]) -> EvalResult: ...
    def _build_eval_prompt(self, task: TaskSpec, gen_output: GeneratorOutput,
                           qa_results: list[QAResult]) -> str: ...
```

Key behaviors:
1. `evaluate()`:
   - If `task.skip_qa`: return EvalResult(score=100, passed=True)
   - Run `_run_qa_gates()` — parallel via ThreadPoolExecutor
   - If any required gate fails AND all are non-retryable (pre-existing): return EvalResult(score=0, passed=False, retryable=False)
   - Run `_run_eval_agent()` — intelligent assessment with scoring
   - Return combined EvalResult

2. `_run_qa_gates()`:
   - Reuse existing `run_qa_gate()` from `qa/__init__.py`
   - Run all checks in parallel
   - Return list of QAResult

3. `_build_eval_prompt()`:
   - Start with `config.prompt_prefix` (from YAML)
   - Append `task.acceptance_criteria`
   - Append `task.evaluator_prompt`
   - Append `gen_output.diff` (truncated to 50K chars)
   - Append `gen_output.output` (truncated to 10K chars)
   - Append QA gate results summary
   - Instruct: output JSON with `{"score": 0-100, "passed": bool, "feedback": {"summary": "...", "specific_errors": [...], "files_to_check": [...], "remediation_steps": [...]}}`
   - MUST NOT include `task.generator_prompt`

4. `_run_eval_agent()`:
   - Create provider from `config.provider`
   - Call `dispatch_simple()`
   - Parse JSON verdict → EvalResult
   - On parse failure: default to score=0 with generic feedback

5. Write `tests/test_evaluator_runner.py` — mock provider + mock QA gates, test:
   - QA gates run in parallel
   - Eval prompt includes acceptance_criteria but NOT generator_prompt
   - Score parsing works
   - Feedback structure is correct
   - skip_qa returns 100

### Acceptance Criteria

- `EvaluatorRunner.evaluate()` returns `EvalResult` with score, passed, feedback, qa_results
- QA gates execute in parallel
- Eval agent prompt contains acceptance_criteria + evaluator_prompt + diff + qa_results
- Eval agent prompt does NOT contain generator_prompt (test assertion)
- Score < pass_threshold → `passed=False`
- JSON parse failure → score=0 with fallback feedback
- Provider created from `EvaluatorConfig.provider`
- ≥15 unit tests covering: scoring, feedback parsing, QA gate parallel execution, context isolation, skip_qa

### Evaluator Prompt

Verify: (1) build eval prompt for a TaskSpec with generator_prompt="implement X" — assert that string does NOT appear in the prompt, (2) acceptance_criteria IS in the prompt, (3) mock evaluator returning score=45 → passed=False, (4) mock evaluator returning score=85 → passed=True, (5) QA gates run concurrently (check ThreadPoolExecutor usage).

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_evaluator_runner.py tests/ -x -q --tb=short"
  timeout: 120

---

## T7: Orchestrator

**ID:** 7
**Depends on:** [3, 4, 5, 6]
**Module:** `src/lindy_orchestrator/orchestrator.py` (new)

### Description

Create `Orchestrator` — the pipeline coordinator that replaces `scheduler.py`. Owns the DAG execution loop, wires up three runners, manages worktrees, handles signals, and drives the retry loop.

### Generator Prompt

Create `src/lindy_orchestrator/orchestrator.py`:

```python
class Orchestrator:
    def __init__(self, config: OrchestratorConfig, hooks: HookRegistry | None = None,
                 logger: ActionLogger | None = None, on_progress: Callable | None = None,
                 verbose: bool = False): ...
    def run(self, goal: str) -> ExecutionResult: ...
    def _run_task(self, task_state: TaskState, branch_name: str) -> None: ...
    def _attempt_task(self, task_state: TaskState, worktree: Path,
                      branch_name: str, feedback: StructuredFeedback | None) -> EvalResult: ...
```

Key behaviors:
1. `__init__()`:
   - Create `PlannerRunner(config.planner, config)`
   - Create `GeneratorRunner(config.generator, config)`
   - Create `EvaluatorRunner(config.evaluator, config)`
   - Initialize HookRegistry, ActionLogger
   - Set up signal handler (SIGINT → graceful shutdown flag)

2. `run(goal)`:
   - Call `planner.plan(goal)` → TaskPlan
   - Convert TaskSpec[] → TaskState[] (all pending)
   - Emit SESSION_START event
   - DAG loop with ThreadPoolExecutor:
     ```python
     while not all_terminal(states):
         for state in next_ready(states):
             pool.submit(self._run_task, state, branch_name)
     ```
   - On completion: emit SESSION_END, generate report
   - Return ExecutionResult (plan, states, duration, total_cost)

3. `_run_task(task_state)`:
   - Create worktree
   - Compute branch name
   - Run lifecycle hooks (before_run, after_run)
   - Retry loop:
     ```python
     for attempt in range(max_retries + 1):
         eval_result = self._attempt_task(task_state, worktree, branch, feedback)
         task_state.attempts.append(AttemptRecord(...))
         if eval_result.passed:
             task_state.status = COMPLETED; break
         feedback = eval_result.feedback
     else:
         task_state.status = FAILED
     ```
   - Cleanup worktree
   - Emit events: TASK_STARTED, PHASE_CHANGED, TASK_COMPLETED/FAILED

4. `_attempt_task()`:
   - Emit PHASE_CHANGED(phase="generate")
   - `gen_output = generator.execute(task, worktree, branch, feedback)`
   - If not gen_output.success: return EvalResult(score=0, ...)
   - Emit PHASE_CHANGED(phase="evaluate")
   - `eval_result = evaluator.evaluate(task, gen_output, worktree)`
   - Emit EVAL_SCORED(score=eval_result.score)
   - Return eval_result

5. Session checkpoint: save TaskState[] to JSON after each task completes (reuse session.py logic).

6. Config hot-reload: check config mtime periodically (reuse existing `check_reload` logic).

7. Write `tests/test_orchestrator.py` — mock all three runners, test:
   - DAG ordering (task with depends_on runs after dependency)
   - Parallel execution (independent tasks run concurrently)
   - Retry loop (evaluator fails → generator retried with feedback)
   - Signal handling (SIGINT → graceful shutdown)
   - Session checkpoint save/restore

Keep `scheduler.py` intact — it will be removed in T12.

### Acceptance Criteria

- `Orchestrator.run(goal)` executes full pipeline: plan → generate → evaluate → retry
- DAG scheduling correct: dependencies respected, independent tasks parallel
- Retry loop: on eval score < threshold, generator re-runs with feedback
- Signal handling: SIGINT sets flag, running tasks complete, new tasks don't start
- Worktree created per task, cleaned up after
- Events emitted: SESSION_START, TASK_STARTED, PHASE_CHANGED, EVAL_SCORED, TASK_COMPLETED/FAILED, SESSION_END
- Session checkpoint written after each task
- ≥20 unit tests

### Evaluator Prompt

Verify: (1) mock planner returns 3-task DAG with T2 depending on T1, T3 independent — assert T1 runs before T2, T3 runs in parallel with T1, (2) mock evaluator returns score=40 on first call, 90 on second — assert generator called twice, (3) SIGINT during execution — assert running task completes but pending tasks don't start, (4) all events emitted in correct order.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_orchestrator.py tests/ -x -q --tb=short"
  timeout: 180

---

## T8: Visualization Update

**ID:** 8
**Depends on:** [7]
**Module:** `src/lindy_orchestrator/dashboard.py`, `src/lindy_orchestrator/dag.py`, `src/lindy_orchestrator/web/server.py`, `src/lindy_orchestrator/reporter.py`

### Description

Update all visualization modules to support the three-phase pipeline. Add phase display, evaluator scores, attempt history, and interactive controls to the web dashboard.

### Generator Prompt

1. **Terminal Dashboard** (`dashboard.py`):
   - Subscribe to new events: `PHASE_CHANGED`, `EVAL_SCORED`
   - In-progress tasks show: `[Generate → att. 1]` or `[Evaluate → 72/100]`
   - Completed tasks show final score: `✓ 1 backend: Design auth (95/100)`
   - Update `_TaskDetail` to track: `phase`, `attempt`, `last_score`

2. **DAG Renderer** (`dag.py`):
   - `_node_text()` — append phase + score for in-progress tasks
   - No structural changes needed

3. **Web Dashboard** (`web/server.py`):
   - Update `_INDEX_HTML`:

   **Sidebar enhancement:**
   - Pipeline phase indicator (Plan → Generate → Evaluate progress bar)
   - Acceptance criteria section (from TaskSpec, sent via init event)
   - Attempt history: table of `[attempt, score, feedback_summary, duration]`
   - Per-phase cost breakdown

   **New SSE events handling:**
   - `phase_changed` → update node card to show current phase
   - `eval_scored` → update node card with score, add to attempt history

   **Interactive controls (bottom bar):**
   - Add `<div class="controls">` with buttons: Pause, Skip, Force Pass, Resume
   - Each button sends POST to new API endpoints

   **New HTTP endpoints in `_Handler.do_POST()`:**
   - `POST /api/pause` → set `server.paused = True`
   - `POST /api/resume` → set `server.paused = False`
   - `POST /api/task/{id}/skip` → add task ID to `server.skip_queue`
   - `POST /api/task/{id}/force-pass` → add task ID to `server.force_pass_queue`

   **Orchestrator integration:**
   - `WebDashboard` exposes `paused`, `skip_queue`, `force_pass_queue` properties
   - `Orchestrator` checks these in the DAG loop:
     - `if dashboard.paused: wait`
     - `if task.id in dashboard.skip_queue: mark SKIPPED`
     - `if task.id in dashboard.force_pass_queue: skip evaluator, mark COMPLETED`

   **Init event enhancement:**
   - Include `acceptance_criteria` per task in init payload
   - Include `attempts` history for resumed sessions

4. **Reporter** (`reporter.py`):
   - `generate_execution_summary()` — add attempt history column
   - `save_summary_report()` — add per-attempt score + feedback + cost breakdown per role
   - Add cost breakdown table: Planner $X, Generator $Y, Evaluator $Z, Total $T

5. Write `tests/test_dashboard_pipeline.py` — test new event handling, phase display.

### Acceptance Criteria

- Terminal dashboard shows phase + attempt + score for running tasks
- Web dashboard shows pipeline progress, acceptance criteria, attempt history
- Interactive controls (pause/skip/force-pass/resume) work via POST endpoints
- Reporter includes attempt history and cost breakdown
- SSE events include phase_changed and eval_scored
- ≥10 new tests for visualization changes

### Evaluator Prompt

Verify: (1) terminal dashboard text includes "[Generate → att. 1]" format, (2) web HTML includes controls div with pause/skip/force-pass buttons, (3) POST handler exists for /api/pause, /api/task/*/skip, /api/task/*/force-pass, (4) reporter markdown includes attempt history table, (5) init SSE event includes acceptance_criteria per task.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_dashboard*.py tests/test_reporter*.py tests/ -x -q --tb=short"
  timeout: 120

---

## T9: CLI Wiring

**ID:** 9
**Depends on:** [7]
**Module:** `src/lindy_orchestrator/cli.py`, `src/lindy_orchestrator/cli_ext.py`

### Description

Wire CLI commands (`run`, `plan`, `resume`) to use the new `Orchestrator` instead of `execute_plan`/`generate_plan`. Remove deleted commands (issues, run-issue, mailbox).

### Generator Prompt

1. **`cli.py` — `run` command:**
   - Replace `generate_plan()` + `execute_plan()` with `Orchestrator(config).run(goal)`
   - Pass `hooks`, `logger`, `on_progress`, `verbose`, `console` to Orchestrator
   - Web dashboard: pass to Orchestrator for interactive controls integration
   - Keep `--web` / `--web-port` flags

2. **`cli.py` — `plan` command:**
   - Replace `generate_plan()` with `PlannerRunner(config.planner, config).plan(goal)`
   - Display TaskSpec[] with new fields (acceptance_criteria shown)

3. **`cli.py` — `resume` command:**
   - Load session checkpoint
   - Convert saved TaskState[] back to Orchestrator state
   - Call `Orchestrator.resume(states)` (add resume method to Orchestrator)

4. **`cli_ext.py`:**
   - Remove `issues` command function
   - Remove `run-issue` command function
   - Remove `mailbox` command function
   - Remove tracker-related imports

5. **`cli.py` — `onboard/init` command:**
   - Update YAML generation to produce new format (planner/generator/evaluator blocks)
   - Auto-generate default prompts for each role

6. **`print_task_list()`** in reporter.py or cli_helpers.py:
   - Show `acceptance_criteria` (truncated) in task list display

7. Write `tests/test_cli_pipeline.py` — test CLI wiring with mocked Orchestrator.

### Acceptance Criteria

- `lindy run "goal"` uses Orchestrator pipeline
- `lindy plan "goal"` uses PlannerRunner, shows acceptance_criteria in output
- `lindy resume` loads checkpoint and continues via Orchestrator
- Removed commands (issues, run-issue, mailbox) no longer in CLI
- `lindy onboard` generates new YAML format
- ≥8 tests for CLI wiring

### Evaluator Prompt

Verify: (1) `lindy run --help` works without error, (2) `lindy plan --help` works, (3) `lindy resume --help` works, (4) `lindy issues` → error/not found, (5) `lindy mailbox` → error/not found, (6) onboard generates YAML with planner/generator/evaluator sections.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_cli*.py tests/ -x -q --tb=short"
  timeout: 120

---

## T10: Integration Tests

**ID:** 10
**Depends on:** [8, 9]
**Module:** `tests/`

### Description

Write integration tests that verify the full pipeline works end-to-end with mocked providers. Test DAG ordering, retry loops, context isolation, session checkpoint/resume, dashboard events.

### Generator Prompt

Create `tests/test_integration_pipeline.py`:

1. **test_full_pipeline_happy_path:**
   - Mock planner returns 2-task DAG (T1 independent, T2 depends on T1)
   - Mock generator returns success for both
   - Mock evaluator returns score=90 for both
   - Assert: T1 completes before T2 starts, both COMPLETED

2. **test_retry_loop:**
   - Mock evaluator returns score=30 on first call, score=85 on second
   - Assert: generator called twice, feedback from first eval passed to second generate
   - Assert: task ends COMPLETED after 2 attempts

3. **test_max_retries_exceeded:**
   - Mock evaluator always returns score=20
   - max_retries=2
   - Assert: generator called 3 times (initial + 2 retries), task ends FAILED

4. **test_context_isolation_generator:**
   - Capture the prompt passed to generator provider
   - Assert: prompt contains generator_prompt
   - Assert: prompt does NOT contain acceptance_criteria
   - Assert: prompt does NOT contain evaluator_prompt

5. **test_context_isolation_evaluator:**
   - Capture the prompt passed to evaluator provider
   - Assert: prompt contains acceptance_criteria + evaluator_prompt
   - Assert: prompt does NOT contain generator_prompt

6. **test_parallel_execution:**
   - 3-task DAG: T1, T2, T3 all independent
   - Track execution timestamps
   - Assert: all three started within 1 second of each other (parallel)

7. **test_dependency_ordering:**
   - T1 → T2 → T3 chain
   - Assert: T1 completes before T2 starts, T2 before T3

8. **test_signal_handling:**
   - Send SIGINT during execution
   - Assert: running task completes, pending tasks don't start, result is partial

9. **test_session_checkpoint_resume:**
   - Run pipeline, mock crash after T1 completes
   - Resume from checkpoint
   - Assert: T1 not re-executed, T2 starts

10. **test_skip_qa:**
    - Task with skip_qa=True
    - Assert: evaluator returns score=100 immediately, no QA gates run

11. **test_dashboard_events:**
    - Collect all emitted events
    - Assert: SESSION_START, TASK_STARTED, PHASE_CHANGED(generate), PHASE_CHANGED(evaluate), EVAL_SCORED, TASK_COMPLETED, SESSION_END in order

12. **test_different_providers_per_role:**
    - Config: planner=claude_cli, generator=codex_cli, evaluator=claude_cli
    - Assert: each runner creates the correct provider type

### Acceptance Criteria

- All 12 integration tests pass
- Context isolation verified with prompt assertions
- Retry loop verified with attempt counting
- Parallel execution verified with timing
- No flaky tests (use deterministic mocks, no real time dependencies)

### Evaluator Prompt

Verify: (1) all 12 tests exist and pass, (2) context isolation tests assert on actual prompt content, (3) no test imports from deleted modules, (4) tests use mock providers not real CLI.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_integration_pipeline.py -x -v --tb=short"
  timeout: 120

---

## T11: End-to-End Tests

**ID:** 11
**Depends on:** [10]
**Module:** `tests/`

### Description

Write end-to-end tests using fixture projects and mock CLI binaries. These tests exercise the full code path including file I/O, git operations, config loading, and process management.

### Generator Prompt

Create `tests/test_e2e_pipeline.py`:

1. **test_e2e_plan_and_execute:**
   - Create a fixture project in tmp_path with orchestrator.yaml (new format), two modules
   - Mock `claude` binary that returns canned plan JSON (with generator_prompt + acceptance_criteria + evaluator_prompt)
   - Mock `claude` binary for generator: creates a file, commits
   - Mock `claude` binary for evaluator: returns `{"score": 90, "passed": true, "feedback": ...}`
   - Run `Orchestrator(config).run("add a feature")`
   - Assert: plan created, tasks executed, worktrees created/cleaned, session saved

2. **test_e2e_retry_with_feedback:**
   - Same fixture, but mock evaluator returns score=40 first, score=85 second
   - Assert: generator prompt on second call includes feedback from first eval
   - Assert: generator prompt on second call does NOT include acceptance_criteria
   - Assert: task completes after 2 attempts

3. **test_e2e_config_backward_compat:**
   - Create fixture with old-format YAML (dispatcher: instead of generator:)
   - Assert: loads correctly, plan and execute work

4. **test_e2e_web_dashboard_sse:**
   - Start Orchestrator with `--web`
   - Connect to SSE endpoint
   - Assert: init event contains tasks with acceptance_criteria
   - Assert: phase_changed events fire during execution
   - Assert: eval_scored events fire with scores

5. **test_e2e_session_resume:**
   - Run orchestrator, interrupt after first task
   - Resume from session file
   - Assert: first task not re-dispatched, second task executes

6. **test_e2e_cli_run:**
   - Use subprocess to call `lindy run --dry-run "test goal"` with fixture config
   - Assert: exit code 0, output contains task plan

### Acceptance Criteria

- All 6 e2e tests pass
- Tests use real file I/O, real git operations (in tmp_path)
- Tests verify the full pipeline from config load → plan → generate → evaluate → report
- Backward compatibility with old config format verified
- Web dashboard SSE events verified

### Evaluator Prompt

Verify: (1) all 6 tests pass, (2) tests create real git repos in tmp_path, (3) tests don't depend on real `claude` binary (all mocked), (4) context isolation asserted at e2e level (grep generator prompt for absence of acceptance_criteria), (5) no test takes >30s.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_e2e_pipeline.py -x -v --tb=short"
  timeout: 180

---

## T12: Cleanup, Migration, and PR

**ID:** 12
**Depends on:** [11]
**Module:** entire codebase

### Description

Final cleanup: remove old scheduler.py, planner.py, and deprecated models. Update all imports. Ensure all tests pass. Create PR.

### Generator Prompt

1. **Remove deprecated files:**
   - Delete `src/lindy_orchestrator/scheduler.py` (replaced by orchestrator.py)
   - Delete `src/lindy_orchestrator/planner.py` (replaced by planner_runner.py)
   - Delete `src/lindy_orchestrator/codex_dispatcher.py` (functionality in providers/)
   - Delete `src/lindy_orchestrator/dispatcher.py` (functionality in providers/)

2. **Clean up models.py:**
   - Remove `TaskItem` (replaced by TaskSpec + TaskState)
   - Remove backward-compat aliases
   - Keep: TaskStatus, QACheck, QAResult, StructuredFeedback, DispatchResult (still used by providers)

3. **Clean up scheduler_helpers.py:**
   - Remove all `inject_*` and `gather_*` functions (now in runners)
   - Keep: `inject_qa_gates` if still used by evaluator, else remove
   - Rename file to `helpers.py` or merge remaining utilities into appropriate runners

4. **Update all imports:**
   - `grep -rn "from.*scheduler import\|from.*planner import\|from.*dispatcher import" src/ tests/`
   - Update each to new locations

5. **Delete obsolete test files:**
   - `tests/test_scheduler*.py` (replaced by test_orchestrator.py)
   - `tests/test_planner*.py` (replaced by test_planner_runner.py)
   - `tests/test_dispatcher*.py` (replaced by provider tests if any)
   - `tests/test_inject_claude_md.py` (inject_* functions deleted)
   - Any remaining tests for deleted features

6. **Final test run:**
   - `uv run python -m pytest tests/ -x -q --tb=short`
   - Target: ≥1200 tests passing (from original 1330, minus deleted ~130)

7. **Update README.md:**
   - Document new YAML format with planner/generator/evaluator
   - Document pipeline architecture
   - Remove references to deleted features (mailbox, tracker, layer_check, otel)

8. **Version bump:** `pyproject.toml` → `0.15.0` (minor bump for breaking changes)

9. **Create PR** with title: `feat: pipeline architecture — Planner/Generator/Evaluator role separation`

### Acceptance Criteria

- No references to deleted files in imports
- All tests pass (≥1200)
- No `TaskItem` usage in non-test code
- README updated with new architecture
- Version bumped to 0.15.0
- PR created and CI passes

### Evaluator Prompt

Verify: (1) `grep -rn "from.*scheduler import" src/` returns nothing (only orchestrator imports), (2) `grep -rn "TaskItem" src/` returns nothing, (3) `grep -rn "from.*planner import" src/` returns nothing (only planner_runner), (4) all tests pass, (5) README documents pipeline architecture, (6) version is 0.15.0.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/ -x -q --tb=short"
  timeout: 300
