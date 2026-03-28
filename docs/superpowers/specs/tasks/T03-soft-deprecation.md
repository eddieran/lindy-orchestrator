---
task: T3
title: Soft Feature Deprecation
depends_on: [2]
status: pending
---

## T3: Soft Feature Deprecation

**ID:** 3
**Depends on:** [2]
**Module:** multiple files (no file deletions)

### Description

Soft-deprecate removed features: make config sections no-op, disable code paths, but do NOT delete any files. This allows T4/T5/T6 to work against a codebase that still compiles and passes tests. Hard deletion happens in T12.

### Generator Prompt

1. **Config** — already done in T2 (warnings for removed sections). No additional work.

2. **scheduler_helpers.py** — make `inject_*` functions no-op:
   - `inject_mailbox_messages()` → add `return` at top, keep function signature
   - `inject_claude_md()` → add `return` at top (gather_* variant is the real one)
   - `inject_status_content()` → add `return` at top
   - `inject_branch_delivery()` → add `return` at top
   - Add `# DEPRECATED: no-op, will be removed in v0.15` comment to each

3. **Layer check** — disable auto-injection:
   - In `scheduler_helpers.py:inject_qa_gates()`, comment out the layer_check injection block
   - In `qa/__init__.py`, keep the `from . import layer_check` import (file still exists)

4. **Mailbox references** — no-op:
   - In `cli_status.py`, skip mailbox section rendering (add early return / `if False` guard)
   - In `hooks.py`, keep MAILBOX event types in enum (don't break deserialization)

5. **Planner API mode** — disable:
   - In `planner.py`, make `_plan_via_api()` raise `NotImplementedError("API mode removed, use CLI")`
   - In config validator (T2), if `planner.mode == "api"`, log warning and override to "cli"

6. **OTel** — disable:
   - In `scheduler.py`, wrap OTel block with `if False:` or remove the block (it's guarded by `config.otel.enabled` which is already false by default)

7. **Do NOT delete any files.** All modules remain importable.

8. Update tests:
   - Tests calling `inject_*` functions: update assertions (they now no-op)
   - Tests for layer_check: still pass (module exists, just not auto-injected)
   - Tests for planner API mode: update to expect NotImplementedError

### Acceptance Criteria

- No file deletions in this task
- All `inject_*` functions are no-ops
- Layer check not auto-injected but module still importable
- Mailbox section in cli_status skipped
- Planner API mode raises NotImplementedError
- ALL existing tests pass (updated as needed)
- `import lindy_orchestrator.mailbox` still works
- `import lindy_orchestrator.qa.layer_check` still works

### Evaluator Prompt

Verify: (1) no files deleted (`git diff --stat` shows only modifications, no deletions), (2) `inject_mailbox_messages` exists but is a no-op (returns immediately), (3) `_plan_via_api` raises NotImplementedError, (4) all tests pass, (5) `from lindy_orchestrator.mailbox import Mailbox` still works.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/ -x -q --tb=short"
  timeout: 180
