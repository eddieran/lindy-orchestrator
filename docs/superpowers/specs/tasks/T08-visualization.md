---
task: T8
title: Visualization Update
depends_on: [7]
status: pending
---

## T8: Visualization Update

**ID:** 8
**Depends on:** [7]
**Module:** `src/lindy_orchestrator/dashboard.py`, `src/lindy_orchestrator/dag.py`, `src/lindy_orchestrator/web/server.py`, `src/lindy_orchestrator/reporter.py`

### Description

Update all visualization modules to consume `ExecutionResult`/`TaskState` and support the three-phase pipeline. Add phase display, evaluator scores, attempt history, and interactive controls (via CommandQueue) to the web dashboard.

### Generator Prompt

1. **Terminal Dashboard** (`dashboard.py`):
   - Subscribe to new events: `PHASE_CHANGED`, `EVAL_SCORED`
   - Update `_on_heartbeat`/event handlers to track `phase` and `last_score` per task
   - In-progress tasks annotation: `[Generate → att. 1]` or `[Evaluate → 72/100]`
   - Completed tasks annotation: score if available
   - `Dashboard` now takes `ExecutionResult` (or `list[TaskState]`) instead of `TaskPlan`
   - `_build_summary()`: include total cost

2. **DAG Renderer** (`dag.py`):
   - `_node_text()` — if task has phase info in annotation, include it
   - Accept `TaskState` list (extract spec for display, status from state)
   - No structural changes to tree layout

3. **Web Dashboard** (`web/server.py`):
   Update `_INDEX_HTML`:

   **Sidebar enhancement:**
   - Pipeline phase indicator (colored dots: Plan → Generate → Evaluate)
   - Acceptance criteria section (from TaskSpec, sent via init event)
   - Attempt history table: `[#, score, feedback summary, duration, cost]`
   - Per-phase cost breakdown (generate cost, evaluate cost)

   **New SSE event handling in JS:**
   - `phase_changed` → update node card phase indicator + annotation
   - `eval_scored` → update score display on node card, add to attempt history table

   **Interactive controls via CommandQueue:**
   - Add `<div class="controls">` with buttons: Pause, Resume, Skip, Force Pass
   - `do_POST()` handler:
     - `POST /api/pause` → `server.command_queue.pause()`
     - `POST /api/resume` → `server.command_queue.resume()`
     - `POST /api/task/{id}/skip` → `server.command_queue.skip(int(id))`
     - `POST /api/task/{id}/force-pass` → `server.command_queue.force_pass(int(id))`
     - Return 200 JSON `{"ok": true}`
   - `WebDashboard.__init__` accepts `command_queue: CommandQueue | None`
   - Buttons disabled/enabled based on task state (JS logic)

   **Init event enhancement:**
   - Include `acceptance_criteria` per task
   - Include `attempts` history for resumed sessions
   - Include `phase` per task

4. **Reporter** (`reporter.py`):
   - `generate_execution_summary()` — accept `ExecutionResult` instead of `TaskPlan`
   - Add attempt history: for each task with >1 attempt, show attempt table
   - Add cost breakdown: Planner $X, Generator $Y, Evaluator $Z, Total $T
   - `save_summary_report()` — same changes for Markdown output

5. Write `tests/test_dashboard_pipeline.py`:
   - PHASE_CHANGED event updates task annotation
   - EVAL_SCORED event updates task score
   - POST /api/pause returns 200
   - Reporter includes attempt history

### Acceptance Criteria

- Terminal dashboard shows phase + attempt + score for running tasks
- Web dashboard shows pipeline progress, acceptance criteria, attempt history
- Interactive controls work via POST → CommandQueue (not mutable server fields)
- Reporter includes attempt history and cost breakdown
- SSE events include phase_changed and eval_scored
- Init event includes acceptance_criteria per task
- ≥12 new tests

### Evaluator Prompt

Verify: (1) terminal dashboard text includes phase annotation format, (2) web HTML includes controls div with buttons, (3) `do_POST` handler exists and calls command_queue methods, (4) reporter markdown includes attempt history, (5) init SSE payload includes acceptance_criteria field, (6) no direct mutation of server state by POST handler (uses CommandQueue).

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_dashboard*.py tests/test_reporter*.py tests/ -x -q --tb=short"
  timeout: 120
