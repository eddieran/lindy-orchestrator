# Pipeline Architecture — Task DAG (rev 2)

**Spec:** `2026-03-28-pipeline-architecture-design.md`
**Revision:** Post-Codex review. Key changes: split T3 into soft/hard, added T2b provider refactor, expanded models/feedback/serialization, fixed false parallelism.

## DAG Overview

```
Level 0:  T1 (models + serialization)
              │
Level 1:  T2 (config schema)
              │
         ┌────┴────┐
Level 2: T2b      T3                ← T2b: provider refactor, T3: soft deprecation
         (providers)(soft-rm)          both depend on T2, orthogonal
         └────┬────┘
              │
         ┌────┼────┬────┐
Level 3:  T4  T5   T6               ← all depend on T2b, orthogonal
         (plan)(gen)(eval)
              │
Level 4:  T7 (orchestrator)          ← depends on T3,T4,T5,T6
              │
         ┌────┴────┐
Level 5:  T8       T9                ← both depend on T7, orthogonal
         (viz)    (CLI+onboard)
              │
Level 6:  T10 (integration tests)    ← depends on T8,T9
              │
Level 7:  T11 (e2e tests)            ← depends on T10
              │
Level 8:  T12 (hard delete + PR)     ← depends on T11
```

**Parallelism:** T2b/T3 parallel. T4/T5/T6 parallel. T8/T9 parallel. All others sequential.

---

## T1: Data Models + Serialization

**ID:** 1
**Depends on:** none
**Module:** `src/lindy_orchestrator/models.py`

### Description

Define new data models for the three-role pipeline, including serialization for checkpointing, the extended evaluator feedback schema, and the ExecutionResult wrapper for dashboard/reporter consumption. Keep all existing models intact.

### Generator Prompt

