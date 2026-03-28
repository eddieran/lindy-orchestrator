# lindy-orchestrator Reference

Detailed API reference and configuration guide. For an overview, see [README.md](../README.md).

---

## Configuration Schema

Complete `.orchestrator/config.yaml` with all fields and defaults:

```yaml
project:
  name: "my-project"
  branch_prefix: "af"              # Task branches: af/task-1, af/task-2, ...

modules:
  - name: backend
    path: backend/
    status_md: STATUS.md           # Default: STATUS.md
    repo: myorg/my-backend         # GitHub slug (required for ci_check gate)
    ci_workflow: ci.yml            # Default: ci.yml
    role: ""                       # Set to "qa" to mark as QA dispatcher target
  - name: frontend
    path: frontend/
    repo: myorg/my-frontend

planner:
  provider: claude_cli             # "claude_cli" (default) or "codex_cli"
  timeout_seconds: 120             # Planner timeout
  prompt: ""                       # Custom planner prompt (empty = use default)

generator:
  provider: claude_cli             # "claude_cli" (default) or "codex_cli"
  timeout_seconds: 1800            # Hard timeout per task dispatch (30 min)
  stall_timeout: 600               # Kill process after 10 min of silence
  permission_mode: bypassPermissions
  max_output_chars: 50000          # Truncate agent output beyond this
  prompt_prefix: ""                # Prepended to every generator prompt

evaluator:
  provider: claude_cli             # "claude_cli" (default) or "codex_cli"
  timeout_seconds: 300             # Evaluator agent timeout
  pass_threshold: 80               # Score (0-100) below which task retries
  prompt_prefix: ""                # Prepended to every evaluator prompt

qa_gates:
  ci_check:
    timeout_seconds: 900           # CI polling timeout (15 min)
    poll_interval: 30              # Poll every 30 seconds
  structural_check:
    max_file_lines: 500            # Flag files exceeding this line count
    sensitive_patterns:             # Patterns for sensitive files
      - ".env"
      - "*.key"
      - "*.pem"
  custom:                          # User-defined command-based gates
    - name: pytest
      command: "pytest --tb=short -q"
      cwd: "{module_path}"        # Resolved to module's absolute path
      timeout: 600
      diff_only: false             # If true, only check changed files
      modules: []                  # Empty = apply to all modules
    - name: lint
      command: "ruff check {changed_files}"
      diff_only: true              # Only lint files changed on this branch
      modules: []

safety:
  dry_run: false
  max_retries_per_task: 2          # Retries with evaluator feedback on failure
  max_parallel: 3                  # Max concurrent task dispatches

lifecycle_hooks:
  after_create: ""                 # Shell command after worktree creation
  before_run: ""                   # Shell command before agent dispatch
  after_run: ""                    # Shell command after successful dispatch
  before_remove: ""                # Shell command before worktree removal

logging:
  dir: ".orchestrator/logs"
  session_dir: ".orchestrator/sessions"
  log_file: "actions.jsonl"
```

### Backward Compatibility

Old-format YAML with `dispatcher:` instead of `generator:` still loads with a deprecation warning. The `dispatcher` fields are mapped to `generator` automatically.

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

## Pipeline Roles

### Planner

Decomposes a goal into a dependency-ordered task DAG. Each task includes:
- `generator_prompt` ... what the Generator should do
- `acceptance_criteria` ... human-readable success criteria
- `evaluator_prompt` ... what the Evaluator should verify

### Generator

Dispatches code agents with strict context isolation. The Generator sees:
- `generator_prompt` (from Planner)
- CLAUDE.md / CODEX.md instructions (selected by `generator.provider`)
- Module STATUS.md
- Branch delivery instructions
- Retry feedback (if retrying)

The Generator **never** sees acceptance_criteria or evaluator_prompt.

### Evaluator

Two-phase evaluation:
1. **QA gates** ... run in parallel (ci_check, structural_check, command_check, custom)
2. **Agent scoring** ... evaluator agent scores 0-100 against acceptance criteria

Scoring rubric:
- 90-100: All criteria met, code clean, tests pass
- 70-89: Most criteria met, minor issues
- 50-69: Some criteria met, notable gaps
- 30-49: Significant gaps, multiple failing criteria
- 0-29: Fundamental issues

`passed` is computed in code from `score >= pass_threshold`, never trusted from LLM output.

---

## DispatchProvider Protocol

Defined in `providers/base.py`:

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
        """Streaming dispatch with stall detection."""
        ...

    def dispatch_simple(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
    ) -> DispatchResult:
        """Blocking dispatch for quick tasks (planning, evaluation)."""
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
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
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
    STALL_KILLED      = "stall_killed"
    TASK_HEARTBEAT    = "task_heartbeat"
    CHECKPOINT_SAVED  = "checkpoint_saved"
    SESSION_START     = "session_start"
    SESSION_END       = "session_end"
    PHASE_CHANGED     = "phase_changed"
    EVAL_SCORED       = "eval_scored"
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
- **Retry 2+**: Simplified prompt targeting only failing files and specific errors
