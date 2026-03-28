"""Tests for lifecycle hook execution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lindy_orchestrator.orchestrator import _run_lifecycle_hook


@pytest.fixture()
def progress():
    return MagicMock()


class TestRunLifecycleHook:
    def test_empty_hook_not_executed(self, progress: MagicMock, tmp_path: Path) -> None:
        result = _run_lifecycle_hook("after_create", "", tmp_path, progress)
        assert result is True

    @patch("lindy_orchestrator.orchestrator.subprocess.run")
    def test_success_returns_true(
        self, mock_run: MagicMock, progress: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_lifecycle_hook("after_create", "echo hello", tmp_path, progress)
        assert result is True
        mock_run.assert_called_once()

    @patch("lindy_orchestrator.orchestrator.subprocess.run")
    def test_failure_returns_false_without_raising(
        self, mock_run: MagicMock, progress: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="oops")
        result = _run_lifecycle_hook("before_run", "false", tmp_path, progress)
        assert result is False

    @patch("lindy_orchestrator.orchestrator.subprocess.run")
    def test_timeout_handled(
        self, mock_run: MagicMock, progress: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)
        result = _run_lifecycle_hook("after_run", "sleep 100", tmp_path, progress, timeout=5)
        assert result is False

    @patch("lindy_orchestrator.orchestrator.subprocess.run")
    def test_compound_command_uses_sh(
        self, mock_run: MagicMock, progress: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        _run_lifecycle_hook("after_create", "echo a && echo b", tmp_path, progress)
        cmd_args = mock_run.call_args[0][0]
        assert cmd_args == ["sh", "-c", "echo a && echo b"]

    @patch("lindy_orchestrator.orchestrator.subprocess.run")
    def test_simple_command_uses_shlex(
        self, mock_run: MagicMock, progress: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        _run_lifecycle_hook("after_create", "echo hello", tmp_path, progress)
        cmd_args = mock_run.call_args[0][0]
        assert cmd_args == ["echo", "hello"]

    @patch("lindy_orchestrator.orchestrator.subprocess.run")
    def test_cwd_passed_correctly(
        self, mock_run: MagicMock, progress: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        _run_lifecycle_hook("after_create", "pwd", tmp_path, progress)
        assert mock_run.call_args[1]["cwd"] == str(tmp_path)

    @patch("lindy_orchestrator.orchestrator.subprocess.run")
    def test_exception_returns_false(
        self, mock_run: MagicMock, progress: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.side_effect = OSError("not found")
        result = _run_lifecycle_hook("before_remove", "missing_cmd", tmp_path, progress)
        assert result is False


class TestLifecycleHooksConfigDefaults:
    def test_defaults(self) -> None:
        from lindy_orchestrator.config import LifecycleHooksConfig

        lc = LifecycleHooksConfig()
        assert lc.after_create == ""
        assert lc.before_run == ""
        assert lc.after_run == ""
        assert lc.before_remove == ""
        assert lc.timeout == 60

    def test_config_includes_lifecycle_hooks(self) -> None:
        from lindy_orchestrator.config import OrchestratorConfig

        cfg = OrchestratorConfig()
        assert hasattr(cfg, "lifecycle_hooks")
        assert cfg.lifecycle_hooks.timeout == 60
