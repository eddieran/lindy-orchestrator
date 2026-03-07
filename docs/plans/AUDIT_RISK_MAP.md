# Audit Risk Map — lindy-orchestrator v0.5.2

> **Date:** 2026-03-07
> **Scope:** Full codebase audit — 54 source files, 34 test files, 505 tests
> **Branch:** `af/task-1`

## Automated Check Results

| Check | Result |
|-------|--------|
| `ruff check src/ tests/` | All checks passed |
| `ruff format --check src/ tests/` | 88 files already formatted |
| `pytest tests/ -x -q --tb=short` | 505 passed in 16.00s |

---

## Summary

| Risk Level | Count |
|------------|-------|
| **HIGH** | 14 |
| **MEDIUM** | 22 |
| **LOW** | 21 |
| **Total** | 57 |

| Category | High | Medium | Low | Total |
|----------|------|--------|-----|-------|
| Security concerns | 1 | 1 | 1 | 3 |
| Exception handling gaps | 1 | 3 | 3 | 7 |
| Test coverage gaps | 4 | 6 | 1 | 11 |
| Logging gaps | 3 | 3 | 2 | 8 |
| Dependency health | 1 | 3 | 3 | 7 |
| Long functions/classes | 0 | 5 | 2 | 7 |
| Unused functions/dead code | 0 | 3 | 5 | 8 |
| Duplicated logic | 0 | 3 | 2 | 5 |
| Missing type hints | 0 | 2 | 3 | 5 |
| Deprecated APIs | 0 | 0 | 2 | 2 |
| Unused imports | 0 | 0 | 1 | 1 |

---

## HIGH Risk Findings

### H-01 — Command injection via `shell=True` in custom QA gates

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/qa/__init__.py` |
| **Lines** | 97–108 |
| **Category** | Security concern |
| **Status** | Confirmed |

`_run_custom_command_gate` calls `subprocess.run(command, shell=True)` where `command` is built via `gate_def.command.format(module_path=module_path)`. While `gate_def.command` comes from `orchestrator.yaml` (operator-controlled), `module_path` is substituted without sanitization. A module path containing shell metacharacters (`;`, `|`, `$()`) would be executed verbatim. The `cwd` parameter is similarly formatted without path-traversal validation.

**Recommended action:** (a) Validate `module_path` is within `project_root` using `Path.resolve().is_relative_to()`. (b) Use `shlex.split()` with `shell=False` where possible. (c) Document the trust boundary if `shell=True` must be retained.

---

### H-02 — Session persistence has no error handling

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/session.py` |
| **Lines** | 87–93 |
| **Category** | Exception handling gap |
| **Status** | Confirmed |

`SessionManager._save` and `_load` perform bare file I/O and JSON parsing with no exception handling. An `OSError` (full disk) in `_save` propagates unhandled to the CLI. A `json.JSONDecodeError` or `KeyError` in `_load` (truncated session file) crashes `resume` with no user-facing message.

**Recommended action:** Wrap `_save` in `try/except OSError` with a meaningful message. Wrap `_load` in `try/except (json.JSONDecodeError, KeyError, TypeError)` and return `None` or raise a descriptive `SessionCorruptError`.

---

### H-03 — `dispatcher.py` has no structured logging

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/dispatcher.py` |
| **Lines** | Entire file |
| **Category** | Logging gap |
| **Status** | Confirmed |

The core dispatch engine — subprocess spawning, stdout reading, stall events, process kill — has zero `logging.*` calls. All diagnostics go only to `DispatchResult.output`. In headless CI environments, there is no audit trail for transient errors.

**Recommended action:** Add `import logging; log = logging.getLogger(__name__)`. Log at DEBUG for normal events (process spawn, event count), WARNING for stall events, ERROR for process kill / unexpected exceptions.

---

### H-04 — `scheduler.py` has no structured logging

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/scheduler.py` |
| **Lines** | Entire file |
| **Category** | Logging gap |
| **Status** | Confirmed |

Task execution orchestration — dispatch, QA gate runs, retry logic — relies entirely on the `progress` callback with no structured logging. Same issue as H-03.

**Recommended action:** Add `import logging; log = logging.getLogger(__name__)`. Log task start/complete/fail, QA gate results, and retry attempts.

---

