---
task: T12
title: Hard Delete + Cleanup + PR
depends_on: [11]
status: pending
---

## T12: Hard Delete + Cleanup + PR

**ID:** 12
**Depends on:** [11]
**Module:** entire codebase

### Description

Final cleanup: hard-delete all deprecated files and code, remove old models, update all imports, verify all tests pass, create PR.

### Generator Prompt

1. **Delete deprecated files:**
   - `src/lindy_orchestrator/scheduler.py`
   - `src/lindy_orchestrator/planner.py`
   - `src/lindy_orchestrator/dispatcher.py`
   - `src/lindy_orchestrator/codex_dispatcher.py`
   - `src/lindy_orchestrator/mailbox.py`
   - `src/lindy_orchestrator/qa/layer_check.py`
   - `src/lindy_orchestrator/trackers/` (entire directory)
   - `src/lindy_orchestrator/otel.py` (if exists)

2. **Clean up models.py:**
   - Remove `TaskItem` class
   - Remove `tasks_v2` transitional field from `TaskPlan` -- rename `tasks` to `list[TaskSpec]`
   - Remove any backward-compat aliases
   - Keep: TaskStatus, QACheck, QAResult, DispatchResult (still used by providers)
   - Remove `StructuredFeedback` if fully replaced by `EvalFeedback`

3. **Clean up scheduler_helpers.py:**
   - Remove all `inject_*` functions (already no-op'd in T3)
   - Remove all `gather_*` functions (now in runners)
   - Remove `inject_qa_gates` (now in evaluator_runner)
   - If file is now nearly empty, delete it and move any remaining utilities to `helpers.py` or inline

4. **Clean up config.py:**
   - Remove `MailboxConfig`, `TrackerConfig`, `OTelConfig`, `LayerCheckConfig`, `StallEscalationConfig`
   - Remove `DispatcherConfig` (or keep as thin alias if needed for migration)
   - Remove backward-compat validators for old sections

5. **Clean up hooks.py:**
   - Remove `MAILBOX_*` event types if they exist
   - Remove `STALL_WARNING` event type (only STALL_KILLED remains)

6. **Clean up discovery/:**
   - `discovery/generator.py`: remove mailbox directory creation
   - `discovery/templates/agent_docs.py`: remove layer_check/mailbox references

7. **Update all imports:**
   - `grep -rn "from.*scheduler import\|from.*planner import\|from.*dispatcher import\|from.*mailbox import\|from.*layer_check import\|from.*trackers import" src/ tests/`
   - Fix each broken import

8. **Delete obsolete test files:**
   - `tests/test_scheduler*.py`
   - `tests/test_planner*.py` (old planner tests)
   - `tests/test_dispatcher*.py`
   - `tests/test_codex_dispatcher*.py`
   - `tests/test_inject_claude_md.py`
   - `tests/test_layer_check*.py`
   - `tests/test_mailbox*.py`
   - `tests/test_tracker*.py`
   - `tests/test_otel*.py`
   - Any other tests that only test deleted code

9. **Final full test suite run:**
   - `uv run python -m pytest tests/ -x -q --tb=short`
   - Verify no import errors, no test failures

10. **Update README.md:**
    - Document new YAML format with planner/generator/evaluator
    - Document pipeline architecture (brief)
    - Remove references to deleted features
    - Update CLI command list

11. **Version bump:** `pyproject.toml` -> `0.15.0`

12. **Create PR** with title: `feat: pipeline architecture -- Planner/Generator/Evaluator role separation`

### Acceptance Criteria

- All deleted files gone: `ls src/lindy_orchestrator/scheduler.py` -> not found
- No import of deleted modules: `grep -rn "from.*scheduler import\|from.*planner import\|from.*dispatcher import\|from.*mailbox import" src/` -> nothing
- No `TaskItem` in non-test code: `grep -rn "TaskItem" src/` -> nothing
- No `inject_*` functions: `grep -rn "inject_mailbox\|inject_claude_md\|inject_status_content\|inject_branch_delivery" src/` -> nothing
- All tests pass
- README updated
- Version is 0.15.0
- PR created, CI passes

### Evaluator Prompt

Verify: (1) `grep -rn "from.*scheduler import" src/` returns nothing, (2) `grep -rn "TaskItem" src/` returns nothing, (3) `grep -rn "from.*planner import" src/` returns nothing (only planner_runner), (4) `grep -rn "from.*mailbox import" src/` returns nothing, (5) all tests pass, (6) README documents pipeline architecture, (7) version is 0.15.0, (8) `python -c "import lindy_orchestrator"` works without error.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/ -x -q --tb=short"
  timeout: 300
