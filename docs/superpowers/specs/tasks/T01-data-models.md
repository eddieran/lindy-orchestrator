---
task: T1
title: Data Models + Serialization
depends_on: none
status: pending
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
