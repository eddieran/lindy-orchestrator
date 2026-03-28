"""Thread-safe command queue for interactive execution controls."""

from __future__ import annotations

import queue
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Command:
    """A control command emitted by the web dashboard."""

    name: str
    task_id: int | None = None
    timestamp: str = ""


class CommandQueue:
    """FIFO queue of interactive control commands."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Command] = queue.Queue()

    def enqueue(self, name: str, task_id: int | None = None) -> Command:
        command = Command(
            name=name,
            task_id=task_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._queue.put(command)
        return command

    def pause(self) -> Command:
        return self.enqueue("pause")

    def resume(self) -> Command:
        return self.enqueue("resume")

    def skip(self, task_id: int) -> Command:
        return self.enqueue("skip", task_id)

    def force_pass(self, task_id: int) -> Command:
        return self.enqueue("force_pass", task_id)

    def get_nowait(self) -> Command:
        return self._queue.get_nowait()

    def empty(self) -> bool:
        return self._queue.empty()
