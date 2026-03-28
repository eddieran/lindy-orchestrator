---
task: T11
title: End-to-End Tests
depends_on: [10]
status: pending
---

## T11: End-to-End Tests

## Context & Prerequisites

**Architecture spec:** `docs/superpowers/specs/2026-03-28-pipeline-architecture-design.md` — read this first for full design context.

**Tech stack:**
- Models: Python dataclasses (`from dataclasses import dataclass, field`)
- Config: Pydantic v2 (`from pydantic import BaseModel, model_validator`)
- Testing: pytest via `uv run python -m pytest`
- Python 3.11+, type hints throughout

**Project structure:** All source in `src/lindy_orchestrator/`, tests in `tests/`.

**Prior task outputs:** All pipeline code exists: Orchestrator, PlannerRunner, GeneratorRunner, EvaluatorRunner, WebDashboard, CommandQueue, all new models.

**Fixture YAML (new format):**
```yaml
project:
  name: test-project
  branch_prefix: test

modules:
  - name: backend
    path: backend/

planner:
  provider: claude_cli
  timeout_seconds: 60

generator:
  provider: claude_cli
  timeout_seconds: 120
  stall_timeout: 30
  permission_mode: bypassPermissions

evaluator:
  provider: claude_cli
  timeout_seconds: 60
  pass_threshold: 80

safety:
  max_retries_per_task: 2
  max_parallel: 2
```

**Old format YAML (for backward compat test):**
```yaml
project:
  name: test-project
dispatcher:
  provider: claude_cli
  timeout_seconds: 120
modules:
  - name: backend
    path: backend/
```

**Mock CLI binary pattern (from existing test infrastructure):**
The existing test suite mocks CLI binaries by:
1. Creating a shell script in `tmp_path/bin/claude` that echoes canned JSON
2. Adding `tmp_path/bin` to PATH via `monkeypatch.setenv("PATH", ...)`
Search existing tests for pattern: `grep -rn "mock.*claude\|bin/claude\|monkeypatch.*PATH" tests/`

**WebDashboard for SSE test:**
```python
from lindy_orchestrator.web.server import WebDashboard
dashboard = WebDashboard(plan, hooks, command_queue=cmd_queue, port=0)  # port=0 for random
dashboard.start()
# Connect: urllib.request.urlopen(f"http://localhost:{dashboard.port}/events")
```

**Checkpoint file format (JSON):**
```json
{
  "_checkpoint_version": 2,
  "session_id": "20260328_120000_abc12345",
  "goal": "add a feature",
  "plan": {"goal": "...", "tasks": [...]},
  "states": [
    {"spec": {...}, "status": "completed", "phase": "done", "attempts": [...], ...},
    {"spec": {...}, "status": "pending", "phase": "pending", "attempts": [], ...}
  ]
}
```

**ID:** 11
**Depends on:** [10]
**Module:** `tests/`

### Description

E2E tests with fixture projects, real git operations, and mock CLI binaries. Exercise the full code path including file I/O, config loading, and process management.

### Generator Prompt

Create `tests/test_e2e_pipeline.py`:

1. **test_e2e_plan_and_execute:**
   - Create fixture project in tmp_path: orchestrator.yaml (new format), two module dirs, git init
   - Mock `claude` binary returns canned plan JSON with generator_prompt + acceptance_criteria + evaluator_prompt
   - Mock `claude` binary for generator: creates a file, commits
   - Mock `claude` binary for evaluator: returns `{"score": 90, "feedback": {"summary": "looks good", "specific_errors": [], "failed_criteria": [], "evidence": "all tests pass", "missing_behaviors": []}}`
   - Run `Orchestrator(config).run("add a feature")`
   - Assert: plan created, tasks executed, worktrees created/cleaned, session saved, ExecutionResult returned

2. **test_e2e_retry_with_feedback:**
   - Same fixture, mock evaluator returns score=40 first (with failed_criteria=["must add tests"]), score=85 second
   - Assert: generator prompt on second call includes "must add tests"
   - Assert: generator prompt on second call does NOT include acceptance_criteria
   - Assert: task completes after 2 attempts

3. **test_e2e_config_backward_compat:**
   - Create fixture with old-format YAML (dispatcher: instead of generator:)
   - Assert: loads correctly, plan and execute work

4. **test_e2e_web_dashboard_sse:**
   - Start Orchestrator with WebDashboard
   - Connect to SSE endpoint via urllib
   - Assert: init event contains tasks with acceptance_criteria
   - Assert: phase_changed events fire
   - Assert: eval_scored events fire with scores

5. **test_e2e_session_resume:**
   - Run orchestrator, interrupt after first task
   - Resume from session file
   - Assert: first task not re-dispatched, second task executes

6. **test_e2e_checkpoint_serialization_roundtrip:**
   - Run pipeline to completion
   - Load checkpoint JSON
   - Deserialize all TaskState via from_dict
   - Assert: all fields preserved, nested AttemptRecord intact

### Acceptance Criteria

- All 6 e2e tests pass
- Tests use real file I/O and real git operations (in tmp_path)
- Tests verify full pipeline from config load -> plan -> generate -> evaluate -> report
- Backward compatibility with old config format verified
- Context isolation asserted at e2e level
- Checkpoint serialization roundtrip verified
- No test takes >30s

### Evaluator Prompt

Verify: (1) all 6 tests pass, (2) tests create real git repos in tmp_path, (3) all `claude` binaries are mocked, (4) context isolation asserted (grep generator prompt for absence of acceptance_criteria), (5) checkpoint roundtrip test asserts field-level equality.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_e2e_pipeline.py -x -v --tb=short"
  timeout: 180
