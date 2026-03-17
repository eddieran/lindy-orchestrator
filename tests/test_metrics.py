"""Tests for MetricsCollector."""

from __future__ import annotations

import threading
import time

from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.metrics import MetricsCollector


class TestAttachDetach:
    def test_attach_registers_handler(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)
        assert hooks.handler_count == 1  # one on_any handler

    def test_detach_removes_handler(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)
        mc.detach()
        assert hooks.handler_count == 0

    def test_detach_without_attach_is_safe(self):
        mc = MetricsCollector()
        mc.detach()  # should not raise

    def test_double_detach_is_safe(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)
        mc.detach()
        mc.detach()  # should not raise


class TestTaskLifecycle:
    def test_task_started_tracked(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        snap = mc.snapshot()

        assert snap.total_tasks == 1
        assert snap.in_progress == 1
        assert 1 in snap.per_task
        assert snap.per_task[1].module == "backend"
        assert snap.per_task[1].status == "in_progress"

    def test_task_completed_with_duration(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        time.sleep(0.05)  # small delay to get measurable duration
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="backend"))

        snap = mc.snapshot()
        assert snap.completed == 1
        assert snap.in_progress == 0
        assert snap.per_task[1].status == "completed"
        assert snap.per_task[1].duration_seconds > 0

    def test_task_failed_tracked(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        hooks.emit(
            Event(
                type=EventType.TASK_FAILED,
                task_id=1,
                module="backend",
                data={"reason": "timeout"},
            )
        )

        snap = mc.snapshot()
        assert snap.failed == 1
        assert snap.per_task[1].status == "failed"

    def test_task_skipped_tracked(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_SKIPPED, task_id=2, module="frontend"))

        snap = mc.snapshot()
        assert snap.skipped == 1
        assert snap.per_task[2].status == "skipped"

    def test_task_skipped_without_start(self):
        """Skipped tasks may never have been started."""
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_SKIPPED, task_id=3, module="docs"))
        snap = mc.snapshot()
        assert 3 in snap.per_task
        assert snap.per_task[3].status == "skipped"


class TestQATracking:
    def test_qa_passed_counted(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        hooks.emit(
            Event(
                type=EventType.QA_PASSED,
                task_id=1,
                module="backend",
                data={"gate": "structural"},
            )
        )

        snap = mc.snapshot()
        assert snap.qa_passed == 1
        assert snap.per_task[1].qa_passed == 1

    def test_qa_failed_counted(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        hooks.emit(
            Event(
                type=EventType.QA_FAILED,
                task_id=1,
                module="backend",
                data={"gate": "layer_check"},
            )
        )

        snap = mc.snapshot()
        assert snap.qa_failed == 1
        assert snap.per_task[1].qa_failed == 1

    def test_multiple_qa_events(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        hooks.emit(Event(type=EventType.QA_PASSED, task_id=1, module="backend"))
        hooks.emit(Event(type=EventType.QA_PASSED, task_id=1, module="backend"))
        hooks.emit(Event(type=EventType.QA_FAILED, task_id=1, module="backend"))

        snap = mc.snapshot()
        assert snap.qa_passed == 2
        assert snap.qa_failed == 1


class TestRetryTracking:
    def test_retry_increments_dispatch_count(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        hooks.emit(
            Event(
                type=EventType.TASK_RETRYING,
                task_id=1,
                module="backend",
                data={"retry": 1, "max_retries": 3},
            )
        )

        snap = mc.snapshot()
        assert snap.per_task[1].retry_count == 1
        assert snap.total_dispatches == 2  # initial + retry


class TestPerModuleAggregation:
    def test_per_module_metrics(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="backend"))
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=3, module="frontend"))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="backend"))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=3, module="frontend"))
        hooks.emit(Event(type=EventType.TASK_FAILED, task_id=2, module="backend"))

        snap = mc.snapshot()

        assert "backend" in snap.per_module
        assert "frontend" in snap.per_module

        be = snap.per_module["backend"]
        assert be.task_count == 2
        assert be.completed == 1
        assert be.failed == 1

        fe = snap.per_module["frontend"]
        assert fe.task_count == 1
        assert fe.completed == 1

    def test_qa_aggregated_per_module(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        hooks.emit(Event(type=EventType.QA_PASSED, task_id=1, module="backend"))
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="frontend"))
        hooks.emit(Event(type=EventType.QA_FAILED, task_id=2, module="frontend"))

        snap = mc.snapshot()
        assert snap.per_module["backend"].qa_passed == 1
        assert snap.per_module["frontend"].qa_failed == 1


class TestSnapshotIndependence:
    def test_snapshot_is_frozen(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        snap1 = mc.snapshot()

        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="backend"))
        snap2 = mc.snapshot()

        # snap1 should not be affected by later events
        assert snap1.completed == 0
        assert snap1.in_progress == 1
        assert snap2.completed == 1
        assert snap2.in_progress == 0

    def test_empty_snapshot(self):
        mc = MetricsCollector()
        snap = mc.snapshot()
        assert snap.total_tasks == 0
        assert snap.completed == 0
        assert snap.per_module == {}
        assert snap.per_task == {}


class TestSessionEnd:
    def test_session_end_updates_dispatch_count(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
        hooks.emit(
            Event(
                type=EventType.SESSION_END,
                data={"total_dispatches": 42, "has_failures": False},
            )
        )

        snap = mc.snapshot()
        assert snap.total_dispatches == 42


class TestNoneTaskId:
    def test_events_without_task_id_are_ignored(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        # Events with task_id=None should be silently ignored
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=None, module="backend"))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=None, module="backend"))
        hooks.emit(Event(type=EventType.QA_PASSED, task_id=None, module="backend"))

        snap = mc.snapshot()
        assert snap.total_tasks == 0


class TestThreadSafety:
    def test_concurrent_events(self):
        """Multiple threads emitting events concurrently should not corrupt state."""
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        n_tasks = 50
        barrier = threading.Barrier(n_tasks)

        def emit_lifecycle(task_id: int) -> None:
            barrier.wait()
            hooks.emit(Event(type=EventType.TASK_STARTED, task_id=task_id, module="backend"))
            hooks.emit(Event(type=EventType.QA_PASSED, task_id=task_id, module="backend"))
            hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=task_id, module="backend"))

        threads = [threading.Thread(target=emit_lifecycle, args=(i,)) for i in range(n_tasks)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = mc.snapshot()
        assert snap.total_tasks == n_tasks
        assert snap.completed == n_tasks
        assert snap.qa_passed == n_tasks
