---
task: T11
title: End-to-End Tests
depends_on: [10]
status: pending
---

## T11: End-to-End Tests

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
