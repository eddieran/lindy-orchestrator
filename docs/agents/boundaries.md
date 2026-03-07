# Module Boundaries -- lindy-orchestrator

> Negative constraints: what does NOT belong where.
> These rules prevent scope creep and cross-module pollution.

## Module Isolation

- Single-package project -- all code under `src/lindy_orchestrator/`
- No cross-package imports outside `src/lindy_orchestrator/`
- Subpackages (`qa/`, `providers/`, `trackers/`, `status/`, `discovery/`,
  `entropy/`) use relative imports within the package

## Provider Boundary

- New dispatch backends must implement `DispatchProvider` Protocol (`providers/base.py`)
- CLI, scheduler, and planner never import provider implementations directly
- Always use `create_provider()` factory from `providers/__init__.py`

## QA Gate Boundary

- New gates use the `@register("gate_name")` decorator in `qa/__init__.py`
- The scheduler calls `run_qa_gate()` without knowing gate implementations
- Gate resolution: custom YAML gates > built-in registered gates > unknown (fail)

## Tracker Boundary

- New tracker backends must implement `TrackerProvider` Protocol (`trackers/base.py`)
- CLI creates trackers via `create_tracker()` factory from `trackers/__init__.py`
- Tracker operations (fetch issues, add comments, close issues) go through the protocol

## Sensitive Paths (NEVER modify)

- `.env`
- `.env.*`
- `*.key`
- `*.pem`

## Sensitive Directories (NEVER commit contents)

- `.orchestrator/logs/` -- action logs (JSONL)
- `.orchestrator/sessions/` -- session state files (JSON)

## Exceptions

The following are allowed exceptions to boundary rules:

- **Shared config files** at project root (e.g., `orchestrator.yaml`, `pyproject.toml`)
- **CI/CD pipeline files** that reference multiple modules
- **Documentation files** (`docs/`, `README.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`)
- **Mailbox directory** (`.orchestrator/mailbox/`) -- agents may read/write mailbox
  JSONL files for their own module. The `Mailbox` class enforces path safety
  with `_SAFE_MODULE_RE` validation and `is_relative_to()` checks.
- **Tracker integration** -- the `cli_ext.py:run_issue` command syncs results
  back to the issue tracker (comments, status updates). This crosses the
  boundary between orchestrator and external tracker but is explicitly
  allowed via the `TrackerProvider` protocol.
- **STATUS.md files** -- agents update their own module's STATUS.md. The
  `status/writer.py` module validates content before writing.
- **Report files** (`.orchestrator/reports/`) -- the reporter writes Markdown
  summaries after execution. These are read-only artifacts.
- **Plan files** (`.orchestrator/plans/`) -- the CLI persists plan JSON for
  resume capability. The `gc` command cleans up orphaned plans.
