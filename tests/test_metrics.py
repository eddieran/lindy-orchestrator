"""Tests for the runtime metrics collection system."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.metrics import MetricsCollector, SessionMetricsSnapshot


def _ts(offset_seconds: float = 0.0) -> str:
    """Generate an ISO timestamp with an optional offset from now."""
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.isoformat()


class TestAttachDetach:
    def test_attach_registers_handler(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        assert hooks.handler_count == 0
        mc.attach(hooks)
        assert hooks.handler_count == 1

    def test_detach_removes_handler(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)
        assert hooks.handler_count == 1
        mc.detach(hooks)
        assert hooks.handler_count == 0

    def test_detach_without_attach_no_error(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.detach(hooks)  # should not raise

    def test_events_not_tracked_after_detach(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="a"))
        mc.detach(hooks)
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="a"))

        snap = mc.snapshot()
        # Task was started but completion was after detach — status remains in_progress
        assert snap.per_task[1].status == "in_progress"


class TestTaskLifecycle:
    def test_started_then_completed_with_duration(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        t0 = _ts(0)
        t1 = _ts(5)  # 5 seconds later

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend", timestamp=t0))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="backend", timestamp=t1))

        snap = mc.snapshot()
        tm = snap.per_task[1]
        assert tm.status == "completed"
        assert tm.started_at == t0
        assert tm.completed_at == t1
        assert tm.duration_seconds is not None
        assert abs(tm.duration_seconds - 5.0) < 0.1
        assert tm.module == "backend"
        assert tm.task_id == 1

    def test_started_then_failed(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        t0 = _ts(0)
        t1 = _ts(3)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="frontend", timestamp=t0))
        hooks.emit(Event(type=EventType.TASK_FAILED, task_id=2, module="frontend", timestamp=t1))

        snap = mc.snapshot()
        tm = snap.per_task[2]
        assert tm.status == "failed"
        assert tm.duration_seconds is not None
        assert abs(tm.duration_seconds - 3.0) < 0.1

    def test_skipped_task(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_SKIPPED, task_id=3, module="api"))

        snap = mc.snapshot()
        assert snap.per_task[3].status == "skipped"
        assert snap.skipped == 1

    def test_retries_tracked(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        hooks.emit(Event(type=EventType.TASK_RETRYING, task_id=1, module="mod"))
        hooks.emit(Event(type=EventType.TASK_RETRYING, task_id=1, module="mod"))

        snap = mc.snapshot()
        assert snap.per_task[1].retry_count == 2

    def test_description_tracked(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(
            Event(
                type=EventType.TASK_STARTED,
                task_id=1,
                module="backend",
                data={"description": "Setup API"},
            )
        )

        snap = mc.snapshot()
        assert snap.per_task[1].description == "Setup API"


class TestCostAccumulation:
    def test_cost_defaults_to_zero(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="m"))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="m"))

        snap = mc.snapshot()
        assert snap.per_task[1].cost_usd == 0.0
        assert snap.total_cost == 0.0

    def test_cost_from_event_data(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="m"))
        hooks.emit(
            Event(
                type=EventType.TASK_COMPLETED,
                task_id=1,
                module="m",
                data={"cost_usd": 0.25},
            )
        )
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="m"))
        hooks.emit(
            Event(
                type=EventType.TASK_FAILED,
                task_id=2,
                module="m",
                data={"cost_usd": 0.10},
            )
        )

        snap = mc.snapshot()
        assert snap.per_task[1].cost_usd == 0.25
        assert snap.per_task[2].cost_usd == 0.10
        assert abs(snap.total_cost - 0.35) < 0.001
        assert abs(snap.per_module["m"].total_cost - 0.35) < 0.001


class TestQATracking:
    def test_qa_passed_and_failed_counts(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        hooks.emit(Event(type=EventType.QA_PASSED, task_id=1, module="mod"))
        hooks.emit(Event(type=EventType.QA_PASSED, task_id=1, module="mod"))
        hooks.emit(Event(type=EventType.QA_FAILED, task_id=1, module="mod"))

        snap = mc.snapshot()
        assert snap.per_task[1].qa_pass_count == 2
        assert snap.per_task[1].qa_fail_count == 1

    def test_session_level_qa_counts(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))
        hooks.emit(Event(type=EventType.QA_PASSED, task_id=1, module="mod"))
        hooks.emit(Event(type=EventType.QA_PASSED, task_id=1, module="mod"))
        hooks.emit(Event(type=EventType.QA_FAILED, task_id=1, module="mod"))

        snap = mc.snapshot()
        assert snap.qa_pass_count == 2
        assert snap.qa_fail_count == 1

    def test_no_qa_events_gives_zero_counts(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod"))

        snap = mc.snapshot()
        assert snap.qa_pass_count == 0
        assert snap.qa_fail_count == 0


class TestPerModuleAggregation:
    def test_multi_module_breakdown(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        t0 = _ts(0)
        t1 = _ts(10)

        # Module A: 2 tasks (1 completed, 1 failed)
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="a", timestamp=t0))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="a", timestamp=t1))
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="a", timestamp=t0))
        hooks.emit(Event(type=EventType.TASK_FAILED, task_id=2, module="a", timestamp=t1))

        # Module B: 1 task (completed)
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=3, module="b", timestamp=t0))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=3, module="b", timestamp=t1))

        snap = mc.snapshot()
        assert snap.total_tasks == 3
        assert snap.completed == 2
        assert snap.failed == 1

        assert "a" in snap.per_module
        assert snap.per_module["a"].task_count == 2
        assert snap.per_module["a"].completed == 1
        assert snap.per_module["a"].failed == 1

        assert "b" in snap.per_module
        assert snap.per_module["b"].task_count == 1
        assert snap.per_module["b"].completed == 1

    def test_avg_duration_per_module(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        t0 = _ts(0)
        t2 = _ts(2)
        t4 = _ts(4)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod", timestamp=t0))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="mod", timestamp=t2))
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="mod", timestamp=t0))
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=2, module="mod", timestamp=t4))

        snap = mc.snapshot()
        # durations: 2s and 4s → avg 3s
        assert snap.per_module["mod"].avg_duration is not None
        assert abs(snap.per_module["mod"].avg_duration - 3.0) < 0.1

    def test_per_module_qa_counts(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="a"))
        hooks.emit(Event(type=EventType.QA_PASSED, task_id=1, module="a"))
        hooks.emit(Event(type=EventType.QA_FAILED, task_id=1, module="a"))
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="b"))
        hooks.emit(Event(type=EventType.QA_PASSED, task_id=2, module="b"))

        snap = mc.snapshot()
        assert snap.per_module["a"].qa_pass_count == 1
        assert snap.per_module["a"].qa_fail_count == 1
        assert snap.per_module["b"].qa_pass_count == 1
        assert snap.per_module["b"].qa_fail_count == 0


class TestSnapshotIndependence:
    def test_snapshot_is_independent_copy(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="m"))

        snap1 = mc.snapshot()
        assert snap1.total_tasks == 1

        # More events after snapshot
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=2, module="m"))
        snap2 = mc.snapshot()

        # snap1 should be unchanged
        assert snap1.total_tasks == 1
        assert snap2.total_tasks == 2

    def test_mutating_snapshot_does_not_affect_collector(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="m"))

        snap = mc.snapshot()
        # Mutate the snapshot
        snap.per_task[1].status = "hacked"
        snap.total_tasks = 999

        # Collector should be unaffected
        snap2 = mc.snapshot()
        assert snap2.per_task[1].status == "in_progress"
        assert snap2.total_tasks == 1


class TestSessionTiming:
    def test_elapsed_seconds_tracked(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.SESSION_START, data={"goal": "test"}))
        time.sleep(0.05)
        hooks.emit(Event(type=EventType.SESSION_END, data={}))

        snap = mc.snapshot()
        assert snap.elapsed_seconds is not None
        assert snap.elapsed_seconds >= 0.04

    def test_elapsed_before_session_end(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.SESSION_START, data={"goal": "test"}))
        time.sleep(0.05)

        # No SESSION_END yet — elapsed should use current time
        snap = mc.snapshot()
        assert snap.elapsed_seconds is not None
        assert snap.elapsed_seconds >= 0.04

    def test_no_session_start_gives_none_elapsed(self):
        mc = MetricsCollector()
        snap = mc.snapshot()
        assert snap.elapsed_seconds is None

    def test_started_at_from_session_start(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        ts = _ts(0)
        hooks.emit(Event(type=EventType.SESSION_START, timestamp=ts, data={"goal": "test"}))

        snap = mc.snapshot()
        assert snap.started_at == ts

    def test_no_session_start_gives_none_started_at(self):
        mc = MetricsCollector()
        snap = mc.snapshot()
        assert snap.started_at is None


class TestConcurrencySafety:
    def test_concurrent_events_no_data_corruption(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.SESSION_START))

        errors: list[str] = []
        barrier = threading.Barrier(10)

        def emit_task_events(task_id: int) -> None:
            try:
                barrier.wait(timeout=5)
                t0 = _ts(0)
                t1 = _ts(1)
                hooks.emit(
                    Event(
                        type=EventType.TASK_STARTED,
                        task_id=task_id,
                        module=f"mod-{task_id}",
                        timestamp=t0,
                    )
                )
                hooks.emit(
                    Event(type=EventType.QA_PASSED, task_id=task_id, module=f"mod-{task_id}")
                )
                hooks.emit(
                    Event(
                        type=EventType.TASK_COMPLETED,
                        task_id=task_id,
                        module=f"mod-{task_id}",
                        timestamp=t1,
                    )
                )
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=emit_task_events, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"

        snap = mc.snapshot()
        assert snap.total_tasks == 10
        assert snap.completed == 10
        assert all(tm.status == "completed" for tm in snap.per_task.values())
        assert all(tm.qa_pass_count == 1 for tm in snap.per_task.values())

    def test_concurrent_snapshots_safe(self):
        hooks = HookRegistry()
        mc = MetricsCollector()
        mc.attach(hooks)

        hooks.emit(Event(type=EventType.SESSION_START))
        for i in range(5):
            hooks.emit(Event(type=EventType.TASK_STARTED, task_id=i, module="m"))

        snapshots: list[SessionMetricsSnapshot] = []
        errors: list[str] = []

        def take_snapshot() -> None:
            try:
                snapshots.append(mc.snapshot())
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=take_snapshot) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        assert len(snapshots) == 20
        assert all(s.total_tasks == 5 for s in snapshots)
