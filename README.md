# lindy-orchestrator

Lightweight, git-native multi-agent orchestration for repository work.

## Overview

`lindy-orchestrator` coordinates planning, execution, and validation around your existing code agents instead of trying to replace them. The current architecture is organized around a Planner, Generator, and Evaluator split:

```text
Planner -> TaskSpec[] -> Generator -> GeneratorOutput -> Evaluator -> pass/fail + feedback
```

The orchestrator owns dependency ordering, worktree isolation, retries, QA gates, checkpointing, and reporting. Agents work against the repo; the orchestrator handles the harness.

## Install

```bash
pip install lindy-orchestrator
pip install -e ".[dev]"
```

Requirements:

- Python 3.11+
- `claude` or `codex` in `PATH`

## Quick Start

```bash
lindy-orchestrate onboard
lindy-orchestrate plan "Add JWT auth"
lindy-orchestrate run "Add JWT auth"
lindy-orchestrate resume
```

## Pipeline

The intended flow is:

1. Planner reads project context and module status files.
2. Planner emits `TaskSpec` items with generator and evaluator instructions.
3. Generator executes a task in an isolated worktree and returns output plus diff context.
4. Evaluator runs QA gates and semantic review, then either passes the task or returns retry feedback.
5. Orchestrator advances the task DAG until all reachable tasks are complete.

Key runtime pieces:

- `planner_runner.py`
- `orchestrator.py`
- `providers/claude_cli.py`
- `providers/codex_cli.py`
- `qa/`

## Configuration

Project configuration lives in `.orchestrator/config.yaml`.

Example:

```yaml
project:
  name: "my-project"
  branch_prefix: "af"

modules:
  - name: backend
    path: backend/
  - name: frontend
    path: frontend/

planner:
  provider: claude_cli
  timeout_seconds: 120
  prompt: |
    You are the planner for {project_name}.

generator:
  provider: claude_cli
  timeout_seconds: 1800
  stall_timeout: 600
  permission_mode: bypassPermissions
  prompt_prefix: |
    You are a code generation agent.

evaluator:
  provider: claude_cli
  timeout_seconds: 300
  pass_threshold: 80
  prompt_prefix: |
    You are a code evaluation agent.

qa_gates:
  ci_check:
    enabled: true
  structural_check:
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

Required concepts:

- `project`
- `modules`
- planner/generator/evaluator role configuration
- `qa_gates`
- `safety`
- `logging`

## CLI

Primary commands:

- `run`: plan and execute a goal
- `plan`: generate a task plan only
- `resume`: continue a saved session
- `status`: module overview plus recent logs
- `logs`: alias for `status --logs-only`
- `validate`: validate config and module paths
- `gc`: clean stale branches, sessions, and logs
- `scan`: run entropy checks
- `onboard`: generate `.orchestrator/` project scaffolding
- `config`: manage provider defaults
- `stats`: inspect execution metrics
- `clear`: remove generated orchestration files
- `version`: print version information

Examples:

```bash
lindy-orchestrate run "Implement API auth" --provider codex_cli
lindy-orchestrate plan --file goal.md
lindy-orchestrate status --json
lindy-orchestrate validate
lindy-orchestrate gc --apply
```

## QA Gates

Built-in gates:

- `ci_check`
- `command_check`
- `agent_check`
- `structural_check`

Custom QA commands can be added under `qa_gates.custom`.

## Session Files

The orchestrator writes state under `.orchestrator/`, including:

- `config.yaml`
- `status/`
- `logs/`
- `sessions/`
- `plans/`
- `reports/`
- `claude/` and `codex/` instruction files

## Development

Common commands:

```bash
uv sync --extra dev --frozen
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run python -m pytest tests/ -x -q --tb=short
uv run python -c "import lindy_orchestrator"
```

## License

MIT
