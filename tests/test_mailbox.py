"""Tests for the inter-agent mailbox system."""

from __future__ import annotations

import threading

from lindy_orchestrator.mailbox import Mailbox, Message, format_mailbox_messages


class TestMailbox:
    def test_send_and_receive(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        msg = Message(
            from_module="backend",
            to_module="frontend",
            content="UserSchema added 'role' field",
        )
        mb.send(msg)

        received = mb.receive("frontend")
        assert len(received) == 1
        assert received[0].from_module == "backend"
        assert received[0].content == "UserSchema added 'role' field"

    def test_receive_empty_inbox(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        assert mb.receive("nonexistent") == []

    def test_unread_only(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        mb.send(Message(from_module="a", to_module="b", content="msg1"))
        mb.send(Message(from_module="a", to_module="b", content="msg2"))

        # Acknowledge first message
        messages = mb.receive("b")
        mb.acknowledge("b", messages[0].id)

        # Only msg2 should be pending
        unread = mb.receive("b", unread_only=True)
        assert len(unread) == 1
        assert unread[0].content == "msg2"

    def test_all_messages_includes_acknowledged(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        mb.send(Message(from_module="a", to_module="b", content="msg1"))
        mb.send(Message(from_module="a", to_module="b", content="msg2"))

        messages = mb.receive("b")
        mb.acknowledge("b", messages[0].id)

        all_msgs = mb.all_messages("b")
        assert len(all_msgs) == 2

    def test_pending_count(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        assert mb.pending_count("x") == 0

        mb.send(Message(from_module="a", to_module="x", content="1"))
        mb.send(Message(from_module="b", to_module="x", content="2"))
        assert mb.pending_count("x") == 2

        messages = mb.receive("x")
        mb.acknowledge("x", messages[0].id)
        assert mb.pending_count("x") == 1

    def test_multiple_recipients(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        mb.send(Message(from_module="a", to_module="b", content="for b"))
        mb.send(Message(from_module="a", to_module="c", content="for c"))

        assert len(mb.receive("b")) == 1
        assert len(mb.receive("c")) == 1
        assert mb.receive("b")[0].content == "for b"
        assert mb.receive("c")[0].content == "for c"

    def test_message_types(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        mb.send(
            Message(
                from_module="a",
                to_module="b",
                content="need API",
                message_type="request",
                priority="high",
            )
        )
        msg = mb.receive("b")[0]
        assert msg.message_type == "request"
        assert msg.priority == "high"

    def test_in_reply_to(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        original = Message(from_module="a", to_module="b", content="question?")
        mb.send(original)

        reply = Message(
            from_module="b",
            to_module="a",
            content="answer!",
            message_type="response",
            in_reply_to=original.id,
        )
        mb.send(reply)

        replies = mb.receive("a")
        assert len(replies) == 1
        assert replies[0].in_reply_to == original.id

    def test_thread_safety(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        errors = []

        def send_messages(module: str, count: int):
            try:
                for i in range(count):
                    mb.send(
                        Message(
                            from_module=module,
                            to_module="target",
                            content=f"msg {i}",
                        )
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=send_messages, args=("a", 10)),
            threading.Thread(target=send_messages, args=("b", 10)),
            threading.Thread(target=send_messages, args=("c", 10)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        all_msgs = mb.all_messages("target")
        assert len(all_msgs) == 30

    def test_acknowledge_nonexistent(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        mb.send(Message(from_module="a", to_module="b", content="test"))
        mb.acknowledge("b", "nonexistent-id")  # should not raise
        assert mb.pending_count("b") == 1

    def test_jsonl_persistence(self, tmp_path):
        mb_dir = tmp_path / "mailbox"
        mb1 = Mailbox(mb_dir)
        mb1.send(Message(from_module="a", to_module="b", content="persist me"))

        # Create new Mailbox instance pointing to same dir
        mb2 = Mailbox(mb_dir)
        received = mb2.receive("b")
        assert len(received) == 1
        assert received[0].content == "persist me"

    def test_task_id_field(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        mb.send(Message(from_module="a", to_module="b", content="task related", task_id=42))
        msg = mb.receive("b")[0]
        assert msg.task_id == 42


class TestFormatMailboxMessages:
    def test_format_single_message(self):
        msgs = [
            Message(
                from_module="backend",
                to_module="frontend",
                content="API ready at /api/v1/users",
            )
        ]
        result = format_mailbox_messages(msgs)
        assert "backend" in result
        assert "/api/v1/users" in result

    def test_format_with_priority(self):
        msgs = [
            Message(
                from_module="a",
                to_module="b",
                content="urgent fix needed",
                priority="urgent",
            )
        ]
        result = format_mailbox_messages(msgs)
        assert "[URGENT]" in result

    def test_format_normal_priority_no_tag(self):
        msgs = [Message(from_module="a", to_module="b", content="routine update")]
        result = format_mailbox_messages(msgs)
        assert "[NORMAL]" not in result

    def test_format_empty_list(self):
        assert format_mailbox_messages([]) == ""

    def test_format_multiple_messages(self):
        msgs = [
            Message(from_module="a", to_module="b", content="first"),
            Message(from_module="c", to_module="b", content="second"),
        ]
        result = format_mailbox_messages(msgs)
        assert "first" in result
        assert "second" in result
        assert result.count("- **From") == 2
