# Iteration 2.5: Tool Hardening — Diff-Aware QA & Worktree Resilience

## Problem Statement

Three systemic issues cause wasted retries, long execution times, and cascading failures:

1. **command_check only checks exit code** — pre-existing lint/test issues in the project cause agent retries that can never succeed (agent didn't write the bad code)
2. **Retry prompts include pre-existing violations** — agent wastes time trying to fix code it didn't touch
3. **Worktree creation fails when branch is occupied** — falls back to shared dir, then QA gates fail because CWD doesn't exist

## Changes

### Fix 1: Diff-aware command_check (`command_check.py`)

After a command_check fails:
1. Get changed files via existing `_get_changed_files()`
2. Extract file paths from the gate output (regex: `file.py:line:col` etc.)
3. Partition violations: "in agent-changed files" vs "in unchanged files"
4. If ALL violations are in unchanged files → `retryable=False`
5. If mixed → `retryable=True`, annotate output marking which are pre-existing

This reuses the existing `_get_changed_files()` helper and adds ~40 lines.

### Fix 2: Filter pre-existing violations from retry feedback (`feedback.py`)

In `build_structured_feedback()`:
- Accept optional `changed_files` parameter
- Filter `_extract_specific_errors()` to only include errors in changed files
- Retry prompt only shows violations the agent can actually fix

### Fix 3: Worktree branch conflict resilience (`worktree.py`)

When `git worktree add` fails due to branch already checked out:
1. Detect the "already used by worktree" error
2. Create worktree in detached HEAD mode instead
3. Create a new unique branch name inside the worktree

## Verification

- `pytest tests/test_command_check_baseline.py -v` (new test file)
- `pytest tests/ -v` (no regressions)
- `ruff check src/ tests/ && ruff format --check src/ tests/`
