# Audit Risk Map — lindy-orchestrator v0.5.2

> **Date:** 2026-03-07 | **Branch:** `af/task-1`
> **Scope:** 54 source files, 34 test files, 505 tests
> **Details:** [AUDIT_RISK_MAP_details.md](AUDIT_RISK_MAP_details.md)

## Automated Check Results

| Check | Result |
|-------|--------|
| `ruff check src/ tests/` | All checks passed (0 warnings) |
| `ruff format --check src/ tests/` | 88 files already formatted |
| `pytest tests/ -x -q --tb=short` | 505 passed |

---

## Summary

| Risk Level | Count |
|------------|-------|
| **HIGH** | 14 |
| **MEDIUM** | 27 |
| **LOW** | 23 |
| **Total** | 64 |

| Category | H | M | L | Total |
|----------|---|---|---|-------|
| Security concerns | 1 | 5 | 3 | 9 |
| Exception handling gaps | 1 | 5 | 3 | 9 |
| Test coverage gaps | 4 | 6 | 1 | 11 |
| Logging gaps | 3 | 3 | 2 | 8 |
| Dependency health | 1 | 3 | 3 | 7 |
| Long functions/classes (>80 lines) | 0 | 5 | 2 | 7 |
| Unused functions/dead code | 0 | 3 | 5 | 8 |
| Duplicated logic | 0 | 3 | 2 | 5 |
| Missing type hints | 0 | 2 | 3 | 5 |
| Deprecated APIs | 0 | 0 | 2 | 2 |
| Unused imports | 0 | 0 | 1 | 1 |

**Note:** Zero `import logging` calls exist in the entire source tree.

---

## HIGH Risk Findings

### H-01 — Command injection via `shell=True` in custom QA gates

- **File:** `src/lindy_orchestrator/qa/__init__.py` | **Lines:** 97-108 | **Category:** Security
- **Status:** Confirmed
- `_run_custom_command_gate` calls `subprocess.run(command, shell=True)` where `module_path` is substituted via `.format()` without sanitization. Shell metacharacters (`;`, `|`, `$()`) in module paths execute verbatim.
- **Fix:** Validate `module_path` with `Path.resolve().is_relative_to()`. Use `shlex.split()` + `shell=False`.

### H-02 — Session persistence has no error handling

- **File:** `src/lindy_orchestrator/session.py` | **Lines:** 87-93 | **Category:** Exception handling
- **Status:** Confirmed
- `_save` and `_load` perform bare file I/O and JSON parsing with no exception handling. `OSError` (full disk) or `json.JSONDecodeError` (corrupt file) propagate unhandled.
- **Fix:** Wrap in `try/except` with descriptive error messages.

### H-03 — `dispatcher.py` has no structured logging

- **File:** `src/lindy_orchestrator/dispatcher.py` | **Lines:** Entire file | **Category:** Logging
- **Status:** Confirmed
- Core dispatch engine (subprocess spawning, stall events, process kill) has zero `logging.*` calls. No audit trail in headless CI.
- **Fix:** Add `log = logging.getLogger(__name__)`. Log spawn/stall/kill events.

### H-04 — `scheduler.py` has no structured logging

- **File:** `src/lindy_orchestrator/scheduler.py` | **Lines:** Entire file | **Category:** Logging
- **Status:** Confirmed
- Task orchestration relies entirely on `progress` callback with no structured logging.
- **Fix:** Add `log = logging.getLogger(__name__)`. Log task start/complete/fail, QA results, retries.

### H-05 — `session.py` has no logging

- **File:** `src/lindy_orchestrator/session.py` | **Lines:** Entire file | **Category:** Logging
- **Status:** Confirmed
- Session persistence has zero logging. Combined with H-02, silent data loss is possible.
- **Fix:** Add logging at INFO for save/load, WARNING for corrupted sessions.

### H-06 — `ActionLogger` has zero test coverage

- **File:** `src/lindy_orchestrator/logger.py` | **Lines:** 1-68 | **Category:** Test coverage
- **Status:** Confirmed
- No test file references `ActionLogger`, `log_action`, `log_dispatch`, or `log_qa`. All three public methods and edge cases (output truncation, dict output, pass/fail branching) are untested.
- **Fix:** Add `tests/test_logger.py`.

### H-07 — Planner internals untested

- **File:** `src/lindy_orchestrator/planner.py` | **Lines:** 102-281 | **Category:** Test coverage
- **Status:** Confirmed
- `_read_all_statuses`, `_parse_task_plan`, `_format_prompt`, `_plan_via_api`, and `generate_plan` dry-run are entirely untested. `_parse_task_plan` has critical JSON parsing with untested fallback paths.
- **Fix:** Add `tests/test_planner.py`.

### H-08 — Scheduler execution engine untested