### H-05 — `session.py` has no logging

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/session.py` |
| **Lines** | Entire file |
| **Category** | Logging gap |
| **Status** | Confirmed |

Session persistence (read/write JSON to disk) has zero logging. Failures are completely invisible unless they propagate to the CLI. Combined with H-02, this means silent data loss is possible.

**Recommended action:** Add logging at INFO for session save/load, WARNING for corrupted sessions.

---

### H-06 — `ActionLogger` has zero test coverage

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/logger.py` |
| **Lines** | 1–68 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

No test file references `ActionLogger`, `log_action`, `log_dispatch`, or `log_qa`. All three public methods are completely untested, including the output truncation branch (`len(output) > 5000`), the `dict` output branch, and `log_qa` pass/fail branching.

**Recommended action:** Add `tests/test_logger.py` with `tmp_path`-based fixtures exercising all three public methods and edge cases.

---

### H-07 — Planner internals untested

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/planner.py` |
| **Lines** | 102–281 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

`_read_all_statuses`, `_parse_task_plan`, `_format_prompt`, `_plan_via_api`, and `generate_plan` dry-run mode are entirely untested. `_parse_task_plan` contains critical JSON parsing logic with fallback paths that have never been exercised.

**Recommended action:** Add `tests/test_planner.py`. Unit test `_parse_task_plan` and `_format_prompt` directly. Test `generate_plan` in dry-run mode. Test the `anthropic` ImportError path.

---

### H-08 — Scheduler execution engine untested

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/scheduler.py` |
| **Lines** | 27–503 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

`execute_plan` and `_execute_single_task` — the most critical execution path in the codebase — have no unit tests. Auto-injection of QA gates, the retry loop, checkpoint calls, and dependency-failure skip logic are all unverified.

**Recommended action:** Add `tests/test_scheduler_integration.py`. Mock `create_provider` to return a fake provider. Test dry-run, parallel dispatch, QA-gate retry logic, and checkpoint calls.

---

### H-09 — CLI main commands untested

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/cli.py` |
| **Lines** | 61–489 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

Only `version` is tested. The five main commands — `run`, `plan`, `status`, `logs`, `resume` — are exercised nowhere via `typer.testing.CliRunner`. Key branches like plan-file loading, planning failure, and session resume are unverified.

**Recommended action:** Add `tests/test_cli_commands.py` using `CliRunner`. Mock `find_claude_cli`, `generate_plan`, `execute_plan`, and `SessionManager`.

---

### H-10 — `_execute_single_task` is 334 lines

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/scheduler.py` |
| **Lines** | 170–503 |
| **Category** | Long function |
| **Status** | Confirmed |

The most egregious long-function violation. A single function handles auto-injection of QA gates, branch-naming logic, mailbox injection, event heartbeat callbacks, dispatch, delivery checking, all QA gate iteration, and full retry logic. Nearly impossible to test individual concerns in isolation.

**Recommended action:** Extract `_inject_qa_gates`, `_inject_mailbox_messages`, `_inject_branch_instructions`, `_run_qa_gates`, and `_handle_retry` as separate functions.

---

### H-11 — `dispatch_agent` is 273 lines

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/dispatcher.py` |
| **Lines** | 133–405 |
| **Category** | Long function |
| **Status** | Confirmed |

Contains reader thread setup, the full event loop with hard-timeout, stall-escalation (two-stage warn/kill), heartbeat tracking, tool-aware stall multipliers, grace-period logic, EOF sentinel handling, callback invocation, output extraction, and truncation — all inline.

**Recommended action:** Extract `_run_stall_check`, `_process_event_line`, and `_finalize_result` as separate helpers.

---

### H-12 — No `pytest-cov` in dev dependencies; no coverage in CI

| Field | Value |
|-------|-------|
| **File** | `pyproject.toml` |
| **Lines** | 36–40 |
| **Category** | Dependency health |
| **Status** | Confirmed |

`pytest-cov` is absent from dev dependencies. CI runs tests with no coverage flags, no minimum threshold enforcement, and no coverage reporting. Impossible to quantify test gaps automatically.

**Recommended action:** Add `"pytest-cov>=5.0"` to dev deps. Update CI to: `pytest tests/ -x -q --tb=short --cov=src/lindy_orchestrator --cov-report=term-missing --cov-fail-under=80`.

---

### H-13 — `long_function`: `execute_plan` is 121 lines

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/scheduler.py` |
| **Lines** | 47–167 |
| **Category** | Long function |
| **Status** | Confirmed |

