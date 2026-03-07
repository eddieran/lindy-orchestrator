# lindy-orchestrator

[![CI](https://github.com/eddieran/lindy-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/eddieran/lindy-orchestrator/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/lindy-orchestrator.svg)](https://pypi.org/project/lindy-orchestrator/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Lightweight, git-native multi-agent orchestration framework for autonomous project execution.

lindy-orchestrator decomposes natural-language goals into dependency-ordered tasks, dispatches them to [Claude Code](https://docs.anthropic.com/en/docs/claude-code) agents working in isolated module directories, validates results through pluggable QA gates, and coordinates everything through markdown files and git.

**No database. No shared memory. No infrastructure. Just git, markdown, and your existing project.**

## Why

Multi-module projects need coordination. Telling an LLM agent to "add user authentication" across a backend and frontend requires decomposing the goal, ordering dependencies, dispatching work to the right directory, and verifying the result. lindy-orchestrator automates this entire loop:

```
Goal (natural language)
  → LLM decomposes into task DAG
    → Parallel dispatch to module agents
      → QA gates validate each result
        → Retry with feedback on failure
          → Final report
```

Each module stays isolated (its own `CLAUDE.md`, `STATUS.md`, working directory). The orchestrator is the only thing that sees the whole picture.

## Install

```bash
# From PyPI
pip install lindy-orchestrator

# With Anthropic API support (optional)
pip install lindy-orchestrator[api]

# From source
git clone https://github.com/eddieran/lindy-orchestrator.git
cd lindy-orchestrator
pip install -e ".[dev]"
```

**Requirements:** Python 3.11+ and [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) in `PATH`.

## Quick Start

```bash
cd my-project

# Option A: quick scaffold (auto-detects modules)
lindy-orchestrate init

# Option B: deep onboard (generates CLAUDE.md, CONTRACTS.md, STATUS.md)
lindy-orchestrate onboard

# Preview the task plan
lindy-orchestrate plan "Add user authentication with JWT"

# Execute
lindy-orchestrate run "Add user authentication with JWT"
```

See [docs/USAGE.md](docs/USAGE.md) for the full usage guide.

## How It Works

```
1. Read all module STATUS.md files (current state)
2. Send goal + context to LLM → JSON task plan with dependency DAG
3. Scheduler dispatches ready tasks in parallel (respecting dependencies)
4. Each task: Claude Code agent works in module directory → commits → pushes
5. QA gates validate each result (CI status, shell commands, agent review)
6. On QA failure: augment prompt with feedback, retry (up to N times)
7. On dependency failure: skip downstream tasks, continue independent ones
8. Generate execution report
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `init` | Quick scaffold — detect modules, generate `orchestrator.yaml` and `STATUS.md` |
| `onboard` | Deep onboard — static analysis, interactive Q&A, full artifact generation |
| `run <goal>` | Decompose and execute a goal with parallel dispatch and QA gates |
| `plan <goal>` | Generate a task plan without executing (use `-o plan.json` to save) |
| `status` | Show module health, active tasks, blockers, and recent logs (`--json` for machine output) |
| `logs` | Show recent action logs (`-n 50` for count, `--json` for raw JSONL) |
| `resume` | Resume a previous session (latest or by session ID) |
| `validate` | Validate config, module paths, STATUS.md, and Claude CLI availability |
| `gc` | Clean up stale branches, old sessions, oversized logs (`--apply` to execute) |
| `scan` | Scan for entropy: architecture drift, contract violations, quality decay |
| `issues` | List issues from the configured tracker (`--label`, `--status`, `--json`) |
| `run-issue <id>` | Fetch an issue from the tracker and execute it as a goal |
| `mailbox [module]` | View or send inter-agent mailbox messages (`--send-to`, `-m`) |

All commands accept `-c path/to/orchestrator.yaml` to specify a config file.

## Configuration

`orchestrator.yaml` in your project root:

```yaml
project:
  name: "my-project"
  branch_prefix: "af"              # task branches: af/task-1, af/task-2, ...

modules:
  - name: backend
    path: backend/
    repo: myorg/my-backend          # GitHub slug (required for ci_check gate)
    ci_workflow: ci.yml
  - name: frontend
    path: frontend/
    repo: myorg/my-frontend

planner:
  mode: cli                         # "cli" uses claude -p; "api" uses Anthropic SDK

dispatcher:
  timeout_seconds: 1800             # hard timeout per task dispatch (30 min)
  stall_timeout_seconds: 600        # no-output stall detection (10 min)
  permission_mode: bypassPermissions

qa_gates:
  custom:
    - name: pytest
      command: "pytest --tb=short -q"
      cwd: "{module_path}"          # resolved to module's absolute path

safety:
  dry_run: false
  max_retries_per_task: 2
  max_parallel: 3

tracker:
  enabled: false                      # enable issue tracker integration
  provider: github                    # "github" or "linear"
  repo: myorg/my-project             # GitHub repo slug
  sync_on_complete: true              # auto-comment and close on completion

mailbox:
  enabled: false                      # enable inter-agent messaging
  inject_on_dispatch: true            # auto-inject pending messages into prompts
```

## Key Concepts

### Modules

Independent directories in your project (services, packages, microservices). Each module gets its own `STATUS.md`, `CLAUDE.md`, and isolated agent workspace. Auto-detected by marker files: `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, etc.

### STATUS.md

Human-readable, git-diffable state file per module. Tracks active work, completed tasks, backlogs, cross-module requests, key metrics, and blockers. The orchestrator reads these programmatically; agents and humans read and write them in natural language.

### CONTRACTS.md

Single source of truth for cross-module interfaces. Generated by `onboard` when module coupling is moderate or higher. Defines API contracts, shared schemas, task ID conventions, and the CI delivery protocol.

### QA Gates

Pluggable validation that runs after each task dispatch:

| Gate | Description |
|------|-------------|
| `structural_check` | File size limits, sensitive file detection, import boundary enforcement |
| `layer_check` | Intra-module layer ordering (parsed from ARCHITECTURE.md) |
| `ci_check` | Polls GitHub Actions workflow status via `gh` CLI |
| `command_check` | Runs arbitrary shell commands (exit code 0 = pass) |
| `agent_check` | Dispatches a separate QA agent for semantic validation |
| Custom YAML | User-defined command gates in `orchestrator.yaml` |

On failure, the orchestrator augments the original prompt with QA feedback and retries up to `max_retries_per_task` times.

### Dispatch Modes

| Mode | Function | Use Case |
|------|----------|----------|
| Streaming | `dispatch_agent()` | Long tasks — real-time heartbeat, stall detection, event callbacks |
| Blocking | `dispatch_agent_simple()` | Short tasks — plan generation, reports, no thread overhead |

The streaming dispatcher monitors agent output with a 10-minute stall floor (never kills before 10 min of silence, regardless of config) and a configurable hard timeout as a safety net.

### Sessions

Persistent execution state stored as JSON in `.orchestrator/sessions/`. Supports pause and resume across terminal sessions via `lindy-orchestrate resume`.

## Architecture

```
src/lindy_orchestrator/
├── cli.py                  # Typer CLI entry point
├── cli_ext.py              # Extension commands (gc, scan, validate, issues, etc.)
├── cli_helpers.py          # Shared CLI helper functions
├── cli_onboard.py          # Onboard/init command registration
├── config.py               # YAML config loading + Pydantic validation
├── models.py               # Core data models (TaskPlan, TaskItem, QACheck, ...)
├── dag.py                  # DAG utilities for task dependency resolution
├── dashboard.py            # Rich live dashboard for execution monitoring
├── dispatcher.py           # Claude CLI subprocess management (streaming + blocking)
├── gc.py                   # Garbage collection (stale branches, old sessions, logs)
├── hooks.py                # Hook registry for event-driven callbacks
├── logger.py               # Append-only JSONL audit trail
├── mailbox.py              # Inter-agent mailbox messaging system
├── planner.py              # Goal → TaskPlan decomposition via LLM
├── prompts.py              # LLM prompt templates (Jinja2)
├── reporter.py             # Rich console output formatting
├── scheduler.py            # DAG-based parallel execution with retry logic
├── session.py              # Session state persistence and resume
├── qa/
│   ├── __init__.py         # Gate registry and runner
│   ├── structural_check.py # File size, sensitive files, import boundaries
│   ├── layer_check.py      # Intra-module layer ordering
│   ├── ci_check.py         # GitHub Actions CI polling
│   ├── command_check.py    # Shell command execution
│   ├── agent_check.py      # Agent-based semantic validation
│   └── feedback.py         # QA failure feedback formatting
├── status/
│   ├── parser.py           # STATUS.md → structured data
│   ├── templates.py        # STATUS.md generation
│   └── writer.py           # Surgical markdown table updates
├── entropy/
│   └── scanner.py          # Architecture drift and quality decay detection
├── trackers/
│   ├── base.py             # Tracker interface
│   ├── factory.py          # Tracker provider factory
│   └── github_issues.py    # GitHub Issues integration
├── dispatchers/
│   ├── base.py             # Dispatcher provider interface
│   └── claude_cli.py       # Claude CLI dispatcher implementation
└── discovery/
    ├── analyzer.py         # Static project analysis (tech stack, dependencies)
    ├── interview.py        # Interactive and non-interactive onboarding Q&A
    ├── generator.py        # Artifact generation (CLAUDE.md, CONTRACTS.md, ...)
    └── templates/          # Jinja2 template renderers
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint and format
ruff check src/ tests/
ruff format src/ tests/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

## License

[MIT](LICENSE)
