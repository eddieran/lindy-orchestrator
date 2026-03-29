"""Tests for the event hook system."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from lindy_orchestrator.hooks import (
    Event,
    EventType,
    HookRegistry,
    make_progress_adapter,
)


class TestHookRegistry:
    def test_observability_event_types_exist(self):
        assert EventType.AGENT_EVENT.value == "agent_event"
        assert EventType.AGENT_OUTPUT.value == "agent_output"
        assert EventType.GIT_DIFF_CAPTURED.value == "git_diff_captured"
        assert EventType.SESSION_RESUMED.value == "session_resumed"

    def test_on_and_emit(self):
        reg = HookRegistry()
        received = []
        reg.on(EventType.TASK_STARTED, lambda e: received.append(e))

        event = Event(type=EventType.TASK_STARTED, task_id=1, module="backend")
        reg.emit(event)

        assert len(received) == 1
        assert received[0].task_id == 1

    def test_handler_only_fires_for_matching_type(self):
        reg = HookRegistry()
        received = []
        reg.on(EventType.TASK_STARTED, lambda e: received.append(e))

        reg.emit(Event(type=EventType.TASK_COMPLETED, task_id=1))
        assert len(received) == 0

    def test_on_any_fires_for_all_events(self):
        reg = HookRegistry()
        received = []
        reg.on_any(lambda e: received.append(e.type))

        reg.emit(Event(type=EventType.TASK_STARTED))
        reg.emit(Event(type=EventType.TASK_COMPLETED))
        reg.emit(Event(type=EventType.QA_PASSED))

        assert received == [
            EventType.TASK_STARTED,
            EventType.TASK_COMPLETED,
            EventType.QA_PASSED,
        ]

    def test_multiple_handlers_same_event(self):
        reg = HookRegistry()
        calls = []
        reg.on(EventType.TASK_FAILED, lambda e: calls.append("a"))
        reg.on(EventType.TASK_FAILED, lambda e: calls.append("b"))

        reg.emit(Event(type=EventType.TASK_FAILED))
        assert calls == ["a", "b"]

    def test_remove_handler(self):
        reg = HookRegistry()
        received = []

        def handler(e):
            received.append(e)

        reg.on(EventType.TASK_STARTED, handler)
        reg.emit(Event(type=EventType.TASK_STARTED))
        assert len(received) == 1

        reg.remove(EventType.TASK_STARTED, handler)
        reg.emit(Event(type=EventType.TASK_STARTED))
        assert len(received) == 1  # no new events

    def test_remove_any_handler(self):
        reg = HookRegistry()
        received = []

        def handler(e):
            received.append(e)

        reg.on_any(handler)
        reg.emit(Event(type=EventType.TASK_STARTED))
        assert len(received) == 1

        reg.remove_any(handler)
        reg.emit(Event(type=EventType.TASK_STARTED))
        assert len(received) == 1

    def test_clear(self):
        reg = HookRegistry()
        reg.on(EventType.TASK_STARTED, lambda e: None)
        reg.on_any(lambda e: None)
        assert reg.handler_count == 2

        reg.clear()
        assert reg.handler_count == 0

    def test_handler_count(self):
        reg = HookRegistry()
        assert reg.handler_count == 0

        reg.on(EventType.TASK_STARTED, lambda e: None)
        reg.on(EventType.TASK_COMPLETED, lambda e: None)
        reg.on_any(lambda e: None)
        assert reg.handler_count == 3

    def test_thread_safety(self):
        """Concurrent emits and registrations should not raise."""
        reg = HookRegistry()
        counter = {"count": 0}
        lock = threading.Lock()

        def handler(e):
            with lock:
                counter["count"] += 1

        reg.on(EventType.TASK_STARTED, handler)

        threads = []
        for _ in range(20):
            t = threading.Thread(target=lambda: reg.emit(Event(type=EventType.TASK_STARTED)))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert counter["count"] == 20

    def test_remove_nonexistent_handler_no_error(self):
        reg = HookRegistry()
        reg.remove(EventType.TASK_STARTED, lambda e: None)  # should not raise

    def test_remove_any_nonexistent_handler_no_error(self):
        reg = HookRegistry()
        reg.remove_any(lambda e: None)  # should not raise

    def test_event_default_timestamp(self):
        event = Event(type=EventType.TASK_STARTED)
        assert event.timestamp  # should be auto-populated
        assert "T" in event.timestamp  # ISO format

    def test_event_data_defaults(self):
        event = Event(type=EventType.TASK_STARTED)
        assert event.data == {}
        assert event.task_id is None
        assert event.module == ""


class TestProgressAdapter:
    def test_task_started(self):
        mock = MagicMock()
        adapter = make_progress_adapter(mock)

        adapter(
            Event(
                type=EventType.TASK_STARTED,
                task_id=1,
                module="backend",
                data={"description": "Add API"},
            )
        )
        mock.assert_called_once()
        msg = mock.call_args[0][0]
        assert "Task 1" in msg
        assert "backend" in msg
        assert "Add API" in msg

    def test_task_completed(self):
        mock = MagicMock()
        adapter = make_progress_adapter(mock)

        adapter(Event(type=EventType.TASK_COMPLETED, task_id=2))
        msg = mock.call_args[0][0]
        assert "COMPLETED" in msg
        assert "Task 2" in msg

    def test_task_failed(self):
        mock = MagicMock()
        adapter = make_progress_adapter(mock)

        adapter(
            Event(
                type=EventType.TASK_FAILED,
                task_id=3,
                data={"reason": "timeout"},
            )
        )
        msg = mock.call_args[0][0]
        assert "FAILED" in msg
        assert "timeout" in msg

    def test_task_retrying(self):
        mock = MagicMock()
        adapter = make_progress_adapter(mock)

        adapter(
            Event(
                type=EventType.TASK_RETRYING,
                data={"retry": 1, "max_retries": 3},
            )
        )
        msg = mock.call_args[0][0]
        assert "1/3" in msg

    def test_qa_passed(self):
        mock = MagicMock()
        adapter = make_progress_adapter(mock)

        adapter(
            Event(
                type=EventType.QA_PASSED,
                data={"gate": "structural_check", "output": "All clear"},
            )
        )
        msg = mock.call_args[0][0]
        assert "PASS" in msg
        assert "structural_check" in msg

    def test_qa_failed(self):
        mock = MagicMock()
        adapter = make_progress_adapter(mock)

        adapter(
            Event(
                type=EventType.QA_FAILED,
                data={"gate": "layer_check", "output": "Violation found"},
            )
        )
        msg = mock.call_args[0][0]
        assert "FAIL" in msg
        assert "layer_check" in msg

    def test_unknown_event_produces_empty(self):
        mock = MagicMock()
        adapter = make_progress_adapter(mock)

        adapter(Event(type=EventType.SESSION_START))
        mock.assert_not_called()  # empty string → not called

    def test_stall_warning(self):
        mock = MagicMock()
        adapter = make_progress_adapter(mock)

        adapter(
            Event(
                type=EventType.STALL_WARNING,
                data={"stall_seconds": 300},
            )
        )
        msg = mock.call_args[0][0]
        assert "STALL WARNING" in msg
        assert "300" in msg

    def test_checkpoint_saved(self):
        mock = MagicMock()
        adapter = make_progress_adapter(mock)

        adapter(
            Event(
                type=EventType.CHECKPOINT_SAVED,
                data={"checkpoint_count": 5},
            )
        )
        msg = mock.call_args[0][0]
        assert "Checkpoint #5" in msg
