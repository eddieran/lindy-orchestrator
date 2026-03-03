"""Tests for scheduler delivery check logic."""

from pathlib import Path
from unittest.mock import patch

from lindy_orchestrator.scheduler import _check_delivery


def _fake_run(returncode=0, stdout="", stderr=""):
    """Create a fake subprocess.CompletedProcess."""
    return type(
        "CompletedProcess",
        (),
        {"returncode": returncode, "stdout": stdout, "stderr": stderr},
    )()


class TestCheckDelivery:
    """Tests for _check_delivery using merge-base fork-point comparison."""

    @patch("lindy_orchestrator.scheduler.subprocess.run")
    def test_branch_not_found(self, mock_run, tmp_path: Path):
        # Both local and remote branch checks return empty
        mock_run.side_effect = [
            _fake_run(stdout=""),  # git branch --list
            _fake_run(stdout=""),  # git branch -r --list
        ]
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is False
        assert "not found" in msg

    @patch("lindy_orchestrator.scheduler.subprocess.run")
    def test_branch_exists_with_commits(self, mock_run, tmp_path: Path):
        mock_run.side_effect = [
            _fake_run(stdout="  af/task-1\n"),  # branch exists
            _fake_run(stdout="abc123\n"),  # merge-base
            _fake_run(stdout="3\n"),  # rev-list --count
        ]
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is True
        assert "3 new commit(s)" in msg

    @patch("lindy_orchestrator.scheduler.subprocess.run")
    def test_branch_exists_no_commits(self, mock_run, tmp_path: Path):
        mock_run.side_effect = [
            _fake_run(stdout="  af/task-1\n"),  # branch exists
            _fake_run(stdout="abc123\n"),  # merge-base
            _fake_run(stdout="0\n"),  # rev-list --count = 0
        ]
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is False
        assert "no new commits" in msg

    @patch("lindy_orchestrator.scheduler.subprocess.run")
    def test_merge_base_fails_fallback(self, mock_run, tmp_path: Path):
        """When merge-base fails (unrelated branches), falls back gracefully."""
        mock_run.side_effect = [
            _fake_run(stdout="  af/task-1\n"),  # branch exists
            _fake_run(returncode=1, stdout=""),  # merge-base fails
            _fake_run(stdout="5\n"),  # rev-list --count (all commits on branch)
        ]
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is True
        assert "5 new commit(s)" in msg

    @patch("lindy_orchestrator.scheduler.subprocess.run")
    def test_remote_branch_found(self, mock_run, tmp_path: Path):
        mock_run.side_effect = [
            _fake_run(stdout=""),  # local branch not found
            _fake_run(stdout="  origin/af/task-1\n"),  # remote branch found
            _fake_run(stdout="abc123\n"),  # merge-base
            _fake_run(stdout="2\n"),  # rev-list --count
        ]
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is True
        assert "2 new commit(s)" in msg

    @patch("lindy_orchestrator.scheduler.subprocess.run")
    def test_exception_returns_error(self, mock_run, tmp_path: Path):
        mock_run.side_effect = OSError("git not found")
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is False
        assert "Delivery check error" in msg
