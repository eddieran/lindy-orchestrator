# Coordination Protocol -- lindy-orchestrator

> Detailed rules for multi-agent coordination. The root CLAUDE.md
> contains a summary; this document is the full reference.

## STATUS.md as Message Bus

Each module has a `STATUS.md` file that tracks:
- **Module Metadata**: health (GREEN/YELLOW/RED), last update, session ID
- **Active Work**: current tasks with status and blockers
- **Completed (Recent)**: recently finished tasks
- **Cross-Module Requests**: OPEN/IN_PROGRESS/DONE requests between modules
- **Cross-Module Deliverables**: completed outputs for other modules
- **Key Metrics**: module-specific health indicators
- **Blockers**: issues that prevent progress

The orchestrator reads all STATUS.md files during the planning phase via
`status/parser.py` and injects their summaries into the LLM planning prompt.

### Request Flow

1. Module A needs work from Module B -- A creates a Cross-Module Request in A's STATUS.md
2. Orchestrator picks up open requests during planning
3. Module B executes the request and records a deliverable
4. Module A reads the deliverable and marks the request DONE

### STATUS.md Format

STATUS.md files use a structured Markdown table format. The parser (`status/parser.py`)
is lenient: it extracts what it can and never crashes on unexpected formats. See
`status/templates.py` for the canonical scaffold.

### STATUS.md Maintenance

The `gc` command monitors STATUS.md staleness. Files not updated within
`--status-stale` days (default: 7) are flagged. The `status/writer.py` module
provides helpers for updating the meta timestamp.

## Branch-Based Delivery

- Each task produces a branch: `{branch_prefix}/task-{id}` (default prefix: `af`)
- Agents commit and push to their branch
- The scheduler verifies branch existence and commit count after dispatch
- Branches are merged after QA gates pass

Branch delivery instructions are automatically injected into the agent prompt
by the scheduler before the first dispatch. On retry, the same branch is reused
(agents are told not to recreate the branch).

## QA Gates

Every task is verified by quality checks before marking complete. Gates are
pluggable and registered via decorator in `qa/__init__.py`.

### Built-in Gates

| Gate | Description | Implementation |
|------|-------------|----------------|
| `ci_check` | Waits for CI pipeline pass/fail on the task branch | `qa/ci_check.py` -- uses `gh` CLI to poll workflow runs |
| `command_check` | Runs a custom shell command (test suite, linter, build) | `qa/command_check.py` -- subprocess with timeout |
| `agent_check` | Dispatches a QA agent for complex validation | `qa/agent_check.py` -- uses the same dispatch provider |
| `structural_check` | File size limits, sensitive file detection, import boundaries | `qa/structural_check.py` -- git-aware structural lint |
| `layer_check` | Intra-module layer ordering (parsed from ARCHITECTURE.md) | `qa/layer_check.py` -- verifies import direction |

### Custom YAML Gates

Users can define custom command-based gates in `orchestrator.yaml`:

```yaml
qa_gates:
  custom:
    - name: pytest
      command: "pytest tests/ -x"
      cwd: "{module_path}"
      timeout: 600
      modules: ["backend"]       # empty = all modules
```

Module-scoped shorthand is also supported:

```yaml
qa_gates:
  backend:
    - name: pytest
      command: "cd backend && pytest"
  frontend:
    - name: playwright
      command: "npx playwright test"
```

The config loader (`config.py:_normalize_qa_gates()`) normalizes module-scoped
gates into the unified `custom` list.

### Gate Resolution Order

When `run_qa_gate()` is called:
1. Custom gates from config (name match)
2. Built-in registered gates (decorator registry)
3. Unknown gate -- immediate failure

### Structural Check Details

The `structural_check` gate validates:
- **File size**: no file exceeds `max_file_lines` (default: 500)
- **Sensitive files**: no staged files match `.env`, `*.key`, `*.pem`
- **Import boundaries**: no cross-module imports (for multi-module projects)

Violations include remediation messages that teach the agent how to fix the issue
(e.g., "Split into `foo_core.py` and `foo_helpers.py`").

### Layer Check Details

The `layer_check` gate enforces intra-module layer ordering:
- Parses layer definitions from `ARCHITECTURE.md` (e.g., `models -> schemas -> services -> routes -> main`)
- Verifies that `layer[i]` only imports from `layer[j]` where `j <= i`
- Shared directories (`utils/`, `shared/`, `common/`) are exempt (treated as layer -1)
- Test directories are excluded from checking

