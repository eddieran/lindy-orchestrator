"""Runtime metrics collection for orchestrator sessions.

Subscribes to HookRegistry via on_any to aggregate task durations, costs,
QA pass rates, and per-module breakdowns. Thread-safe via threading.Lock.
"""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from .hooks import Event, EventType, HookRegistry


@dataclass
class TaskMetrics:
    duration_seconds: float | None = None
    cost_usd: float = 0.0
    status: str = "pending"
    qa_passed: int = 0
    qa_failed: int = 0
    retries: int = 0
    module: str = ""
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class ModuleMetrics:
    name: str = ""
    total_cost: float = 0.0
    task_count: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    qa_pass_rate: float | None = None
    avg_duration: float | None = None


@dataclass
class SessionMetricsSnapshot:
    total_cost: float = 0.0
    total_tasks: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    qa_pass_rate: float | None = None
    avg_duration: float | None = None
    per_module: dict[str, ModuleMetrics] = field(default_factory=dict)
    per_task: dict[int, TaskMetrics] = field(default_factory=dict)
    elapsed_seconds: float | None = None


def _parse_duration(start: str, end: str) -> float | None:
    """Compute seconds between two ISO timestamps."""
    try:
        t0 = datetime.fromisoformat(start)
        t1 = datetime.fromisoformat(end)
        return (t1 - t0).total_seconds()
    except (ValueError, TypeError):
        return None


class MetricsCollector:
    """Collects runtime metrics from hook events. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[int, TaskMetrics] = {}
        self._session_start_mono: float | None = None
        self._session_end_mono: float | None = None

    def attach(self, hooks: HookRegistry) -> None:
        """Register the collector as an on_any handler."""
        hooks.on_any(self._handle)

    def detach(self, hooks: HookRegistry) -> None:
        """Remove the collector from the hook registry."""
        hooks.remove_any(self._handle)

    def _handle(self, event: Event) -> None:
        """Single event handler dispatching internally by event type."""
        with self._lock:
            self._dispatch(event)

    def _dispatch(self, event: Event) -> None:  # noqa: C901
        """Route event to the appropriate handler. Must be called under lock."""
        match event.type:
            case EventType.SESSION_START:
                self._session_start_mono = time.monotonic()
            case EventType.SESSION_END:
                self._session_end_mono = time.monotonic()
            case EventType.TASK_STARTED:
                if event.task_id is not None:
                    self._tasks[event.task_id] = TaskMetrics(
                        status="in_progress",
                        module=event.module,
                        started_at=event.timestamp,
                    )
            case EventType.TASK_COMPLETED:
                if event.task_id is not None:
                    tm = self._tasks.get(event.task_id)
                    if tm is None:
                        tm = TaskMetrics(module=event.module)
                        self._tasks[event.task_id] = tm
                    tm.status = "completed"
                    tm.completed_at = event.timestamp
                    if tm.started_at and tm.completed_at:
                        tm.duration_seconds = _parse_duration(tm.started_at, tm.completed_at)
            case EventType.TASK_FAILED:
                if event.task_id is not None:
                    tm = self._tasks.get(event.task_id)
                    if tm is None:
                        tm = TaskMetrics(module=event.module)
                        self._tasks[event.task_id] = tm
                    tm.status = "failed"
                    tm.completed_at = event.timestamp
                    if tm.started_at and tm.completed_at:
                        tm.duration_seconds = _parse_duration(tm.started_at, tm.completed_at)
            case EventType.TASK_SKIPPED:
                if event.task_id is not None:
                    tm = self._tasks.get(event.task_id)
                    if tm is None:
                        tm = TaskMetrics(module=event.module)
                        self._tasks[event.task_id] = tm
                    tm.status = "skipped"
            case EventType.TASK_RETRYING:
                if event.task_id is not None:
                    tm = self._tasks.get(event.task_id)
                    if tm:
                        tm.retries += 1
            case EventType.QA_PASSED:
                if event.task_id is not None:
                    tm = self._tasks.get(event.task_id)
                    if tm:
                        tm.qa_passed += 1
            case EventType.QA_FAILED:
                if event.task_id is not None:
                    tm = self._tasks.get(event.task_id)
                    if tm:
                        tm.qa_failed += 1

    def snapshot(self) -> SessionMetricsSnapshot:
        """Return a frozen copy of current metrics, safe for cross-thread use."""
        with self._lock:
            # Deep copy per-task metrics
            per_task = {tid: copy.copy(tm) for tid, tm in self._tasks.items()}

            # Compute elapsed
            elapsed: float | None = None
            if self._session_start_mono is not None:
                end = self._session_end_mono or time.monotonic()
                elapsed = end - self._session_start_mono

            # Aggregate per-module
            modules: dict[str, ModuleMetrics] = {}
            for tm in per_task.values():
                mod = tm.module or "_unknown"
                if mod not in modules:
                    modules[mod] = ModuleMetrics(name=mod)
                mm = modules[mod]
                mm.task_count += 1
                mm.total_cost += tm.cost_usd
                if tm.status == "completed":
                    mm.completed += 1
                elif tm.status == "failed":
                    mm.failed += 1
                elif tm.status == "skipped":
                    mm.skipped += 1

            # Compute qa_pass_rate and avg_duration per module
            for mod, mm in modules.items():
                total_qa = sum(
                    t.qa_passed + t.qa_failed for t in per_task.values() if t.module == mod
                )
                if total_qa > 0:
                    passed = sum(t.qa_passed for t in per_task.values() if t.module == mod)
                    mm.qa_pass_rate = passed / total_qa
                durations = [
                    t.duration_seconds
                    for t in per_task.values()
                    if t.module == mod and t.duration_seconds is not None
                ]
                if durations:
                    mm.avg_duration = sum(durations) / len(durations)

            # Session-level aggregates
            total_tasks = len(per_task)
            completed = sum(1 for t in per_task.values() if t.status == "completed")
            failed = sum(1 for t in per_task.values() if t.status == "failed")
            skipped = sum(1 for t in per_task.values() if t.status == "skipped")
            total_cost = sum(t.cost_usd for t in per_task.values())

            total_qa = sum(t.qa_passed + t.qa_failed for t in per_task.values())
            qa_pass_rate: float | None = None
            if total_qa > 0:
                qa_pass_rate = sum(t.qa_passed for t in per_task.values()) / total_qa

            all_durations = [
                t.duration_seconds for t in per_task.values() if t.duration_seconds is not None
            ]
            avg_duration: float | None = None
            if all_durations:
                avg_duration = sum(all_durations) / len(all_durations)

            return SessionMetricsSnapshot(
                total_cost=total_cost,
                total_tasks=total_tasks,
                completed=completed,
                failed=failed,
                skipped=skipped,
                qa_pass_rate=qa_pass_rate,
                avg_duration=avg_duration,
                per_module=modules,
                per_task=per_task,
                elapsed_seconds=elapsed,
            )