- **File:** `src/lindy_orchestrator/scheduler.py` | **Lines:** 27-503 | **Category:** Test coverage
- **Status:** Confirmed
- `execute_plan` and `_execute_single_task` — the most critical execution path — have no unit tests. Auto-injection, retry loop, checkpoint, and dependency-failure skip are all unverified.
- **Fix:** Add `tests/test_scheduler_integration.py` with mocked provider.

### H-09 — CLI main commands untested

- **File:** `src/lindy_orchestrator/cli.py` | **Lines:** 61-489 | **Category:** Test coverage
- **Status:** Confirmed
- Only `version` is tested. `run`, `plan`, `status`, `logs`, `resume` are not exercised via `CliRunner`.
- **Fix:** Add `tests/test_cli_commands.py`.

### H-10 — `_execute_single_task` is 334 lines

- **File:** `src/lindy_orchestrator/scheduler.py` | **Lines:** 170-503 | **Category:** Long function
- **Status:** Confirmed
- Handles QA gate injection, branch naming, mailbox injection, heartbeat, dispatch, delivery check, QA iteration, and retry — all inline.
- **Fix:** Extract `_inject_qa_gates`, `_inject_mailbox_messages`, `_run_qa_gates`, `_handle_retry`.

### H-11 — `dispatch_agent` is 273 lines

- **File:** `src/lindy_orchestrator/dispatcher.py` | **Lines:** 133-405 | **Category:** Long function
- **Status:** Confirmed
- Contains reader thread, event loop, stall escalation, heartbeat tracking, tool-aware multipliers, EOF handling, callback invocation, output extraction — all inline.
- **Fix:** Extract `_run_stall_check`, `_process_event_line`, `_finalize_result`.

### H-12 — No `pytest-cov` in dev dependencies; no coverage in CI

- **File:** `pyproject.toml` | **Lines:** 36-40 | **Category:** Dependency health
- **Status:** Confirmed
- No coverage measurement, no minimum threshold, no reporting.
- **Fix:** Add `pytest-cov>=5.0` to dev deps. CI: `--cov=src/lindy_orchestrator --cov-fail-under=80`.

### H-13 — `execute_plan` is 121 lines

- **File:** `src/lindy_orchestrator/scheduler.py` | **Lines:** 47-167 | **Category:** Long function
- **Status:** Confirmed
- Plan iteration, session management, hook emission, parallel dispatch, and checkpointing in one body.
- **Fix:** Extract `_start_session`, `_end_session`, parallel dispatch loop.

### H-14 — `run` CLI command is 139 lines

- **File:** `src/lindy_orchestrator/cli.py` | **Lines:** 62-200 | **Category:** Long function
- **Status:** Confirmed
- Planning, execution, session management, and report display inlined.
- **Fix:** Extract `_do_planning`, `_do_execution`, `_print_report`.

---

## MEDIUM Risk Findings

