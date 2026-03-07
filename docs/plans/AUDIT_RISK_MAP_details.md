# Audit Risk Map — Detailed Findings

> Supplement to [AUDIT_RISK_MAP.md](AUDIT_RISK_MAP.md).
> Contains expanded descriptions for LOW risk findings and additional context.

---

## LOW Risk Findings — Expanded

### L-01 — `callable` builtin used as type annotation

- **File:** `src/lindy_orchestrator/qa/feedback.py:160`
- `_PARSERS: list[tuple[str, callable]]` — `callable` is a runtime function, not a type.
  Produces incorrect type-checker results.
- **Fix:** Change to `list[tuple[str, Callable[[str], str]]]`.

### L-02 — Pydantic v2 private attribute without `PrivateAttr`

- **File:** `src/lindy_orchestrator/config.py:131`
- `_config_dir: Path = Path(".")` inside a `BaseModel` subclass works by accident in
  Pydantic v2 (silently ignored as ClassVar). Will break if behavior changes.
- **Fix:** Use `_config_dir: Path = PrivateAttr(default=Path("."))`.

### L-03 — `ExecutionProgress` dataclass defined but never used

- **File:** `src/lindy_orchestrator/scheduler.py:27-44`
- Presumably a scaffold for future progress tracking. Never instantiated.
- **Fix:** Delete, or integrate into `execute_plan`.

### L-04 — `Dashboard.update_heartbeat()` never called from production code

- **File:** `src/lindy_orchestrator/dashboard.py:83-88`
- Only called from a single test. Heartbeat state never fed live data.
- **Fix:** Wire into `_on_event` callback in `scheduler._execute_single_task`, or remove.

### L-05 — `_detect_module_ci()` always returns hardcoded `"ci.yml"`

- **File:** `src/lindy_orchestrator/discovery/generator.py:171-175`
- Ignores its `mod` parameter entirely. Equivalent to always writing `ci_workflow: ci.yml`.
- **Fix:** Remove and hardcode, or use `analyzer._detect_ci()`.

### L-06 — `referenced` set built but never used in `_find_orphan_plans`

- **File:** `src/lindy_orchestrator/gc.py:274-282`
- The set is populated but never checked in the filter condition.
  Orphan detection is purely age-based.
- **Fix:** Either use `referenced` in the filter, or remove the dead loop.

### L-07 — `last_error` in `ci_check.py` always empty

- **File:** `src/lindy_orchestrator/qa/ci_check.py:55, 69`
- `last_error` is assigned `""` and never updated. The timeout message always
  produces `Last: ` with no content.
- **Fix:** Remove `last_error`, or capture error info from `_query_runs`.

### L-08 — Runtime `assert` + throwaway Protocol check

- **File:** `src/lindy_orchestrator/trackers/github_issues.py:97-99`
- Module-level `assert isinstance(GitHubIssuesProvider, type)` fires at import time
  and is stripped by `-O`. `_provider_check` is assigned and immediately discarded.
- **Fix:** Remove. Use `TYPE_CHECKING`-guarded annotation if needed.

### L-09 — `_format_violations()` duplicated in two files

- **Files:** `src/lindy_orchestrator/qa/structural_check.py:267-277`,
  `src/lindy_orchestrator/qa/layer_check.py:272-282`
- Nearly identical formatting functions. Only the "all passed" string differs.
- **Fix:** Consolidate with the header string as a parameter.

### L-10 — Triple-indirection wrappers for `plan_to_dict`/`plan_from_dict`

- **Files:** `src/lindy_orchestrator/cli_helpers.py:54-65`,
  `src/lindy_orchestrator/cli.py:54-58`
- `cli_helpers` wraps `models.plan_to_dict/plan_from_dict`, then `cli.py`
  aliases them again. Three indirection levels with no added value.
- **Fix:** Import directly from `.models` in `cli.py` and `cli_ext.py`.

### L-11 — `from typing import Optional` with `__future__` annotations

- **Files:** `cli.py`, `cli_ext.py`, `cli_init.py`, `cli_scaffold.py`
- With `from __future__ import annotations`, `Optional[str]` can be
  replaced with `str | None`, making the `Optional` import unnecessary.