The top-level execution loop handles plan iteration, session management, hook emission, parallel dispatch coordination, and checkpoint logic in a single function body.

**Recommended action:** Extract session lifecycle (`_start_session`, `_end_session`) and the parallel-dispatch coordination loop.

---

### H-14 — `run` CLI command is 139 lines

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/cli.py` |
| **Lines** | 62–200 |
| **Category** | Long function |
| **Status** | Confirmed |

The `run` command inlines planning, execution, session management, and report display in a single function body.

**Recommended action:** Extract `_do_planning`, `_do_execution`, and `_print_report` helpers.

---

## MEDIUM Risk Findings

### M-01 — `shell=True` in `CommandCheckGate.check`

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/qa/command_check.py` |
| **Lines** | 58–68 |
| **Category** | Security concern |
| **Status** | Confirmed |

`use_shell = isinstance(command, str)` means any string command runs with `shell=True`. The `command` value flows from the LLM-generated task plan. If the planner LLM generates a malicious `command_check` gate with shell-injected command, it executes with full shell expansion.

**Recommended action:** Default to `shlex.split(command)` with `shell=False`. Document the LLM-trust-boundary risk.

---

### M-02 — `on_event` callback silently swallowed

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/dispatcher.py` |
| **Lines** | 352–353 |
| **Category** | Exception handling gap |
| **Status** | Confirmed |

`except Exception: pass` on the `on_event` callback means dashboard update failures, hook exceptions, and stall-tracking bugs are permanently invisible at runtime.

**Recommended action:** Replace `pass` with `logging.getLogger(__name__).debug("on_event callback error", exc_info=True)`.

---

### M-03 — Outer exception in `dispatch_agent` swallows traceback

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/dispatcher.py` |
| **Lines** | 355–372 |
| **Category** | Exception handling gap |
| **Status** | Confirmed |

`except Exception as e` in the main loop logs to `DispatchResult.output` but swallows the original traceback — no `logging.exception()` or re-raise.

**Recommended action:** Add `log.exception("dispatch_agent failed")` before constructing the error result.

---

### M-04 — `_read_stderr` silently ignores read failure

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/dispatcher.py` |
| **Lines** | 418–419 |
| **Category** | Exception handling gap |
| **Status** | Confirmed |

`except Exception: pass` means error diagnostics are lost when the process has already exited abnormally.

**Recommended action:** Log at WARNING level before returning `""`.

---

### M-05 — `gc._delete_branch` never checks returncode

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/gc.py` |
| **Lines** | 144–151 |
| **Category** | Logging gap |
| **Status** | Confirmed |

`subprocess.run` is called for `git branch -d` with no return-value check, no exception handling, no logging. `action.applied = True` is set before the call, so the GC report incorrectly reports success even on failure.

**Recommended action:** Check `proc.returncode` and log a warning on failure; set `action.applied` only on success.

---

### M-06 — Mailbox has no logging; JSONL parse errors silently `continue`

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/mailbox.py` |
| **Lines** | 64–75 |
| **Category** | Logging gap |
| **Status** | Confirmed |

Inter-agent mailbox file I/O with threading has no logging. JSONL parse errors in `receive` silently `continue`.

**Recommended action:** Add logging at WARNING for parse errors, DEBUG for send/receive events.

---

### M-07 — GitHub Issues tracker silently returns empty on failure

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/trackers/github_issues.py` |
| **Lines** | 54–61 |
| **Category** | Logging gap |
| **Status** | Confirmed |

`gh` CLI subprocess calls for network/auth failures silently return `[]` or `False` with no log.

**Recommended action:** Log at WARNING when `gh` calls fail.

---

### M-08 — `register_ext_commands` is 414 lines

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/cli_ext.py` |
| **Lines** | 18–431 |
| **Category** | Long function |
| **Status** | Confirmed |

Seven independent CLI command bodies inlined inside one parent function using a factory-with-closures pattern.

**Recommended action:** Move each command body to a standalone function; the registration function becomes a short wiring loop.

---

### M-09 — `run_issue` nested function is 139 lines

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/cli_ext.py` |
| **Lines** | 212–350 |
| **Category** | Long function |
| **Status** | Confirmed |

**Recommended action:** Extract to a standalone function.

---