| ID | File | Lines | Category | Description |
|----|------|-------|----------|-------------|
| M-01 | `qa/command_check.py` | 58-68 | Security | `shell=True` when command is `str`; command from LLM plan. Fix: default to `shlex.split()` + `shell=False`. |
| M-02 | `dispatcher.py` | 352-353 | Exception | `except Exception: pass` on `on_event` callback silences all errors. Fix: log at DEBUG. |
| M-03 | `dispatcher.py` | 355-372 | Exception | Outer `except Exception as e` swallows traceback. Fix: add `log.exception()`. |
| M-04 | `dispatcher.py` | 418-419 | Exception | `_read_stderr` silently ignores read failure. Fix: log at WARNING. |
| M-05 | `gc.py` | 144-151 | Logging | `_delete_branch` never checks `returncode`; `action.applied=True` before call. Fix: check rc, log on failure. |
| M-06 | `mailbox.py` | 64-75 | Logging | JSONL parse errors silently `continue`. No logging anywhere. Fix: log parse errors at WARNING. |
| M-07 | `trackers/github_issues.py` | 54-61 | Logging | `gh` CLI failures silently return `[]`/`False`. Fix: log at WARNING. |
| M-08 | `cli_ext.py` | 18-431 | Long function | `register_ext_commands`: 7 command bodies inlined via closures (414 lines). Fix: standalone functions. |
| M-09 | `cli_ext.py` | 212-350 | Long function | `run_issue` nested function is 139 lines. Fix: extract to standalone. |
| M-10 | `cli.py` | 342-458 | Long function | `resume` is 117 lines. Fix: extract session-loading helpers. |
| M-11 | `prompts.py` | 95-130, 195-210 | Dead code (candidate) | `REPORT_PROMPT_TEMPLATE` / `render_report_prompt` never called. Fix: delete or wire in. |
| M-12 | `status/writer.py` | 13-34 | Dead code (candidate) | `update_meta_timestamp()` / `update_root_status()` defined, re-exported, never called. Fix: implement or delete. |
| M-13 | `qa/feedback.py` | 172-303 | Dead code (candidate) | v0.4.0 structured feedback classes never called from scheduler. Only in tests. Fix: integrate or remove. |
| M-14 | `qa/structural_check.py` + `qa/layer_check.py` | 201-240, 230-269 | Duplication | `_get_staged_files()` identical in both files. Fix: extract to `qa/_git_helpers.py`. |
| M-15 | `cli_init.py` + `discovery/analyzer.py` | multiple | Duplication | `_MODULE_MARKERS`, `_IGNORED_DIRS`, `_detect_modules()`, `_detect_tech()` duplicated with slight variations. Fix: call `analyze_project()`. |
| M-16 | `planner.py` + `scheduler.py` | 143-160, 237-263 | Duplication | Near-identical heartbeat timer closures. Fix: extract `make_heartbeat_callback()`. |
| M-17 | `cli_helpers.py` | 41 | Missing types | `load_cfg` missing `-> OrchestratorConfig` return annotation. Imported in 6+ places. Fix: add annotation. |
| M-18 | 5 QA gate files | `check()` methods | Missing types | All `check()` methods use untyped `**kwargs`. Fix: introduce `CheckContext` dataclass. |
| M-19 | `prompts.py` | 133-210 | Test coverage | `render_plan_prompt` / `render_report_prompt` untested. Fix: add `tests/test_prompts.py`. |
| M-20 | `reporter.py` | 105-154 | Test coverage | `print_goal_report` / `print_status_table` untested. Fix: test with `Console(file=StringIO())`. |
| M-21 | `cli_ext.py` | 34-431 | Test coverage | Only `mailbox` command tested. 5 others (`gc`, `scan`, `validate`, `issues`, `run-issue`) have zero tests. Fix: add `tests/test_cli_ext.py`. |
| M-22 | `cli_init.py` | 51-271 | Test coverage | `init` and `onboard` commands untested. Fix: add `tests/test_cli_init.py`. |
| M-23 | `cli_helpers.py` | 19-38 | Test coverage | `resolve_goal` file-path, stdin, no-input branches untested. Fix: add tests. |
| M-24 | `qa/feedback.py` | 172-356 | Test coverage | `build_retry_prompt` retry>=2 branch underspecified. Fix: parametrized tests. |
| M-25 | `pyproject.toml` | 21-26 | Dependency | No upper-bound pins. Pydantic v3 / typer 1.0 = silent breakage. Fix: `pydantic>=2.6,<3`, `typer>=0.12,<1`. |
| M-26 | `pyproject.toml` | 34-35 | Dependency | `anthropic` optional dep has no upper bound. Fix: `anthropic>=0.40,<1`. |
| M-27 | `.github/workflows/ci.yml` | 26-27 | Dependency | CI installs `.[dev]` but not `.[dev,api]`. API code path never validated. Fix: `pip install -e '.[dev,api]'`. |
| M-28 | `qa/__init__.py` + `qa/command_check.py` | 97-98, 42-47 | Security | `str.format()` allows attribute access on passed objects (`{module_path.__class__}`). Fix: use `str.replace()` instead. |
| M-29 | `config.py` | 48 | Security | Default `permission_mode: bypassPermissions` grants agents full permissions. Fix: default to restricted mode, require opt-in. |
| M-30 | `session.py` | 52-56 | Security | Path traversal via user-supplied `session_id` (`../../etc/passwd`). Fix: validate pattern + `is_relative_to()`. |
| M-31 | `mailbox.py` | 45-46 | Security | Path traversal via module name in `_inbox_path()`. Fix: validate alphanumeric + `is_relative_to()`. |
| M-32 | `logger.py` | 40-41 | Exception | `ActionLogger.log_action` has no exception handling; `OSError` crashes orchestrator. Fix: wrap in `try/except`, fallback to stderr. |
| M-33 | `hooks.py` | 64-73 | Exception | `HookRegistry.emit()` has no exception protection; one failing handler blocks all subsequent handlers. Fix: wrap each handler call. |

---

## LOW Risk Findings

See [AUDIT_RISK_MAP_details.md](AUDIT_RISK_MAP_details.md) for full descriptions.