- **Fix:** Replace `Optional[X]` with `X | None` throughout; remove import.

### L-12 — Checkpoint failure silently swallowed

- **File:** `src/lindy_orchestrator/scheduler.py:154-155`
- `except Exception: pass` after checkpoint save. May lose progress state silently.
- **Fix:** Log at WARNING level.

### L-13 — Mailbox injection failure silently swallowed

- **File:** `src/lindy_orchestrator/scheduler.py:278-279`
- `except Exception: pass` — no logging, no indication of failure.
- **Fix:** Log at WARNING level.

### L-14 — `list_sessions` silently skips unreadable files

- **File:** `src/lindy_orchestrator/session.py:83-84`
- `except Exception: pass` in the session listing loop.
- **Fix:** Log at DEBUG level when skipping.

### L-15 — Delivery check subprocess errors not logged

- **File:** `src/lindy_orchestrator/scheduler_helpers.py:67`
- Errors return a string message but are not logged to any logger.
- **Fix:** Add logging at WARNING.

### L-16 — `_version_callback` uses `print()` instead of `console.print()`

- **File:** `src/lindy_orchestrator/cli.py:27`
- Inconsistent with the rest of the CLI output which uses Rich.
- **Fix:** Change to `console.print(...)`.

### L-17 — `register_init_commands` and `scaffold` are long

- **Files:** `src/lindy_orchestrator/cli_init.py:51-199` (149 lines),
  `src/lindy_orchestrator/cli_scaffold.py:162-285` (124 lines)
- Both exceed 80-line threshold but less severe than H-10/H-11.
- **Fix:** Extract major subsections into helper functions.

### L-18 — CLI command functions missing `-> None` return annotations

- **Files:** `cli.py`, `cli_init.py`, `cli_scaffold.py`
- `run`, `plan`, `status`, `logs`, `resume`, `version`, etc. lack return type.
- **Fix:** Add `-> None` to all command functions.

### L-19 — `register_init_commands` and `register_scaffold_command` missing parameter types

- **Files:** `cli_init.py:51`, `cli_scaffold.py:158`
- `console` parameter is untyped.
- **Fix:** Add `console: Console`.

### L-20 — CI uses `-x` (fail-fast) without showing multiple failures

- **File:** `.github/workflows/ci.yml:34-35`
- Only the first broken test is visible on a failing PR.
- **Fix:** Consider `--maxfail=5` instead of `-x`.

### L-21 — No `mypy` type-checking step in CI

- **Files:** `.github/workflows/ci.yml`, `pyproject.toml`
- Codebase uses type hints, declares `"Typing :: Typed"` classifier, but no
  type checker runs in CI.
- **Fix:** Add `mypy>=1.10` to dev deps and a CI step.

### L-22 — No `timeout-minutes` on CI job

