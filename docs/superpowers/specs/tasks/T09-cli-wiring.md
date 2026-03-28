---
task: T9
title: CLI Wiring + Non-Runtime Consumers
depends_on: [7]
status: pending
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
- >=10 tests

### Evaluator Prompt

Verify: (1) `lindy run --help` works without error, (2) `lindy plan --help` works, (3) `grep -rn "mailbox" src/lindy_orchestrator/cli_status.py` returns nothing, (4) `grep -rn "mailbox\|layer_check" src/lindy_orchestrator/discovery/templates/` returns nothing, (5) onboard YAML output contains `generator:` not `dispatcher:`, (6) all tests pass.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_cli*.py tests/ -x -q --tb=short"
  timeout: 120
