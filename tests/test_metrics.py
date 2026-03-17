"""Tests for metrics collection thread safety.

The analytics module's compute_aggregate_stats performs aggregation
from session files. These tests verify:
- Thread-safe concurrent event aggregation via aggregate_log_metrics
- Concurrent session summary loading is safe
- Edge cases in metric computation (zero denominators, missing fields)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from lindy_orchestrator.analytics import (
    AggregateStats,
    LogEntry,
    LogMetrics,
    ModuleStats,
    SessionSummary,
    aggregate_log_metrics,
    compute_aggregate_stats,
    load_session_summaries,
)


def _write_session(sessions_dir: Path, session_id: str, data: dict) -> Path:
    path = sessions_dir / f"{session_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _make_session_data(
    session_id: str = "s1",
    tasks: list[dict] | None = None,
    started_at: str = "2026-01-01T00:00:00+00:00",
    completed_at: str = "2026-01-01T00:05:00+00:00",
) -> dict:
    if tasks is None:
        tasks = [
            {"id": 1, "module": "mod", "status": "completed", "cost_usd": 0.01},
        ]
    return {
        "session_id": session_id,
        "goal": "test",
        "status": "completed",
        "started_at": started_at,
        "completed_at": completed_at,
        "plan_json": {"goal": "test", "tasks": tasks},
    }


class TestAggregateLogMetricsThreadSafety:
    """aggregate_log_metrics must produce correct results even from concurrent calls."""

    def test_concurrent_aggregation_produces_same_result(self):
        entries = [
            LogEntry(action="dispatch", result="success"),
            LogEntry(action="dispatch", result="error"),
            LogEntry(action="quality_gate", result="pass", details={"passed": True}),
            LogEntry(action="quality_gate", result="fail", details={"passed": False}),
        ] * 10  # 40 entries total

        expected = aggregate_log_metrics(entries)
        results: list[LogMetrics] = []
        lock = threading.Lock()

        def aggregate():
            m = aggregate_log_metrics(entries)
            with lock:
                results.append(m)

        threads = [threading.Thread(target=aggregate) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for m in results:
            assert m.dispatch_count == expected.dispatch_count
            assert m.dispatch_success == expected.dispatch_success
            assert m.dispatch_error == expected.dispatch_error
            assert m.qa_total == expected.qa_total
            assert m.qa_pass == expected.qa_pass
            assert m.qa_fail == expected.qa_fail


class TestConcurrentSessionLoading:
    """Concurrent reads of session files must not corrupt data."""

    def test_concurrent_load_returns_consistent_results(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        for i in range(10):
            _write_session(sessions_dir, f"s{i}", _make_session_data(session_id=f"s{i}"))

        results: list[list[SessionSummary]] = []
        lock = threading.Lock()

        def load():
            r = load_session_summaries(sessions_dir)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=load) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for r in results:
            assert len(r) == 10

    def test_concurrent_compute_aggregate_stats(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        tasks = [
            {"id": 1, "module": "backend", "status": "completed", "cost_usd": 0.10},
            {"id": 2, "module": "frontend", "status": "failed", "cost_usd": 0.05},
        ]
        for i in range(5):
            _write_session(
                sessions_dir, f"s{i}", _make_session_data(session_id=f"s{i}", tasks=tasks)
            )

        results: list[AggregateStats] = []
        lock = threading.Lock()

        def compute():
            r = compute_aggregate_stats(sessions_dir, log_path=None)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=compute) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for r in results:
            assert r.total_tasks == 10
            assert r.completed == 5
            assert r.failed == 5


class TestMetricsEdgeCases:
    """Edge cases in metric computation."""

    def test_qa_pass_rate_zero_completed_zero_failed(self):
        """qa_pass_rate should be 0 when no completed or failed tasks."""
        stats = AggregateStats()
        stats.completed = 0
        stats.failed = 0
        total_terminal = stats.completed + stats.failed
        rate = stats.completed / total_terminal if total_terminal > 0 else 0.0
        assert rate == 0.0

    def test_failure_rate_zero_tasks(self):
        """failure_rate should be 0 when total_tasks is 0."""
        stats = AggregateStats()
        stats.total_tasks = 0
        stats.failed = 0
        rate = stats.failed / stats.total_tasks if stats.total_tasks > 0 else 0.0
        assert rate == 0.0

    def test_module_stats_avg_duration_zero_tasks(self):
        ms = ModuleStats(name="test", task_count=0)
        avg = ms.avg_duration / ms.task_count if ms.task_count > 0 else 0.0
        assert avg == 0.0

    def test_session_summary_defaults(self):
        s = SessionSummary()
        assert s.session_id == ""
        assert s.task_count == 0
        assert s.total_cost == 0.0
        assert s.duration_seconds == 0.0
        assert s.modules == []

    def test_aggregate_stats_with_all_skipped(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        tasks = [
            {"id": 1, "module": "backend", "status": "skipped", "cost_usd": 0.0},
            {"id": 2, "module": "backend", "status": "skipped", "cost_usd": 0.0},
        ]
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1", tasks=tasks))

        stats = compute_aggregate_stats(sessions_dir, log_path=None)
        assert stats.skipped == 2
        assert stats.completed == 0
        assert stats.failed == 0
        assert stats.qa_pass_rate == 0.0

    def test_module_duration_with_missing_timestamps(self, tmp_path: Path):
        """Tasks without started_at/completed_at should not crash duration calc."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        tasks = [
            {"id": 1, "module": "backend", "status": "completed", "cost_usd": 0.01},
        ]
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1", tasks=tasks))

        stats = compute_aggregate_stats(sessions_dir, log_path=None)
        assert "backend" in stats.per_module
        assert stats.per_module["backend"].avg_duration == pytest.approx(0.0)

    def test_module_duration_with_invalid_iso_dates(self, tmp_path: Path):
        """Tasks with malformed date strings should not crash."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        tasks = [
            {
                "id": 1,
                "module": "backend",
                "status": "completed",
                "cost_usd": 0.01,
                "started_at": "not-a-date",
                "completed_at": "also-not-a-date",
            },
        ]
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1", tasks=tasks))

        stats = compute_aggregate_stats(sessions_dir, log_path=None)
        assert "backend" in stats.per_module
        assert stats.per_module["backend"].avg_duration == pytest.approx(0.0)

    def test_log_metrics_defaults(self):
        m = LogMetrics()
        assert m.dispatch_count == 0
        assert m.dispatch_success == 0
        assert m.dispatch_error == 0
        assert m.qa_total == 0
        assert m.qa_pass == 0
        assert m.qa_fail == 0
