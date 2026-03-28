---
task: T9
title: CLI Wiring + Non-Runtime Consumers
depends_on: [7]
status: pending
---

## T9: CLI Wiring + Non-Runtime Consumers

## Context & Prerequisites

**Architecture spec:** `docs/superpowers/specs/2026-03-28-pipeline-architecture-design.md` — read this first for full design context.

**Tech stack:**
- Models: Python dataclasses (`from dataclasses import dataclass, field`)
- Config: Pydantic v2 (`from pydantic import BaseModel, model_validator`)
- Testing: pytest via `uv run python -m pytest`
- Python 3.11+, type hints throughout

**Project structure:** All source in `src/lindy_orchestrator/`, tests in `tests/`.

**Prior task outputs:**
- T7: `Orchestrator` class in `orchestrator.py`:
  ```python
  class Orchestrator:
      def __init__(self, config: OrchestratorConfig, hooks: HookRegistry | None = None,
                   logger: ActionLogger | None = None, on_progress: Callable | None = None,
                   verbose: bool = False, command_queue: CommandQueue | None = None): ...
      def run(self, goal: str) -> ExecutionResult: ...
      def resume(self, states: list[TaskState], plan: TaskPlan) -> ExecutionResult: ...
  ```
- T7: `CommandQueue` class (also in `orchestrator.py`)
- T4: `PlannerRunner` in `planner_runner.py`:
  ```python
  class PlannerRunner:
      def __init__(self, config: PlannerConfig, project_config: OrchestratorConfig): ...
      def plan(self, goal: str) -> TaskPlan: ...
  ```
- T8: `WebDashboard` updated to accept `command_queue` parameter
- T1: `TaskState` with `to_dict()`/`from_dict()`, `ExecutionResult`

**CLI framework:** The project uses `click` (see `cli.py` imports). Commands are decorated with `@click.command()` and grouped with `@click.group()`.

**Current run command pattern (in `cli.py`):**
```python
@cli.command()
@click.argument("goal")
@click.option("--web/--no-web", ...)
def run(goal, web, ...):
    config = load_config(config_path)
    plan = generate_plan(goal, config, ...)
    result = execute_plan(plan, config, logger, ...)
```
Replace `generate_plan()` + `execute_plan()` with `Orchestrator(config, ...).run(goal)`.

**Session checkpoint format (from `session.py`):**
```python
class SessionManager:
    def save(self, session_id, data: dict): ...  # writes to {session_dir}/{session_id}.json
    def load(self, session_id) -> dict | None: ...
    def load_latest(self) -> tuple[str, dict] | None: ...
```
For resume: load checkpoint → deserialize each task via `TaskState.from_dict()` → call `orchestrator.resume(states, plan)`.

**YAML generation (discovery/generator.py):**
Current code generates YAML with `planner:` and `dispatcher:` blocks. Change `dispatcher:` to `generator:`, add `evaluator:` block, remove `mailbox:` and `tracker:` sections. Look for the `_generate_config_yaml()` or similar function.

**Files with mailbox references to clean (from grep):**
- `cli_status.py` — `_collect_mailbox_data()` function + mailbox import
- `discovery/generator.py` — `(orch_dir / "mailbox").mkdir(...)` line
- `discovery/templates/agent_docs.py` — mailbox documentation reference

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
- >=10 tests

### Evaluator Prompt

Verify: (1) `lindy run --help` works without error, (2) `lindy plan --help` works, (3) `grep -rn "mailbox" src/lindy_orchestrator/cli_status.py` returns nothing, (4) `grep -rn "mailbox\|layer_check" src/lindy_orchestrator/discovery/templates/` returns nothing, (5) onboard YAML output contains `generator:` not `dispatcher:`, (6) all tests pass.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_cli*.py tests/ -x -q --tb=short"
  timeout: 120