## Structured QA Feedback and Retry

When a QA gate fails, the system does not simply re-run the task. Instead:

1. **Classification** -- `qa/feedback.py` classifies the failure (test_failure,
   lint_error, type_error, boundary_violation, build_error, timeout).

2. **Parsing** -- Gate-specific parsers extract structured information:
   - **pytest**: extracts FAILED test paths, assertion messages
   - **ruff/eslint**: extracts file:line violations with rule codes
   - **tsc**: extracts TypeScript error codes and locations

3. **Remediation** -- Structured feedback is formatted with:
   - Specific error list
   - Files to check
   - Step-by-step fix instructions

4. **Prompt augmentation** -- The failed task's prompt is augmented with the
   structured feedback and re-dispatched. Maximum retries are configurable
   per project (`safety.max_retries_per_task`, default: 2).

5. **Progressive focus** -- On retry 2+, the prompt is simplified to focus
   only on the failing files and errors, not the full original task.

## Cross-Module Request Protocol

When you need work from another module:
1. Add an entry to your STATUS.md "Cross-Module Requests" table
2. Set status to OPEN
3. Include priority (P0=critical, P1=high, P2=normal)
4. The orchestrator will pick it up in the next planning cycle

Do NOT directly modify files in other modules.

## Mailbox System

The mailbox provides near-real-time inter-agent messaging without external infrastructure.

### Storage

Messages are stored as JSONL files at `.orchestrator/mailbox/{module}.jsonl`.
Each message is a JSON object with fields: `id`, `from_module`, `to_module`,
`content`, `message_type` (request/response/notification), `priority`
(low/normal/high/urgent), `status` (pending/read/acknowledged), `created_at`.

### Automatic Injection

When `mailbox.inject_on_dispatch` is true (default), the scheduler automatically
injects pending mailbox messages into the agent prompt before dispatch. This
allows agents to receive messages from other modules without polling.

### CLI Commands

```bash
lindy-orchestrate mailbox                        # Summary of all modules
lindy-orchestrate mailbox frontend               # Pending messages for frontend
lindy-orchestrate mailbox --send-to backend \
    --send-from frontend -m "Need API endpoint"  # Send a message
lindy-orchestrate mailbox frontend --json        # JSON output
```

### Thread Safety

The `Mailbox` class uses a threading lock for concurrent reads/writes, making
it safe for parallel task execution.

## Hook / Event System

The orchestrator emits lifecycle events via `HookRegistry` (`hooks.py`).
All subsystems can subscribe to events without tight coupling.

### Event Types

| Event | Emitted When |
|-------|-------------|
| `session_start` | Execution begins |
| `session_end` | Execution completes |
| `task_started` | A task begins dispatch |
| `task_completed` | A task passes all QA gates |
| `task_failed` | A task exhausts retries or dispatch fails |
| `task_retrying` | A task is being retried with QA feedback |
| `task_skipped` | A task is skipped (dependency failed) |
| `task_heartbeat` | A tool use event is received during dispatch |
| `qa_passed` | A QA gate passes |
| `qa_failed` | A QA gate fails |
| `stall_warning` | No agent output for warn threshold |
| `stall_killed` | Agent killed after kill threshold |
| `checkpoint_saved` | Session checkpoint persisted |
| `mailbox_message` | A mailbox message is processed |

### Subscribing

```python
from lindy_orchestrator.hooks import HookRegistry, Event, EventType

hooks = HookRegistry()
hooks.on(EventType.TASK_COMPLETED, lambda e: print(f"Task {e.task_id} done"))
hooks.on_any(lambda e: log.info("Event: %s", e.type))
```

### Dashboard Integration

The `Dashboard` class subscribes to hook events and renders a live Rich panel
showing the task DAG with real-time status updates, tool use annotations,
and a summary bar with task counts and elapsed time.

## ARCHITECTURE.md

The structural map at the project root defines:
- Module topology and tech stacks
- Component diagram (ASCII)
- Data flow description
- Layer structure per package
- Dependency directions between modules
- Boundary rules
- Sensitive paths

Read ARCHITECTURE.md before planning any cross-module work.