| ID | File | Lines | Category | Description |
|----|------|-------|----------|-------------|
| L-01 | `qa/feedback.py` | 160 | Deprecated | `callable` used as type annotation instead of `Callable`. |
| L-02 | `config.py` | 131 | Deprecated | Pydantic v2 private attribute without `PrivateAttr`. |
| L-03 | `scheduler.py` | 27-44 | Dead code (candidate) | `ExecutionProgress` dataclass defined, never used. |
| L-04 | `dashboard.py` | 83-88 | Dead code (candidate) | `update_heartbeat()` never called from production. |
| L-05 | `discovery/generator.py` | 171-175 | Dead code | `_detect_module_ci()` ignores its `mod` parameter, always returns `"ci.yml"`. |
| L-06 | `gc.py` | 274-282 | Dead code | `referenced` set built but never used in `_find_orphan_plans`. |
| L-07 | `qa/ci_check.py` | 55, 69 | Dead code | `last_error` always empty; timeout message shows `Last: `. |
| L-08 | `trackers/github_issues.py` | 97-99 | Dead code | Runtime `assert isinstance(...)` at import; stripped by `-O`. |
| L-09 | `qa/structural_check.py` + `qa/layer_check.py` | 267-277, 272-282 | Duplication | `_format_violations()` nearly identical in two files. |
| L-10 | `cli_helpers.py` + `cli.py` | 54-65, 54-58 | Duplication | Triple-indirection wrappers for `plan_to_dict`/`plan_from_dict`. |
| L-11 | `cli.py`, `cli_ext.py`, `cli_init.py`, `cli_scaffold.py` | various | Unused import | `from typing import Optional` redundant with `__future__` annotations. |
| L-12 | `scheduler.py` | 154-155 | Exception | Checkpoint failure silently swallowed (`except Exception: pass`). |
| L-13 | `scheduler.py` | 278-279 | Exception | Mailbox injection failure silently swallowed. |
| L-14 | `session.py` | 83-84 | Exception | `list_sessions` silently skips unreadable files. |
| L-15 | `scheduler_helpers.py` | 67 | Logging | Delivery check errors return message but not logged. |
| L-16 | `cli.py` | 27 | Logging | `_version_callback` uses `print()` instead of `console.print()`. |
| L-17 | `cli_init.py` + `cli_scaffold.py` | 51-199, 162-285 | Long function | `register_init_commands` (149 lines), `scaffold` (124 lines). |
| L-18 | `cli.py`, `cli_init.py`, `cli_scaffold.py` | various | Missing types | CLI command functions missing `-> None` return annotations. |
| L-19 | `cli_init.py`, `cli_scaffold.py` | 51, 158 | Missing types | `console` parameter is untyped. |
| L-20 | `.github/workflows/ci.yml` | 34-35 | Dependency | `-x` (fail-fast) hides multiple failures. Consider `--maxfail=5`. |
| L-21 | `.github/workflows/ci.yml` + `pyproject.toml` | — | Dependency | No `mypy` in CI despite `"Typing :: Typed"` classifier. |
| L-22 | `.github/workflows/ci.yml` | 10-11 | Dependency | No `timeout-minutes` on CI job (default: 6 hours). |
| L-23 | `dashboard.py` | 83 | Test coverage | `update_heartbeat` TTY path not exercised in tests. |
| L-24 | `session.py` | 91-93 | Security | `SessionState(**data)` from untrusted JSON; no schema validation. Fix: validate keys explicitly. |
| L-25 | `planner.py` | 197-245 | Security | LLM-generated plan parsed as executable tasks without command validation. Fix: sanitize `qa_checks.params`. |

---

## Priority Remediation Roadmap

### Phase 1 — Security and Reliability (address first)

| Finding | Action |
|---------|--------|
| H-01, M-28 | Sanitize `module_path` + replace `str.format()` in QA gate shell commands |
| M-01 | Default `CommandCheckGate` to `shell=False` |
| M-29 | Change default `permission_mode` from `bypassPermissions` to restricted |
| M-30, M-31 | Add path traversal guards to session and mailbox path construction |
| H-02 | Add error handling to `SessionManager._save`/`_load` |
| H-03, H-04, H-05 | Add structured logging to `dispatcher.py`, `scheduler.py`, `session.py` |
| M-02, M-03, M-04 | Fix swallowed exceptions in `dispatcher.py` |
| M-32, M-33 | Add exception handling to `ActionLogger` and `HookRegistry.emit()` |

### Phase 2 — Test Coverage (build confidence)

| Finding | Action |
|---------|--------|
| H-12 | Add `pytest-cov` and enforce minimum coverage threshold |
| H-06..H-09 | Add tests for `ActionLogger`, planner, scheduler, CLI commands |
| M-19..M-24 | Fill remaining test gaps |

### Phase 3 — Code Quality (reduce maintenance burden)

| Finding | Action |
|---------|--------|
| H-10, H-11, H-13, H-14 | Decompose `_execute_single_task`, `dispatch_agent`, `execute_plan`, `run` |
| M-14, M-15, M-16 | Consolidate duplicated logic |
| M-11, M-12, M-13 | Remove confirmed dead code |
| M-25, M-26, M-27 | Pin dependency upper bounds |

### Phase 4 — Polish (low urgency)

- L-01 through L-25: Address as part of regular maintenance cycles.
