"""Tests for async handler exception isolation in the hook system.

Verifies that:
- A failing handler does not prevent subsequent handlers from firing
- Exception isolation works for both specific and on_any handlers
- Exceptions are logged correctly
- Concurrent emits with failing handlers remain safe
"""

from __future__ import annotations

import logging
import threading

from lindy_orchestrator.hooks import Event, EventType, HookRegistry


class TestHandlerExceptionIsolation:
    """Exception in one handler must not prevent others from executing."""

    def test_specific_handler_exception_does_not_block_next(self):
        reg = HookRegistry()
        calls: list[str] = []

        def handler_a(e: Event) -> None:
            calls.append("a")
            raise ValueError("boom")

        def handler_b(e: Event) -> None:
            calls.append("b")

        reg.on(EventType.TASK_STARTED, handler_a)
        reg.on(EventType.TASK_STARTED, handler_b)

        reg.emit(Event(type=EventType.TASK_STARTED))

        assert calls == ["a", "b"]

    def test_on_any_handler_exception_does_not_block_next(self):
        reg = HookRegistry()
        calls: list[str] = []

        def handler_a(e: Event) -> None:
            calls.append("a")
            raise RuntimeError("fail")

        def handler_b(e: Event) -> None:
            calls.append("b")

        reg.on_any(handler_a)
        reg.on_any(handler_b)

        reg.emit(Event(type=EventType.TASK_COMPLETED))

        assert calls == ["a", "b"]

    def test_specific_exception_does_not_block_on_any(self):
        """A failing specific handler must not prevent on_any handlers."""
        reg = HookRegistry()
        calls: list[str] = []

        def specific(e: Event) -> None:
            calls.append("specific")
            raise TypeError("bad type")

        def any_handler(e: Event) -> None:
            calls.append("any")

        reg.on(EventType.TASK_FAILED, specific)
        reg.on_any(any_handler)

        reg.emit(Event(type=EventType.TASK_FAILED))

        assert calls == ["specific", "any"]

    def test_on_any_exception_does_not_block_other_on_any(self):
        reg = HookRegistry()
        calls: list[str] = []

        reg.on_any(lambda e: (_ for _ in ()).throw(ValueError("x")))  # noqa: B023
        # Use a simpler approach:
        calls.clear()

        def fail_handler(e: Event) -> None:
            calls.append("fail")
            raise KeyError("missing")

        def ok_handler(e: Event) -> None:
            calls.append("ok")

        reg.clear()
        reg.on_any(fail_handler)
        reg.on_any(ok_handler)

        reg.emit(Event(type=EventType.SESSION_START))

        assert calls == ["fail", "ok"]

    def test_all_handlers_fire_despite_multiple_exceptions(self):
        """Even if every handler raises, all should still be attempted."""
        reg = HookRegistry()
        calls: list[str] = []

        for name in ("a", "b", "c"):

            def make_handler(n: str):
                def handler(e: Event) -> None:
                    calls.append(n)
                    raise RuntimeError(f"{n} failed")

                return handler

            reg.on(EventType.QA_FAILED, make_handler(name))

        reg.emit(Event(type=EventType.QA_FAILED))

        assert calls == ["a", "b", "c"]


class TestHandlerExceptionLogging:
    """Verify that handler exceptions produce log warnings."""

    def test_exception_logs_warning(self, caplog):
        reg = HookRegistry()

        def bad_handler(e: Event) -> None:
            raise ValueError("handler error")

        reg.on(EventType.TASK_STARTED, bad_handler)

        with caplog.at_level(logging.WARNING, logger="lindy_orchestrator.hooks"):
            reg.emit(Event(type=EventType.TASK_STARTED))

        assert any("Hook handler" in r.message and "failed" in r.message for r in caplog.records)

    def test_on_any_exception_logs_warning(self, caplog):
        reg = HookRegistry()

        def bad_handler(e: Event) -> None:
            raise RuntimeError("any handler error")

        reg.on_any(bad_handler)

        with caplog.at_level(logging.WARNING, logger="lindy_orchestrator.hooks"):
            reg.emit(Event(type=EventType.TASK_COMPLETED))

        assert any("Hook handler" in r.message for r in caplog.records)


class TestConcurrentExceptionIsolation:
    """Thread safety when handlers raise during concurrent emits."""

    def test_concurrent_emits_with_failing_handler(self):
        reg = HookRegistry()
        ok_count = {"n": 0}
        lock = threading.Lock()

        def failing(e: Event) -> None:
            raise ValueError("concurrent fail")

        def counting(e: Event) -> None:
            with lock:
                ok_count["n"] += 1

        reg.on(EventType.TASK_STARTED, failing)
        reg.on(EventType.TASK_STARTED, counting)

        threads = []
        for _ in range(20):
            t = threading.Thread(target=lambda: reg.emit(Event(type=EventType.TASK_STARTED)))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert ok_count["n"] == 20

    def test_concurrent_registration_and_emit_with_exceptions(self):
        """Registering handlers while emitting events with failures should be safe."""
        reg = HookRegistry()
        results: list[str] = []
        lock = threading.Lock()

        def bad(e: Event) -> None:
            raise RuntimeError("bad")

        def good(e: Event) -> None:
            with lock:
                results.append("ok")

        reg.on(EventType.TASK_COMPLETED, bad)
        reg.on(EventType.TASK_COMPLETED, good)

        def register_more():
            for _ in range(5):
                reg.on(EventType.TASK_COMPLETED, good)

        def emit_events():
            for _ in range(5):
                reg.emit(Event(type=EventType.TASK_COMPLETED))

        t1 = threading.Thread(target=register_more)
        t2 = threading.Thread(target=emit_events)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # At minimum, the original 'good' handler should have been called 5 times
        assert len(results) >= 5
