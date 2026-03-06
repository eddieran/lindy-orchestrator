"""JSONL-based inter-agent mailbox for near-real-time module communication.

Each module has an inbox file at .orchestrator/mailbox/{module}.jsonl.
Messages are appended atomically. No external infrastructure required.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Message:
    """A single inter-agent message."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    from_module: str = ""
    to_module: str = ""
    content: str = ""
    message_type: str = "request"  # request | response | notification
    priority: str = "normal"  # low | normal | high | urgent
    status: str = "pending"  # pending | read | acknowledged
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    in_reply_to: str | None = None
    task_id: int | None = None


class Mailbox:
    """Git-native inter-agent mailbox using JSONL files.

    Storage: {mailbox_dir}/{module_name}.jsonl
    Thread-safe for concurrent reads and writes.
    """

    def __init__(self, mailbox_dir: Path) -> None:
        self.mailbox_dir = mailbox_dir
        self.mailbox_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _inbox_path(self, module: str) -> Path:
        return self.mailbox_dir / f"{module}.jsonl"

    def send(self, message: Message) -> None:
        """Append a message to the recipient's inbox."""
        path = self._inbox_path(message.to_module)
        line = json.dumps(asdict(message), default=str) + "\n"
        with self._lock:
            with open(path, "a") as f:
                f.write(line)

    def receive(self, module: str, unread_only: bool = True) -> list[Message]:
        """Read messages for a module."""
        path = self._inbox_path(module)
        if not path.exists():
            return []

        messages = []
        with self._lock:
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    msg = Message(**data)
                    if unread_only and msg.status != "pending":
                        continue
                    messages.append(msg)
                except (json.JSONDecodeError, TypeError):
                    continue
        return messages

    def acknowledge(self, module: str, message_id: str) -> None:
        """Mark a message as acknowledged by rewriting the inbox."""
        path = self._inbox_path(module)
        if not path.exists():
            return

        with self._lock:
            lines = path.read_text().splitlines()
            updated = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("id") == message_id:
                        data["status"] = "acknowledged"
                    updated.append(json.dumps(data, default=str))
                except json.JSONDecodeError:
                    updated.append(line)
            path.write_text("\n".join(updated) + "\n" if updated else "")

    def pending_count(self, module: str) -> int:
        """Count unread messages for a module."""
        return len(self.receive(module, unread_only=True))

    def all_messages(self, module: str) -> list[Message]:
        """Read all messages (including acknowledged) for a module."""
        return self.receive(module, unread_only=False)


def format_mailbox_messages(messages: list[Message]) -> str:
    """Format pending messages for prompt injection."""
    if not messages:
        return ""
    parts = []
    for msg in messages:
        priority_tag = f" [{msg.priority.upper()}]" if msg.priority != "normal" else ""
        parts.append(
            f"- **From {msg.from_module}**{priority_tag} ({msg.message_type}): {msg.content}"
        )
    return "\n".join(parts)
