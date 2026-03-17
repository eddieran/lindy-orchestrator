"""Thread-safe metrics collector for orchestrator sessions.

Subscribes to HookRegistry events and aggregates runtime metrics
(task counts, durations, costs, QA results) into frozen snapshots
that can be safely read from any thread.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .hooks import Event, EventType, HookRegistry


@dataclass(frozen=True)
class TaskMetrics:
    """Metrics for a single task."""

    task_id: int
    module: str
    status: str = "in_progress"  # in_progress | completed | failed | skipped
    duration_seconds: float = 0.0
    cost_usd: float = 0.0
    qa_passed: int = 0
    qa_failed: int = 0
    retry_count: int = 0


@dataclass(frozen=True)
class ModuleMetrics:
    """Aggregated metrics for a single module."""

    name: str
    total_cost_usd: float = 0.0
    task_count: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    qa_passed: int = 0
    qa_failed: int = 0
    total_duration_seconds: float = 0.0


@dataclass(frozen=True)
class SessionMetricsSnapshot:
    """Point-in-time snapshot of all session metrics."""

    total_tasks: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    in_progress: int = 0
    total_cost_usd: float = 0.0
    total_duration_seconds: float = 0.0
    total_dispatches: int = 0
    qa_passed: int = 0
    qa_failed: int = 0
    per_module: dict[str, ModuleMetrics] = field(default_factory=dict)
    per_task: dict[int, TaskMetrics] = field(default_factory=dict)


class _MutableTask:
    """Internal mutable task state (not exposed outside collector)."""

    __slots__ = (
        "task_id",
        "module",
        "status",
        "start_time",
        "duration_seconds",
        "cost_usd",
        "qa_passed",
        "qa_failed",
        "retry_count",
    )

    def __init__(self, task_id: int, module: str) -> None:
        self.task_id = task_id
        self.module = module
        self.status = "in_progress"
        self.start_time: float = time.monotonic()
        self.duration_seconds: float = 0.0
        self.cost_usd: float = 0.0
        self.qa_passed: int = 0
        self.qa_failed: int = 0
        self.retry_count: int = 0

    def freeze(self) -> TaskMetrics:
        return TaskMetrics(
            task_id=self.task_id,
            module=self.module,
            status=self.status,
            duration_seconds=self.duration_seconds,
            cost_usd=self.cost_usd,
            qa_passed=self.qa_passed,
            qa_failed=self.qa_failed,
            retry_count=self.retry_count,
        )


class MetricsCollector:
    """Collects runtime metrics from HookRegistry events.

    Thread-safe: uses an internal lock for all mutations.
    ``snapshot()`` returns a frozen copy safe to pass across threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[int, _MutableTask] = {}
        self._total_dispatches: int = 0
        self._hooks: HookRegistry | None = None

    def attach(self, hooks: HookRegistry) -> None:
        """Subscribe to all events via on_any."""
        self._hooks = hooks
        hooks.on_any(self._on_event)

    def detach(self) -> None:
        """Unsubscribe from hooks."""
        if self._hooks is not None:
            self._hooks.remove_any(self._on_event)
            self._hooks = None

    def _on_event(self, event: Event) -> None:
        """Central event dispatcher — routes by event type."""
        handler = self._dispatch_table.get(event.type)
        if handler is not None:
            handler(self, event)

    def _on_task_started(self, event: Event) -> None:
        task_id = event.task_id
        if task_id is None:
            return
        with self._lock:
            self._tasks[task_id] = _MutableTask(task_id, event.module)
            self._total_dispatches += 1

    def _on_task_completed(self, event: Event) -> None:
        task_id = event.task_id
        if task_id is None:
            return
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t.status = "completed"
                t.duration_seconds = time.monotonic() - t.start_time

    def _on_task_failed(self, event: Event) -> None:
        task_id = event.task_id
        if task_id is None:
            return
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t.status = "failed"
                t.duration_seconds = time.monotonic() - t.start_time

    def _on_task_skipped(self, event: Event) -> None:
        task_id = event.task_id
        if task_id is None:
            return
        with self._lock:
            # Skipped tasks may not have been started
            if task_id not in self._tasks:
                self._tasks[task_id] = _MutableTask(task_id, event.module)
            self._tasks[task_id].status = "skipped"

    def _on_task_retrying(self, event: Event) -> None:
        task_id = event.task_id
        if task_id is None:
            return
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t.retry_count = event.data.get("retry", t.retry_count + 1)
                self._total_dispatches += 1

    def _on_qa_passed(self, event: Event) -> None:
        task_id = event.task_id
        if task_id is None:
            return
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t.qa_passed += 1

    def _on_qa_failed(self, event: Event) -> None:
        task_id = event.task_id
        if task_id is None:
            return
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t.qa_failed += 1

    def _on_session_end(self, event: Event) -> None:
        # Update total_dispatches from authoritative event data if available
        dispatches = event.data.get("total_dispatches")
        if dispatches is not None:
            with self._lock:
                self._total_dispatches = dispatches

    _dispatch_table: dict[EventType, Any] = {
        EventType.TASK_STARTED: _on_task_started,
        EventType.TASK_COMPLETED: _on_task_completed,
        EventType.TASK_FAILED: _on_task_failed,
        EventType.TASK_SKIPPED: _on_task_skipped,
        EventType.TASK_RETRYING: _on_task_retrying,
        EventType.QA_PASSED: _on_qa_passed,
        EventType.QA_FAILED: _on_qa_failed,
        EventType.SESSION_END: _on_session_end,
    }

    def snapshot(self) -> SessionMetricsSnapshot:
        """Return a frozen point-in-time copy of all metrics."""
        with self._lock:
            per_task: dict[int, TaskMetrics] = {}
            module_agg: dict[str, dict[str, float | int]] = {}

            for tid, t in self._tasks.items():
                per_task[tid] = t.freeze()

                m = module_agg.setdefault(
                    t.module,
                    {
                        "total_cost_usd": 0.0,
                        "task_count": 0,
                        "completed": 0,
                        "failed": 0,
                        "skipped": 0,
                        "qa_passed": 0,
                        "qa_failed": 0,
                        "total_duration_seconds": 0.0,
                    },
                )
                m["task_count"] += 1  # type: ignore[operator]
                m["total_cost_usd"] += t.cost_usd  # type: ignore[operator]
                m["qa_passed"] += t.qa_passed  # type: ignore[operator]
                m["qa_failed"] += t.qa_failed  # type: ignore[operator]
                m["total_duration_seconds"] += t.duration_seconds  # type: ignore[operator]
                if t.status == "completed":
                    m["completed"] += 1  # type: ignore[operator]
                elif t.status == "failed":
                    m["failed"] += 1  # type: ignore[operator]
                elif t.status == "skipped":
                    m["skipped"] += 1  # type: ignore[operator]

            per_module = {
                name: ModuleMetrics(name=name, **vals)  # type: ignore[arg-type]
                for name, vals in module_agg.items()
            }

            total_tasks = len(self._tasks)
            completed = sum(1 for t in self._tasks.values() if t.status == "completed")
            failed = sum(1 for t in self._tasks.values() if t.status == "failed")
            skipped = sum(1 for t in self._tasks.values() if t.status == "skipped")
            in_progress = sum(1 for t in self._tasks.values() if t.status == "in_progress")
            total_cost = sum(t.cost_usd for t in self._tasks.values())
            total_dur = sum(t.duration_seconds for t in self._tasks.values())

            return SessionMetricsSnapshot(
                total_tasks=total_tasks,
                completed=completed,
                failed=failed,
                skipped=skipped,
                in_progress=in_progress,
                total_cost_usd=total_cost,
                total_duration_seconds=total_dur,
                total_dispatches=self._total_dispatches,
                qa_passed=sum(t.qa_passed for t in self._tasks.values()),
                qa_failed=sum(t.qa_failed for t in self._tasks.values()),
                per_module=per_module,
                per_task=per_task,
            )
