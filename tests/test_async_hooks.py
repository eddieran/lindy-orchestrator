"""Tests for async handler support in HookRegistry."""

from __future__ import annotations

import threading
import time

from lindy_orchestrator.hooks import (
    Event,
    EventType,
    HookRegistry,
)


class TestAsyncHandlerRegistration:
    def test_on_async_increments_handler_count(self):
        reg = HookRegistry()

        async def handler(e: Event) -> None:
            pass

        reg.on_async(EventType.TASK_STARTED, handler)
        assert reg.handler_count == 1

    def test_on_any_async_increments_handler_count(self):
        reg = HookRegistry()

        async def handler(e: Event) -> None:
            pass

        reg.on_any_async(handler)
        assert reg.handler_count == 1

    def test_mixed_sync_async_handler_count(self):
        reg = HookRegistry()
        reg.on(EventType.TASK_STARTED, lambda e: None)
        reg.on_any(lambda e: None)

        async def ah(e: Event) -> None:
            pass

        reg.on_async(EventType.TASK_COMPLETED, ah)
        reg.on_any_async(ah)
        assert reg.handler_count == 4

    def test_remove_async_handler(self):
        reg = HookRegistry()

        async def handler(e: Event) -> None:
            pass

        reg.on_async(EventType.TASK_STARTED, handler)
        assert reg.handler_count == 1
        reg.remove_async(EventType.TASK_STARTED, handler)
        assert reg.handler_count == 0

    def test_remove_any_async_handler(self):
        reg = HookRegistry()

        async def handler(e: Event) -> None:
            pass

        reg.on_any_async(handler)
        assert reg.handler_count == 1
        reg.remove_any_async(handler)
        assert reg.handler_count == 0

    def test_remove_nonexistent_async_handler_no_error(self):
        reg = HookRegistry()

        async def handler(e: Event) -> None:
            pass

        reg.remove_async(EventType.TASK_STARTED, handler)
        reg.remove_any_async(handler)

    def test_clear_removes_async_handlers(self):
        reg = HookRegistry()

        async def handler(e: Event) -> None:
            pass

        reg.on_async(EventType.TASK_STARTED, handler)
        reg.on_any_async(handler)
        assert reg.handler_count == 2
        reg.clear()
        assert reg.handler_count == 0


class TestAsyncHandlerEmit:
    def test_async_handler_fires(self):
        reg = HookRegistry()
        received = []
        done = threading.Event()

        async def handler(e: Event) -> None:
            received.append(e.type)
            done.set()

        reg.on_async(EventType.TASK_STARTED, handler)
        reg.emit(Event(type=EventType.TASK_STARTED, task_id=1))

        assert done.wait(timeout=2), "async handler did not fire within timeout"
        assert received == [EventType.TASK_STARTED]
        reg.shutdown()

    def test_async_any_handler_fires(self):
        reg = HookRegistry()
        received = []
        done = threading.Event()

        async def handler(e: Event) -> None:
            received.append(e.type)
            if len(received) >= 2:
                done.set()

        reg.on_any_async(handler)
        reg.emit(Event(type=EventType.TASK_STARTED))
        reg.emit(Event(type=EventType.TASK_COMPLETED))

        assert done.wait(timeout=2), "async any handler did not fire for both events"
        assert EventType.TASK_STARTED in received
        assert EventType.TASK_COMPLETED in received
        reg.shutdown()

    def test_sync_and_async_coexist(self):
        reg = HookRegistry()
        sync_calls = []
        async_calls = []
        done = threading.Event()

        def sync_handler(e: Event) -> None:
            sync_calls.append(e.type)

        async def async_handler(e: Event) -> None:
            async_calls.append(e.type)
            done.set()

        reg.on(EventType.TASK_STARTED, sync_handler)
        reg.on_async(EventType.TASK_STARTED, async_handler)

        reg.emit(Event(type=EventType.TASK_STARTED))

        # Sync fires immediately
        assert sync_calls == [EventType.TASK_STARTED]

        # Async fires on background thread
        assert done.wait(timeout=2)
        assert async_calls == [EventType.TASK_STARTED]
        reg.shutdown()

    def test_async_handler_exception_does_not_block(self):
        reg = HookRegistry()
        received = []
        done = threading.Event()

        async def bad_handler(e: Event) -> None:
            raise RuntimeError("boom")

        async def good_handler(e: Event) -> None:
            received.append(e.type)
            done.set()

        reg.on_async(EventType.TASK_STARTED, bad_handler)
        reg.on_async(EventType.TASK_STARTED, good_handler)

        reg.emit(Event(type=EventType.TASK_STARTED))

        assert done.wait(timeout=2), "good handler should still fire"
        assert received == [EventType.TASK_STARTED]
        reg.shutdown()

    def test_async_handler_only_fires_for_matching_type(self):
        reg = HookRegistry()
        received = []

        async def handler(e: Event) -> None:
            received.append(e.type)

        reg.on_async(EventType.TASK_STARTED, handler)
        reg.emit(Event(type=EventType.TASK_COMPLETED))

        # Give a bit of time for potential spurious delivery
        time.sleep(0.1)
        assert received == []
        reg.shutdown()


class TestShutdown:
    def test_shutdown_stops_background_loop(self):
        reg = HookRegistry()
        done = threading.Event()

        async def handler(e: Event) -> None:
            done.set()

        reg.on_async(EventType.TASK_STARTED, handler)
        reg.emit(Event(type=EventType.TASK_STARTED))
        done.wait(timeout=2)

        # Background loop should be running
        assert reg._async_loop is not None
        assert reg._async_thread is not None

        reg.shutdown()
        assert reg._async_loop is None
        assert reg._async_thread is None

    def test_sync_handlers_work_after_shutdown(self):
        reg = HookRegistry()
        received = []

        def sync_handler(e: Event) -> None:
            received.append(e.type)

        async def async_handler(e: Event) -> None:
            pass

        reg.on(EventType.TASK_STARTED, sync_handler)
        reg.on_async(EventType.TASK_COMPLETED, async_handler)

        # Trigger async loop creation then shut it down
        reg.emit(Event(type=EventType.TASK_COMPLETED))
        reg.shutdown()

        # Sync handlers must still work
        reg.emit(Event(type=EventType.TASK_STARTED))
        assert received == [EventType.TASK_STARTED]

    def test_shutdown_idempotent(self):
        reg = HookRegistry()
        reg.shutdown()  # no-op when no loop
        reg.shutdown()  # still no-op

    def test_no_async_loop_created_for_sync_only(self):
        """If only sync handlers are used, no background loop is created."""
        reg = HookRegistry()
        reg.on(EventType.TASK_STARTED, lambda e: None)
        reg.emit(Event(type=EventType.TASK_STARTED))
        assert reg._async_loop is None
        assert reg._async_thread is None


class TestBackwardCompat:
    def test_pure_sync_usage_unchanged(self):
        """Existing sync-only code should work identically."""
        reg = HookRegistry()
        received = []

        reg.on(EventType.TASK_STARTED, lambda e: received.append("specific"))
        reg.on_any(lambda e: received.append("any"))

        reg.emit(Event(type=EventType.TASK_STARTED))
        assert received == ["specific", "any"]

    def test_handler_error_does_not_propagate(self):
        reg = HookRegistry()

        def bad(e: Event) -> None:
            raise ValueError("boom")

        received = []

        def good(e: Event) -> None:
            received.append(True)

        reg.on(EventType.TASK_STARTED, bad)
        reg.on(EventType.TASK_STARTED, good)
        reg.emit(Event(type=EventType.TASK_STARTED))
        assert received == [True]
