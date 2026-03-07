# Architecture -- lindy-orchestrator

> This is a **map**, not a manual. It tells you what exists where,
> how modules relate, and -- critically -- what does NOT belong where.

## Module Topology

Single-package Python project:

- **lindy-orchestrator/** (`./`) -- Python 3.11+, Pydantic, Typer, Rich, PyYAML

## Component Diagram

```
                        +-------------------+
                        |     CLI Layer     |
                        | cli.py  cli_ext   |
                        | cli_onboard       |
                        | cli_status        |
                        +--------+----------+
                                 |
                 +---------------+---------------+
                 |                               |
        +--------v--------+            +--------v--------+
        |     Planner     |            |    Scheduler     |
        | planner.py      |            | scheduler.py     |
        | prompts.py      |            | scheduler_helpers |
        +---------+-------+            +--------+--------+
                  |                             |
                  |        +--------------------+
                  |        |                    |
          +-------v--------v--+        +--------v--------+
          |    Providers       |        |   QA Gates      |
          | providers/         |        | qa/             |
          |  base.py (Protocol)|        |  __init__.py    |
          |  claude_cli.py     |        |  ci_check       |
          |  codex_cli.py      |        |  command_check  |
          +--------------------+        |  agent_check    |
                                        |  structural_chk |
          +--------------------+        |  layer_check    |
          |    Dispatcher      |        |  feedback.py    |
          | dispatcher.py      |        +-----------------+
          | codex_dispatcher.py|
          +--------------------+        +-----------------+
                                        |   Mailbox       |
          +--------------------+        | mailbox.py      |
          |    Hooks / Events  |        +-----------------+
          | hooks.py           |
          +--------------------+        +-----------------+
                                        |   Trackers      |
          +--------------------+        | trackers/       |
          |    Dashboard       |        |  base.py        |
          | dashboard.py       |        |  github_issues  |
          | dag.py             |        |  factory.py     |
          +--------------------+        +-----------------+

          +--------------------+        +-----------------+
          |    Session         |        |   Status        |
          | session.py         |        | status/         |
          +--------------------+        |  parser.py      |
                                        |  writer.py      |
          +--------------------+        |  templates.py   |
          |    Logger          |        +-----------------+
          | logger.py          |
          +--------------------+        +-----------------+
                                        |   Discovery     |
          +--------------------+        | discovery/      |
          |    Reporter        |        |  analyzer.py    |
          | reporter.py        |        |  interview.py   |
          +--------------------+        |  generator.py   |
                                        |  templates/     |
          +--------------------+        +-----------------+
          |    Config          |
          | config.py          |        +-----------------+
          +--------------------+        |   Entropy       |
                                        | entropy/        |
          +--------------------+        |  scanner.py     |
          |    Models          |        |  scanner_types  |
          | models.py          |        |  scanner_helpers|
          +--------------------+        +-----------------+

          +--------------------+
          |    GC              |
          | gc.py              |
          +--------------------+
```

## Data Flow

1. **Goal Input** -- User provides a natural-language goal via CLI (`run`, `plan`,
   or `run-issue`).

2. **Planning** -- `planner.py` reads all module `STATUS.md` files via `status/parser.py`,
   builds context including `ARCHITECTURE.md`, and calls an LLM (via `providers/`) to
   decompose the goal into a `TaskPlan` (a DAG of `TaskItem` objects).

3. **Scheduling** -- `scheduler.py` walks the task DAG in topological order, dispatching
   independent tasks in parallel (up to `safety.max_parallel` workers) using
   `concurrent.futures.ThreadPoolExecutor`.

4. **Dispatch** -- Each task is sent to an agent subprocess via a `DispatchProvider`
   (`providers/claude_cli.py` or `providers/codex_cli.py`). The dispatcher streams JSONL
   events, monitors heartbeat/stall, and collects the result.

5. **QA Verification** -- After each dispatch, QA gates run sequentially against the
   task output. On failure, `qa/feedback.py` formats structured remediation and the task
   is re-dispatched (up to `safety.max_retries_per_task`).

6. **Reporting** -- `reporter.py` prints per-task results and saves a Markdown summary
   to `.orchestrator/reports/`.

7. **Session Persistence** -- `session.py` checkpoints the plan state after each task
   resolves. The `resume` command reloads a session and retries failed tasks.

## Layer Structure per Package

### cli

Entry point layer. Defines Typer commands and wires together all subsystems.

| File | Responsibility |
|------|---------------|
| `cli.py` | Main Typer app: `run`, `plan`, `resume`, `version` |
| `cli_ext.py` | Extension commands: `gc`, `scan`, `validate`, `issues`, `run-issue`, `mailbox` |
| `cli_helpers.py` | Shared CLI utilities (config loading, plan serialization, goal resolution) |
| `cli_init.py` | `init` command -- quick project initialization |
| `cli_onboard.py` | `onboard` command -- guided project setup |
| `cli_onboard_helpers.py` | Onboard helper functions |
| `cli_scaffold.py` | Scaffold command |
| `cli_status.py` | `status` command -- module health overview |

### config

Configuration loading and validation. Single file.

| File | Responsibility |
|------|---------------|
| `config.py` | YAML config loading, Pydantic models for all config sections (`OrchestratorConfig`, `PlannerConfig`, `DispatcherConfig`, `QAGatesConfig`, `SafetyConfig`, `MailboxConfig`, `TrackerConfig`, etc.) |

### models

Core data structures shared across all subsystems. Single file.

| File | Responsibility |
|------|---------------|
| `models.py` | `TaskPlan`, `TaskItem`, `TaskStatus`, `QACheck`, `QAResult`, `DispatchResult`, `ModuleStatus`, `ProjectProfile`, `DiscoveryContext`, plan serialization helpers |

### planner

Goal decomposition via LLM. Supports CLI mode (subprocess) and API mode (Anthropic SDK).

| File | Responsibility |
|------|---------------|
| `planner.py` | `generate_plan()`: reads statuses, builds prompt, calls LLM, parses JSON output |
| `prompts.py` | Prompt template rendering for plan generation |

### scheduler

DAG-based parallel task execution with retry logic.

| File | Responsibility |
|------|---------------|
| `scheduler.py` | `execute_plan()`: topological dispatch, QA gate orchestration, retry loop |
| `scheduler_helpers.py` | Branch delivery check, QA gate injection |

### providers

Pluggable agent dispatch backends. Protocol-based abstraction.

| File | Responsibility |
|------|---------------|
| `base.py` | `DispatchProvider` Protocol (dispatch + dispatch_simple) |
| `__init__.py` | `create_provider()` factory |
| `claude_cli.py` | Claude Code CLI provider (wraps `dispatcher.py`) |
| `codex_cli.py` | OpenAI Codex CLI provider (wraps `codex_dispatcher.py`) |

### dispatcher / codex_dispatcher

Low-level CLI subprocess management with streaming, heartbeat, and stall detection.

| File | Responsibility |
|------|---------------|
| `dispatcher.py` | `dispatch_agent()` (streaming) and `dispatch_agent_simple()` (blocking) for Claude CLI |
| `codex_dispatcher.py` | Same two dispatch modes for Codex CLI |

### qa

Pluggable QA gate system with decorator-based registration.

| File | Responsibility |
|------|---------------|
| `__init__.py` | Gate registry, `run_qa_gate()` dispatcher, custom command gate runner |
| `ci_check.py` | CI pipeline pass/fail check via `gh` CLI |
| `command_check.py` | Custom shell command gate |
| `agent_check.py` | Dispatches a QA agent for complex validation |
| `structural_check.py` | File size limits, sensitive file detection, import boundary enforcement |
| `layer_check.py` | Intra-module layer ordering enforcement (parsed from `ARCHITECTURE.md`) |
| `feedback.py` | Structured remediation feedback: parses pytest/ruff/tsc output into actionable fix instructions |

### status

STATUS.md parsing, writing, and scaffolding.

| File | Responsibility |
|------|---------------|
| `parser.py` | `parse_status_md()`: lenient Markdown table parser for STATUS.md sections |
| `writer.py` | `update_meta_timestamp()`, `update_root_status()` |
| `templates.py` | `generate_status_md()`: scaffold template for new modules |

### discovery

Project onboarding: static analysis, interactive interview, artifact generation.

| File | Responsibility |
|------|---------------|
| `analyzer.py` | `analyze_project()`: tech stack detection, dependency parsing, dir tree generation |
| `analyzer_helpers.py` | Command detection, dependency parsing helpers |
| `interview.py` | Interactive Q&A (or non-interactive defaults) to build `DiscoveryContext` |
| `generator.py` | `generate_artifacts()`: produces orchestrator.yaml, CLAUDE.md, CONTRACTS.md, STATUS.md, ARCHITECTURE.md, agent docs |
| `templates/` | String templates for each generated artifact |

### entropy

Architecture drift detection and quality grading.

| File | Responsibility |
|------|---------------|
| `scanner.py` | `run_scan()`: checks architecture drift, contract compliance, STATUS.md consistency, quality metrics |
| `scanner_types.py` | `ScanFinding`, `ModuleGrade`, `ScanReport` dataclasses |
| `scanner_helpers.py` | Module grading logic, report formatting |

### trackers

External issue tracker integration.

| File | Responsibility |
|------|---------------|
| `base.py` | `TrackerProvider` Protocol, `TrackerIssue` dataclass |
| `factory.py` | `create_tracker()` factory |
| `github_issues.py` | GitHub Issues provider using `gh` CLI |

### hooks

Central event system for orchestrator lifecycle.

| File | Responsibility |
|------|---------------|
| `hooks.py` | `HookRegistry`, `Event`, `EventType` enum (14 event types), `make_progress_adapter()` |

### mailbox

JSONL-based inter-agent messaging.

| File | Responsibility |
|------|---------------|
| `mailbox.py` | `Mailbox` class (send/receive/acknowledge), `Message` dataclass, `format_mailbox_messages()` for prompt injection |

### session

Session state persistence for multi-session continuity.

| File | Responsibility |
|------|---------------|
| `session.py` | `SessionManager`, `SessionState` dataclass, checkpoint/resume support |

### dashboard

Live DAG visualization during execution.

| File | Responsibility |
|------|---------------|
| `dashboard.py` | `Dashboard` class: Rich Live panel driven by hook events |
| `dag.py` | `render_dag()`: ASCII tree rendering with status icons and annotation bubbles |

### reporter

Terminal output formatting and summary generation.

| File | Responsibility |
|------|---------------|
| `reporter.py` | `PlanProgress` (live spinner), `generate_execution_summary()`, `save_summary_report()`, `print_status_table()` |

### logger

Append-only JSONL action log.

| File | Responsibility |
|------|---------------|
| `logger.py` | `ActionLogger`: logs dispatches, QA results, session events |

### gc

Garbage collection for agent-generated artifacts.

| File | Responsibility |
|------|---------------|
| `gc.py` | `run_gc()`: stale branches, old sessions, log rotation, STATUS.md drift, orphan plans |

## Boundary Rules

1. **Single package** -- All code lives under `src/lindy_orchestrator/`. No cross-package imports.

2. **Provider abstraction** -- New dispatch backends must implement `DispatchProvider` Protocol
   in `providers/base.py`. CLI, scheduler, and planner never import provider implementations directly.

3. **QA gate registration** -- New gates use the `@register("gate_name")` decorator in `qa/__init__.py`.
   The scheduler calls `run_qa_gate()` without knowing gate implementations.

4. **Tracker abstraction** -- New tracker backends must implement `TrackerProvider` Protocol
   in `trackers/base.py`. The CLI creates trackers via `create_tracker()` factory.

5. **Config as source of truth** -- All runtime behavior is driven by `OrchestratorConfig`.
   No module should read orchestrator.yaml directly; use `load_config()`.

6. **Models are shared** -- `models.py` is the only file imported by every subsystem. Keep it
   dependency-free (stdlib + Pydantic only).

7. **Hook events, not callbacks** -- Subsystems emit `Event` objects via `HookRegistry`.
   The dashboard, progress display, and future integrations subscribe to events.

## Sensitive Paths (DO NOT commit)

- `.env`
- `.env.*`
- `*.key`
- `*.pem`
- `.orchestrator/logs/`
- `.orchestrator/sessions/`
