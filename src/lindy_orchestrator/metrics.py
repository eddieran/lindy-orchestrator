"""Session-level metrics collection via hook events.

MetricsCollector attaches to a HookRegistry, listens for lifecycle events,
and produces a snapshot dict summarising the session.
"""

from __future__ import annotations

import time
from typing import Any

from .hooks import Event, EventHandler, EventType, HookRegistry


class MetricsCollector:
    """Collects aggregate metrics from hook events during a session."""

    def __init__(self) -> None:
        self._start: float = time.monotonic()
        self._tasks_started: int = 0
        self._tasks_completed: int = 0
        self._tasks_failed: int = 0
        self._tasks_skipped: int = 0
        self._tasks_retried: int = 0
        self._qa_passed: int = 0
        self._qa_failed: int = 0
        self._checkpoints: int = 0
        self._total_events: int = 0
        self._hooks: HookRegistry | None = None
        self._handler: EventHandler | None = None

    # ------------------------------------------------------------------
    # Attach / detach
    # ------------------------------------------------------------------

    def attach(self, hooks: HookRegistry) -> None:
        """Register an on_any handler to collect metrics."""
        self._hooks = hooks
        self._handler = self._on_event
        hooks.on_any(self._handler)

    def detach(self) -> None:
        """Unregister the handler from the hook registry."""
        if self._hooks and self._handler:
            self._hooks.remove_any(self._handler)
        self._hooks = None
        self._handler = None

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a summary dict of all collected metrics."""
        elapsed = round(time.monotonic() - self._start, 2)
        return {
            "elapsed_seconds": elapsed,
            "total_events": self._total_events,
            "tasks_started": self._tasks_started,
            "tasks_completed": self._tasks_completed,
            "tasks_failed": self._tasks_failed,
            "tasks_skipped": self._tasks_skipped,
            "tasks_retried": self._tasks_retried,
            "qa_passed": self._qa_passed,
            "qa_failed": self._qa_failed,
            "checkpoints": self._checkpoints,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        self._total_events += 1
        match event.type:
            case EventType.TASK_STARTED:
                self._tasks_started += 1
            case EventType.TASK_COMPLETED:
                self._tasks_completed += 1
            case EventType.TASK_FAILED:
                self._tasks_failed += 1
            case EventType.TASK_SKIPPED:
                self._tasks_skipped += 1
            case EventType.TASK_RETRYING:
                self._tasks_retried += 1
            case EventType.QA_PASSED:
                self._qa_passed += 1
            case EventType.QA_FAILED:
                self._qa_failed += 1
            case EventType.CHECKPOINT_SAVED:
                self._checkpoints += 1