### M-10 — `resume` CLI command is 117 lines

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/cli.py` |
| **Lines** | 342–458 |
| **Category** | Long function |
| **Status** | Confirmed |

**Recommended action:** Extract session-loading and task-unskipping logic into helpers.

---

### M-11 — `REPORT_PROMPT_TEMPLATE` / `render_report_prompt` never called

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/prompts.py` |
| **Lines** | 95–130, 195–210 |
| **Category** | Unused function |
| **Status** | Candidate |

Defined but never called from production code. The report is assembled directly in `cli.py` / `cli_ext.py`.

**Recommended action:** Delete, or wire into the CLI report generation path.

---

### M-12 — `status/writer.py` functions never called

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/status/writer.py` |
| **Lines** | 13–34 |
| **Category** | Unused function |
| **Status** | Candidate |

`update_meta_timestamp()` and `update_root_status()` are re-exported from `status/__init__.py` but never called anywhere in source or tests.

**Recommended action:** Either implement the call sites or delete both functions and the re-export.

---

### M-13 — v0.4.0 structured feedback block never called from production

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/qa/feedback.py` |
| **Lines** | 172–303 |
| **Category** | Unused function |
| **Status** | Candidate |

`FailureCategory`, `StructuredFeedback`, `classify_failure()`, `build_structured_feedback()`, `build_retry_prompt()` — none are called from the scheduler retry path. Only exercised in `test_feedback_integration.py`. The coexistence of two feedback systems is a maintenance burden.

**Recommended action:** Integrate into the scheduler retry path, or remove as dead code.

---

### M-14 — `_get_staged_files()` duplicated across two files

| Field | Value |
|-------|-------|
| **Files** | `src/lindy_orchestrator/qa/structural_check.py:201–240`, `src/lindy_orchestrator/qa/layer_check.py:230–269` |
| **Category** | Duplicated logic |
| **Status** | Confirmed |

Identical function — same name, signature, two subprocess calls, same filter logic. Bug fixes must be applied twice.

**Recommended action:** Extract to a shared `qa/_git_helpers.py` module.

---

### M-15 — Module detection logic duplicated

| Field | Value |
|-------|-------|
| **Files** | `src/lindy_orchestrator/cli_init.py:15–48, 202–227`, `src/lindy_orchestrator/discovery/analyzer.py:18–62, 99–141` |
| **Category** | Duplicated logic |
| **Status** | Confirmed |

`_MODULE_MARKERS`, `_IGNORED_DIRS`, `_detect_modules()`, `_detect_tech()` all exist in both files with slightly different implementations.

**Recommended action:** Remove copies in `cli_init.py` and call `analyze_project()` from `discovery/analyzer.py`.

---

### M-16 — Heartbeat timer logic duplicated

| Field | Value |
|-------|-------|
| **Files** | `src/lindy_orchestrator/planner.py:143–160`, `src/lindy_orchestrator/scheduler.py:237–263` |
| **Category** | Duplicated logic |
| **Status** | Confirmed |

Near-identical heartbeat closures with `_hb_count`, `_hb_start`, `_hb_last_print`, 30-second print interval.

**Recommended action:** Extract a `make_heartbeat_callback()` factory to a shared helper.

---

### M-17 — `load_cfg` missing return type annotation

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/cli_helpers.py` |
| **Lines** | 41 |
| **Category** | Missing type hints |
| **Status** | Confirmed |

`load_cfg` is imported in at least six places but its return type (`OrchestratorConfig`) is not annotated, preventing mypy from propagating the type.

**Recommended action:** Add `-> OrchestratorConfig` return annotation.

---

### M-18 — All QA gate `check()` methods use untyped `**kwargs`

| Field | Value |
|-------|-------|
| **Files** | `src/lindy_orchestrator/qa/agent_check.py`, `ci_check.py`, `command_check.py`, `layer_check.py`, `structural_check.py` |
| **Category** | Missing type hints |
| **Status** | Confirmed |

All five built-in QA gate `check()` methods accept `**kwargs` without documenting expected keys (`module_path`, `dispatcher_config`, `qa_module`). The gate protocol is enforced only by convention.

**Recommended action:** Introduce a typed `CheckContext` dataclass, or annotate `**kwargs: Any` with documentation.

---

### M-19 — `render_plan_prompt` and `render_report_prompt` untested

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/prompts.py` |
| **Lines** | 133–210 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

