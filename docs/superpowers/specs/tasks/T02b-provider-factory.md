---
task: T2b
title: Provider Factory Refactor
depends_on: [2]
status: pending
---

## T2b: Provider Factory Refactor

**ID:** 2b
**Depends on:** [2]
**Module:** `src/lindy_orchestrator/providers/`, `src/lindy_orchestrator/dispatch_core.py`

### Description

Refactor `create_provider()` to accept `RoleProviderConfig` instead of `DispatcherConfig`. Inline the dispatcher wrapper modules into provider implementations. Simplify dispatch_core stall detection to single timeout. This decouples providers from the old dispatcher config and enables role-specific provider creation.

### Generator Prompt

1. **Refactor `create_provider()`** in `providers/__init__.py`:
   - Change signature: `create_provider(config: RoleProviderConfig) -> DispatchProvider`
   - Add overload/compat: `create_provider(config: DispatcherConfig)` still works (extracts provider + timeout)
   - Keep the factory simple: `if config.provider == "claude_cli": return ClaudeCLIProvider(...)`

2. **Refactor `ClaudeCLIProvider`** (`providers/claude_cli.py`):
   - Remove dependency on `dispatcher.py` module — inline the `streaming_dispatch()` and `parse_event()` calls directly, or import from `dispatch_core.py`
   - Accept `RoleProviderConfig` in constructor (provider name + timeout)
   - Keep `permission_mode` and `max_output_chars` as optional kwargs (only Generator needs them)

3. **Refactor `CodexCLIProvider`** (`providers/codex_cli.py`):
   - Same pattern: remove dependency on `codex_dispatcher.py`, inline or use `dispatch_core.py`
   - Accept `RoleProviderConfig`

4. **Simplify `dispatch_core.py` stall detection:**
   - Remove two-stage `warn_after`/`kill_after` logic
   - Replace with single `stall_timeout_seconds` parameter
   - If no events for `stall_timeout_seconds` → kill the process
   - Remove `STALL_WARNING` event emission (only `STALL_KILLED` remains)
   - Keep the `long_running_tool_multiplier` (1.5x for Bash) — it's a good heuristic

5. **Keep `dispatcher.py` and `codex_dispatcher.py` files** — but they become thin shims importing from providers. Mark with `# DEPRECATED: use providers/ directly`. Don't delete yet (tests import `_parse_event` and `_read_stderr` from them).

6. **Add backward-compat re-exports** if tests import from old locations.

7. Write `tests/test_provider_factory.py`:
   - `create_provider(RoleProviderConfig(provider="claude_cli"))` returns ClaudeCLIProvider
   - `create_provider(RoleProviderConfig(provider="codex_cli"))` returns CodexCLIProvider
   - `create_provider(old_DispatcherConfig)` still works
   - Stall detection with single timeout (mock time, verify kill)

### Acceptance Criteria

- `create_provider(RoleProviderConfig(...))` works for both providers
- `create_provider(DispatcherConfig(...))` still works (backward compat)
- Providers no longer depend on `dispatcher.py`/`codex_dispatcher.py` for core logic
- dispatch_core has single stall timeout (no warn stage)
- Existing dispatcher/codex_dispatcher tests still pass (via shims)
- ≥8 unit tests in test_provider_factory.py

### Evaluator Prompt

Verify: (1) `create_provider(RoleProviderConfig(provider="claude_cli"))` returns a valid provider, (2) `create_provider(RoleProviderConfig(provider="codex_cli"))` works, (3) `grep -n "stall_escalation\|warn_after\|STALL_WARNING" src/lindy_orchestrator/dispatch_core.py` returns nothing or only comments, (4) `from lindy_orchestrator.dispatcher import _parse_event` still works (shim), (5) all existing tests pass.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_provider*.py tests/test_dispatcher*.py tests/test_codex*.py tests/ -x -q --tb=short"
  timeout: 120
