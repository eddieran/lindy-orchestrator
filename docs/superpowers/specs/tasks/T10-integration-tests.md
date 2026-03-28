---
task: T10
title: Integration Tests
depends_on: [8, 9]
status: pending
---

## T10: Integration Tests

**ID:** 10
**Depends on:** [8, 9]
**Module:** `tests/`

### Description

Write integration tests that verify the full pipeline with mocked providers. Test DAG ordering, retry loops, context isolation, retryable semantics, command queue, session checkpoint/resume, dashboard events.

### Generator Prompt

Create `tests/test_integration_pipeline.py`:

1. **test_full_pipeline_happy_path:**
   - Mock planner returns 2-task DAG (T1 independent, T2 depends on T1)
   - Mock generator returns success for both
   - Mock evaluator returns score=90 for both
   - Assert: T1 completes before T2 starts, both COMPLETED

2. **test_retry_loop:**
   - Mock evaluator returns score=30 (retryable=True) on first call, score=85 on second
   - Assert: generator called twice, feedback from first eval passed to second generate
   - Assert: task ends COMPLETED after 2 attempts

3. **test_max_retries_exceeded:**
   - Mock evaluator always returns score=20, retryable=True
   - max_retries=2
   - Assert: generator called 3 times, task ends FAILED

4. **test_not_retryable:**
   - Mock evaluator returns score=10, retryable=False
   - Assert: generator called once, task ends FAILED immediately

5. **test_context_isolation_generator:**
   - Capture the prompt passed to generator provider
   - Assert: prompt contains generator_prompt text
   - Assert: prompt does NOT contain acceptance_criteria text
   - Assert: prompt does NOT contain evaluator_prompt text
   - Assert: prompt DOES contain STATUS.md content

6. **test_context_isolation_evaluator:**
   - Capture the prompt passed to evaluator provider
   - Assert: prompt contains acceptance_criteria + evaluator_prompt
   - Assert: prompt does NOT contain generator_prompt text
   - Assert: prompt contains scoring rubric

7. **test_parallel_execution:**
   - 3-task DAG: T1, T2, T3 all independent
   - Use threading.Barrier(3) inside mock generator — all three must reach barrier (proves parallel)
   - Assert: all three COMPLETED

8. **test_dependency_ordering:**
   - T1 -> T2 -> T3 chain
   - Assert: completion order is T1, T2, T3

9. **test_signal_handling:**
   - Send SIGINT during execution
   - Assert: running task completes, pending tasks marked appropriately

10. **test_command_queue_pause_resume:**
    - Pause after T1 completes, verify T2 doesn't start
    - Resume, verify T2 starts

11. **test_command_queue_skip:**
    - Skip T2 before it starts
    - Assert: T2 status is SKIPPED, T3 (depends on T2) is SKIPPED

12. **test_command_queue_force_pass:**
    - Mock evaluator returns score=30 for T1
    - Force-pass T1 during retry
    - Assert: T1 ends COMPLETED

13. **test_session_checkpoint_resume:**
    - Run pipeline, save checkpoint after T1 completes
    - Create new Orchestrator, resume from checkpoint
    - Assert: T1 not re-executed (status already COMPLETED), T2 executes

14. **test_dashboard_events:**
    - Collect all emitted events via hooks
    - Assert order: SESSION_START, TASK_STARTED, PHASE_CHANGED(generating), PHASE_CHANGED(evaluating), EVAL_SCORED, TASK_COMPLETED, SESSION_END

15. **test_different_providers_per_role:**
    - Config: planner=claude_cli, generator=codex_cli, evaluator=claude_cli
    - Assert: each runner creates the correct provider

16. **test_eval_feedback_passed_to_generator:**
    - Mock evaluator returns EvalFeedback with failed_criteria=["tests must pass"], evidence="3 tests failed"
    - Capture generator prompt on retry
    - Assert: "tests must pass" appears in retry prompt
    - Assert: "3 tests failed" appears in retry prompt

### Acceptance Criteria

- All 16 integration tests pass
- Context isolation verified with actual prompt content assertions
- Retry + retryable semantics verified
- Parallel execution verified with barrier (not timing)
- Command queue tested (pause, skip, force_pass)
- Checkpoint/resume tested
- No flaky tests

### Evaluator Prompt

Verify: (1) all 16 tests exist and pass, (2) context isolation tests use actual string assertions, (3) parallel test uses barrier not sleep, (4) tests use mock providers, (5) no imports from deleted/deprecated modules.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_integration_pipeline.py -x -v --tb=short"
  timeout: 180
