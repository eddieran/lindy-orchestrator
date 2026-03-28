"""Central event hook system for orchestrator lifecycle events.

Provides a thread-safe registry for event handlers that fire on task
state transitions, QA results, stall detection, checkpoints, and more.
Supports both synchronous and asynchronous handlers.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


class EventType(str, Enum):
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_RETRYING = "task_retrying"
    TASK_SKIPPED = "task_skipped"
    QA_PASSED = "qa_passed"
    QA_FAILED = "qa_failed"
    STALL_WARNING = "stall_warning"
    STALL_KILLED = "stall_killed"
    TASK_HEARTBEAT = "task_heartbeat"
    PHASE_CHANGED = "phase_changed"
    EVAL_SCORED = "eval_scored"
    CHECKPOINT_SAVED = "checkpoint_saved"
    MAILBOX_MESSAGE = "mailbox_message"
    SESSION_START = "session_start"
    SESSION_END = "session_end"


@dataclass
class Event:
    type: EventType
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data: dict[str, Any] = field(default_factory=dict)
    task_id: int | None = None
    module: str = ""


EventHandler = Callable[[Event], None]
AsyncEventHandler = Callable[[Event], Awaitable[None]]


class HookRegistry:
    """Central registry for event handlers. Thread-safe.

    Supports both synchronous and asynchronous handlers. Async handlers
    run on a lazily-created background daemon thread with its own event loop.
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = {}
        self._any_handlers: list[EventHandler] = []
        self._async_handlers: dict[EventType, list[AsyncEventHandler]] = {}
        self._async_any_handlers: list[AsyncEventHandler] = []
        self._lock = threading.Lock()
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._async_thread: threading.Thread | None = None

    def _ensure_async_loop(self) -> asyncio.AbstractEventLoop:
        """Lazily create a background daemon thread with an event loop."""
        if self._async_loop is not None and self._async_loop.is_running():
            return self._async_loop

        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True, name="hooks-async")
        thread.start()
        self._async_loop = loop
        self._async_thread = thread
        return loop

    # -- Sync handler registration --

    def on(self, event_type: EventType, handler: EventHandler) -> None:
        """Register a handler for a specific event type."""
        with self._lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)

    def on_any(self, handler: EventHandler) -> None:
        """Register a handler that fires on every event."""
        with self._lock:
            self._any_handlers.append(handler)

    # -- Async handler registration --

    def on_async(self, event_type: EventType, handler: AsyncEventHandler) -> None:
        """Register an async handler for a specific event type."""
        with self._lock:
            if event_type not in self._async_handlers:
                self._async_handlers[event_type] = []
            self._async_handlers[event_type].append(handler)

    def on_any_async(self, handler: AsyncEventHandler) -> None:
        """Register an async handler that fires on every event."""
        with self._lock:
            self._async_any_handlers.append(handler)

    # -- Emit --

    def emit(self, event: Event) -> None:
        """Fire all matching handlers.

        Synchronous handlers run inline. Async handlers are scheduled
        on the background event loop (fire-and-forget).
        """
        with self._lock:
            specific = list(self._handlers.get(event.type, []))
            any_handlers = list(self._any_handlers)
            async_specific = list(self._async_handlers.get(event.type, []))
            async_any = list(self._async_any_handlers)

        # Separate any misplaced async handlers registered via on()/on_any()
        sync_specific: list[EventHandler] = []
        sync_any: list[EventHandler] = []
        for handler in specific:
            if inspect.iscoroutinefunction(handler):
                async_specific.append(handler)  # type: ignore[arg-type]
            else:
                sync_specific.append(handler)
        for handler in any_handlers:
            if inspect.iscoroutinefunction(handler):
                async_any.append(handler)  # type: ignore[arg-type]
            else:
                sync_any.append(handler)

        for handler in sync_specific:
            try:
                handler(event)
            except Exception:
                log.warning("Hook handler %s failed for %s", handler, event.type, exc_info=True)
        for handler in sync_any:
            try:
                handler(event)
            except Exception:
                log.warning("Hook handler %s failed for %s", handler, event.type, exc_info=True)

        # Schedule async handlers on background loop
        if async_specific or async_any:
            loop = self._ensure_async_loop()
            for handler in async_specific:
                loop.call_soon_threadsafe(self._schedule_async, loop, handler, event)
            for handler in async_any:
                loop.call_soon_threadsafe(self._schedule_async, loop, handler, event)

    @staticmethod
    def _schedule_async(
        loop: asyncio.AbstractEventLoop,
        handler: AsyncEventHandler,
        event: Event,
    ) -> None:
        """Create a task for an async handler with error handling."""

        async def _safe_run() -> None:
            try:
                await handler(event)
            except Exception:
                log.warning(
                    "Async hook handler %s failed for %s", handler, event.type, exc_info=True
                )

        loop.create_task(_safe_run())

    # -- Removal --

    def remove(self, event_type: EventType, handler: EventHandler) -> None:
        """Remove a specific handler."""
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def remove_any(self, handler: EventHandler) -> None:
        """Remove an on_any handler."""
        with self._lock:
            if handler in self._any_handlers:
                self._any_handlers.remove(handler)

    def remove_async(self, event_type: EventType, handler: AsyncEventHandler) -> None:
        """Remove an async handler for a specific event type."""
        with self._lock:
            handlers = self._async_handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def remove_any_async(self, handler: AsyncEventHandler) -> None:
        """Remove an async on_any handler."""
        with self._lock:
            if handler in self._async_any_handlers:
                self._async_any_handlers.remove(handler)

    def clear(self) -> None:
        """Remove all handlers (sync and async)."""
        with self._lock:
            self._handlers.clear()
            self._any_handlers.clear()
            self._async_handlers.clear()
            self._async_any_handlers.clear()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop the background async event loop gracefully.

        After shutdown, sync handlers continue to work normally.
        Async handlers will no longer be dispatched until a new emit
        triggers loop recreation.
        """
        loop = self._async_loop
        thread = self._async_thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=timeout)
        self._async_loop = None
        self._async_thread = None

    @property
    def handler_count(self) -> int:
        """Total number of registered handlers (sync + async)."""
        with self._lock:
            sync = sum(len(h) for h in self._handlers.values()) + len(self._any_handlers)
            async_ = sum(len(h) for h in self._async_handlers.values()) + len(
                self._async_any_handlers
            )
            return sync + async_


def make_progress_adapter(
    on_progress: Callable[[str], None],
) -> EventHandler:
    """Create an EventHandler that converts Events to on_progress strings.

    Backward-compat bridge: existing code passes on_progress callbacks;
    this wraps them as hook handlers.
    """

    def _adapter(event: Event) -> None:
        msg = _event_to_progress_string(event)
        if msg:
            on_progress(msg)

    return _adapter


def _event_to_progress_string(event: Event) -> str:
    """Convert an Event to a Rich-formatted progress string."""
    task_id = event.data.get("task_id", event.task_id)
    module = event.data.get("module", event.module)
    desc = event.data.get("description", "")

    match event.type:
        case EventType.TASK_STARTED:
            return f"\n  [bold]Task {task_id}:[/] [{module}] {desc}"
        case EventType.TASK_COMPLETED:
            return f"    [bold green]Task {task_id} COMPLETED[/]"
        case EventType.TASK_FAILED:
            reason = event.data.get("reason", "")
            return f"    [bold red]Task {task_id} FAILED[/] {reason}"
        case EventType.TASK_SKIPPED:
            return f"    [dim]Task {task_id} SKIPPED[/] (dependency failed)"
        case EventType.TASK_RETRYING:
            retry = event.data.get("retry", 0)
            max_retries = event.data.get("max_retries", 0)
            return f"    [yellow]QA failed, retrying with feedback[/] ({retry}/{max_retries})..."
        case EventType.QA_PASSED:
            gate = event.data.get("gate", "")
            output = event.data.get("output", "")[:100]
            return f"      [green]PASS[/] ({gate}): {output}"
        case EventType.QA_FAILED:
            gate = event.data.get("gate", "")
            output = event.data.get("output", "")[:200]
            return f"      [red]FAIL[/] ({gate}): {output}"
        case EventType.STALL_WARNING:
            secs = event.data.get("stall_seconds", 0)
            return f"    [yellow]STALL WARNING[/]: no events for {secs}s"
        case EventType.STALL_KILLED:
            secs = event.data.get("stall_seconds", 0)
            return f"    [red]STALL KILLED[/]: no events for {secs}s"
        case EventType.CHECKPOINT_SAVED:
            count = event.data.get("checkpoint_count", 0)
            return f"    [dim]Checkpoint #{count} saved[/]"
        case _:
            return ""
