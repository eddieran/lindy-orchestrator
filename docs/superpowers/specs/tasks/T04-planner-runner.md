---
task: T4
title: Planner Runner
depends_on: [T2b]
status: pending
---

# T4: Planner Runner

**ID:** 4
**Depends on:** [2b]
**Module:** `src/lindy_orchestrator/planner_runner.py` (new)

## Description

Create `PlannerRunner` — extracts planning logic from `planner.py` into the new role-based runner. Uses `PlannerConfig` for provider selection and prompt. Outputs `TaskPlan` with `TaskSpec[]`.

## Generator Prompt

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

## Acceptance Criteria

- `PlannerRunner.plan(goal)` returns `TaskPlan` with `tasks_v2: list[TaskSpec]`
- Each `TaskSpec` has non-empty `generator_prompt`, `acceptance_criteria`, `evaluator_prompt`
- Provider created from `PlannerConfig.provider`
- Custom `config.prompt` overrides default template
- JSON parse errors produce a meaningful single-task error plan
- Cycle detection works
- ≥12 unit tests

## Evaluator Prompt

Verify: (1) `PlannerRunner` creates provider from config, not hardcoded, (2) default prompt template includes instructions for all three output fields, (3) `_parse_plan` validates TaskSpec fields and catches cycles, (4) unit tests cover happy path + error cases, (5) existing tests unaffected.

## QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_planner_runner.py tests/ -x -q --tb=short"
  timeout: 120