No test references these functions. They shape all planning LLM calls.

**Recommended action:** Add `tests/test_prompts.py`.

---

### M-20 — `print_goal_report` and `print_status_table` untested

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/reporter.py` |
| **Lines** | 105–154 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

Called from CLI commands but never tested. Output formatting is unverified.

**Recommended action:** Add tests using `Console(file=StringIO())`.

---

### M-21 — CLI ext commands (`gc`, `scan`, `validate`, `issues`, `run-issue`) untested

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/cli_ext.py` |
| **Lines** | 34–431 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

Only `mailbox` command is tested. Five other commands have no test coverage.

**Recommended action:** Add `tests/test_cli_ext.py` with mocked dependencies.

---

### M-22 — `cli_init.py` commands (`init`, `onboard`) untested

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/cli_init.py` |
| **Lines** | 51–271 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

**Recommended action:** Add `tests/test_cli_init.py` with `tmp_path` fixtures.

---

### M-23 — `resolve_goal` branches untested

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/cli_helpers.py` |
| **Lines** | 19–38 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

The file-path branch, stdin branch, and no-input exit path are all untested.

**Recommended action:** Add tests mocking `sys.stdin` and asserting `typer.Exit`.

---

### M-24 — `build_retry_prompt` retry>=2 branch underspecified

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/qa/feedback.py` |
| **Lines** | 172–356 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

**Recommended action:** Extend `test_feedback_integration.py` with parametrized cases.

---

### M-25 — No upper-bound pins on runtime dependencies

| Field | Value |
|-------|-------|
| **File** | `pyproject.toml` |
| **Lines** | 21–26 |
| **Category** | Dependency health |
| **Status** | Confirmed |

All deps use only `>=`. Pydantic v3 and typer 1.0 are the highest-risk silent-breakage vectors.

**Recommended action:** Add `pydantic>=2.6,<3` and `typer>=0.12,<1`.

---

### M-26 — `anthropic` optional dep has no upper bound

| Field | Value |
|-------|-------|
| **File** | `pyproject.toml` |
| **Lines** | 34–35 |
| **Category** | Dependency health |
| **Status** | Confirmed |

**Recommended action:** Pin to `anthropic>=0.40,<1`.

---

### M-27 — CI does not install the `api` optional group

| Field | Value |
|-------|-------|
| **File** | `.github/workflows/ci.yml` |
| **Lines** | 26–27 |
| **Category** | Dependency health |
| **Status** | Confirmed |

CI installs `.[dev]` but not `.[dev,api]`. The API code path is never validated, even with mocked clients.

**Recommended action:** Change to `pip install -e '.[dev,api]'`.

---

## LOW Risk Findings

### L-01 — `callable` builtin used as type annotation

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/qa/feedback.py` |
| **Lines** | 160 |
| **Category** | Security concern (misclassified type) / Deprecated API |
| **Status** | Confirmed |

`_PARSERS: list[tuple[str, callable]]` — `callable` is a runtime function, not a type. Produces incorrect type-checker results.

**Recommended action:** Change to `list[tuple[str, Callable[[str], str]]]`.

---

### L-02 — Pydantic v2 private attribute without `PrivateAttr`

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/config.py` |
| **Lines** | 131 |
| **Category** | Deprecated API |
| **Status** | Confirmed |

`_config_dir: Path = Path(".")` inside a `BaseModel` subclass works by accident in Pydantic v2 — it's silently ignored as a ClassVar. Will break if Pydantic's behavior changes.

**Recommended action:** Use `_config_dir: Path = PrivateAttr(default=Path("."))`.

---

### L-03 — `ExecutionProgress` dataclass defined but never used

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/scheduler.py` |
| **Lines** | 27–44 |
| **Category** | Unused function |
| **Status** | Candidate |

Presumably a scaffold for future progress tracking.

**Recommended action:** Delete, or integrate into `execute_plan`.

---

### L-04 — `Dashboard.update_heartbeat()` never called from production code

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/dashboard.py` |
| **Lines** | 83–88 |
| **Category** | Unused function |
| **Status** | Candidate |

Only called from a single test. Heartbeat state is never fed live data.

**Recommended action:** Wire into the `_on_event` callback in `scheduler._execute_single_task`, or remove.

---

### L-05 — `_detect_module_ci()` always returns hardcoded `"ci.yml"`

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/discovery/generator.py` |
| **Lines** | 171–175 |
| **Category** | Dead code |
| **Status** | Confirmed |

