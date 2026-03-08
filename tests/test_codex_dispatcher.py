"""Tests for the codex_dispatcher module."""

from __future__ import annotations

import json
import subprocess
import threading
from unittest.mock import patch

import pytest

from lindy_orchestrator.codex_dispatcher import (
    _extract_result_from_lines,
    _extract_tool_use,
    _parse_event,
    dispatch_codex_agent,
    dispatch_codex_agent_simple,
    find_codex_cli,
)
from lindy_orchestrator.config import DispatcherConfig


# ---------------------------------------------------------------------------
# find_codex_cli
# ---------------------------------------------------------------------------


class TestFindCodexCli:
    def test_returns_path_when_found(self):
        with patch(
            "lindy_orchestrator.codex_dispatcher.shutil.which", return_value="/usr/bin/codex"
        ):
            assert find_codex_cli() == "/usr/bin/codex"

    def test_returns_none_when_not_found(self):
        with patch("lindy_orchestrator.codex_dispatcher.shutil.which", return_value=None):
            assert find_codex_cli() is None


# ---------------------------------------------------------------------------
# JSONL parsing helpers
# ---------------------------------------------------------------------------


class TestCodexParseEvent:
    def test_valid_json(self):
        line = '{"type": "result", "result": "done"}'
        result = _parse_event(line)
        assert result == {"type": "result", "result": "done"}

    def test_invalid_json(self):
        assert _parse_event("not json") is None

    def test_empty_line(self):
        assert _parse_event("") is None
        assert _parse_event("  ") is None

    def test_with_newline(self):
        result = _parse_event('{"type": "result"}\n')
        assert result == {"type": "result"}


class TestCodexExtractToolUse:
    def test_claude_style_tool_use(self):
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "id": "1", "input": {}}]},
        }
        assert _extract_tool_use(event) == "Bash"

    def test_codex_style_function_call(self):
        event = {"type": "function_call", "name": "shell"}
        assert _extract_tool_use(event) == "shell"

    def test_text_only_event(self):
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        }
        assert _extract_tool_use(event) == ""

    def test_non_assistant_event(self):
        assert _extract_tool_use({"type": "system"}) == ""
        assert _extract_tool_use({"type": "result"}) == ""


class TestCodexExtractResultFromLines:
    def test_result_event(self):
        lines = [
            '{"type":"system","subtype":"init"}\n',
            '{"type":"result","result":"All done!"}\n',
        ]
        assert _extract_result_from_lines(lines) == "All done!"

    def test_fallback_to_text_blocks(self):
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}\n',
        ]
        assert _extract_result_from_lines(lines) == "hello"

    def test_codex_message_event_fallback(self):
        lines = [
            '{"type":"message","content":"codex output"}\n',
        ]
        assert _extract_result_from_lines(lines) == "codex output"

    def test_empty_lines(self):
        assert _extract_result_from_lines([]) == ""


# ---------------------------------------------------------------------------
# Helpers for integration tests
# ---------------------------------------------------------------------------


def _make_stream_lines(*events: dict) -> list[str]:
    return [json.dumps(e) + "\n" for e in events]


class FakeStderr:
    def read(self):
        return ""

    def fileno(self):
        return 100


class FakeStderrWithContent:
    def __init__(self, content: str = "some error output"):
        self._content = content

    def read(self):
        return self._content

    def fileno(self):
        return 100


class FakePopen:
    def __init__(self, lines: list[str], returncode: int = 0, stderr_content: str = ""):
        self._lines = list(lines)
        self.returncode: int | None = None
        self._final_returncode = returncode
        self.stdout = self
        self.stderr = FakeStderrWithContent(stderr_content) if stderr_content else FakeStderr()
        self._killed = False

    def __iter__(self):
        for line in self._lines:
            yield line
        self.returncode = self._final_returncode

    def poll(self) -> int | None:
        if self._killed:
            self.returncode = -9
            return -9
        return self.returncode

    def kill(self):
        self._killed = True
        self.returncode = -9

    def wait(self):
        if self.returncode is None:
            self.returncode = self._final_returncode


