# lindy-orchestrator Reference

Detailed API reference and configuration guide. For an overview, see [README.md](../README.md).

---

## Configuration Schema

Complete `orchestrator.yaml` with all fields and defaults:

```yaml
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

### Module-Scoped QA Gates

Users can write module-scoped gates as a shorthand:

```yaml
qa_gates:
  backend:
    - name: pytest
      command: "cd backend && pytest"
  frontend:
    - name: playwright
      command: "npx playwright test"
```

These are auto-normalized to the `custom` list with `modules` set accordingly.

---

## DispatchProvider Protocol

Defined in `providers/base.py`. All dispatch providers must implement:

```python
@runtime_checkable
class DispatchProvider(Protocol):
    def dispatch(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        stall_seconds: int | None = None,
    ) -> DispatchResult:
        """Streaming dispatch with heartbeat/stall detection."""
        ...

    def dispatch_simple(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
    ) -> DispatchResult:
        """Blocking dispatch for quick tasks (planning, reports)."""
        ...
```

### DispatchResult

```python
@dataclass
class DispatchResult:
    module: str
    success: bool
    output: str
    exit_code: int = 0
    duration_seconds: float = 0.0
    truncated: bool = False
    error: str | None = None
    event_count: int = 0
    last_tool_use: str = ""
```

---

## Hook / Event System API

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

### Event

```python
@dataclass
class Event:
    type: EventType
    timestamp: str       # ISO 8601 UTC
    data: dict[str, Any]
    task_id: int | None = None
    module: str = ""
```

### HookRegistry

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

---

## Mailbox Message Structure

```python
@dataclass
class Message:
    id: str              # Auto-generated UUID
    from_module: str
    to_module: str
    content: str
    message_type: str    # "request", "response", or "notification"
    priority: str        # "low", "normal", "high", or "urgent"
    status: str          # "pending", "read", or "acknowledged"
    in_reply_to: str     # Optional parent message ID
    task_id: int | None  # Optional associated task ID
    created_at: str      # ISO 8601 timestamp
```

---

## Structured QA Feedback Model

```python
class FailureCategory(str, Enum):
    TEST_FAILURE = "test_failure"
    LINT_ERROR = "lint_error"
    TYPE_ERROR = "type_error"
    BUILD_ERROR = "build_error"
    BOUNDARY_VIOLATION = "boundary_violation"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"

@dataclass
class StructuredFeedback:
    category: FailureCategory
    summary: str
    specific_errors: list[str]
    remediation_steps: list[str]
    files_to_check: list[str]
    retry_number: int
```

### Retry Prompt Strategy

- **Retry 1**: Full original prompt + structured feedback with errors, files, and remediation
- **Retry 2+**: Simplified prompt targeting only failing files and specific errors. Skips the original prompt to focus agent effort on fixes.
