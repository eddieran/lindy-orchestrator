# lindy-orchestrator

[![CI](https://github.com/eddieran/lindy-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/eddieran/lindy-orchestrator/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/lindy-orchestrator.svg)](https://pypi.org/project/lindy-orchestrator/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Lightweight, git-native multi-agent orchestration framework for autonomous project execution.

lindy-orchestrator decomposes natural-language goals into dependency-ordered task DAGs, dispatches them to AI coding agents working in isolated module directories, validates results through pluggable QA gates, and coordinates everything through markdown files and git.

**No database. No shared memory. No infrastructure. Just git, markdown, and your existing project.**

---

## Table of Contents

- [Why](#why)
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

## Why

Multi-module projects need coordination. Telling an LLM agent to "add user authentication" across a backend and frontend requires decomposing the goal, ordering dependencies, dispatching work to the right directory, and verifying the result. lindy-orchestrator automates this entire loop:

```
Goal (natural language)
  -> LLM decomposes into task DAG
    -> Parallel dispatch to module agents
      -> QA gates validate each result
        -> Retry with structured feedback on failure
          -> Execution summary report
```

Each module stays isolated (its own `CLAUDE.md`, `STATUS.md`, working directory). The orchestrator is the only thing that sees the whole picture.

---

## Install

```bash
# From PyPI
pip install lindy-orchestrator

# With Anthropic API support (optional, for API planner mode)
pip install lindy-orchestrator[api]

# From source
git clone https://github.com/eddieran/lindy-orchestrator.git
cd lindy-orchestrator
pip install -e ".[dev]"
```

**Requirements:**
- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) in `PATH` (default provider)
- Or [OpenAI Codex CLI](https://github.com/openai/codex) in `PATH` (alternative provider)

---

## Quick Start

```bash
cd my-project

# Onboard: auto-detects modules, generates config and artifacts
lindy-orchestrate onboard

# Preview a task plan (no execution)
lindy-orchestrate plan "Add user authentication with JWT"

# Execute the plan with full orchestration
lindy-orchestrate run "Add user authentication with JWT"

# Use the Codex CLI provider instead of Claude
lindy-orchestrate run "Add user auth" --provider codex_cli
```

---

## How It Works

```
1. Read all module STATUS.md files (current state)
2. Send goal + context to LLM -> JSON task plan with dependency DAG
3. Scheduler dispatches ready tasks in parallel (respecting dependencies)
4. Each task: agent works in module directory -> commits -> pushes to task branch
5. QA gates validate each result (CI, shell commands, structural checks, agent review)
6. On QA failure: augment prompt with structured remediation feedback, retry (up to N times)
7. On dependency failure: skip downstream tasks, continue independent ones
8. Generate execution summary report with per-task metrics
```

---

## CLI Reference

All commands accept `-c path/to/orchestrator.yaml` to specify a config file.

### `run`

Execute a goal with full orchestration: plan, dispatch, QA, retry, report.

```bash
lindy-orchestrate run "Add user authentication with JWT"
lindy-orchestrate run --file goal.md           # Read goal from file
lindy-orchestrate run --file -                  # Read goal from stdin
lindy-orchestrate run --plan plan.json          # Execute a saved plan (skip planning)
lindy-orchestrate run --dry-run                 # Analyze only, no dispatch
lindy-orchestrate run --verbose                 # Show detailed output (tool use, etc.)
lindy-orchestrate run --provider codex_cli      # Use Codex CLI instead of Claude
```

| Flag | Short | Description |
|------|-------|-------------|
| `--file` | `-f` | Read goal from a file; use `-` for stdin |
| `--plan` | `-p` | Execute a previously saved plan JSON (skip LLM planning) |
| `--config` | `-c` | Path to `orchestrator.yaml` |
| `--dry-run` | | Read and analyze only, no agent dispatch |
| `--verbose` | `-v` | Show detailed output including tool use events |
| `--provider` | | Dispatch provider: `claude_cli` (default) or `codex_cli` |

### `plan`

Generate a task plan without executing it.

```bash
lindy-orchestrate plan "Add user authentication with JWT"
lindy-orchestrate plan --file goal.md
lindy-orchestrate plan -o plan.json             # Also save to a specific path
```

Plans are automatically saved to `.orchestrator/plans/` and can be executed later with `run --plan`.

| Flag | Short | Description |
|------|-------|-------------|
| `--file` | `-f` | Read goal from a file; use `-` for stdin |
| `--output` | `-o` | Save plan JSON to a specific path |
| `--config` | `-c` | Path to `orchestrator.yaml` |

### `resume`

Resume a previous session from its last checkpoint. Skips already-completed tasks and re-executes failed/pending ones.

```bash
lindy-orchestrate resume                        # Resume the latest session
lindy-orchestrate resume abc12345               # Resume a specific session ID
lindy-orchestrate resume --verbose
```

| Flag | Short | Description |
|------|-------|-------------|
| `--config` | `-c` | Path to `orchestrator.yaml` |
| `--verbose` | `-v` | Show detailed output |

### `onboard`

Unified onboarding command. Detects project state and adapts:

- **Empty project** (no source files) -> scaffold mode (LLM-driven, requires description)
- **Existing project** without config -> init + onboard mode (detect, interview, generate)
- **Already onboarded** (has `orchestrator.yaml`) -> re-onboard mode (update config)

```bash
lindy-orchestrate onboard                                # Auto-detect mode
lindy-orchestrate onboard "A SaaS billing platform"      # Scaffold empty project
lindy-orchestrate onboard --file description.md           # Scaffold from file
lindy-orchestrate onboard --depth 2                       # Deeper module scan
lindy-orchestrate onboard -y                              # Non-interactive, use defaults
lindy-orchestrate onboard --force                         # Overwrite existing files
```

| Flag | Short | Description |
|------|-------|-------------|
| `--file` | `-f` | Read project description from file (scaffold mode) |
| `--depth` | | Directory scan depth for module detection (default: 1) |
| `--non-interactive` | `-y` | Skip confirmation prompts, use defaults |
| `--force` | | Overwrite existing files |

### `init`

Quick scaffold: detect modules, generate `orchestrator.yaml` and `STATUS.md` files.

```bash
lindy-orchestrate init
lindy-orchestrate init --modules backend,frontend     # Specify modules manually
lindy-orchestrate init --depth 2                       # Deeper scan
lindy-orchestrate init --no-status                     # Skip STATUS.md creation
```

| Flag | Short | Description |
|------|-------|-------------|
| `--modules` | `-m` | Comma-separated module names (skip auto-detect) |
| `--depth` | | Directory scan depth (default: 1) |
| `--no-status` | | Skip STATUS.md creation |
| `--force` | | Overwrite existing files |

### `status`

Show module health overview, mailbox summary, and recent log entries.

```bash
lindy-orchestrate status
lindy-orchestrate status --json                # Machine-readable JSON output
lindy-orchestrate status -n 50                 # Show last 50 log entries
lindy-orchestrate status --status-only         # Module health table only
lindy-orchestrate status --logs-only           # Recent logs only
```

| Flag | Short | Description |
|------|-------|-------------|
| `--json` | | Output as JSON for scripting |
| `--last` | `-n` | Number of recent log entries to show (default: 10) |
| `--status-only` | | Show only the module status table |
| `--logs-only` | | Show only recent log entries |
| `--config` | `-c` | Path to `orchestrator.yaml` |

### `logs`

Alias for `status --logs-only`. Shows recent action logs.

```bash
lindy-orchestrate logs
lindy-orchestrate logs -n 50
lindy-orchestrate logs --json                  # Raw JSONL output
```

### `validate`

Validate configuration, module paths, STATUS.md files, and CLI availability.

```bash
lindy-orchestrate validate
lindy-orchestrate validate -c path/to/orchestrator.yaml
```

### `gc`

Clean up stale branches, old sessions, oversized logs, and orphan plans. Runs in dry-run mode by default.

```bash
lindy-orchestrate gc                           # Dry run: show what would be cleaned
lindy-orchestrate gc --apply                   # Actually perform cleanup
lindy-orchestrate gc --branch-age 7            # Max age for task branches (days)
lindy-orchestrate gc --session-age 14          # Max age for sessions (days)
lindy-orchestrate gc --log-size 5              # Max log file size (MB)
lindy-orchestrate gc --status-stale 3          # STATUS.md stale threshold (days)
```

| Flag | Description |
|------|-------------|
| `--apply` | Actually perform cleanup (default: dry run) |
| `--branch-age` | Max age for task branches in days (default: 14) |
| `--session-age` | Max age for sessions in days (default: 30) |
| `--log-size` | Max log file size in MB (default: 10) |
| `--status-stale` | STATUS.md stale threshold in days (default: 7) |
| `-c`, `--config` | Path to `orchestrator.yaml` |

### `scan`

Scan for entropy: architecture drift, contract violations, STATUS.md inconsistency, and code quality decay.

```bash
lindy-orchestrate scan
lindy-orchestrate scan --module backend        # Scan a specific module
lindy-orchestrate scan --grade-only            # Show only per-module grades (A-F)
```

| Flag | Description |
|------|-------------|
| `--module` | Scan a specific module only |
| `--grade-only` | Show only per-module grades |
| `-c`, `--config` | Path to `orchestrator.yaml` |

### `issues`

List issues from the configured issue tracker.

```bash
lindy-orchestrate issues
lindy-orchestrate issues --label bug           # Filter by label
lindy-orchestrate issues --status closed       # Filter by status
lindy-orchestrate issues -n 50                 # Fetch up to 50 issues
lindy-orchestrate issues --json                # JSON output
```

| Flag | Short | Description |
|------|-------|-------------|
| `--label` | | Filter by label |
| `--status` | | Issue status filter (default: `open`) |
| `--limit` | `-n` | Max issues to fetch (default: 20) |
| `--json` | | Output as JSON |
| `-c`, `--config` | | Path to `orchestrator.yaml` |

### `run-issue`

Fetch an issue from the tracker and execute it as a goal. On completion, comments on the issue and optionally closes it.

```bash
lindy-orchestrate run-issue 42
lindy-orchestrate run-issue 42 --dry-run
lindy-orchestrate run-issue 42 --verbose
```

| Flag | Short | Description |
|------|-------|-------------|
| `--dry-run` | | Plan only, don't execute |
| `--verbose` | `-v` | Show detailed output |
| `-c`, `--config` | | Path to `orchestrator.yaml` |

### `mailbox`

View or send inter-agent mailbox messages.

```bash
lindy-orchestrate mailbox                      # Summary of all modules
lindy-orchestrate mailbox frontend             # View pending messages for frontend
lindy-orchestrate mailbox frontend --json      # JSON output
lindy-orchestrate mailbox --send-to backend --send-from frontend -m "Need API endpoint"
lindy-orchestrate mailbox --send-to backend -m "Urgent request" --priority high
```

| Flag | Short | Description |
|------|-------|-------------|
| `--send-to` | | Send a message to a module |
| `--send-from` | | Sender module name (default: `cli`) |
| `--message` | `-m` | Message content |
| `--priority` | `-p` | Message priority: `low`, `normal`, `high`, `urgent` |
| `--json` | | Output as JSON |
| `-c`, `--config` | | Path to `orchestrator.yaml` |

### `version`

Print the current version.

```bash
lindy-orchestrate version
lindy-orchestrate version --json
lindy-orchestrate --version                    # Also works via the -V flag
```

---

## Configuration

`orchestrator.yaml` in your project root. All sections except `project` and `modules` are optional with sensible defaults.

```yaml
# orchestrator.yaml — full schema

project:
  name: "my-project"
  branch_prefix: "af"              # Task branches: af/task-1, af/task-2, ...

modules:
  - name: backend
    path: backend/
    status_md: STATUS.md           # Default: STATUS.md
    claude_md: CLAUDE.md           # Default: CLAUDE.md
    repo: myorg/my-backend         # GitHub slug (required for ci_check gate)
    ci_workflow: ci.yml            # Default: ci.yml
    role: ""                       # Set to "qa" to mark as QA dispatcher target
  - name: frontend
    path: frontend/
    repo: myorg/my-frontend

planner:
  mode: cli                        # "cli" uses claude -p; "api" uses Anthropic SDK
  model: claude-sonnet-4-20250514  # Model for API mode
  max_tokens: 4096                 # Max tokens for API mode
  timeout_seconds: 120             # Planner timeout
  prompt_template: null            # Path to custom Jinja2 template

dispatcher:
  provider: claude_cli             # "claude_cli" (default) or "codex_cli"
  timeout_seconds: 1800            # Hard timeout per task dispatch (30 min)
  stall_timeout_seconds: 600       # Backward-compat stall detection (10 min)
  stall_escalation:
    warn_after_seconds: 300        # Emit warning event after 5 min of silence
    kill_after_seconds: 600        # Kill process after 10 min of silence
  permission_mode: bypassPermissions
  max_output_chars: 50000          # Truncate agent output beyond this

qa_gates:
  ci_check:
    timeout_seconds: 900           # CI polling timeout (15 min)
    poll_interval: 30              # Poll every 30 seconds
  structural:
    max_file_lines: 500            # Flag files exceeding this line count
    enforce_module_boundary: true   # Detect cross-module imports
    sensitive_patterns:             # Patterns for sensitive files
      - ".env"
      - "*.key"
      - "*.pem"
  layer_check:
    enabled: true                  # Enforce ARCHITECTURE.md layer ordering
    unknown_file_policy: skip      # "skip" or "warn" for files outside layers
  custom:                          # User-defined command-based gates
    - name: pytest
      command: "pytest --tb=short -q"
      cwd: "{module_path}"        # Resolved to module's absolute path
      timeout: 600
      modules: []                  # Empty = apply to all modules
    - name: eslint
      command: "npx eslint src/"
      cwd: "{module_path}"
      modules: ["frontend"]        # Only runs on the frontend module

  # Module-scoped shorthand (auto-normalized to `custom` list):
  # backend:
  #   - name: pytest
  #     command: "cd backend && pytest"
  # frontend:
  #   - name: playwright
  #     command: "npx playwright test"

safety:
  dry_run: false
  max_retries_per_task: 2          # Retries with QA feedback on failure
  max_parallel: 3                  # Max concurrent task dispatches

mailbox:
  enabled: true                    # Enabled by default
  dir: ".orchestrator/mailbox"     # Mailbox storage directory
  inject_on_dispatch: true         # Auto-inject pending messages into prompts

tracker:
  enabled: false                   # Set to true to enable issue tracking
  provider: github                 # "github" (uses `gh` CLI)
  repo: ""                         # GitHub slug; empty = current repo
  labels:                          # Filter issues by these labels
    - orchestrator
  sync_on_complete: true           # Auto-comment and close issues on completion

logging:
  dir: ".orchestrator/logs"
  session_dir: ".orchestrator/sessions"
  log_file: "actions.jsonl"
```

---

## Provider System

lindy-orchestrator uses a pluggable provider system for agent dispatch. Providers implement the `DispatchProvider` protocol defined in `providers/base.py`.

### `DispatchProvider` Protocol

```python
class DispatchProvider(Protocol):
    def dispatch(
        self, module, working_dir, prompt,
        on_event=None, stall_seconds=None,
    ) -> DispatchResult:
        """Streaming dispatch with heartbeat/stall detection."""
        ...

    def dispatch_simple(
        self, module, working_dir, prompt,
    ) -> DispatchResult:
        """Blocking dispatch for quick tasks (planning, reports)."""
        ...
```

### Built-in Providers

| Provider | CLI Binary | Flag Value | Description |
|----------|-----------|------------|-------------|
| `claude_cli` | `claude` | `--provider claude_cli` | Anthropic Claude Code CLI (default) |
| `codex_cli` | `codex` | `--provider codex_cli` | OpenAI Codex CLI |

Both providers support two dispatch modes:

| Mode | Method | Use Case |
|------|--------|----------|
| Streaming | `dispatch()` | Long tasks: real-time heartbeat, stall detection, event callbacks |
| Blocking | `dispatch_simple()` | Short tasks: plan generation, reports, no thread overhead |

The streaming dispatcher monitors agent output with a two-stage stall escalation:
1. **Warn** after `stall_escalation.warn_after_seconds` (default 5 min) of silence
2. **Kill** after `stall_escalation.kill_after_seconds` (default 10 min) of silence

A 10-minute minimum floor applies (the dispatcher never kills before 10 min of silence, regardless of config). Bash-tool-aware: long-running shell commands get 50% additional time.

### Setting the Provider

```bash
# Via CLI flag (overrides config)
lindy-orchestrate run "goal" --provider codex_cli
```

```yaml
# Via orchestrator.yaml
dispatcher:
  provider: codex_cli
```

### Adding a Custom Provider

1. Create a class implementing `DispatchProvider` in `providers/`
2. Register it in `providers/__init__.py` `create_provider()` factory

---

## QA Gates

Pluggable validation runs after each task dispatch. Gates execute sequentially per task.

### Built-in Gates

| Gate | Description |
|------|-------------|
| `ci_check` | Polls GitHub Actions workflow status via `gh` CLI |
| `command_check` | Runs arbitrary shell commands (exit code 0 = pass) |
| `agent_check` | Dispatches a separate QA agent for semantic validation |
| `structural_check` | File size limits, module boundary enforcement, sensitive file detection |
| `layer_check` | Intra-module layer ordering enforcement based on ARCHITECTURE.md |

### Custom YAML Gates

Define command-based gates in `orchestrator.yaml`:

```yaml
qa_gates:
  custom:
    - name: pytest
      command: "pytest --tb=short -q"
      cwd: "{module_path}"
      timeout: 600
      modules: []              # Empty = all modules
```

The `{module_path}` placeholder is resolved to the module's absolute filesystem path at runtime.

### Retry Behavior

On QA failure, the orchestrator:
1. Parses the failure output with gate-specific parsers (pytest, ruff, tsc)
2. Builds structured remediation feedback with specific errors, file paths, and fix steps
3. Augments the original prompt with this feedback
4. Retries up to `safety.max_retries_per_task` times

---

## Hook / Event System

The `HookRegistry` provides a thread-safe, synchronous event system for lifecycle events.

### EventType Enum

```python
class EventType(str, Enum):
    TASK_STARTED      = "task_started"
    TASK_COMPLETED    = "task_completed"
    TASK_FAILED       = "task_failed"
    TASK_RETRYING     = "task_retrying"
    TASK_SKIPPED      = "task_skipped"
    QA_PASSED         = "qa_passed"
    QA_FAILED         = "qa_failed"
    STALL_WARNING     = "stall_warning"
    STALL_KILLED      = "stall_killed"
    TASK_HEARTBEAT    = "task_heartbeat"
    CHECKPOINT_SAVED  = "checkpoint_saved"
    MAILBOX_MESSAGE   = "mailbox_message"
    SESSION_START     = "session_start"
    SESSION_END       = "session_end"
```

### HookRegistry API

```python
hooks = HookRegistry()

# Register for specific events
hooks.on(EventType.TASK_COMPLETED, my_handler)

# Register for all events
hooks.on_any(my_catch_all_handler)

# Emit events
hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))

# Remove handlers
hooks.remove(EventType.TASK_COMPLETED, my_handler)
hooks.clear()  # Remove all
```

Each `Event` carries: `type`, `timestamp`, `data` (dict), `task_id`, and `module`.

The live dashboard, progress reporting, and checkpoint system all subscribe to hooks.

---

## Live DAG Dashboard

When running in a terminal, `run` and `resume` display a live-updating ASCII DAG tree powered by Rich Live panels. The dashboard subscribes to hook events and re-renders on every state change.

```
+------------------------------ Executing ------------------------------+
| DAG: Add user authentication with JWT                                 |
| +-- * 1 backend: Add JWT auth endpoint            <- tool: Edit       |
| +-- * 2 frontend: Add login page                  <- starting...      |
| +-- o 3 backend: Add auth middleware (depends: 1)                     |
|                                                                       |
|   V 1 completed  X 0 failed  * 1 running  o 1 pending  2:34          |
+-----------------------------------------------------------------------+
```

- Status icons: `V` completed, `X` failed, `*` running, `o` pending, `-` skipped
- Annotation bubbles show latest tool use when `--verbose` is set
- Falls back to text-based progress on non-TTY (CI, piped output)

---

## Inter-Agent Mailbox

A JSONL-based, file-backed messaging system for near-real-time communication between module agents. No external infrastructure required.

### How It Works

- Each module has an inbox at `.orchestrator/mailbox/{module}.jsonl`
- Messages are appended atomically with thread-safe locking
- When `mailbox.inject_on_dispatch` is true (default), pending messages are injected into the agent's prompt before dispatch

### Message Structure

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Auto-generated UUID |
| `from_module` | string | Sender module name |
| `to_module` | string | Recipient module name |
| `content` | string | Message body |
| `message_type` | string | `request`, `response`, or `notification` |
| `priority` | string | `low`, `normal`, `high`, or `urgent` |
| `status` | string | `pending`, `read`, or `acknowledged` |
| `in_reply_to` | string | Optional parent message ID |
| `task_id` | int | Optional associated task ID |

### CLI Usage

```bash
# View mailbox summary
lindy-orchestrate mailbox

# View messages for a module
lindy-orchestrate mailbox backend

# Send a message
lindy-orchestrate mailbox --send-to backend --send-from frontend -m "Need API endpoint for /users"
```

---

## Entropy Scanner

Detects architecture drift, contract violations, STATUS.md inconsistency, and code quality decay across modules.

### Checks Performed

| Check | Description |
|-------|-------------|
| Architecture drift | Compares ARCHITECTURE.md declarations against actual filesystem |
| Contract compliance | Validates CONTRACTS.md completeness for multi-module projects |
| STATUS.md consistency | Checks health values, freshness, and stale in-progress tasks |
| Quality metrics | Flags oversized files (>500 lines), missing test directories |

### Grading

Each module receives a grade (A-F) based on the weighted severity of findings:
- `error` = high severity
- `warning` = medium severity
- `info` = low severity

```bash
lindy-orchestrate scan
lindy-orchestrate scan --grade-only
lindy-orchestrate scan --module backend
```

---

## Garbage Collection

The `gc` command cleans up entropy from agent-generated artifacts.

### Cleanup Categories

| Category | Description |
|----------|-------------|
| Stale branches | Task branches (`af/task-*`) older than threshold |
| Old sessions | Session JSON files older than threshold (archived, not deleted) |
| Log rotation | Action log files exceeding size limit (rotated with timestamp) |
| STATUS.md drift | STATUS.md files not updated within threshold |
| Orphan plans | Plan JSON files >30 days old with no session reference |

```bash
lindy-orchestrate gc                    # Dry run
lindy-orchestrate gc --apply            # Execute cleanup
```

---

## Issue Tracker Integration

Connect to GitHub Issues (via `gh` CLI) to fetch issues and execute them as orchestration goals.

### Setup

```yaml
tracker:
  enabled: true
  provider: github
  repo: "owner/repo"              # Or empty for current repo
  labels: ["orchestrator"]
  sync_on_complete: true
```

Requires `gh` CLI installed and authenticated (`gh auth login`).

### Workflow

1. `lindy-orchestrate issues` -- list open issues matching configured labels
2. `lindy-orchestrate run-issue 42` -- fetch issue #42, decompose as goal, execute
3. On completion, the orchestrator comments on the issue with a task summary
4. If all tasks pass, the issue is automatically closed (when `sync_on_complete: true`)

---

## Structured QA Feedback

When QA gates fail, the orchestrator generates structured remediation feedback rather than passing raw output. Gate-specific parsers extract actionable information:

| Parser | Triggered By | Extracts |
|--------|-------------|----------|
| pytest | `pytest`, `py.test` | Failed test paths, assertion details, fix guidance |
| ruff | `ruff`, `eslint`, `flake8` | File:line violations, rule codes, auto-fix commands |
| tsc | `tsc`, `typescript` | File:line type errors, TS error codes |
| generic | All other gates | Truncated output with run-locally guidance |

Retry prompts are progressively focused:
- **Retry 1**: Full original prompt + structured feedback
- **Retry 2+**: Simplified prompt targeting only failing files and specific errors

### `StructuredFeedback` Model

```python
@dataclass
class StructuredFeedback:
    category: FailureCategory    # test_failure, lint_error, type_error, etc.
    summary: str
    specific_errors: list[str]
    remediation_steps: list[str]
    files_to_check: list[str]
    retry_number: int
```

---

## Execution Summary Reports

After every `run` or `resume`, the orchestrator generates:

1. **Console summary** -- Rich-formatted panel with per-task status table, timing, retries, QA results, and output previews
2. **Markdown report** -- Saved to `.orchestrator/reports/{session_id}_summary.md` with full task details

Reports include:
- Goal status (completed / paused)
- Per-task: module, description, status, duration, retry count, QA results, output preview
- Execution metrics: total tasks, pass/fail/skip counts, total duration, estimated cost

---

## Key Concepts

### Modules

Independent directories in your project (services, packages, microservices). Each module gets its own `STATUS.md`, `CLAUDE.md`, and isolated agent workspace. Auto-detected by marker files: `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `CMakeLists.txt`, `Makefile`.

The special module names `root` and `*` refer to the project root directory for fullstack tasks that span modules.

### STATUS.md

Human-readable, git-diffable state file per module. Tracks active work, completed tasks, backlogs, cross-module requests, key metrics, and blockers. The orchestrator reads these programmatically; agents and humans read and write them in natural language.

### CONTRACTS.md

Single source of truth for cross-module interfaces. Generated by `onboard` when module coupling is moderate or higher. Defines API contracts, shared schemas, task ID conventions, and the CI delivery protocol.

### Sessions

Persistent execution state stored as JSON in `.orchestrator/sessions/`. Each session records the goal, plan snapshot, task statuses, and timestamps. Supports:
- **Pause/resume** across terminal sessions via `lindy-orchestrate resume`
- **Mid-execution checkpoints** that save plan state after each task completes
- **Session listing** for history review

### Branch Delivery

Each task is assigned a branch (`{branch_prefix}/task-{id}`). The orchestrator injects branch delivery instructions into every agent prompt, requiring agents to create the branch, commit, and push. A delivery check verifies the branch has commits after dispatch.

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
|-- models.py                    # Core data models (TaskPlan, TaskItem, QACheck, DispatchResult, ...)
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
|   |-- github_issues.py         # GitHub Issues provider (via `gh` CLI)
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
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ --cov=src/lindy_orchestrator --cov-report=term-missing

# Lint and format
ruff check src/ tests/
ruff format src/ tests/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

---

## License

[MIT](LICENSE)
