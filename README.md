# lindy-orchestrator

[![CI](https://github.com/eddieran/lindy-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/eddieran/lindy-orchestrator/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/lindy-orchestrator.svg)](https://pypi.org/project/lindy-orchestrator/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Lightweight, git-native multi-agent orchestration framework for autonomous project execution.

Decomposes natural-language goals into dependency-ordered task DAGs, dispatches them to AI coding agents in isolated module directories, validates through pluggable QA gates, and coordinates via markdown and git. No database, no shared memory — just git and your existing project.

---

## Table of Contents

- [Install](#install)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [CLI Reference](#cli-reference)
- [Configuration](#configuration)
- [Provider System](#provider-system)
- [QA Gates](#qa-gates)
- [Hook / Event System](#hook--event-system)
- [Live DAG Dashboard](#live-dag-dashboard)
- [Inter-Agent Mailbox](#inter-agent-mailbox)
- [Entropy Scanner](#entropy-scanner)
- [Garbage Collection](#garbage-collection)
- [Issue Tracker Integration](#issue-tracker-integration)
- [Structured QA Feedback](#structured-qa-feedback)
- [Execution Summary Reports](#execution-summary-reports)
- [Key Concepts](#key-concepts)
- [Architecture](#architecture)
- [Development](#development)
- [License](#license)

---

## Install

```bash
pip install lindy-orchestrator            # PyPI
pip install lindy-orchestrator[api]       # with Anthropic API support
git clone https://github.com/eddieran/lindy-orchestrator.git && cd lindy-orchestrator
pip install -e ".[dev]"                   # from source
```

**Requirements:** Python 3.11+, [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (default provider) or [OpenAI Codex CLI](https://github.com/openai/codex) (alternative provider) in PATH.

---

## Quick Start

```bash
lindy-orchestrate onboard                              # detect modules, generate config
lindy-orchestrate plan "Add user authentication"       # preview task plan
lindy-orchestrate run "Add user authentication"        # execute with full orchestration
lindy-orchestrate run "Add auth" --provider codex_cli  # use Codex CLI instead of Claude
```

---

## How It Works

```
1. Read module STATUS.md files (current state)
2. LLM decomposes goal into JSON task DAG with dependencies
3. Scheduler dispatches ready tasks in parallel
4. Each task: agent works in module dir -> commits -> pushes to task branch
5. QA gates validate (CI, shell commands, structural checks, agent review)
6. On failure: structured remediation feedback -> retry (up to N times)
7. Generate execution summary report with per-task metrics
```

---

## CLI Reference

All commands accept `-c path/to/orchestrator.yaml` to specify a config file.

| Command | Description |
|---------|-------------|
| `run` | Execute a goal: plan, dispatch, QA, retry, report |
| `plan` | Generate a task plan without executing |
| `resume` | Resume a previous session from checkpoint |
| `onboard` | Unified onboarding (scaffold / init+onboard / re-onboard) |
| `init` | Quick scaffold: detect modules, generate config |
| `status` | Module health, mailbox summary, recent logs |
| `logs` | Alias for `status --logs-only` |
| `validate` | Validate config, module paths, CLI availability |
| `gc` | Clean stale branches, old sessions, oversized logs |
| `scan` | Entropy scanner: drift, contracts, quality grades |
| `issues` | List issues from configured tracker |
| `run-issue` | Fetch issue and execute as orchestration goal |
| `mailbox` | View or send inter-agent messages |
| `version` | Print version (`--json` for JSON, `-V` flag also works) |

### `run`

```bash
lindy-orchestrate run "Add JWT auth"
lindy-orchestrate run --file goal.md              # goal from file (- for stdin)
lindy-orchestrate run --plan plan.json            # execute saved plan, skip planning
lindy-orchestrate run --dry-run --verbose         # analyze only, detailed output
lindy-orchestrate run --provider codex_cli        # use Codex CLI
```

| Flag | Short | Description |
|------|-------|-------------|
| `--file` | `-f` | Read goal from file; `-` for stdin |
| `--plan` | `-p` | Execute saved plan JSON (skip LLM planning) |
| `--dry-run` | | Analyze only, no agent dispatch |
| `--verbose` | `-v` | Show tool use events and detailed output |
| `--provider` | | Dispatch provider: `claude_cli` (default) or `codex_cli` |

### `plan`

```bash
lindy-orchestrate plan "Add JWT auth"
lindy-orchestrate plan --file goal.md -o plan.json
```

Plans auto-save to `.orchestrator/plans/` and can be executed later with `run --plan`.

| Flag | Short | Description |
|------|-------|-------------|
| `--file` | `-f` | Read goal from file |
| `--output` | `-o` | Save plan JSON to specific path |

### `resume`

```bash
lindy-orchestrate resume                # resume latest session
lindy-orchestrate resume abc12345       # resume specific session ID
```

Skips completed tasks, resets failed tasks to pending, re-executes remaining.

### `onboard`

Detects project state and adapts: empty project -> scaffold (LLM-driven, requires description); existing project without config -> init+onboard (detect, interview, generate); already onboarded -> re-onboard (update).

```bash
lindy-orchestrate onboard                           # auto-detect mode
lindy-orchestrate onboard "A SaaS platform"         # scaffold empty project
lindy-orchestrate onboard --depth 2 -y --force      # deep scan, non-interactive
```

| Flag | Short | Description |
|------|-------|-------------|
| `--file` | `-f` | Project description from file (scaffold mode) |
| `--depth` | | Module scan depth (default: 1) |
| `--non-interactive` | `-y` | Skip prompts, use defaults |
| `--force` | | Overwrite existing files |

### `init`

Quick scaffold: detect modules, generate `orchestrator.yaml` and `STATUS.md` files.

```bash
lindy-orchestrate init --modules backend,frontend --depth 2 --no-status
```

| Flag | Short | Description |
|------|-------|-------------|
| `--modules` | `-m` | Comma-separated module names (skip auto-detect) |
| `--depth` | | Scan depth (default: 1) |
| `--no-status` | | Skip STATUS.md creation |
| `--force` | | Overwrite existing files |

### `status`

```bash
lindy-orchestrate status                  # full overview
lindy-orchestrate status --json           # JSON output
lindy-orchestrate status -n 50            # last 50 log entries
lindy-orchestrate status --status-only    # health table only
lindy-orchestrate status --logs-only      # logs only
```

| Flag | Short | Description |
|------|-------|-------------|
| `--json` | | Machine-readable JSON |
| `--last` | `-n` | Recent log entries (default: 10) |
| `--status-only` | | Module status table only |
| `--logs-only` | | Recent logs only |

### `logs` / `validate`

`logs` is an alias for `status --logs-only` (accepts `--json` and `-n`). `validate` checks configuration, module paths, STATUS.md files, and CLI availability.

### `gc`

Dry-run by default. Use `--apply` to execute cleanup.

```bash
lindy-orchestrate gc                                # dry run
lindy-orchestrate gc --apply --branch-age 7         # clean branches >7 days
```

| Flag | Description |
|------|-------------|
| `--apply` | Execute cleanup (default: dry run) |
| `--branch-age` | Max branch age in days (default: 14) |
| `--session-age` | Max session age in days (default: 30) |
| `--log-size` | Max log file size in MB (default: 10) |
| `--status-stale` | STATUS.md stale threshold in days (default: 7) |

### `scan`

Scan for entropy. Use `--module backend` to scope, `--grade-only` for A-F grades only.

### `issues`

List issues from configured tracker. Flags: `--label`, `--status` (default: open), `-n` limit (default: 20), `--json`.

### `run-issue`

Fetch an issue, decompose as goal, execute. On completion, comments and optionally closes (`tracker.sync_on_complete`). Accepts `--dry-run` and `--verbose`.

### `mailbox`

```bash
lindy-orchestrate mailbox                                              # summary
lindy-orchestrate mailbox frontend --json                              # module messages
lindy-orchestrate mailbox --send-to backend -m "Need API" -p high      # send message
```

| Flag | Short | Description |
|------|-------|-------------|
| `--send-to` | | Recipient module |
| `--send-from` | | Sender (default: `cli`) |
| `--message` | `-m` | Message content |
| `--priority` | `-p` | `low`, `normal`, `high`, `urgent` |
| `--json` | | JSON output |

---

## Configuration

`orchestrator.yaml` in project root. Only `project` and `modules` are required; all other sections have sensible defaults.

| Section | Key Fields |
|---------|-----------|
| `project` | `name`, `branch_prefix` (default: `af`) |
| `modules[]` | `name`, `path`, `status_md`, `claude_md`, `repo`, `ci_workflow`, `role` |
| `planner` | `mode` (cli/api), `model`, `max_tokens`, `timeout_seconds`, `prompt_template` |
| `dispatcher` | `provider` (claude_cli/codex_cli), `timeout_seconds`, `stall_escalation` {`warn_after_seconds`, `kill_after_seconds`}, `permission_mode`, `max_output_chars` |
| `qa_gates` | `ci_check`, `structural` {`max_file_lines`, `enforce_module_boundary`, `sensitive_patterns`}, `layer_check` {`enabled`, `unknown_file_policy`}, `custom[]` {`name`, `command`, `cwd`, `timeout`, `modules`} |
| `safety` | `dry_run`, `max_retries_per_task`, `max_parallel` |
| `mailbox` | `enabled` (default: true), `dir`, `inject_on_dispatch` |
| `tracker` | `enabled`, `provider` (github), `repo`, `labels[]`, `sync_on_complete` |
| `logging` | `dir`, `session_dir`, `log_file` |

See [docs/REFERENCE.md](docs/REFERENCE.md) for the complete annotated YAML schema with all defaults and module-scoped QA gate normalization.

---

## Provider System

Pluggable dispatch via the `DispatchProvider` protocol (defined in `providers/base.py`). Two dispatch modes:

| Mode | Method | Use Case |
|------|--------|----------|
| Streaming | `dispatch()` | Long tasks: real-time heartbeat, stall detection, event callbacks |
| Blocking | `dispatch_simple()` | Short tasks: plan generation, reports |

| Provider | Binary | Flag | Description |
|----------|--------|------|-------------|
| `claude_cli` | `claude` | `--provider claude_cli` | Claude Code CLI (default) |
| `codex_cli` | `codex` | `--provider codex_cli` | OpenAI Codex CLI |

Stall escalation: warn after silence threshold, kill after kill threshold. 10-minute minimum floor applies. Bash-tool-aware: long-running commands get 50% extra time. To add a custom provider, implement `DispatchProvider` in `providers/` and register in `providers/__init__.py`.

---

## QA Gates

Pluggable validation runs after each task dispatch, sequentially per task.

| Gate | Description |
|------|-------------|
| `ci_check` | Poll GitHub Actions workflow via `gh` CLI |
| `command_check` | Run shell commands (exit 0 = pass) |
| `agent_check` | Dispatch separate QA agent for semantic validation |
| `structural_check` | File size limits, module boundary enforcement, sensitive file detection |
| `layer_check` | Intra-module layer ordering based on ARCHITECTURE.md |

Custom command-based gates are defined under `qa_gates.custom` in config. The `{module_path}` placeholder resolves to the module's absolute path. On failure, the orchestrator builds structured remediation feedback and retries up to `safety.max_retries_per_task` times.

---

## Hook / Event System

The `HookRegistry` provides a thread-safe, synchronous event system. Events: `TASK_STARTED`, `TASK_COMPLETED`, `TASK_FAILED`, `TASK_RETRYING`, `TASK_SKIPPED`, `QA_PASSED`, `QA_FAILED`, `STALL_WARNING`, `STALL_KILLED`, `TASK_HEARTBEAT`, `CHECKPOINT_SAVED`, `MAILBOX_MESSAGE`, `SESSION_START`, `SESSION_END`.

Register with `hooks.on(EventType, handler)` or `hooks.on_any(handler)`. Each `Event` carries `type`, `timestamp`, `data`, `task_id`, `module`. The dashboard, progress reporting, and checkpoint system all subscribe. See [docs/REFERENCE.md](docs/REFERENCE.md) for the full API.

---

## Live DAG Dashboard

When running in a terminal, `run` and `resume` display a live-updating ASCII DAG tree powered by Rich Live panels, subscribing to hook events:

```
+--------------------------- Executing ----------------------------+
| DAG: Add user authentication with JWT                            |
| +-- * 1 backend: Add JWT auth endpoint         <- tool: Edit    |
| +-- * 2 frontend: Add login page               <- starting...   |
| +-- o 3 backend: Add auth middleware (depends: 1)                |
|                                                                  |
|   V 1 completed  X 0 failed  * 1 running  o 1 pending    2:34   |
+------------------------------------------------------------------+
```

Icons: `V` completed, `X` failed, `*` running, `o` pending, `-` skipped. `--verbose` shows tool use annotations. Falls back to text progress on non-TTY (CI, piped output).

---

## Inter-Agent Mailbox

JSONL-based, file-backed messaging for near-real-time inter-module communication. Each module has an inbox at `.orchestrator/mailbox/{module}.jsonl`. Messages are appended atomically with thread-safe locking. When `mailbox.inject_on_dispatch` is true (default), pending messages are injected into agent prompts before dispatch.

Messages carry: `id` (UUID), `from_module`, `to_module`, `content`, `message_type` (request/response/notification), `priority` (low/normal/high/urgent), `status` (pending/read/acknowledged), optional `in_reply_to` and `task_id`.

---

## Entropy Scanner

Scans for architecture drift, contract violations, STATUS.md inconsistency, and code quality decay.

| Check | Description |
|-------|-------------|
| Architecture drift | ARCHITECTURE.md declarations vs actual filesystem |
| Contract compliance | CONTRACTS.md completeness for multi-module projects |
| STATUS.md consistency | Health values, freshness, stale in-progress tasks |
| Quality metrics | Oversized files (>500 lines), missing test directories |

Each module receives a grade (A-F) based on weighted severity (error > warning > info).

---

## Garbage Collection

The `gc` command cleans up entropy from agent-generated artifacts. Dry-run by default.

| Category | Description |
|----------|-------------|
| Stale branches | Task branches (`{prefix}/task-*`) older than threshold |
| Old sessions | Session JSON files archived past threshold |
| Log rotation | Action logs exceeding size limit (rotated with timestamp) |
| STATUS.md drift | Stale STATUS.md files not updated within threshold |
| Orphan plans | Plan files >30 days old with no session reference |

---

## Issue Tracker Integration

Connect to GitHub Issues via `gh` CLI. Requires `gh auth login`.

```yaml
tracker:
  enabled: true
  provider: github
  repo: "owner/repo"           # empty = current repo
  labels: [orchestrator]
  sync_on_complete: true
```

Workflow: `lindy-orchestrate issues` lists matching issues -> `run-issue 42` fetches issue #42, decomposes as goal, executes -> on completion, comments with task summary and closes (if all tasks pass and `sync_on_complete` is true).

---

## Structured QA Feedback

On QA failure, gate-specific parsers extract actionable remediation instead of passing raw output:

| Parser | Triggered By | Extracts |
|--------|-------------|----------|
| pytest | `pytest`, `py.test` | Failed test paths, assertion details, fix guidance |
| ruff | `ruff`, `eslint`, `flake8` | File:line violations, rule codes, auto-fix commands |
| tsc | `tsc`, `typescript` | File:line type errors, TS error codes |
| generic | All other gates | Truncated output with run-locally guidance |

Retry prompts are progressively focused: retry 1 includes the full original prompt plus structured feedback; retry 2+ uses a simplified prompt targeting only failing files and specific errors. See [docs/REFERENCE.md](docs/REFERENCE.md) for the `StructuredFeedback` model.

---

## Execution Summary Reports

After every `run` or `resume`, the orchestrator generates:

1. **Console summary** — Rich panel with per-task status table, timing, retries, QA results, and output previews
2. **Markdown report** — Saved to `.orchestrator/reports/{session_id}_summary.md`

Reports include: goal status, per-task metrics (module, status, duration, retries, QA), and aggregate counts (total, pass, fail, skip, duration, estimated cost).

---

## Key Concepts

**Modules** — Independent directories (services, packages). Auto-detected by markers: `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `CMakeLists.txt`, `Makefile`. Special names `root` and `*` refer to the project root.

**STATUS.md** — Human-readable, git-diffable state per module. Tracks active work, completed tasks, backlogs, cross-module requests, metrics, and blockers.

**CONTRACTS.md** — Cross-module interface definitions. Generated by `onboard` when coupling is moderate or higher.

**Sessions** — JSON state in `.orchestrator/sessions/`. Supports pause/resume, mid-execution checkpoints, and history.

**Branch delivery** — Each task gets branch `{branch_prefix}/task-{id}`. Agents create, commit, push. Delivery check verifies branch exists.

---

## Architecture

```
src/lindy_orchestrator/
|-- __init__.py                  # Package init, version
|-- cli.py                       # Typer CLI entry point (run, plan, resume, version)
|-- cli_ext.py                   # Extension commands (gc, scan, validate, issues, run-issue, mailbox)
|-- cli_status.py                # Status and logs commands
|-- cli_onboard.py               # Unified onboard command (scaffold/init/re-onboard)
|-- cli_init.py                  # Quick init + legacy onboard commands
|-- cli_helpers.py               # Shared CLI utilities
|-- cli_onboard_helpers.py       # Onboard-specific helpers
|-- cli_scaffold.py              # Scaffold mode helpers
|-- config.py                    # YAML config loading + Pydantic models
|-- models.py                    # Core data models (TaskPlan, TaskItem, QACheck, DispatchResult)
|-- planner.py                   # Goal -> TaskPlan decomposition via LLM
|-- scheduler.py                 # DAG-based parallel execution with retry logic
|-- scheduler_helpers.py         # Scheduler utility functions
|-- dispatcher.py                # Claude CLI subprocess management (streaming + blocking)
|-- codex_dispatcher.py          # Codex CLI subprocess management (streaming + blocking)
|-- prompts.py                   # LLM prompt templates (Jinja2)
|-- session.py                   # Session state persistence and resume
|-- logger.py                    # Append-only JSONL audit trail
|-- reporter.py                  # Rich console output, execution summaries
|-- hooks.py                     # Event hook system (HookRegistry, EventType)
|-- dashboard.py                 # Live DAG dashboard (Rich Live panel)
|-- dag.py                       # DAG visualization (ASCII tree rendering)
|-- mailbox.py                   # JSONL-based inter-agent mailbox
|-- gc.py                        # Garbage collection (branches, sessions, logs)
|-- providers/
|   |-- __init__.py              # Provider registry and factory
|   |-- base.py                  # DispatchProvider protocol
|   |-- claude_cli.py            # Claude Code CLI provider
|   |-- codex_cli.py             # OpenAI Codex CLI provider
|-- qa/
|   |-- __init__.py              # Gate registry and runner
|   |-- ci_check.py              # GitHub Actions CI polling
|   |-- command_check.py         # Shell command execution
|   |-- agent_check.py           # Agent-based semantic validation
|   |-- structural_check.py      # File size, boundaries, sensitive files
|   |-- layer_check.py           # ARCHITECTURE.md layer ordering
|   |-- feedback.py              # Structured QA feedback and remediation
|-- entropy/
|   |-- __init__.py
|   |-- scanner.py               # Entropy scanner (drift, contracts, quality)
|   |-- scanner_helpers.py       # Grading and report formatting
|   |-- scanner_types.py         # ScanReport, ScanFinding, ModuleGrade
|-- trackers/
|   |-- __init__.py              # Tracker factory
|   |-- base.py                  # TrackerIssue dataclass
|   |-- factory.py               # Provider factory
|   |-- github_issues.py         # GitHub Issues provider (via gh CLI)
|-- status/
|   |-- __init__.py
|   |-- parser.py                # STATUS.md -> structured data
|   |-- templates.py             # STATUS.md generation
|   |-- writer.py                # Surgical markdown table updates
|-- discovery/
|   |-- __init__.py
|   |-- analyzer.py              # Static project analysis (tech stack, dependencies)
|   |-- analyzer_helpers.py      # Analysis utility functions
|   |-- interview.py             # Interactive and non-interactive onboarding Q&A
|   |-- generator.py             # Artifact generation (CLAUDE.md, CONTRACTS.md, ...)
|   |-- templates/
|       |-- __init__.py
|       |-- root_claude_md.py    # Root CLAUDE.md template
|       |-- module_claude_md.py  # Per-module CLAUDE.md template
|       |-- architecture_md.py   # ARCHITECTURE.md template
|       |-- contracts_md.py      # CONTRACTS.md template
|       |-- agent_docs.py        # Agent protocol docs template
```

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
pytest tests/ --cov=src/lindy_orchestrator --cov-report=term-missing
ruff check src/ tests/ && ruff format src/ tests/
```

## License

[MIT](LICENSE)
