---
task: T5
title: Generator Runner
depends_on: [2b]
status: pending
---

## T5: Generator Runner

**ID:** 5
**Depends on:** [2b]
**Module:** `src/lindy_orchestrator/generator_runner.py` (new)

### Description

Create `GeneratorRunner` — extracts dispatch + prompt building logic. Strict context isolation: sees only `generator_prompt`, CLAUDE.md (selected by `generator.provider`), module STATUS.md, branch instructions, and feedback on retry. Never sees `acceptance_criteria` or `evaluator_prompt`.

### Generator Prompt

Create `src/lindy_orchestrator/generator_runner.py`:

```python
class GeneratorRunner:
    def __init__(self, config: GeneratorConfig, project_config: OrchestratorConfig): ...
    def execute(self, task: TaskSpec, worktree: Path, branch_name: str,
                feedback: EvalFeedback | None = None,
                on_progress: Callable | None = None) -> GeneratorOutput: ...
    def _build_prompt(self, task: TaskSpec, worktree: Path, branch_name: str,
                      feedback: EvalFeedback | None) -> str: ...
```

Key behaviors:
1. `_build_prompt()`:
   - Start with `config.prompt_prefix` (from YAML, may be empty)
   - Append CLAUDE.md / CODEX.md instructions — determine which based on `config.provider` (not `project_config.dispatcher.provider`). Read from `.orchestrator/{provider_dir}/root.md` and `.orchestrator/{provider_dir}/{task.module}.md`. This is the same logic as `gather_claude_md` but reads `config.provider` instead of `project_config.dispatcher.provider`.
   - Append module STATUS.md content (from `.orchestrator/status/{task.module}.md`) — Generator needs to know current module state
   - Append `task.generator_prompt` (from Planner)
   - Append branch delivery instructions (reuse logic from `gather_branch_delivery`)
   - If retry and feedback is not None: append structured retry section with `feedback.summary`, `feedback.specific_errors`, `feedback.remediation_steps`, `feedback.failed_criteria` (if any), `feedback.files_to_check`. Format clearly.
   - **MUST NOT include `task.acceptance_criteria` or `task.evaluator_prompt`**

2. `execute()`:
   - Create provider via `create_provider(config.to_role_provider_config())`
   - Also pass `permission_mode` and `max_output_chars` to provider (GeneratorConfig-specific)
   - Dispatch with streaming via `provider.dispatch()`, passing `stall_seconds=config.stall_timeout`
   - Collect output
   - Compute diff via `git diff HEAD` in worktree (subprocess, cwd=worktree)
   - Return `GeneratorOutput(success, output, diff, cost_usd, duration_seconds, event_count, last_tool)`

3. Progress callback: The `on_progress` callback receives tool names and event counts from the provider's `on_event`. Keep it simple — extract tool name, pass to callback. No complex HeartbeatTracker.

4. Write `tests/test_generator_runner.py`:
   - Prompt construction includes generator_prompt
   - Prompt does NOT contain acceptance_criteria (assert "acceptance_criteria_text" not in prompt)
   - Prompt does NOT contain evaluator_prompt
   - CLAUDE.md selected by generator.provider, not dispatcher.provider
   - STATUS.md content included
   - Retry prompt includes feedback.summary + feedback.failed_criteria
   - Diff collection from worktree (mock subprocess)
   - Provider created from GeneratorConfig

### Acceptance Criteria

- `GeneratorRunner.execute()` returns `GeneratorOutput` with success, output, diff, cost
- Prompt NEVER contains acceptance_criteria or evaluator_prompt (test assertion with actual strings)
- CLAUDE.md/CODEX.md selected by `generator.provider`
- STATUS.md content included in prompt
- Custom `prompt_prefix` prepended
- Retry includes structured feedback including failed_criteria
- Provider created from `GeneratorConfig.provider`
- ≥14 unit tests

### Evaluator Prompt

Verify: (1) build a prompt for a TaskSpec with acceptance_criteria="must pass all tests" — assert that exact string does NOT appear in the built prompt, (2) build a prompt with config.provider="codex_cli" — assert it reads from `.orchestrator/codex/` not `.orchestrator/claude/`, (3) provider created from config, (4) STATUS.md content IS in the prompt, (5) existing tests unaffected.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_generator_runner.py tests/ -x -q --tb=short"
  timeout: 120
