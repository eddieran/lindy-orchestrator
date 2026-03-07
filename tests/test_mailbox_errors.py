"""Tests for mailbox error paths and edge cases."""

from __future__ import annotations

import json

from lindy_orchestrator.mailbox import Mailbox, Message, format_mailbox_messages


class TestMailboxCorruptedData:
    def test_receive_with_corrupted_jsonl_line(self, tmp_path):
        """Corrupted lines should be skipped, valid lines parsed."""
        mb_dir = tmp_path / "mailbox"
        mb_dir.mkdir()
        inbox = mb_dir / "mod.jsonl"
        valid_msg = {
            "id": "abc",
            "from_module": "a",
            "to_module": "mod",
            "content": "valid",
            "message_type": "request",
            "priority": "normal",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00",
            "in_reply_to": None,
            "task_id": None,
        }
        inbox.write_text(
            "not valid json\n"
            + json.dumps(valid_msg) + "\n"
            + "{bad json too\n"
        )

        mb = Mailbox(mb_dir)
        messages = mb.receive("mod")
        assert len(messages) == 1
        assert messages[0].content == "valid"

    def test_receive_with_empty_lines(self, tmp_path):
        mb_dir = tmp_path / "mailbox"
        mb_dir.mkdir()
        inbox = mb_dir / "mod.jsonl"
        msg = {
            "id": "x",
            "from_module": "a",
            "to_module": "mod",
            "content": "hello",
            "message_type": "request",
            "priority": "normal",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00",
            "in_reply_to": None,
            "task_id": None,
        }
        inbox.write_text("\n\n" + json.dumps(msg) + "\n\n\n")

        mb = Mailbox(mb_dir)
        messages = mb.receive("mod")
        assert len(messages) == 1

    def test_receive_with_type_error(self, tmp_path):
        """Lines with wrong field types should be skipped."""
        mb_dir = tmp_path / "mailbox"
        mb_dir.mkdir()
        inbox = mb_dir / "mod.jsonl"
        # Valid JSON but unexpected field types
        inbox.write_text('{"unexpected_field": true}\n')

        mb = Mailbox(mb_dir)
        # Should not raise, but may return empty or skip
        messages = mb.receive("mod")
        # The TypeError from Message(**data) should be caught
        assert len(messages) == 0

    def test_acknowledge_nonexistent_inbox(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        # Should not raise
        mb.acknowledge("nonexistent_module", "some-id")

    def test_acknowledge_with_corrupted_lines(self, tmp_path):
        mb_dir = tmp_path / "mailbox"
        mb_dir.mkdir()
        inbox = mb_dir / "mod.jsonl"
        valid = {"id": "abc", "from_module": "a", "to_module": "mod", "content": "hi",
                 "status": "pending"}
        inbox.write_text(
            "corrupted\n"
            + json.dumps(valid) + "\n"
        )

        mb = Mailbox(mb_dir)
        mb.acknowledge("mod", "abc")

        # Read back and verify the corrupted line is preserved and valid one updated
        text = inbox.read_text()
        lines = [ln for ln in text.strip().splitlines() if ln.strip()]
        assert len(lines) == 2
        assert "corrupted" in lines[0]
        updated = json.loads(lines[1])
        assert updated["status"] == "acknowledged"


class TestMailboxPendingCountEdgeCases:
    def test_pending_count_nonexistent_module(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        assert mb.pending_count("ghost") == 0

    def test_pending_count_all_acknowledged(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        mb.send(Message(from_module="a", to_module="b", content="msg1"))
        msgs = mb.receive("b")
        mb.acknowledge("b", msgs[0].id)
        assert mb.pending_count("b") == 0


class TestFormatMailboxMessagesExtended:
    def test_format_with_reply(self):
        msgs = [
            Message(
                from_module="api",
                to_module="frontend",
                content="Schema updated",
                message_type="response",
                priority="high",
            )
        ]
        result = format_mailbox_messages(msgs)
        assert "api" in result
        assert "[HIGH]" in result
        assert "response" in result

    def test_format_with_low_priority(self):
        msgs = [
            Message(
                from_module="a",
                to_module="b",
                content="fyi",
                priority="low",
            )
        ]
        result = format_mailbox_messages(msgs)
        assert "[LOW]" in result