Ignores its `mod` parameter entirely. Equivalent to always writing `ci_workflow: ci.yml`.

**Recommended action:** Remove and hardcode the default, or use `analyzer._detect_ci()`.

---

### L-06 — `referenced` set built but never used in `_find_orphan_plans`

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/gc.py` |
| **Lines** | 274–282 |
| **Category** | Dead code |
| **Status** | Confirmed |

The set is populated but never checked in the filter condition. Orphan detection is purely age-based.

**Recommended action:** Either use `referenced` in the filter, or remove the dead loop.

---

### L-07 — `last_error` in `ci_check.py` always empty

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/qa/ci_check.py` |
| **Lines** | 55, 69 |
| **Category** | Dead code |
| **Status** | Confirmed |

`last_error` is assigned `""` and never updated. The timeout message always produces `Last: `.

**Recommended action:** Remove `last_error`, or capture error info from `_query_runs`.

---

### L-08 — Runtime `assert` + throwaway Protocol check

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/trackers/github_issues.py` |
| **Lines** | 97–99 |
| **Category** | Dead code |
| **Status** | Confirmed |

Module-level `assert isinstance(GitHubIssuesProvider, type)` fires at import time in production and is stripped by `-O`. The `_provider_check` variable is assigned and immediately discarded.

**Recommended action:** Remove the runtime assert. Use `TYPE_CHECKING`-guarded annotation if needed.

---

### L-09 — `_format_violations()` duplicated in two files

| Field | Value |
|-------|-------|
| **Files** | `src/lindy_orchestrator/qa/structural_check.py:267–277`, `src/lindy_orchestrator/qa/layer_check.py:272–282` |
| **Category** | Duplicated logic |
| **Status** | Confirmed |

Nearly identical formatting functions. Only the "all passed" string and header count differ.

**Recommended action:** Consolidate into `structural_check.py` with the "all passed" string as a parameter.

---

### L-10 — Triple-indirection wrappers for `plan_to_dict`/`plan_from_dict`

| Field | Value |
|-------|-------|
| **Files** | `src/lindy_orchestrator/cli_helpers.py:54–65`, `src/lindy_orchestrator/cli.py:54–58` |
| **Category** | Duplicated logic |
| **Status** | Confirmed |

`cli_helpers` wraps `models.plan_to_dict/plan_from_dict`, then `cli.py` aliases them again. Three indirection levels with no added value.

**Recommended action:** Import directly from `.models` in `cli.py` and `cli_ext.py`.

---

### L-11 — `from typing import Optional` with `__future__` annotations

| Field | Value |
|-------|-------|
| **Files** | `src/lindy_orchestrator/cli.py`, `cli_ext.py`, `cli_init.py`, `cli_scaffold.py` |
| **Category** | Unused import |
| **Status** | Confirmed |

With `from __future__ import annotations`, `Optional[str]` can be replaced with `str | None`, making the `Optional` import unnecessary.

**Recommended action:** Replace `Optional[X]` with `X | None` throughout and remove the import.

---

### L-12 — Checkpoint failure silently swallowed

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/scheduler.py` |
| **Lines** | 154–155 |
| **Category** | Exception handling gap |
| **Status** | Confirmed |

`except Exception: pass` after checkpoint save. May lose progress state silently.

**Recommended action:** Log at WARNING level.

---

### L-13 — Mailbox injection failure silently swallowed

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/scheduler.py` |
| **Lines** | 278–279 |
| **Category** | Exception handling gap |
| **Status** | Confirmed |

**Recommended action:** Log at WARNING level.

---

### L-14 — `list_sessions` silently skips unreadable files

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/session.py` |
| **Lines** | 83–84 |
| **Category** | Exception handling gap |
| **Status** | Confirmed |

**Recommended action:** Log at DEBUG level when skipping.

---

### L-15 — Delivery check subprocess errors not logged

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/scheduler_helpers.py` |
| **Lines** | 67 |
| **Category** | Logging gap |
| **Status** | Confirmed |

Errors return a string message but are not logged.

**Recommended action:** Add logging at WARNING.

---

### L-16 — `_version_callback` uses `print()` instead of `console.print()`

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/cli.py` |
| **Lines** | 27 |
| **Category** | Logging gap |
| **Status** | Confirmed |