- **File:** `.github/workflows/ci.yml:10-11`
- If a test hangs (possible given dispatcher's threading model), default
  runner timeout is 6 hours.
- **Fix:** Add `timeout-minutes: 15`.

### L-23 — `update_heartbeat` TTY path not exercised in tests

- **File:** `src/lindy_orchestrator/dashboard.py:83`
- Low priority — non-TTY path is well tested.
- **Fix:** Add one test calling `update_heartbeat` after `start()`.

---

## Dependency Version Snapshot

| Package | Pinned | Installed | Latest Risk |
|---------|--------|-----------|-------------|
| pydantic | `>=2.6` | 2.10.3 | v3 breaking changes |
| typer | `>=0.12` | 0.24.1 | 1.0 API overhaul |
| rich | `>=13.0` | 13.9.4 | Low risk |
| pyyaml | `>=6.0` | 6.0.2 | Low risk |
| anthropic | `>=0.40` (optional) | 0.84.0 | 1.0 breaking changes |
| pytest | `>=8.0` (dev) | — | Low risk |
| pytest-mock | `>=3.12` (dev) | — | Low risk |
| ruff | `>=0.4` (dev) | — | Low risk |

## Test Coverage Map

### Modules with test files

| Source Module | Test File | Coverage Assessment |
|---------------|-----------|---------------------|
| `discovery/analyzer.py` | `test_analyzer.py` | Good |
| `discovery/interview.py` | `test_interview.py` | Good |
| `discovery/generator.py` | `test_generator.py` | Good |
| `discovery/templates/` | `test_templates.py`, `test_architecture_md.py` | Good |
| `config.py` | `test_config.py` | Good |
| `dag.py` | `test_dag.py` | Good |
| `dashboard.py` | `test_dashboard.py` | Good (except L-23) |
| `dispatcher.py` | `test_dispatcher.py`, `test_dispatcher_simple.py` | Partial |
| `entropy/scanner.py` | `test_entropy_scanner.py` | Good |
| `gc.py` | `test_gc.py` | Good |
| `hooks.py` | `test_hooks.py` | Good |
| `mailbox.py` | `test_mailbox.py`, `test_cli_mailbox.py` | Good |
| `models.py` | `test_models.py` | Good |
| `qa/feedback.py` | `test_qa_feedback.py`, `test_feedback_integration.py` | Partial (M-24) |
| `qa/layer_check.py` | `test_layer_check.py` | Good |
| `qa/structural_check.py` | `test_structural_check.py` | Good |
| `session.py` | `test_session.py` | Good |
| `scheduler.py` | `test_scheduler.py` | Partial (H-08) |
| `status/parser.py` | `test_status_parser.py` | Good |
| `trackers/` | `test_trackers.py` | Good |
| `cli_scaffold.py` | `test_cli_scaffold.py` | Good |

### Modules with NO test files

| Source Module | Risk | Notes |
|---------------|------|-------|
| `logger.py` | HIGH (H-06) | 3 public methods, 0 tests |
| `planner.py` | HIGH (H-07) | Critical JSON parsing untested |
| `cli.py` (commands) | HIGH (H-09) | Only `version` tested |
| `cli_ext.py` | MEDIUM (M-21) | Only `mailbox` tested |
| `cli_init.py` | MEDIUM (M-22) | Zero tests |
| `cli_helpers.py` | MEDIUM (M-23) | Key utility untested |
| `prompts.py` | MEDIUM (M-19) | Template rendering untested |
| `reporter.py` | MEDIUM (M-20) | Output formatting untested |
| `providers/claude_cli.py` | LOW | Thin subprocess wrapper |
| `providers/base.py` | LOW | Abstract base class |
| `scheduler_helpers.py` | LOW | Small helper functions |
| `status/writer.py` | N/A | May be dead code (M-12) |
| `status/templates.py` | LOW | Template strings only |

---

## Exception Handling Inventory

All `except Exception` sites in `src/lindy_orchestrator/`:

| File | Line | Pattern | Logged? | Risk |
|------|------|---------|---------|------|
| `scheduler.py` | 134 | `except Exception as e` | No (progress callback) | M |
| `scheduler.py` | 154 | `except Exception: pass` | No | L (L-12) |
| `scheduler.py` | 278 | `except Exception: pass` | No | L (L-13) |
| `dispatcher.py` | 352 | `except Exception: pass` | No | M (M-02) |
| `dispatcher.py` | 355 | `except Exception as e` | No | M (M-03) |
| `dispatcher.py` | 360 | `except Exception: pass` | No | M |
| `dispatcher.py` | 418 | `except Exception: pass` | No | M (M-04) |
| `session.py` | 83 | `except Exception: pass` | No | L (L-14) |
| `cli.py` | 131 | `except Exception as e` | Yes (console) | OK |
| `cli_ext.py` | 118 | `except Exception as e` | Yes (console) | OK |
| `cli_ext.py` | 186 | `except Exception as e` | Yes (console) | OK |
| `cli_ext.py` | 233 | `except Exception as e` | Yes (console) | OK |
| `cli_ext.py` | 273 | `except Exception as e` | Yes (console) | OK |
| `cli_ext.py` | 329 | `except Exception as e` | Yes (console) | OK |
| `cli_helpers.py` | 49 | `except Exception as e` | Yes (console) | OK |
| `qa/agent_check.py` | 75 | `except Exception as e` | Yes (result) | OK |
| `scheduler_helpers.py` | 67 | `except Exception as e` | No | L (L-15) |

**Key observation:** 17 `except Exception` sites total. 7 are properly handled (CLI/QA
contexts). 10 have no logging — all in dispatcher, scheduler, session.
