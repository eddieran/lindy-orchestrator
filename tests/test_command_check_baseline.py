"""Tests for diff-aware command_check retryability and worktree resilience."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from lindy_orchestrator.qa.command_check import (
    _check_retryable,
    _extract_violation_files,
)


class TestExtractViolationFiles:
    def test_ruff_violations(self):
        output = (
            "src/foo.py:10:5: E302 expected 2 blank lines\nsrc/bar.py:20:1: F401 unused import\n"
        )
        files = _extract_violation_files(output)
        assert files == ["src/bar.py", "src/foo.py"]

    def test_eslint_violations(self):
        output = (
            "src/components/App.tsx:15:5: warning  Unexpected var  no-var\n"
            "src/utils/helpers.js:30:10: error  Missing return  consistent-return\n"
        )
        files = _extract_violation_files(output)
        assert "src/components/App.tsx" in files
        assert "src/utils/helpers.js" in files

    def test_pytest_failures(self):
        output = "FAILED tests/test_foo.py::test_bar - AssertionError\n"
        files = _extract_violation_files(output)
        assert files == ["tests/test_foo.py"]

    def test_typescript_errors(self):
        output = "src/index.ts(10,5): error TS2322: Type 'X' is not assignable\n"
        files = _extract_violation_files(output)
        assert files == ["src/index.ts"]

    def test_go_errors(self):
        output = "./pkg/handler.go:42:5: undefined: Foo\n"
        files = _extract_violation_files(output)
        assert "pkg/handler.go" in files

    def test_no_file_paths(self):
        output = "Something went wrong\nError: command failed"
        files = _extract_violation_files(output)
        assert files == []

    def test_mixed_output(self):
        output = (
            "Running tests...\n"
            "src/foo.py:10:1: E302 expected 2 blank lines\n"
            "All checks passed for src/bar.py\n"
            "FAILED tests/test_x.py::test_y - assert 1 == 2\n"
        )
        files = _extract_violation_files(output)
        assert "src/foo.py" in files
        assert "tests/test_x.py" in files
        assert "src/bar.py" not in files  # not in a violation pattern


class TestCheckRetryable:
    def test_all_violations_in_unchanged_files(self):
        """Violations only in files the agent didn't touch → non-retryable."""
        output = "src/old_code.py:10:1: E302 expected 2 blank lines\n"
        with patch(
            "lindy_orchestrator.qa.command_check._get_changed_files",
            return_value=["src/new_file.py"],
        ):
            result = _check_retryable(Path("/project"), Path("/project"), None, output)
        assert result is False

    def test_violations_in_changed_files(self):
        """Violations in files the agent changed → retryable."""
        output = "src/new_file.py:10:1: E302 expected 2 blank lines\n"
        with patch(
            "lindy_orchestrator.qa.command_check._get_changed_files",
            return_value=["src/new_file.py"],
        ):
            result = _check_retryable(Path("/project"), Path("/project"), None, output)
        assert result is True

    def test_mixed_violations(self):
        """Mix of pre-existing and new violations → retryable."""
        output = (
            "src/old_code.py:10:1: E302 expected 2 blank lines\n"
            "src/new_file.py:5:1: F401 unused import\n"
        )
        with patch(
            "lindy_orchestrator.qa.command_check._get_changed_files",
            return_value=["src/new_file.py"],
        ):
            result = _check_retryable(Path("/project"), Path("/project"), None, output)
        assert result is True

    def test_no_changed_files_defaults_retryable(self):
        """Can't determine changed files → safe default: retryable."""
        output = "src/foo.py:10:1: E302 expected 2 blank lines\n"
        with patch(
            "lindy_orchestrator.qa.command_check._get_changed_files",
            return_value=[],
        ):
            result = _check_retryable(Path("/project"), Path("/project"), None, output)
        assert result is True

    def test_no_violations_parsed_defaults_retryable(self):
        """Can't parse file paths from output → safe default: retryable."""
        output = "Build failed with unknown error\n"
        with patch(
            "lindy_orchestrator.qa.command_check._get_changed_files",
            return_value=["src/new_file.py"],
        ):
            result = _check_retryable(Path("/project"), Path("/project"), None, output)
        assert result is True

    def test_basename_matching(self):
        """Basename fallback matches even with different path prefixes."""
        # Output uses a subdir-relative path, changed_files uses project-root-relative
        output = "components/App.tsx:15:5: warning  Unexpected var\n"
        with patch(
            "lindy_orchestrator.qa.command_check._get_changed_files",
            return_value=["frontend/src/components/App.tsx"],
        ):
            result = _check_retryable(Path("/project"), Path("/project"), None, output)
        assert result is True

    def test_cwd_relative_resolution(self):
        """Violations with cwd-relative paths are resolved against project root."""
        # Command ran in /project/backend, output has backend-relative paths
        output = "src/handler.py:10:1: F401 unused import\n"
        with patch(
            "lindy_orchestrator.qa.command_check._get_changed_files",
            return_value=["backend/src/handler.py"],
        ):
            result = _check_retryable(
                Path("/project"),
                Path("/project/backend"),
                None,
                output,
            )
        assert result is True

    def test_exception_in_get_changed_files_defaults_retryable(self):
        """Exception while getting changed files → safe default: retryable."""
        output = "src/foo.py:10:1: E302\n"
        with patch(
            "lindy_orchestrator.qa.command_check._get_changed_files",
            side_effect=RuntimeError("git failed"),
        ):
            result = _check_retryable(Path("/project"), Path("/project"), None, output)
        assert result is True


class TestWorktreeBranchConflict:
    """Test worktree resilience when branch is already checked out."""

    def test_detach_fallback_on_branch_conflict(self, tmp_path):
        """When branch is occupied, worktree should fall back to detached HEAD."""
        from unittest.mock import MagicMock

        from lindy_orchestrator.worktree import create_worktree

        # This test verifies the logic path; actual git operations need a real repo.
        # We mock subprocess to simulate the "already used by worktree" error.
        mock_results = [
            # _ensure_branch: git branch --list
            MagicMock(returncode=0, stdout="  af/task-1\n", stderr=""),
            # git worktree add (fails — branch occupied)
            MagicMock(
                returncode=128,
                stdout="",
                stderr="fatal: 'af/task-1' is already used by worktree at '/other/path'",
            ),
            # git worktree add --detach (succeeds)
            MagicMock(returncode=0, stdout="", stderr=""),
            # git checkout -b worktree-task-1 (succeeds)
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        with patch("lindy_orchestrator.worktree.subprocess") as mock_sub:
            mock_sub.run = MagicMock(side_effect=mock_results)
            mock_sub.TimeoutExpired = TimeoutError

            create_worktree(tmp_path, "af/task-1", 1)

            # Verify detached HEAD fallback was used
            calls = mock_sub.run.call_args_list
            assert any("--detach" in str(c) for c in calls)