Inconsistent with the rest of the CLI output which uses Rich.

**Recommended action:** Change to `console.print(...)`.

---

### L-17 — `register_init_commands` and `scaffold` are long (149 / 124 lines)

| Field | Value |
|-------|-------|
| **Files** | `src/lindy_orchestrator/cli_init.py:51–199`, `src/lindy_orchestrator/cli_scaffold.py:162–285` |
| **Category** | Long function |
| **Status** | Confirmed |

Both exceed 80 lines but are less severe than the high-risk entries.

**Recommended action:** Extract major subsections into helper functions.

---

### L-18 — CLI command functions missing `-> None` return annotations

| Field | Value |
|-------|-------|
| **Files** | `src/lindy_orchestrator/cli.py`, `cli_init.py`, `cli_scaffold.py` |
| **Category** | Missing type hints |
| **Status** | Confirmed |

`run`, `plan`, `status`, `logs`, `resume`, `version`, etc. lack return annotations.

**Recommended action:** Add `-> None` to all command functions.

---

### L-19 — `register_init_commands` and `register_scaffold_command` missing parameter types

| Field | Value |
|-------|-------|
| **Files** | `src/lindy_orchestrator/cli_init.py:51`, `cli_scaffold.py:158` |
| **Category** | Missing type hints |
| **Status** | Confirmed |

`console` parameter is untyped.

**Recommended action:** Add `console: Console`.

---

### L-20 — CI uses `-x` (fail-fast) without showing multiple failures

| Field | Value |
|-------|-------|
| **File** | `.github/workflows/ci.yml` |
| **Lines** | 34–35 |
| **Category** | Dependency health |
| **Status** | Confirmed |

Only the first broken test is visible on a failing PR.

**Recommended action:** Consider `--maxfail=5` instead of `-x`.

---

### L-21 — No `mypy` type-checking step in CI

| Field | Value |
|-------|-------|
| **File** | `.github/workflows/ci.yml` / `pyproject.toml` |
| **Category** | Dependency health |
| **Status** | Confirmed |

Codebase uses type hints and declares `"Typing :: Typed"` classifier, but no type checker runs in CI.

**Recommended action:** Add `mypy>=1.10` to dev deps and a CI step.

---

### L-22 — No `timeout-minutes` on CI job

| Field | Value |
|-------|-------|
| **File** | `.github/workflows/ci.yml` |
| **Lines** | 10–11 |
| **Category** | Dependency health |
| **Status** | Confirmed |

If a test hangs (possible given the dispatcher's threading model), the runner waits the default 6 hours.

**Recommended action:** Add `timeout-minutes: 15`.

---

### L-23 — `update_heartbeat` TTY path not exercised in tests

| Field | Value |
|-------|-------|
| **File** | `src/lindy_orchestrator/dashboard.py` |
| **Lines** | 83 |
| **Category** | Test coverage gap |
| **Status** | Confirmed |

Low priority — the non-TTY path is well tested.

**Recommended action:** Add one test calling `update_heartbeat` after `start()` on an interactive console.

---

## Priority Remediation Roadmap

### Phase 1 — Security & Reliability (address first)
- **H-01**: Sanitize `module_path` in custom QA gate shell commands
- **M-01**: Default `CommandCheckGate` to `shell=False`
- **H-02**: Add error handling to `SessionManager._save`/`_load`
- **H-03, H-04, H-05**: Add structured logging to `dispatcher.py`, `scheduler.py`, `session.py`
- **M-02, M-03, M-04**: Fix swallowed exceptions in `dispatcher.py`

### Phase 2 — Test Coverage (build confidence)
- **H-12**: Add `pytest-cov` and enforce minimum coverage threshold
- **H-06, H-07, H-08, H-09**: Add tests for `ActionLogger`, planner, scheduler, CLI commands
- **M-19–M-24**: Fill remaining test gaps

### Phase 3 — Code Quality (reduce maintenance burden)
- **H-10, H-11**: Decompose `_execute_single_task` and `dispatch_agent`
- **M-14, M-15, M-16**: Consolidate duplicated logic
- **M-11, M-12, M-13**: Remove confirmed dead code
- **M-25, M-26, M-27**: Pin dependency upper bounds

### Phase 4 — Polish (low urgency)
- **L-01–L-23**: Address remaining low-risk items as part of regular maintenance
