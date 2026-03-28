---
task: T2
title: Configuration Schema
depends_on: [1]
status: pending
---

## T2: Configuration Schema

**ID:** 2
**Depends on:** [1]
**Module:** `src/lindy_orchestrator/config.py`

### Description

Update `OrchestratorConfig` to support three-role configuration. Add `PlannerConfig`, `GeneratorConfig`, `EvaluatorConfig`. Maintain backward compatibility — old `dispatcher` key maps to `generator`. Canonicalize gate names.

### Generator Prompt

1. Add `PlannerConfig` pydantic model:
   - `provider`: str = "claude_cli"
   - `timeout_seconds`: int = 120
   - `prompt`: str = "" (empty = use default template)

2. Add `GeneratorConfig` pydantic model:
   - `provider`: str = "claude_cli"
   - `timeout_seconds`: int = 1800
   - `stall_timeout`: int = 600 (single timeout, replaces two-stage)
   - `permission_mode`: str = "bypassPermissions"
   - `max_output_chars`: int = 200_000
   - `prompt_prefix`: str = ""

3. Add `EvaluatorConfig` pydantic model:
   - `provider`: str = "claude_cli"
   - `timeout_seconds`: int = 300
   - `pass_threshold`: int = 80
   - `prompt_prefix`: str = ""

4. In `OrchestratorConfig`:
   - Add `planner: PlannerConfig = PlannerConfig()`
   - Add `generator: GeneratorConfig = GeneratorConfig()`
   - Add `evaluator: EvaluatorConfig = EvaluatorConfig()`
   - Keep `dispatcher: DispatcherConfig` for backward compat
   - Add `model_validator` that maps old `dispatcher` to `generator` fields if `generator` not explicitly set. Log deprecation warning.
   - Add `model_validator` that warns (log.warning, don't error) if `mailbox`, `tracker`, or `otel` sections present in YAML
   - Canonicalize gate names in `QAGatesConfig`: accept both `structural` and `structural_check` as keys, normalize to `structural_check` internally

5. Do NOT delete `MailboxConfig`, `TrackerConfig`, `OTelConfig`, `LayerCheckConfig`, `StallEscalationConfig` classes yet — they are soft-deprecated but still importable. Mark with `# DEPRECATED: removed in v0.15` comment.

6. Each role config should have a `to_role_provider_config() -> RoleProviderConfig` method that extracts just `provider` + `timeout_seconds`.

7. Write/update tests in `tests/test_config*.py`:
   - New-format YAML loads all three role configs
   - Old-format YAML maps dispatcher → generator with deprecation log
   - Removed sections (mailbox, tracker, otel) warn but don't error
   - Gate name canonicalization (structural → structural_check)

### Acceptance Criteria

- `OrchestratorConfig` loads both new format (planner/generator/evaluator) and old format (dispatcher)
- Old YAML with `dispatcher:` still loads with deprecation warning in log
- New YAML with `planner:/generator:/evaluator:` loads cleanly
- Removed config sections log warning if present, don't error
- Gate names normalized to canonical form
- `config.generator.to_role_provider_config()` returns RoleProviderConfig
- All existing config tests pass or are updated

### Evaluator Prompt

Verify: (1) load a new-format YAML — all three role configs populated, (2) load an old-format YAML — `generator` populated from `dispatcher`, (3) removed sections don't crash, (4) `config.evaluator.pass_threshold` defaults to 80, (5) `config.generator.stall_timeout` defaults to 600, (6) gate name `structural` normalized to `structural_check`, (7) existing tests pass.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_config*.py tests/test_schema*.py tests/ -x -q --tb=short"
  timeout: 120
