"""Tests for qa/command_check.py — CommandCheckGate."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch


from lindy_orchestrator.qa.command_check import CommandCheckGate


class TestCommandCheckGate:
    def test_no_command_fails(self, tmp_path):
        gate = CommandCheckGate()
        result = gate.check(params={}, project_root=tmp_path)
        assert not result.passed
        assert "No command specified" in result.output

    def test_empty_command_fails(self, tmp_path):
        gate = CommandCheckGate()
        result = gate.check(params={"command": ""}, project_root=tmp_path)
        assert not result.passed
        assert "No command specified" in result.output

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_successful_command(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="all tests passed", stderr=""
        )
        gate = CommandCheckGate()
        result = gate.check(params={"command": "pytest"}, project_root=tmp_path)
        assert result.passed
        assert "all tests passed" in result.output
        assert result.details["exit_code"] == 0

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_failed_command(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="FAILED", stderr="error details"
        )
        gate = CommandCheckGate()
        result = gate.check(params={"command": "pytest"}, project_root=tmp_path)
        assert not result.passed
        assert result.details["exit_code"] == 1
        assert "stderr" in result.output

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_timeout(self, mock_run, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=5)
        gate = CommandCheckGate()
        result = gate.check(
            params={"command": "sleep 100", "timeout": 5}, project_root=tmp_path
        )
        assert not result.passed
        assert "timed out" in result.output
        assert result.details.get("timeout") is True

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_os_error(self, mock_run, tmp_path):
        mock_run.side_effect = OSError("command not found")
        gate = CommandCheckGate()
        result = gate.check(
            params={"command": "nonexistent-cmd"}, project_root=tmp_path
        )
        assert not result.passed
        assert "Failed to run command" in result.output

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_cwd_from_params(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        gate = CommandCheckGate()
        gate.check(
            params={"command": "echo hi", "cwd": "subdir"},
            project_root=tmp_path,
        )
        call_kwargs = mock_run.call_args
        assert str(tmp_path / "subdir") in call_kwargs[1]["cwd"]

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_cwd_from_module_name(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        gate = CommandCheckGate()
        gate.check(
            params={"command": "echo hi"},
            project_root=tmp_path,
            module_name="backend",
        )
        call_kwargs = mock_run.call_args
        assert "backend" in call_kwargs[1]["cwd"]

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_cwd_module_path_template(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        gate = CommandCheckGate()
        gate.check(
            params={"command": "echo hi", "cwd": "{module_path}"},
            project_root=tmp_path,
            module_name="backend",
        )
        call_kwargs = mock_run.call_args
        assert "backend" in call_kwargs[1]["cwd"]

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_cwd_from_resolved_kwarg(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        gate = CommandCheckGate()
        gate.check(
            params={"command": "echo hi"},
            project_root=tmp_path,
            module_path=str(tmp_path / "resolved"),
        )
        call_kwargs = mock_run.call_args
        assert "resolved" in call_kwargs[1]["cwd"]

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_default_cwd_is_project_root(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        gate = CommandCheckGate()
        gate.check(
            params={"command": "echo hi"},
            project_root=tmp_path,
        )
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["cwd"] == str(tmp_path / ".")

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_list_command_no_shell(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        gate = CommandCheckGate()
        gate.check(
            params={"command": ["echo", "hi"]},
            project_root=tmp_path,
        )
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["shell"] is False

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_string_command_uses_shell(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        gate = CommandCheckGate()
        gate.check(
            params={"command": "echo hi && echo bye"},
            project_root=tmp_path,
        )
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["shell"] is True

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_large_stdout_truncated(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="x" * 6000, stderr=""
        )
        gate = CommandCheckGate()
        result = gate.check(params={"command": "echo hi"}, project_root=tmp_path)
        assert result.passed
        assert len(result.output) == 5000

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_custom_timeout(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        gate = CommandCheckGate()
        gate.check(
            params={"command": "echo hi", "timeout": 60},
            project_root=tmp_path,
        )
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["timeout"] == 60

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_default_timeout_is_300(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        gate = CommandCheckGate()
        gate.check(
            params={"command": "echo hi"},
            project_root=tmp_path,
        )
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["timeout"] == 300

    def test_gate_name(self):
        gate = CommandCheckGate()
        # The gate should produce results with gate="command_check"
        result = gate.check(params={}, project_root=Path("/tmp"))
        assert result.gate == "command_check"