class FakeStallingPopen:
    def __init__(self, stderr_content: str = ""):
        self.returncode: int | None = None
        self.stdout = self
        self.stderr = FakeStderrWithContent(stderr_content) if stderr_content else FakeStderr()
        self._killed = False
        self._stop = threading.Event()

    def __iter__(self):
        self._stop.wait()
        return iter([])

    def poll(self) -> int | None:
        if self._killed:
            self.returncode = -9
            return -9
        return None

    def kill(self):
        self._killed = True
        self.returncode = -9
        self._stop.set()

    def wait(self):
        pass


@pytest.fixture
def config():
    return DispatcherConfig(
        timeout_seconds=60,
        stall_timeout_seconds=2,
        permission_mode="bypassPermissions",
    )


# ---------------------------------------------------------------------------
# dispatch_codex_agent_simple tests
# ---------------------------------------------------------------------------


class TestCodexSimpleDispatch:
    def test_cli_not_found(self, config, tmp_path):
        with patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value=None):
            result = dispatch_codex_agent_simple("backend", tmp_path, "plan stuff", config)
            assert result.success is False
            assert result.error == "cli_not_found"

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.run")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_success_with_json_result(self, mock_cli, mock_run, config, tmp_path):
        # codex exec --json outputs JSONL with a result event
        jsonl_output = json.dumps({"type": "result", "result": "Plan generated!"})
        mock_run.return_value = type(
            "proc",
            (),
            {
                "stdout": jsonl_output,
                "stderr": "",
                "returncode": 0,
            },
        )()
        result = dispatch_codex_agent_simple("backend", tmp_path, "plan", config)
        assert result.success is True
        assert result.output == "Plan generated!"

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.run")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_success_with_plain_text(self, mock_cli, mock_run, config, tmp_path):
        mock_run.return_value = type(
            "proc",
            (),
            {"stdout": "Just text", "stderr": "", "returncode": 0},
        )()
        result = dispatch_codex_agent_simple("backend", tmp_path, "plan", config)
        assert result.success is True
        assert result.output == "Just text"

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.run")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_timeout(self, mock_cli, mock_run, config, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="codex", timeout=60)
        result = dispatch_codex_agent_simple("backend", tmp_path, "plan", config)
        assert result.success is False
        assert result.error == "timeout"

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.run")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_stderr_fallback(self, mock_cli, mock_run, config, tmp_path):
        mock_run.return_value = type(
            "proc",
            (),
            {"stdout": "", "stderr": "Error: something went wrong", "returncode": 1},
        )()
        result = dispatch_codex_agent_simple("backend", tmp_path, "plan", config)
        assert result.success is False
        assert "[stderr]" in result.output

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.run")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_command_uses_codex_flags(self, mock_cli, mock_run, config, tmp_path):
        """Verify the command uses codex-specific flags."""
        mock_run.return_value = type(
            "proc",
            (),
            {"stdout": '{"type":"result","result":"ok"}', "stderr": "", "returncode": 0},
        )()
        dispatch_codex_agent_simple("backend", tmp_path, "test prompt", config)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/codex"
        assert "exec" in cmd
        assert "--full-auto" in cmd
        assert "--json" in cmd
        assert "test prompt" in cmd

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.run")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_file_not_found_error(self, mock_cli, mock_run, config, tmp_path):
        mock_run.side_effect = FileNotFoundError()
        result = dispatch_codex_agent_simple("backend", tmp_path, "plan", config)
        assert result.success is False
        assert result.error == "cli_not_found"

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.run")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_output_truncation(self, mock_cli, mock_run, config, tmp_path):
        config.max_output_chars = 20
        mock_run.return_value = type(
            "proc",
            (),
            {"stdout": "A" * 100, "stderr": "", "returncode": 0},
        )()
        result = dispatch_codex_agent_simple("backend", tmp_path, "plan", config)
        assert result.truncated is True
        assert "TRUNCATED" in result.output


