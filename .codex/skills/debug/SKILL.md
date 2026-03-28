---
name: debug
description:
  Investigate test failures, CLI errors, and execution issues by tracing logs,
  stack traces, and pytest output; use when runs fail, tests break, or behavior
  is unexpected.
---

# Debug

## Goals

- Find why a test, CLI command, or orchestration run is failing.
- Isolate root cause quickly using structured investigation.
- Provide actionable fix with evidence.

## Quick Triage

1. Reproduce the failure with the exact command.
2. Read the full error output (stack trace, assertion, exit code).
3. Identify the failing module/file/line.
4. Check recent changes (`git log --oneline -10`, `git diff origin/main`).
5. Form a hypothesis and verify with a targeted test or print.

## Commands

```bash
# Run tests with verbose output
pytest tests/ -x -v --tb=long

# Run a specific test
pytest tests/test_<module>.py -x -v --tb=long -k "<test_name>"

# Check lint/format (may surface issues)
ruff check src/ tests/
ruff format --check src/ tests/

# Check CLI entry point
python -m lindy_orchestrator.cli --help

# Recent changes that may have introduced the issue
git log --oneline -10
git diff origin/main --stat
```

## Investigation Flow

1. **Reproduce**: run the exact failing command and capture full output.
2. **Locate**: identify the file, function, and line from the traceback.
3. **Understand**: read the failing code and its test to understand expected
   vs actual behavior.
4. **Hypothesize**: form one clear hypothesis for the root cause.
5. **Verify**: write or modify a test to confirm the hypothesis.
6. **Fix**: make the minimal change that addresses root cause.
7. **Validate**: rerun the original failing command plus the full test suite.

## Notes

- Prefer `pytest -x` to stop on first failure during investigation.
- Use `--tb=long` for full tracebacks.
- Check `pyproject.toml` for project configuration and dependencies.
- If the issue is in orchestration logic, check `src/lindy_orchestrator/` modules.
