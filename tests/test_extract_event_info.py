"""Tests for extract_event_info() — universal provider event parsing."""

from lindy_orchestrator.scheduler_helpers import extract_event_info


class TestClaudeEvents:
    def test_tool_use(self):
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]},
        }
        assert extract_event_info(event) == ("Bash", "")

    def test_thinking(self):
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "text": "reasoning about the problem"}]},
        }
        assert extract_event_info(event) == ("", "reasoning about the problem")

    def test_text_block(self):
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "some output"}]},
        }
        assert extract_event_info(event) == ("", "some output")

    def test_tool_and_thinking(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "text": "let me think"},
                    {"type": "tool_use", "name": "Read", "input": {}},
                ]
            },
        }
        tool, reasoning = extract_event_info(event)
        assert tool == "Read"
        assert reasoning == "let me think"


class TestCodexFlatEvents:
    def test_function_call(self):
        event = {"type": "function_call", "name": "shell", "arguments": "ls -la"}
        assert extract_event_info(event) == ("shell", "")


class TestCodexV1Events:
    def test_nested_function_call(self):
        event = {"id": "0", "msg": {"type": "function_call", "name": "shell"}}
        assert extract_event_info(event) == ("shell", "")

    def test_agent_message(self):
        event = {"id": "1", "msg": {"type": "agent_message", "message": "thinking here"}}
        assert extract_event_info(event) == ("", "thinking here")


class TestCodexV2Events:
    def test_command_execution_started(self):
        event = {
            "type": "item.started",
            "item": {"type": "command_execution", "command": "ls"},
        }
        assert extract_event_info(event) == ("shell", "")

    def test_command_execution_completed(self):
        event = {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "ls"},
        }
        assert extract_event_info(event) == ("shell", "")

    def test_agent_message(self):
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "done with analysis"},
        }
        assert extract_event_info(event) == ("", "done with analysis")


class TestUnknownEvents:
    def test_empty_dict(self):
        assert extract_event_info({}) == ("", "")

    def test_unrecognised_type(self):
        assert extract_event_info({"type": "heartbeat", "ts": 123}) == ("", "")

    def test_stall_warning(self):
        event = {"type": "stall_warning", "stall_seconds": 120, "last_tool": "shell"}
        assert extract_event_info(event) == ("", "")