# ---------------------------------------------------------------------------
# dispatch_codex_agent (streaming) tests
# ---------------------------------------------------------------------------


class TestCodexStreamDispatch:
    @patch("lindy_orchestrator.codex_dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_success(self, mock_cli, mock_popen, config, tmp_path):
        lines = _make_stream_lines(
            {"type": "system", "subtype": "init"},
            {"type": "result", "result": "Task completed!"},
        )
        fake = FakePopen(lines, returncode=0)
        mock_popen.return_value = fake
        result = dispatch_codex_agent("backend", tmp_path, "do stuff", config)
        assert result.success is True
        assert result.output == "Task completed!"
        assert result.event_count == 2

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_hard_timeout(self, mock_cli, mock_popen, config, tmp_path):
        fake = FakeStallingPopen()
        mock_popen.return_value = fake
        config.timeout_seconds = 1
        config.stall_timeout_seconds = 600
        result = dispatch_codex_agent("backend", tmp_path, "do stuff", config)
        assert result.success is False
        assert result.error == "timeout"

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_on_event_callback(self, mock_cli, mock_popen, config, tmp_path):
        lines = _make_stream_lines(
            {"type": "system", "subtype": "init"},
            {"type": "result", "result": "done"},
        )
        fake = FakePopen(lines, returncode=0)
        mock_popen.return_value = fake
        events_received: list[dict] = []
        result = dispatch_codex_agent(
            "backend",
            tmp_path,
            "do stuff",
            config,
            on_event=lambda e: events_received.append(e),
        )
        assert result.success is True
        assert len(events_received) == 2

    def test_cli_not_found(self, config, tmp_path):
        with patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value=None):
            result = dispatch_codex_agent("backend", tmp_path, "do stuff", config)
            assert result.success is False
            assert result.error == "cli_not_found"

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_nonzero_exit_code(self, mock_cli, mock_popen, config, tmp_path):
        lines = _make_stream_lines(
            {"type": "result", "subtype": "error", "result": "Failed"},
        )
        fake = FakePopen(lines, returncode=1)
        mock_popen.return_value = fake
        result = dispatch_codex_agent("backend", tmp_path, "do stuff", config)
        assert result.success is False
        assert result.exit_code == 1

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_command_uses_codex_flags(self, mock_cli, mock_popen, config, tmp_path):
        lines = _make_stream_lines({"type": "result", "result": "ok"})
        fake = FakePopen(lines, returncode=0)
        mock_popen.return_value = fake
        dispatch_codex_agent("backend", tmp_path, "test prompt", config)
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/usr/bin/codex"
        assert "exec" in cmd
        assert "--full-auto" in cmd
        assert "--json" in cmd
        assert "test prompt" in cmd

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_callback_exception_does_not_crash(self, mock_cli, mock_popen, config, tmp_path):
        lines = _make_stream_lines(
            {"type": "system", "subtype": "init"},
            {"type": "result", "result": "done"},
        )
        fake = FakePopen(lines, returncode=0)
        mock_popen.return_value = fake

        def bad_callback(event):
            raise RuntimeError("callback crashed!")

        result = dispatch_codex_agent(
            "backend", tmp_path, "do stuff", config, on_event=bad_callback
        )
        assert result.success is True
        assert result.output == "done"

    @patch("lindy_orchestrator.codex_dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.codex_dispatcher.find_codex_cli", return_value="/usr/bin/codex")
    def test_popen_file_not_found(self, mock_cli, mock_popen, config, tmp_path):
        mock_popen.side_effect = FileNotFoundError()
        result = dispatch_codex_agent("backend", tmp_path, "do stuff", config)
        assert result.success is False
        assert result.error == "cli_not_found"
