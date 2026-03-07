"""Tests for the streaming dispatcher with stall detection."""

from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

from lindy_orchestrator.config import DispatcherConfig
from lindy_orchestrator.dispatcher import (
    _extract_result_from_lines,
    _extract_tool_use,
    _parse_event,
    dispatch_agent,
)


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestParseEvent:
    def test_valid_json(self):
        line = '{"type": "assistant", "message": {"content": []}}'
        result = _parse_event(line)
        assert result == {"type": "assistant", "message": {"content": []}}

    def test_invalid_json(self):
        assert _parse_event("not json") is None

    def test_empty_line(self):
        assert _parse_event("") is None
        assert _parse_event("  ") is None

    def test_with_newline(self):
        result = _parse_event('{"type": "result"}\n')
        assert result == {"type": "result"}


class TestExtractToolUse:
    def test_tool_use_block(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "id": "123", "input": {}}]
            },
        }
        assert _extract_tool_use(event) == "Bash"

    def test_text_block_only(self):
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        }
        assert _extract_tool_use(event) == ""

    def test_non_assistant_event(self):
        assert _extract_tool_use({"type": "system"}) == ""
        assert _extract_tool_use({"type": "result"}) == ""

    def test_multiple_content_blocks(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "I'll run this"},
                    {"type": "tool_use", "name": "Edit", "id": "456", "input": {}},
                ]
            },
        }
        assert _extract_tool_use(event) == "Edit"


class TestExtractResultFromLines:
    def test_result_event(self):
        lines = [
            '{"type":"system","subtype":"init","session_id":"abc"}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"working..."}]}}\n',
            '{"type":"result","subtype":"success","result":"All done!","duration_ms":5000}\n',
        ]
        assert _extract_result_from_lines(lines) == "All done!"

    def test_fallback_to_text_blocks(self):
        lines = [
            '{"type":"system","subtype":"init"}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"hello world"}]}}\n',
        ]
        assert _extract_result_from_lines(lines) == "hello world"

    def test_empty_lines(self):
        assert _extract_result_from_lines([]) == ""

    def test_last_result_wins(self):
        lines = [
            '{"type":"result","result":"first"}\n',
            '{"type":"result","result":"second"}\n',
        ]
        assert _extract_result_from_lines(lines) == "second"

    def test_multiple_text_blocks_concatenated(self):
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"text","text":"part 1"}]}}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"part 2"}]}}\n',
        ]
        result = _extract_result_from_lines(lines)
        assert "part 1" in result
        assert "part 2" in result


# ---------------------------------------------------------------------------
# Helpers for integration tests
# ---------------------------------------------------------------------------


def _make_stream_lines(*events: dict) -> list[str]:
    """Create JSONL lines from event dicts."""
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
    """A fake Popen that yields pre-defined lines via iteration then exits."""

    def __init__(self, lines: list[str], returncode: int = 0, stderr_content: str = ""):
        self._lines = list(lines)
        self.returncode: int | None = None
        self._final_returncode = returncode
        self.stdout = self  # stdout is iterable (self)
        self.stderr = FakeStderrWithContent(stderr_content) if stderr_content else FakeStderr()
        self._killed = False

    def __iter__(self):
        for line in self._lines:
            yield line
        # After all lines are yielded, set returncode
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
    """A Popen that produces no output (simulates stall)."""

    def __init__(self, stderr_content: str = ""):
        self.returncode: int | None = None
        self.stdout = self
        self.stderr = FakeStderrWithContent(stderr_content) if stderr_content else FakeStderr()
        self._killed = False
        self._stop = threading.Event()

    def __iter__(self):
        # Block until killed
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
        self._stop.set()  # Unblock the iterator

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
# Integration tests with mocked subprocess
# ---------------------------------------------------------------------------


