"""Tests for ActionLogger — JSONL append-only action logger."""

from __future__ import annotations

import json


from lindy_orchestrator.logger import ActionLogger


class TestActionLoggerInit:
    def test_creates_parent_directory(self, tmp_path):
        log_path = tmp_path / "subdir" / "nested" / "actions.jsonl"
        ActionLogger(log_path)
        assert log_path.parent.exists()

    def test_log_path_stored(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        assert logger.log_path == log_path


class TestLogAction:
    def test_basic_success_entry(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_action("deploy", result="success")

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["action"] == "deploy"
        assert entry["result"] == "success"
        assert "timestamp" in entry

    def test_with_details(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_action("test", details={"module": "backend", "count": 3})

        entry = json.loads(log_path.read_text().strip())
        assert entry["details"] == {"module": "backend", "count": 3}

    def test_without_details_no_key(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_action("test")

        entry = json.loads(log_path.read_text().strip())
        assert "details" not in entry

    def test_string_output_stored(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_action("test", output="hello world")

        entry = json.loads(log_path.read_text().strip())
        assert entry["output"] == "hello world"

    def test_long_string_output_truncated(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        long_output = "x" * 6000
        logger.log_action("test", output=long_output)

        entry = json.loads(log_path.read_text().strip())
        assert entry["output"].endswith("... [truncated]")
        assert len(entry["output"]) == 5000 + len("... [truncated]")

    def test_exactly_5000_chars_not_truncated(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        output = "x" * 5000
        logger.log_action("test", output=output)

        entry = json.loads(log_path.read_text().strip())
        assert entry["output"] == output

    def test_dict_output_stored(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_action("test", output={"key": "value", "count": 42})

        entry = json.loads(log_path.read_text().strip())
        assert entry["output"] == {"key": "value", "count": 42}

    def test_non_string_non_dict_output_cast_to_str(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_action("test", output=12345)

        entry = json.loads(log_path.read_text().strip())
        assert entry["output"] == "12345"

    def test_none_output_excluded(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_action("test", output=None)

        entry = json.loads(log_path.read_text().strip())
        assert "output" not in entry

    def test_append_multiple_entries(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_action("first")
        logger.log_action("second")
        logger.log_action("third")

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["action"] == "first"
        assert json.loads(lines[2])["action"] == "third"

    def test_os_error_fallback_to_stderr(self, tmp_path, capsys):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)

        # Remove the parent directory so open() raises OSError
        import shutil

        shutil.rmtree(tmp_path)

        logger.log_action("test", result="success")

        captured = capsys.readouterr()
        assert "[log fallback]" in captured.err
        assert "test" in captured.err


class TestLogDispatch:
    def test_success_dispatch(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_dispatch(
            module="backend",
            prompt_preview="Fix the bug in auth module",
            result={"success": True, "output": "done"},
        )

        entry = json.loads(log_path.read_text().strip())
        assert entry["action"] == "dispatch"
        assert entry["result"] == "success"
        assert entry["details"]["module"] == "backend"

    def test_error_dispatch(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_dispatch(
            module="frontend",
            prompt_preview="Update UI",
            result={"success": False, "error": "timeout"},
        )

        entry = json.loads(log_path.read_text().strip())
        assert entry["result"] == "error"

    def test_prompt_preview_truncated_to_200(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        long_prompt = "p" * 500
        logger.log_dispatch(
            module="mod",
            prompt_preview=long_prompt,
            result={"success": True},
        )

        entry = json.loads(log_path.read_text().strip())
        assert len(entry["details"]["prompt_preview"]) == 200


class TestLogQA:
    def test_pass(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_qa(gate="ci_check", passed=True, output="all green")

        entry = json.loads(log_path.read_text().strip())
        assert entry["action"] == "quality_gate"
        assert entry["result"] == "pass"
        assert entry["details"]["gate"] == "ci_check"
        assert entry["details"]["passed"] is True

    def test_fail(self, tmp_path):
        log_path = tmp_path / "actions.jsonl"
        logger = ActionLogger(log_path)
        logger.log_qa(gate="command_check", passed=False, output="exit code 1")

        entry = json.loads(log_path.read_text().strip())
        assert entry["result"] == "fail"
        assert entry["details"]["passed"] is False
        assert "exit code 1" in entry["output"]