Add the following dataclasses to `models.py` (alongside existing models, don't modify them):

1. `TaskSpec` — Planner output per task. Fields: `id` (int), `module` (str), `description` (str), `depends_on` (list[int]), `generator_prompt` (str), `acceptance_criteria` (str), `evaluator_prompt` (str), `qa_checks` (list[QACheck]), `skip_qa` (bool=False), `skip_gates` (list[str]=field(default_factory=list)), `timeout_seconds` (int|None=None), `stall_seconds` (int|None=None). Add docstring explaining context isolation: generator_prompt goes to Generator only, acceptance_criteria and evaluator_prompt go to Evaluator only.

2. `GeneratorOutput` — Generator result. Fields: `success` (bool), `output` (str), `diff` (str), `cost_usd` (float=0.0), `duration_seconds` (float=0.0), `event_count` (int=0), `last_tool` (str="").

3. `EvalFeedback` — Rich feedback for retries, covering both QA and semantic failures. Fields: `summary` (str), `specific_errors` (list[str]), `files_to_check` (list[str]), `remediation_steps` (list[str]), `failed_criteria` (list[str]) — which acceptance criteria failed, `evidence` (str) — evaluator's reasoning, `missing_behaviors` (list[str]) — behaviors not implemented. All list fields default to empty list.

4. `EvalResult` — Evaluator verdict. Fields: `score` (int, 0-100), `passed` (bool), `retryable` (bool=True) — False for pre-existing failures not worth retrying, `feedback` (EvalFeedback=field(default_factory=EvalFeedback)), `qa_results` (list[QAResult]=field(default_factory=list)), `cost_usd` (float=0.0), `duration_seconds` (float=0.0). Note: `passed` is computed in code from `score >= threshold`, never trusted from LLM output.

5. `AttemptRecord` — One generate→evaluate cycle. Fields: `attempt` (int), `generator_output` (GeneratorOutput), `eval_result` (EvalResult), `timestamp` (str).

6. `TaskState` — Runtime state for orchestrator. Fields: `spec` (TaskSpec), `status` (TaskStatus), `phase` (str="pending") — one of pending/generating/evaluating/done, `attempts` (list[AttemptRecord]=field(default_factory=list)), `started_at` (str=""), `completed_at` (str=""), `total_cost_usd` (float=0.0). Add `to_dict()` and `from_dict(cls, data)` methods for checkpoint serialization. Serialize nested objects recursively. Include `_checkpoint_version: int = 2` class var for forward compatibility.

7. `ExecutionResult` — Complete execution state for dashboard/reporter. Fields: `plan` (TaskPlan), `states` (list[TaskState]), `duration_seconds` (float=0.0), `total_cost_usd` (float=0.0), `session_id` (str="").

8. `RoleProviderConfig` — Role-agnostic provider config. Fields: `provider` (str="claude_cli"), `timeout_seconds` (int=300). This is used by the refactored `create_provider()` in T2b.

9. Update existing `TaskPlan` — add `planner_cost_usd: float = 0.0` field. Do NOT change `tasks` type yet (still list[TaskItem] for backward compat). Add `tasks_v2: list[TaskSpec] = field(default_factory=list)` as transitional field.

Keep `TaskItem`, `DispatchResult`, `StructuredFeedback`, and all other existing models intact.

Write comprehensive tests in `tests/test_models_v2.py`:
- All new dataclasses importable
- TaskState.to_dict() → from_dict() roundtrip preserves all fields
- Nested serialization (AttemptRecord with GeneratorOutput and EvalResult)
- Default values correct
- Existing models unchanged (import test)

### Acceptance Criteria

- All new dataclasses importable from `lindy_orchestrator.models`
- Existing tests still pass (no existing model changed)
- Type annotations complete, no `Any` types
- TaskState.to_dict/from_dict roundtrip tested with nested data
- EvalFeedback has failed_criteria, evidence, missing_behaviors fields
- EvalResult has retryable field
- TaskSpec has skip_gates field
- TaskSpec docstring explains context isolation
- ≥15 unit tests in test_models_v2.py

### Evaluator Prompt

Verify: (1) `from lindy_orchestrator.models import TaskSpec, GeneratorOutput, EvalResult, EvalFeedback, AttemptRecord, TaskState, ExecutionResult, RoleProviderConfig` works, (2) existing `TaskItem`/`DispatchResult` untouched, (3) `TaskState.to_dict()` returns a plain dict serializable with `json.dumps()`, (4) `TaskState.from_dict(TaskState(...).to_dict())` roundtrips, (5) all existing tests pass unchanged, (6) EvalFeedback has all 7 fields from spec.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_models_v2.py tests/ -x -q --tb=short"
  timeout: 120

---

## T2: Configuration Schema

**ID:** 2
**Depends on:** [1]
**Module:** `src/lindy_orchestrator/config.py`

### Description

Update `OrchestratorConfig` to support three-role configuration. Add `PlannerConfig`, `GeneratorConfig`, `EvaluatorConfig`. Maintain backward compatibility — old `dispatcher` key maps to `generator`. Canonicalize gate names.

### Generator Prompt

1. Add `PlannerConfig` pydantic model:
   - `provider`: str = "claude_cli"
   - `timeout_seconds`: int = 120
   - `prompt`: str = "" (empty = use default template)

2. Add `GeneratorConfig` pydantic model:
   - `provider`: str = "claude_cli"
   - `timeout_seconds`: int = 1800
   - `stall_timeout`: int = 600 (single timeout, replaces two-stage)
   - `permission_mode`: str = "bypassPermissions"
   - `max_output_chars`: int = 200_000
   - `prompt_prefix`: str = ""

3. Add `EvaluatorConfig` pydantic model:
   - `provider`: str = "claude_cli"
   - `timeout_seconds`: int = 300
   - `pass_threshold`: int = 80
   - `prompt_prefix`: str = ""

4. In `OrchestratorConfig`:
   - Add `planner: PlannerConfig = PlannerConfig()`
   - Add `generator: GeneratorConfig = GeneratorConfig()`
   - Add `evaluator: EvaluatorConfig = EvaluatorConfig()`
   - Keep `dispatcher: DispatcherConfig` for backward compat
   - Add `model_validator` that maps old `dispatcher` to `generator` fields if `generator` not explicitly set. Log deprecation warning.
   - Add `model_validator` that warns (log.warning, don't error) if `mailbox`, `tracker`, or `otel` sections present in YAML
   - Canonicalize gate names in `QAGatesConfig`: accept both `structural` and `structural_check` as keys, normalize to `structural_check` internally

5. Do NOT delete `MailboxConfig`, `TrackerConfig`, `OTelConfig`, `LayerCheckConfig`, `StallEscalationConfig` classes yet — they are soft-deprecated but still importable. Mark with `# DEPRECATED: removed in v0.15` comment.

6. Each role config should have a `to_role_provider_config() -> RoleProviderConfig` method that extracts just `provider` + `timeout_seconds`.

7. Write/update tests in `tests/test_config*.py`:
   - New-format YAML loads all three role configs
   - Old-format YAML maps dispatcher → generator with deprecation log
   - Removed sections (mailbox, tracker, otel) warn but don't error
   - Gate name canonicalization (structural → structural_check)

### Acceptance Criteria

- `OrchestratorConfig` loads both new format (planner/generator/evaluator) and old format (dispatcher)
- Old YAML with `dispatcher:` still loads with deprecation warning in log
- New YAML with `planner:/generator:/evaluator:` loads cleanly
- Removed config sections log warning if present, don't error
- Gate names normalized to canonical form
- `config.generator.to_role_provider_config()` returns RoleProviderConfig
- All existing config tests pass or are updated

### Evaluator Prompt

Verify: (1) load a new-format YAML — all three role configs populated, (2) load an old-format YAML — `generator` populated from `dispatcher`, (3) removed sections don't crash, (4) `config.evaluator.pass_threshold` defaults to 80, (5) `config.generator.stall_timeout` defaults to 600, (6) gate name `structural` normalized to `structural_check`, (7) existing tests pass.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_config*.py tests/test_schema*.py tests/ -x -q --tb=short"
  timeout: 120

---

## T2b: Provider Factory Refactor

**ID:** 2b
**Depends on:** [2]
**Module:** `src/lindy_orchestrator/providers/`, `src/lindy_orchestrator/dispatch_core.py`

### Description

Refactor `create_provider()` to accept `RoleProviderConfig` instead of `DispatcherConfig`. Inline the dispatcher wrapper modules into provider implementations. Simplify dispatch_core stall detection to single timeout. This decouples providers from the old dispatcher config and enables role-specific provider creation.

### Generator Prompt

1. **Refactor `create_provider()`** in `providers/__init__.py`:
   - Change signature: `create_provider(config: RoleProviderConfig) -> DispatchProvider`
   - Add overload/compat: `create_provider(config: DispatcherConfig)` still works (extracts provider + timeout)
   - Keep the factory simple: `if config.provider == "claude_cli": return ClaudeCLIProvider(...)`

2. **Refactor `ClaudeCLIProvider`** (`providers/claude_cli.py`):
   - Remove dependency on `dispatcher.py` module — inline the `streaming_dispatch()` and `parse_event()` calls directly, or import from `dispatch_core.py`
   - Accept `RoleProviderConfig` in constructor (provider name + timeout)
   - Keep `permission_mode` and `max_output_chars` as optional kwargs (only Generator needs them)

3. **Refactor `CodexCLIProvider`** (`providers/codex_cli.py`):
   - Same pattern: remove dependency on `codex_dispatcher.py`, inline or use `dispatch_core.py`
   - Accept `RoleProviderConfig`

4. **Simplify `dispatch_core.py` stall detection:**
   - Remove two-stage `warn_after`/`kill_after` logic
   - Replace with single `stall_timeout_seconds` parameter
   - If no events for `stall_timeout_seconds` → kill the process
   - Remove `STALL_WARNING` event emission (only `STALL_KILLED` remains)
   - Keep the `long_running_tool_multiplier` (1.5x for Bash) — it's a good heuristic

5. **Keep `dispatcher.py` and `codex_dispatcher.py` files** — but they become thin shims importing from providers. Mark with `# DEPRECATED: use providers/ directly`. Don't delete yet (tests import `_parse_event` and `_read_stderr` from them).

6. **Add backward-compat re-exports** if tests import from old locations.

7. Write `tests/test_provider_factory.py`:
   - `create_provider(RoleProviderConfig(provider="claude_cli"))` returns ClaudeCLIProvider
   - `create_provider(RoleProviderConfig(provider="codex_cli"))` returns CodexCLIProvider
   - `create_provider(old_DispatcherConfig)` still works
   - Stall detection with single timeout (mock time, verify kill)

### Acceptance Criteria

- `create_provider(RoleProviderConfig(...))` works for both providers
- `create_provider(DispatcherConfig(...))` still works (backward compat)
- Providers no longer depend on `dispatcher.py`/`codex_dispatcher.py` for core logic
- dispatch_core has single stall timeout (no warn stage)
- Existing dispatcher/codex_dispatcher tests still pass (via shims)
- ≥8 unit tests in test_provider_factory.py

### Evaluator Prompt

Verify: (1) `create_provider(RoleProviderConfig(provider="claude_cli"))` returns a valid provider, (2) `create_provider(RoleProviderConfig(provider="codex_cli"))` works, (3) `grep -n "stall_escalation\|warn_after\|STALL_WARNING" src/lindy_orchestrator/dispatch_core.py` returns nothing or only comments, (4) `from lindy_orchestrator.dispatcher import _parse_event` still works (shim), (5) all existing tests pass.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_provider*.py tests/test_dispatcher*.py tests/test_codex*.py tests/ -x -q --tb=short"
  timeout: 120

---

## T3: Soft Feature Deprecation

**ID:** 3
**Depends on:** [2]
**Module:** multiple files (no file deletions)

### Description

Soft-deprecate removed features: make config sections no-op, disable code paths, but do NOT delete any files. This allows T4/T5/T6 to work against a codebase that still compiles and passes tests. Hard deletion happens in T12.

### Generator Prompt

1. **Config** — already done in T2 (warnings for removed sections). No additional work.

2. **scheduler_helpers.py** — make `inject_*` functions no-op:
   - `inject_mailbox_messages()` → add `return` at top, keep function signature
   - `inject_claude_md()` → add `return` at top (gather_* variant is the real one)
   - `inject_status_content()` → add `return` at top
   - `inject_branch_delivery()` → add `return` at top
   - Add `# DEPRECATED: no-op, will be removed in v0.15` comment to each

3. **Layer check** — disable auto-injection:
   - In `scheduler_helpers.py:inject_qa_gates()`, comment out the layer_check injection block
   - In `qa/__init__.py`, keep the `from . import layer_check` import (file still exists)

4. **Mailbox references** — no-op:
   - In `cli_status.py`, skip mailbox section rendering (add early return / `if False` guard)
   - In `hooks.py`, keep MAILBOX event types in enum (don't break deserialization)

5. **Planner API mode** — disable:
   - In `planner.py`, make `_plan_via_api()` raise `NotImplementedError("API mode removed, use CLI")`
   - In config validator (T2), if `planner.mode == "api"`, log warning and override to "cli"

6. **OTel** — disable:
   - In `scheduler.py`, wrap OTel block with `if False:` or remove the block (it's guarded by `config.otel.enabled` which is already false by default)

7. **Do NOT delete any files.** All modules remain importable.

8. Update tests:
   - Tests calling `inject_*` functions: update assertions (they now no-op)
   - Tests for layer_check: still pass (module exists, just not auto-injected)
   - Tests for planner API mode: update to expect NotImplementedError

### Acceptance Criteria

- No file deletions in this task
- All `inject_*` functions are no-ops
- Layer check not auto-injected but module still importable
- Mailbox section in cli_status skipped
- Planner API mode raises NotImplementedError
- ALL existing tests pass (updated as needed)
- `import lindy_orchestrator.mailbox` still works
- `import lindy_orchestrator.qa.layer_check` still works

### Evaluator Prompt

Verify: (1) no files deleted (`git diff --stat` shows only modifications, no deletions), (2) `inject_mailbox_messages` exists but is a no-op (returns immediately), (3) `_plan_via_api` raises NotImplementedError, (4) all tests pass, (5) `from lindy_orchestrator.mailbox import Mailbox` still works.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/ -x -q --tb=short"
  timeout: 180

---

## T4: Planner Runner

**ID:** 4
**Depends on:** [2b]
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
1. `_build_context()`: Assemble planner prompt. If `config.prompt` is non-empty, use it as template. Otherwise use default from `prompts.py`. Inject: module list, STATUS.md summaries (truncated 1500 chars each), ARCHITECTURE.md (truncated 5000 chars), available QA gates (use canonical names like `structural_check`), today's date.

2. `plan()`: Create provider via `create_provider(config.to_role_provider_config())`. Call `provider.dispatch_simple(prompt=context)`. Parse output. Set `plan.planner_cost_usd` from provider result.

3. `_parse_plan()`: Parse JSON into `TaskSpec[]`. Each task MUST have `generator_prompt`, `acceptance_criteria`, `evaluator_prompt`. Validate: IDs unique, depends_on references valid, no cycles (topological sort). On parse failure: return single-task error plan (like current behavior).

4. Update `prompts.py` `PLAN_PROMPT_TEMPLATE`:
   - Rename `prompt` field in task output to `generator_prompt`
   - Add `acceptance_criteria` field — instruct: "human-readable success criteria for this task"
   - Add `evaluator_prompt` field — instruct: "specific verification instructions for the evaluator agent — what to check, what commands to run, what to look for in the diff"
   - Keep existing instructions about dependencies, skip_qa, modules

5. Write `tests/test_planner_runner.py` — mock provider, test:
   - Prompt construction includes module list, STATUS.md, ARCHITECTURE.md
   - JSON parsing produces TaskSpec[] with all three prompt fields
   - Validation catches: duplicate IDs, invalid depends_on, cycles
   - Parse failure produces error plan
   - Custom `config.prompt` overrides default template
   - Provider created from PlannerConfig.provider (not hardcoded)

Do NOT modify `planner.py` — it stays for backward compat until T12.

### Acceptance Criteria

- `PlannerRunner.plan(goal)` returns `TaskPlan` with `tasks_v2: list[TaskSpec]`
- Each `TaskSpec` has non-empty `generator_prompt`, `acceptance_criteria`, `evaluator_prompt`
- Provider created from `PlannerConfig.provider`
- Custom `config.prompt` overrides default template
- JSON parse errors produce a meaningful single-task error plan
- Cycle detection works
- ≥12 unit tests

### Evaluator Prompt

Verify: (1) `PlannerRunner` creates provider from config, not hardcoded, (2) default prompt template includes instructions for all three output fields, (3) `_parse_plan` validates TaskSpec fields and catches cycles, (4) unit tests cover happy path + error cases, (5) existing tests unaffected.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_planner_runner.py tests/ -x -q --tb=short"
  timeout: 120

---

## T5: Generator Runner

**ID:** 5
**Depends on:** [2b]
**Module:** `src/lindy_orchestrator/generator_runner.py` (new)

### Description

Create `GeneratorRunner` — extracts dispatch + prompt building logic. Strict context isolation: sees only `generator_prompt`, CLAUDE.md (selected by `generator.provider`), module STATUS.md, branch instructions, and feedback on retry. Never sees `acceptance_criteria` or `evaluator_prompt`.

### Generator Prompt

Create `src/lindy_orchestrator/generator_runner.py`:

```python
class GeneratorRunner:
    def __init__(self, config: GeneratorConfig, project_config: OrchestratorConfig): ...
    def execute(self, task: TaskSpec, worktree: Path, branch_name: str,
                feedback: EvalFeedback | None = None,
                on_progress: Callable | None = None) -> GeneratorOutput: ...
    def _build_prompt(self, task: TaskSpec, worktree: Path, branch_name: str,
                      feedback: EvalFeedback | None) -> str: ...
```

Key behaviors:
1. `_build_prompt()`:
   - Start with `config.prompt_prefix` (from YAML, may be empty)
   - Append CLAUDE.md / CODEX.md instructions — determine which based on `config.provider` (not `project_config.dispatcher.provider`). Read from `.orchestrator/{provider_dir}/root.md` and `.orchestrator/{provider_dir}/{task.module}.md`. This is the same logic as `gather_claude_md` but reads `config.provider` instead of `project_config.dispatcher.provider`.
   - Append module STATUS.md content (from `.orchestrator/status/{task.module}.md`) — Generator needs to know current module state
   - Append `task.generator_prompt` (from Planner)
   - Append branch delivery instructions (reuse logic from `gather_branch_delivery`)
   - If retry and feedback is not None: append structured retry section with `feedback.summary`, `feedback.specific_errors`, `feedback.remediation_steps`, `feedback.failed_criteria` (if any), `feedback.files_to_check`. Format clearly.
   - **MUST NOT include `task.acceptance_criteria` or `task.evaluator_prompt`**

2. `execute()`:
   - Create provider via `create_provider(config.to_role_provider_config())`
   - Also pass `permission_mode` and `max_output_chars` to provider (GeneratorConfig-specific)
   - Dispatch with streaming via `provider.dispatch()`, passing `stall_seconds=config.stall_timeout`
   - Collect output
   - Compute diff via `git diff HEAD` in worktree (subprocess, cwd=worktree)
   - Return `GeneratorOutput(success, output, diff, cost_usd, duration_seconds, event_count, last_tool)`

3. Progress callback: The `on_progress` callback receives tool names and event counts from the provider's `on_event`. Keep it simple — extract tool name, pass to callback. No complex HeartbeatTracker.

4. Write `tests/test_generator_runner.py`:
   - Prompt construction includes generator_prompt
   - Prompt does NOT contain acceptance_criteria (assert "acceptance_criteria_text" not in prompt)
   - Prompt does NOT contain evaluator_prompt
   - CLAUDE.md selected by generator.provider, not dispatcher.provider
   - STATUS.md content included
   - Retry prompt includes feedback.summary + feedback.failed_criteria
   - Diff collection from worktree (mock subprocess)
   - Provider created from GeneratorConfig

### Acceptance Criteria

- `GeneratorRunner.execute()` returns `GeneratorOutput` with success, output, diff, cost
- Prompt NEVER contains acceptance_criteria or evaluator_prompt (test assertion with actual strings)
- CLAUDE.md/CODEX.md selected by `generator.provider`
- STATUS.md content included in prompt
- Custom `prompt_prefix` prepended
- Retry includes structured feedback including failed_criteria
- Provider created from `GeneratorConfig.provider`
- ≥14 unit tests

### Evaluator Prompt

Verify: (1) build a prompt for a TaskSpec with acceptance_criteria="must pass all tests" — assert that exact string does NOT appear in the built prompt, (2) build a prompt with config.provider="codex_cli" — assert it reads from `.orchestrator/codex/` not `.orchestrator/claude/`, (3) provider created from config, (4) STATUS.md content IS in the prompt, (5) existing tests unaffected.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_generator_runner.py tests/ -x -q --tb=short"
  timeout: 120

---

## T6: Evaluator Runner

**ID:** 6
**Depends on:** [2b]
**Module:** `src/lindy_orchestrator/evaluator_runner.py` (new)

### Description

Create `EvaluatorRunner` — two-phase evaluation: mechanical QA gates (parallel), then agent-based judgment with scoring rubric. Uses extended EvalFeedback for rich retry feedback. Handles timeout/error gracefully.

### Generator Prompt

Create `src/lindy_orchestrator/evaluator_runner.py`:

```python
class EvaluatorRunner:
    def __init__(self, config: EvaluatorConfig, project_config: OrchestratorConfig): ...
    def evaluate(self, task: TaskSpec, gen_output: GeneratorOutput,
                 worktree: Path) -> EvalResult: ...
    def _run_qa_gates(self, checks: list[QACheck], worktree: Path,
                      project_root: Path, module_name: str,
                      module_path: Path | None) -> list[QAResult]: ...
    def _run_eval_agent(self, task: TaskSpec, gen_output: GeneratorOutput,
                        qa_results: list[QAResult]) -> EvalResult: ...
    def _build_eval_prompt(self, task: TaskSpec, gen_output: GeneratorOutput,
                           qa_results: list[QAResult]) -> str: ...
```

Key behaviors:
1. `evaluate()`:
   - If `task.skip_qa`: return EvalResult(score=100, passed=True, retryable=False)
   - Run `_run_qa_gates()` — parallel via ThreadPoolExecutor
   - Check retryability: if all QA failures have `retryable=False` (pre-existing), return EvalResult(score=0, passed=False, retryable=False)
   - Run `_run_eval_agent()` — intelligent assessment
   - Return combined EvalResult

2. `_run_qa_gates()`:
   - Reuse existing `run_qa_gate()` from `qa/__init__.py`
   - Filter by `task.skip_gates`
   - Run all checks in parallel with ThreadPoolExecutor
   - Return list of QAResult

3. `_build_eval_prompt()`:
   - Start with `config.prompt_prefix` (from YAML)
   - Append scoring rubric:
     ```
     Score 90-100: All acceptance criteria met, code clean, tests pass
     Score 70-89: Most criteria met, minor issues
     Score 50-69: Some criteria met, notable gaps
     Score 30-49: Significant gaps, multiple failing criteria
     Score 0-29: Fundamental issues, wrong approach
     ```
   - Append `task.acceptance_criteria`
   - Append `task.evaluator_prompt`
   - Append `gen_output.diff` (truncated to 50K chars)
   - Append `gen_output.output` (truncated to 10K chars)
   - Append QA gate results summary (gate name, pass/fail, output truncated)
   - Instruct output format:
     ```json
     {"score": 0-100, "feedback": {"summary": "...", "specific_errors": [...], "files_to_check": [...], "remediation_steps": [...], "failed_criteria": [...], "evidence": "...", "missing_behaviors": [...]}}
     ```
   - **MUST NOT include `task.generator_prompt`**

4. `_run_eval_agent()`:
   - Create provider from `config.to_role_provider_config()`
   - Call `provider.dispatch_simple(prompt=eval_prompt)`
   - Parse JSON verdict
   - **Compute `passed` in code**: `score >= config.pass_threshold` — do NOT trust model's boolean
   - Compute `retryable` from QA results: if all failures are pre-existing (`qa_result.retryable == False`), set `retryable = False`
   - On JSON parse failure: return EvalResult(score=0, retryable=True, feedback=EvalFeedback(summary="Failed to parse evaluator output", evidence=raw_output[:500]))
   - On provider timeout/error: return EvalResult(score=0, retryable=True, feedback=EvalFeedback(summary=f"Evaluator timed out after {config.timeout_seconds}s"))

5. Write `tests/test_evaluator_runner.py`:
   - QA gates run in parallel (mock gates, check ThreadPoolExecutor usage)
   - Eval prompt contains acceptance_criteria but NOT generator_prompt
   - Score=45 with threshold=80 → passed=False
   - Score=85 with threshold=80 → passed=True
   - `passed` computed in code, not from model output
   - All QA retryable=False → EvalResult.retryable=False
   - JSON parse failure → score=0, retryable=True
   - Provider timeout → score=0, retryable=True
   - skip_qa → score=100 immediately
   - skip_gates filters correctly
   - Provider created from EvaluatorConfig

### Acceptance Criteria

- `EvaluatorRunner.evaluate()` returns EvalResult with score, passed, retryable, feedback, qa_results
- QA gates execute in parallel, filtered by skip_gates
- Eval prompt contains acceptance_criteria + evaluator_prompt + diff + qa_results + rubric
- Eval prompt does NOT contain generator_prompt (test assertion)
- `passed` computed from `score >= threshold` in code
- `retryable` derived from QA gate retryability
- Timeout/error → graceful fallback with retryable=True
- Provider created from `EvaluatorConfig.provider`
- ≥16 unit tests

### Evaluator Prompt

Verify: (1) build eval prompt for a TaskSpec with generator_prompt="implement X" — assert that string does NOT appear, (2) acceptance_criteria IS in the prompt, (3) scoring rubric IS in the prompt, (4) mock evaluator returning `{"score": 45}` → passed=False (threshold 80), (5) mock evaluator returning `{"score": 85}` → passed=True, (6) timeout → EvalResult with score=0 and retryable=True, (7) all QA retryable=False → EvalResult.retryable=False.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_evaluator_runner.py tests/ -x -q --tb=short"
  timeout: 120

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

---

## T8: Visualization Update

**ID:** 8
**Depends on:** [7]
**Module:** `src/lindy_orchestrator/dashboard.py`, `src/lindy_orchestrator/dag.py`, `src/lindy_orchestrator/web/server.py`, `src/lindy_orchestrator/reporter.py`

### Description

Update all visualization modules to consume `ExecutionResult`/`TaskState` and support the three-phase pipeline. Add phase display, evaluator scores, attempt history, and interactive controls (via CommandQueue) to the web dashboard.

### Generator Prompt

1. **Terminal Dashboard** (`dashboard.py`):
   - Subscribe to new events: `PHASE_CHANGED`, `EVAL_SCORED`
   - Update `_on_heartbeat`/event handlers to track `phase` and `last_score` per task
   - In-progress tasks annotation: `[Generate → att. 1]` or `[Evaluate → 72/100]`
   - Completed tasks annotation: score if available
   - `Dashboard` now takes `ExecutionResult` (or `list[TaskState]`) instead of `TaskPlan`
   - `_build_summary()`: include total cost

2. **DAG Renderer** (`dag.py`):
   - `_node_text()` — if task has phase info in annotation, include it
   - Accept `TaskState` list (extract spec for display, status from state)
   - No structural changes to tree layout

3. **Web Dashboard** (`web/server.py`):
   Update `_INDEX_HTML`:

   **Sidebar enhancement:**
   - Pipeline phase indicator (colored dots: Plan → Generate → Evaluate)
   - Acceptance criteria section (from TaskSpec, sent via init event)
   - Attempt history table: `[#, score, feedback summary, duration, cost]`
   - Per-phase cost breakdown (generate cost, evaluate cost)

   **New SSE event handling in JS:**
   - `phase_changed` → update node card phase indicator + annotation
   - `eval_scored` → update score display on node card, add to attempt history table

   **Interactive controls via CommandQueue:**
   - Add `<div class="controls">` with buttons: Pause, Resume, Skip, Force Pass
   - `do_POST()` handler:
     - `POST /api/pause` → `server.command_queue.pause()`
     - `POST /api/resume` → `server.command_queue.resume()`
     - `POST /api/task/{id}/skip` → `server.command_queue.skip(int(id))`
     - `POST /api/task/{id}/force-pass` → `server.command_queue.force_pass(int(id))`
     - Return 200 JSON `{"ok": true}`
   - `WebDashboard.__init__` accepts `command_queue: CommandQueue | None`
   - Buttons disabled/enabled based on task state (JS logic)

   **Init event enhancement:**
   - Include `acceptance_criteria` per task
   - Include `attempts` history for resumed sessions
   - Include `phase` per task

4. **Reporter** (`reporter.py`):
   - `generate_execution_summary()` — accept `ExecutionResult` instead of `TaskPlan`
   - Add attempt history: for each task with >1 attempt, show attempt table
   - Add cost breakdown: Planner $X, Generator $Y, Evaluator $Z, Total $T
   - `save_summary_report()` — same changes for Markdown output

5. Write `tests/test_dashboard_pipeline.py`:
   - PHASE_CHANGED event updates task annotation
   - EVAL_SCORED event updates task score
   - POST /api/pause returns 200
   - Reporter includes attempt history

### Acceptance Criteria

- Terminal dashboard shows phase + attempt + score for running tasks
- Web dashboard shows pipeline progress, acceptance criteria, attempt history
- Interactive controls work via POST → CommandQueue (not mutable server fields)
- Reporter includes attempt history and cost breakdown
- SSE events include phase_changed and eval_scored
- Init event includes acceptance_criteria per task
- ≥12 new tests

### Evaluator Prompt

Verify: (1) terminal dashboard text includes phase annotation format, (2) web HTML includes controls div with buttons, (3) `do_POST` handler exists and calls command_queue methods, (4) reporter markdown includes attempt history, (5) init SSE payload includes acceptance_criteria field, (6) no direct mutation of server state by POST handler (uses CommandQueue).

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_dashboard*.py tests/test_reporter*.py tests/ -x -q --tb=short"
  timeout: 120

---

## T9: CLI Wiring + Non-Runtime Consumers

**ID:** 9
**Depends on:** [7]
**Module:** `src/lindy_orchestrator/cli.py`, `src/lindy_orchestrator/cli_ext.py`, `src/lindy_orchestrator/cli_onboard*.py`, `src/lindy_orchestrator/discovery/`, `src/lindy_orchestrator/cli_status.py`

### Description

Wire CLI commands to the new Orchestrator. Update all non-runtime consumers: onboarding generates new YAML format, discovery templates updated, cli_status removes mailbox references.

### Generator Prompt

1. **`cli.py` — `run` command:**
   - Replace `generate_plan()` + `execute_plan()` with `Orchestrator(config).run(goal)`
   - Pass hooks, logger, on_progress, verbose, console, command_queue
   - Web dashboard: create CommandQueue, pass to both Orchestrator and WebDashboard
   - Keep `--web` / `--web-port` flags

2. **`cli.py` — `plan` command:**
   - Replace `generate_plan()` with `PlannerRunner(config.planner, config).plan(goal)`
   - Display TaskSpec[] with acceptance_criteria shown (truncated in table)

3. **`cli.py` — `resume` command:**
   - Load session checkpoint
   - Deserialize TaskState[] via `TaskState.from_dict()`
   - Call `Orchestrator.resume(states, plan)`

4. **`cli_ext.py`:**
   - Remove `issues` command function
   - Remove `run_issue` / `run-issue` command function
   - Remove `mailbox` command function
   - Remove tracker-related imports

5. **`cli_onboard*.py` + `discovery/generator.py`:**
   - Update YAML generation to produce new format:
     - `planner:` block instead of standalone `planner.mode`
     - `generator:` block instead of `dispatcher:`
     - `evaluator:` block (new)
     - Remove `mailbox:` section
     - Remove `tracker:` section
   - Update discovery templates (`discovery/templates/`) to remove references to mailbox, layer_check

6. **`cli_status.py`:**
   - Remove mailbox summary section
   - Remove mailbox imports

7. **`cli_config.py`:**
   - Update global config handling: `provider` setting maps to `generator.provider`

8. Write `tests/test_cli_pipeline.py`:
   - `run` command uses Orchestrator
   - `plan` command uses PlannerRunner, output shows acceptance_criteria
   - `resume` loads checkpoint correctly
   - Removed commands not in CLI
   - Onboard generates new YAML format

### Acceptance Criteria

- `lindy run "goal"` uses Orchestrator pipeline
- `lindy plan "goal"` uses PlannerRunner, shows acceptance_criteria
- `lindy resume` loads checkpoint via TaskState.from_dict and continues
- Removed commands (issues, run-issue, mailbox) no longer in CLI
- Onboard generates new YAML format with planner/generator/evaluator
- cli_status has no mailbox references
- Discovery templates have no mailbox/layer_check references
- ≥10 tests

### Evaluator Prompt

Verify: (1) `lindy run --help` works without error, (2) `lindy plan --help` works, (3) `grep -rn "mailbox" src/lindy_orchestrator/cli_status.py` returns nothing, (4) `grep -rn "mailbox\|layer_check" src/lindy_orchestrator/discovery/templates/` returns nothing, (5) onboard YAML output contains `generator:` not `dispatcher:`, (6) all tests pass.

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

Write integration tests that verify the full pipeline with mocked providers. Test DAG ordering, retry loops, context isolation, retryable semantics, command queue, session checkpoint/resume, dashboard events.

### Generator Prompt

Create `tests/test_integration_pipeline.py`:

1. **test_full_pipeline_happy_path:**
   - Mock planner returns 2-task DAG (T1 independent, T2 depends on T1)
   - Mock generator returns success for both
   - Mock evaluator returns score=90 for both
   - Assert: T1 completes before T2 starts, both COMPLETED

2. **test_retry_loop:**
   - Mock evaluator returns score=30 (retryable=True) on first call, score=85 on second
   - Assert: generator called twice, feedback from first eval passed to second generate
   - Assert: task ends COMPLETED after 2 attempts

3. **test_max_retries_exceeded:**
   - Mock evaluator always returns score=20, retryable=True
   - max_retries=2
   - Assert: generator called 3 times, task ends FAILED

4. **test_not_retryable:**
   - Mock evaluator returns score=10, retryable=False
   - Assert: generator called once, task ends FAILED immediately

5. **test_context_isolation_generator:**
   - Capture the prompt passed to generator provider
   - Assert: prompt contains generator_prompt text
   - Assert: prompt does NOT contain acceptance_criteria text
   - Assert: prompt does NOT contain evaluator_prompt text
   - Assert: prompt DOES contain STATUS.md content

6. **test_context_isolation_evaluator:**
   - Capture the prompt passed to evaluator provider
   - Assert: prompt contains acceptance_criteria + evaluator_prompt
   - Assert: prompt does NOT contain generator_prompt text
   - Assert: prompt contains scoring rubric

7. **test_parallel_execution:**
   - 3-task DAG: T1, T2, T3 all independent
   - Use threading.Barrier(3) inside mock generator — all three must reach barrier (proves parallel)
   - Assert: all three COMPLETED

8. **test_dependency_ordering:**
   - T1 → T2 → T3 chain
   - Assert: completion order is T1, T2, T3

9. **test_signal_handling:**
   - Send SIGINT during execution
   - Assert: running task completes, pending tasks marked appropriately

10. **test_command_queue_pause_resume:**
    - Pause after T1 completes, verify T2 doesn't start
    - Resume, verify T2 starts

11. **test_command_queue_skip:**
    - Skip T2 before it starts
    - Assert: T2 status is SKIPPED, T3 (depends on T2) is SKIPPED

12. **test_command_queue_force_pass:**
    - Mock evaluator returns score=30 for T1
    - Force-pass T1 during retry
    - Assert: T1 ends COMPLETED

13. **test_session_checkpoint_resume:**
    - Run pipeline, save checkpoint after T1 completes
    - Create new Orchestrator, resume from checkpoint
    - Assert: T1 not re-executed (status already COMPLETED), T2 executes

14. **test_dashboard_events:**
    - Collect all emitted events via hooks
    - Assert order: SESSION_START, TASK_STARTED, PHASE_CHANGED(generating), PHASE_CHANGED(evaluating), EVAL_SCORED, TASK_COMPLETED, SESSION_END

15. **test_different_providers_per_role:**
    - Config: planner=claude_cli, generator=codex_cli, evaluator=claude_cli
    - Assert: each runner creates the correct provider

16. **test_eval_feedback_passed_to_generator:**
    - Mock evaluator returns EvalFeedback with failed_criteria=["tests must pass"], evidence="3 tests failed"
    - Capture generator prompt on retry
    - Assert: "tests must pass" appears in retry prompt
    - Assert: "3 tests failed" appears in retry prompt

### Acceptance Criteria

- All 16 integration tests pass
- Context isolation verified with actual prompt content assertions
- Retry + retryable semantics verified
- Parallel execution verified with barrier (not timing)
- Command queue tested (pause, skip, force_pass)
- Checkpoint/resume tested
- No flaky tests

### Evaluator Prompt

Verify: (1) all 16 tests exist and pass, (2) context isolation tests use actual string assertions, (3) parallel test uses barrier not sleep, (4) tests use mock providers, (5) no imports from deleted/deprecated modules.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_integration_pipeline.py -x -v --tb=short"
  timeout: 180

---

## T11: End-to-End Tests

**ID:** 11
**Depends on:** [10]
**Module:** `tests/`

### Description

E2E tests with fixture projects, real git operations, and mock CLI binaries. Exercise the full code path including file I/O, config loading, and process management.

### Generator Prompt

Create `tests/test_e2e_pipeline.py`:

1. **test_e2e_plan_and_execute:**
   - Create fixture project in tmp_path: orchestrator.yaml (new format), two module dirs, git init
   - Mock `claude` binary returns canned plan JSON with generator_prompt + acceptance_criteria + evaluator_prompt
   - Mock `claude` binary for generator: creates a file, commits
   - Mock `claude` binary for evaluator: returns `{"score": 90, "feedback": {"summary": "looks good", "specific_errors": [], "failed_criteria": [], "evidence": "all tests pass", "missing_behaviors": []}}`
   - Run `Orchestrator(config).run("add a feature")`
   - Assert: plan created, tasks executed, worktrees created/cleaned, session saved, ExecutionResult returned

2. **test_e2e_retry_with_feedback:**
   - Same fixture, mock evaluator returns score=40 first (with failed_criteria=["must add tests"]), score=85 second
   - Assert: generator prompt on second call includes "must add tests"
   - Assert: generator prompt on second call does NOT include acceptance_criteria
   - Assert: task completes after 2 attempts

3. **test_e2e_config_backward_compat:**
   - Create fixture with old-format YAML (dispatcher: instead of generator:)
   - Assert: loads correctly, plan and execute work

4. **test_e2e_web_dashboard_sse:**
   - Start Orchestrator with WebDashboard
   - Connect to SSE endpoint via urllib
   - Assert: init event contains tasks with acceptance_criteria
   - Assert: phase_changed events fire
   - Assert: eval_scored events fire with scores

5. **test_e2e_session_resume:**
   - Run orchestrator, interrupt after first task
   - Resume from session file
   - Assert: first task not re-dispatched, second task executes

6. **test_e2e_checkpoint_serialization_roundtrip:**
   - Run pipeline to completion
   - Load checkpoint JSON
   - Deserialize all TaskState via from_dict
   - Assert: all fields preserved, nested AttemptRecord intact

### Acceptance Criteria

- All 6 e2e tests pass
- Tests use real file I/O and real git operations (in tmp_path)
- Tests verify full pipeline from config load → plan → generate → evaluate → report
- Backward compatibility with old config format verified
- Context isolation asserted at e2e level
- Checkpoint serialization roundtrip verified
- No test takes >30s

### Evaluator Prompt

Verify: (1) all 6 tests pass, (2) tests create real git repos in tmp_path, (3) all `claude` binaries are mocked, (4) context isolation asserted (grep generator prompt for absence of acceptance_criteria), (5) checkpoint roundtrip test asserts field-level equality.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_e2e_pipeline.py -x -v --tb=short"
  timeout: 180

---

## T12: Hard Delete + Cleanup + PR

**ID:** 12
**Depends on:** [11]
**Module:** entire codebase

### Description

Final cleanup: hard-delete all deprecated files and code, remove old models, update all imports, verify all tests pass, create PR.

### Generator Prompt

1. **Delete deprecated files:**
   - `src/lindy_orchestrator/scheduler.py`
   - `src/lindy_orchestrator/planner.py`
   - `src/lindy_orchestrator/dispatcher.py`
   - `src/lindy_orchestrator/codex_dispatcher.py`
   - `src/lindy_orchestrator/mailbox.py`
   - `src/lindy_orchestrator/qa/layer_check.py`
   - `src/lindy_orchestrator/trackers/` (entire directory)
   - `src/lindy_orchestrator/otel.py` (if exists)

2. **Clean up models.py:**
   - Remove `TaskItem` class
   - Remove `tasks_v2` transitional field from `TaskPlan` — rename `tasks` to `list[TaskSpec]`
   - Remove any backward-compat aliases
   - Keep: TaskStatus, QACheck, QAResult, DispatchResult (still used by providers)
   - Remove `StructuredFeedback` if fully replaced by `EvalFeedback`

3. **Clean up scheduler_helpers.py:**
   - Remove all `inject_*` functions (already no-op'd in T3)
   - Remove all `gather_*` functions (now in runners)
   - Remove `inject_qa_gates` (now in evaluator_runner)
   - If file is now nearly empty, delete it and move any remaining utilities to `helpers.py` or inline

4. **Clean up config.py:**
   - Remove `MailboxConfig`, `TrackerConfig`, `OTelConfig`, `LayerCheckConfig`, `StallEscalationConfig`
   - Remove `DispatcherConfig` (or keep as thin alias if needed for migration)
   - Remove backward-compat validators for old sections

5. **Clean up hooks.py:**
   - Remove `MAILBOX_*` event types if they exist
   - Remove `STALL_WARNING` event type (only STALL_KILLED remains)

6. **Clean up discovery/:**
   - `discovery/generator.py`: remove mailbox directory creation
   - `discovery/templates/agent_docs.py`: remove layer_check/mailbox references

7. **Update all imports:**
   - `grep -rn "from.*scheduler import\|from.*planner import\|from.*dispatcher import\|from.*mailbox import\|from.*layer_check import\|from.*trackers import" src/ tests/`
   - Fix each broken import

8. **Delete obsolete test files:**
   - `tests/test_scheduler*.py`
   - `tests/test_planner*.py` (old planner tests)
   - `tests/test_dispatcher*.py`
   - `tests/test_codex_dispatcher*.py`
   - `tests/test_inject_claude_md.py`
   - `tests/test_layer_check*.py`
   - `tests/test_mailbox*.py`
   - `tests/test_tracker*.py`
   - `tests/test_otel*.py`
   - Any other tests that only test deleted code

9. **Final full test suite run:**
   - `uv run python -m pytest tests/ -x -q --tb=short`
   - Verify no import errors, no test failures

10. **Update README.md:**
    - Document new YAML format with planner/generator/evaluator
    - Document pipeline architecture (brief)
    - Remove references to deleted features
    - Update CLI command list

11. **Version bump:** `pyproject.toml` → `0.15.0`

12. **Create PR** with title: `feat: pipeline architecture — Planner/Generator/Evaluator role separation`

### Acceptance Criteria

- All deleted files gone: `ls src/lindy_orchestrator/scheduler.py` → not found
- No import of deleted modules: `grep -rn "from.*scheduler import\|from.*planner import\|from.*dispatcher import\|from.*mailbox import" src/` → nothing
- No `TaskItem` in non-test code: `grep -rn "TaskItem" src/` → nothing
- No `inject_*` functions: `grep -rn "inject_mailbox\|inject_claude_md\|inject_status_content\|inject_branch_delivery" src/` → nothing
- All tests pass
- README updated
- Version is 0.15.0
- PR created, CI passes

### Evaluator Prompt

Verify: (1) `grep -rn "from.*scheduler import" src/` returns nothing, (2) `grep -rn "TaskItem" src/` returns nothing, (3) `grep -rn "from.*planner import" src/` returns nothing (only planner_runner), (4) `grep -rn "from.*mailbox import" src/` returns nothing, (5) all tests pass, (6) README documents pipeline architecture, (7) version is 0.15.0, (8) `python -c "import lindy_orchestrator"` works without error.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/ -x -q --tb=short"
  timeout: 300
