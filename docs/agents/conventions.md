# Coding Conventions -- lindy-orchestrator

> Coding standards derived from the current codebase. Follow these
> when contributing or when agents generate code.

## Python Style

- **Python 3.11+** -- use modern syntax (PEP 604 union types, match/case, etc.)
- **`from __future__ import annotations`** -- every module starts with this
- Use type hints for all function signatures
- Follow PEP 8 naming conventions
- Line length: 100 characters (enforced by Ruff)
- Formatter: Ruff (`ruff format`)
- Linter: Ruff (`ruff check`)

## Data Modeling

- **Pydantic `BaseModel`** for configuration and validated input (`config.py`)
  - Use `model_validate()`, not deprecated `parse_obj()`
  - Use `Field(default_factory=...)` for mutable defaults
- **`@dataclass`** for value objects and internal data structures (`models.py`, `hooks.py`)
  - Use `field(default_factory=list)` for mutable defaults
- **`Protocol`** (from `typing`) for interfaces (`providers/base.py`, `trackers/base.py`)
  - Mark with `@runtime_checkable` for isinstance checks
- **`str, Enum`** for string enumerations (`TaskStatus`, `EventType`, `PlannerMode`)

## File Organization

- Prefer `pathlib.Path` over `os.path` for all filesystem operations
- Use relative imports within the package (`from .models import ...`)
- Split files at 500 lines: extract helpers into `*_helpers.py` sibling files
  (e.g., `scheduler.py` / `scheduler_helpers.py`, `scanner.py` / `scanner_helpers.py`)
- Group related functionality into subpackages (`qa/`, `status/`, `providers/`,
  `trackers/`, `entropy/`, `discovery/`)

## Error Handling

- Use `logging.getLogger(__name__)` for module-level loggers
- Log warnings for recoverable errors; raise exceptions for unrecoverable ones
- Subprocess calls: always set `timeout`, use `capture_output=True`
- Security: validate user-provided paths with regex before substitution
  (see `mailbox.py:_SAFE_MODULE_RE`, `session.py:_SAFE_SESSION_ID_RE`)

## CLI Commands

- Use Typer for CLI definitions
- Use Rich for terminal output (`Console`, `Table`, `Panel`, `Live`)
- Commands that can run in TTY and non-TTY: check `console.is_terminal`
  and fall back to plain text output

## Testing

- Test framework: pytest
- Mocking: pytest-mock
- Coverage: pytest-cov (threshold: 70%)
- Test file naming: `tests/test_*.py`
- Fixtures in `tests/conftest.py`

## Git Conventions

- Branch naming: `{branch_prefix}/task-{id}` (default prefix: `af`)
- Agents must create, commit to, and push their task branch
- Never force-push task branches
