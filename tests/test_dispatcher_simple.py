"""Tests for dispatch_agent_simple."""

from __future__ import annotations
import json
from unittest.mock import patch
from lindy_orchestrator.config import DispatcherConfig
from lindy_orchestrator.dispatcher import dispatch_agent_simple
import pytest


@pytest.fixture
def config():
    return DispatcherConfig(
        timeout_seconds=60,
        stall_timeout_seconds=2,
        permission_mode="bypassPermissions",
    )


class TestSimpleDispatch:
    """Tests for dispatch_agent_simple (blocking JSON mode)."""

    def test_cli_not_found(self, config, tmp_path):
        with patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value=None):
            result = dispatch_agent_simple("backend", tmp_path, "plan stuff", config)
            assert result.success is False
            assert result.error == "cli_not_found"

    @patch("lindy_orchestrator.dispatcher.subprocess.run")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_success_with_json_result(self, mock_cli, mock_run, config, tmp_path):
        """Parses JSON output and extracts result field."""
        mock_run.return_value = type(
            "proc",
            (),
            {
                "stdout": json.dumps({"result": "Plan generated!", "cost_usd": 0.01}),
                "stderr": "",
                "returncode": 0,
            },
        )()

        result = dispatch_agent_simple("backend", tmp_path, "plan stuff", config)

        assert result.success is True
        assert result.output == "Plan generated!"

    @patch("lindy_orchestrator.dispatcher.subprocess.run")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_success_with_plain_text(self, mock_cli, mock_run, config, tmp_path):
        """Non-JSON output is returned as-is."""
        mock_run.return_value = type(
            "proc",
            (),
            {
                "stdout": "Just some text",
                "stderr": "",
                "returncode": 0,
            },
        )()

        result = dispatch_agent_simple("backend", tmp_path, "plan stuff", config)

        assert result.success is True
        assert result.output == "Just some text"

    @patch("lindy_orchestrator.dispatcher.subprocess.run")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_timeout(self, mock_cli, mock_run, config, tmp_path):
        """subprocess.TimeoutExpired → error='timeout'."""
        mock_run.side_effect = __import__("subprocess").TimeoutExpired(cmd="claude", timeout=60)

        result = dispatch_agent_simple("backend", tmp_path, "plan stuff", config)

        assert result.success is False
        assert result.error == "timeout"

    @patch("lindy_orchestrator.dispatcher.subprocess.run")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_stderr_fallback(self, mock_cli, mock_run, config, tmp_path):
        """Empty stdout → falls back to stderr."""
        mock_run.return_value = type(
            "proc",
            (),
            {
                "stdout": "",
                "stderr": "Error: something went wrong",
                "returncode": 1,
            },
        )()

        result = dispatch_agent_simple("backend", tmp_path, "plan stuff", config)

        assert result.success is False
        assert "[stderr]" in result.output