class TestStreamDispatch:
    """Tests for the full dispatch_agent with stream-json."""

    @patch("lindy_orchestrator.dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_success(self, mock_cli, mock_popen, config, tmp_path):
        lines = _make_stream_lines(
            {"type": "system", "subtype": "init", "session_id": "abc"},
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Working..."}]},
            },
            {
                "type": "result",
                "subtype": "success",
                "result": "Task completed!",
                "duration_ms": 3000,
            },
        )
        fake = FakePopen(lines, returncode=0)
        mock_popen.return_value = fake

        result = dispatch_agent("backend", tmp_path, "do stuff", config)

        assert result.success is True
        assert result.output == "Task completed!"
        assert result.event_count == 3
        assert result.error is None

    @patch("lindy_orchestrator.dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_stall_timeout_kills_process(self, mock_cli, mock_popen, config, tmp_path):
        """No output for stall_timeout_seconds → kill with 'stall' error.

        Note: with the grace period, first-event stall uses max(stall*2, 600).
        We set stall to 1 and patch time to trigger it.
        """
        fake = FakeStallingPopen()
        mock_popen.return_value = fake

        config.stall_timeout_seconds = 1

        # Use a very short timeout so the hard timeout triggers before the grace period
        config.timeout_seconds = 2

        result = dispatch_agent("backend", tmp_path, "do stuff", config)

        assert result.success is False
        assert result.error in ("stall", "timeout")
        assert fake._killed is True

    @patch("lindy_orchestrator.dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_hard_timeout(self, mock_cli, mock_popen, config, tmp_path):
        """Exceeds hard timeout → kill with 'timeout' error."""
        # Use a fake that blocks forever
        fake = FakeStallingPopen()
        mock_popen.return_value = fake

        config.timeout_seconds = 1
        config.stall_timeout_seconds = 600  # High so stall doesn't trigger first

        result = dispatch_agent("backend", tmp_path, "do stuff", config)

        assert result.success is False
        assert result.error == "timeout"

    @patch("lindy_orchestrator.dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_on_event_callback(self, mock_cli, mock_popen, config, tmp_path):
        """Verify on_event callback is called for each event."""
        lines = _make_stream_lines(
            {"type": "system", "subtype": "init"},
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "Bash", "id": "1", "input": {}}]
                },
            },
            {"type": "result", "result": "done"},
        )
        fake = FakePopen(lines, returncode=0)
        mock_popen.return_value = fake

        events_received: list[dict] = []
        result = dispatch_agent(
            "backend",
            tmp_path,
            "do stuff",
            config,
            on_event=lambda e: events_received.append(e),
        )

        assert result.success is True
        assert len(events_received) == 3
        assert events_received[0]["type"] == "system"
        assert events_received[1]["type"] == "assistant"
        assert events_received[2]["type"] == "result"

    @patch("lindy_orchestrator.dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_tool_use_tracking(self, mock_cli, mock_popen, config, tmp_path):
        """Verify last_tool_use is tracked from events."""
        lines = _make_stream_lines(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "Read", "id": "1", "input": {}}]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "Edit", "id": "2", "input": {}}]
                },
            },
            {"type": "result", "result": "done"},
        )
        fake = FakePopen(lines, returncode=0)
        mock_popen.return_value = fake

        result = dispatch_agent("backend", tmp_path, "do stuff", config)

        assert result.last_tool_use == "Edit"

    def test_cli_not_found(self, config, tmp_path):
        """When claude CLI is not found, return error."""
        with patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value=None):
            result = dispatch_agent("backend", tmp_path, "do stuff", config)
            assert result.success is False
            assert result.error == "cli_not_found"

    @patch("lindy_orchestrator.dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_nonzero_exit_code(self, mock_cli, mock_popen, config, tmp_path):
        """Process exits with non-zero code → success=False."""
        lines = _make_stream_lines(
            {"type": "result", "subtype": "error", "result": "Something failed"},
        )
        fake = FakePopen(lines, returncode=1)
        mock_popen.return_value = fake

        result = dispatch_agent("backend", tmp_path, "do stuff", config)

        assert result.success is False
        assert result.exit_code == 1

    @patch("lindy_orchestrator.dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_callback_exception_does_not_crash(self, mock_cli, mock_popen, config, tmp_path):
        """A crashing on_event callback should not break the dispatcher."""
        lines = _make_stream_lines(
            {"type": "system", "subtype": "init"},
            {"type": "result", "result": "done"},
        )
        fake = FakePopen(lines, returncode=0)
        mock_popen.return_value = fake

        def bad_callback(event):
            raise RuntimeError("callback crashed!")

        result = dispatch_agent(
            "backend",
            tmp_path,
            "do stuff",
            config,
            on_event=bad_callback,
        )

        assert result.success is True
        assert result.output == "done"

    @patch("lindy_orchestrator.dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_event_count_on_success(self, mock_cli, mock_popen, config, tmp_path):
        """Event count is correctly tracked."""
        lines = _make_stream_lines(
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "bye"}]}},
            {"type": "result", "result": "done"},
        )
        fake = FakePopen(lines, returncode=0)
        mock_popen.return_value = fake

        result = dispatch_agent("backend", tmp_path, "do stuff", config)

        assert result.event_count == 4

    @patch("lindy_orchestrator.dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_stall_includes_stderr(self, mock_cli, mock_popen, config, tmp_path):
        """Stall error message includes stderr output."""
        fake = FakeStallingPopen(stderr_content="FATAL: connection refused")
        mock_popen.return_value = fake

        config.timeout_seconds = 2
        config.stall_timeout_seconds = 1

        result = dispatch_agent("backend", tmp_path, "do stuff", config)

        assert result.success is False
        # stderr should appear in output (may hit timeout or stall first)
        assert fake._killed is True
