"""Tests for the analytics module — session aggregation and log parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lindy_orchestrator.analytics import (
    LogEntry,
    aggregate_log_metrics,
    compute_aggregate_stats,
    load_session_summaries,
    parse_log_entries,
)
from lindy_orchestrator.session import legacy_session_file_path, session_file_path


def _write_session(
    sessions_dir: Path, session_id: str, data: dict, *, layout: str = "flat"
) -> Path:
    """Helper: write a session JSON file in either storage layout."""
    if layout == "dir":
        path = session_file_path(sessions_dir, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = legacy_session_file_path(sessions_dir, session_id)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _make_session_data(
    session_id: str = "abc123",
    goal: str = "Test goal",
    status: str = "completed",
    tasks: list[dict] | None = None,
    started_at: str = "2026-01-01T00:00:00+00:00",
    completed_at: str = "2026-01-01T00:05:00+00:00",
) -> dict:
    """Helper: build a session dict with plan_json."""
    if tasks is None:
        tasks = [
            {
                "id": 1,
                "module": "backend",
                "description": "Task 1",
                "status": "completed",
                "cost_usd": 0.05,
            },
        ]
    return {
        "session_id": session_id,
        "goal": goal,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "plan_json": {"goal": goal, "tasks": tasks},
    }


class TestLoadSessionSummaries:
    def test_empty_dir_returns_empty(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        result = load_session_summaries(sessions_dir)
        assert result == []

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path):
        result = load_session_summaries(tmp_path / "nope")
        assert result == []

    def test_single_session(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1"))

        result = load_session_summaries(sessions_dir)
        assert len(result) == 1
        s = result[0]
        assert s.session_id == "s1"
        assert s.goal == "Test goal"
        assert s.status == "completed"
        assert s.task_count == 1
        assert s.completed == 1
        assert s.total_cost == pytest.approx(0.05)
        assert "backend" in s.modules

    def test_cost_extraction_from_plan_json(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        tasks = [
            {
                "id": 1,
                "module": "backend",
                "description": "T1",
                "status": "completed",
                "cost_usd": 0.10,
            },
            {
                "id": 2,
                "module": "frontend",
                "description": "T2",
                "status": "completed",
                "cost_usd": 0.25,
            },
            {
                "id": 3,
                "module": "backend",
                "description": "T3",
                "status": "failed",
                "cost_usd": 0.03,
            },
        ]
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1", tasks=tasks))

        result = load_session_summaries(sessions_dir)
        assert len(result) == 1
        assert result[0].total_cost == pytest.approx(0.38)
        assert result[0].completed == 2
        assert result[0].failed == 1

    def test_malformed_file_skipped(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        # Write valid session
        _write_session(sessions_dir, "good", _make_session_data(session_id="good"))
        # Write malformed file
        (sessions_dir / "bad.json").write_text("not valid json {{", encoding="utf-8")

        result = load_session_summaries(sessions_dir)
        assert len(result) == 1
        assert result[0].session_id == "good"

    def test_limit_param(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        for i in range(5):
            _write_session(sessions_dir, f"s{i}", _make_session_data(session_id=f"s{i}"))

        result = load_session_summaries(sessions_dir, limit=2)
        assert len(result) == 2

    def test_module_filter(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        # Session with backend tasks
        _write_session(
            sessions_dir,
            "s1",
            _make_session_data(
                session_id="s1",
                tasks=[
                    {
                        "id": 1,
                        "module": "backend",
                        "description": "T1",
                        "status": "completed",
                        "cost_usd": 0.01,
                    }
                ],
            ),
        )
        # Session with only frontend tasks
        _write_session(
            sessions_dir,
            "s2",
            _make_session_data(
                session_id="s2",
                tasks=[
                    {
                        "id": 1,
                        "module": "frontend",
                        "description": "T1",
                        "status": "completed",
                        "cost_usd": 0.02,
                    }
                ],
            ),
        )

        result = load_session_summaries(sessions_dir, module_filter="backend")
        assert len(result) == 1
        assert result[0].session_id == "s1"

    def test_old_format_without_cost_usd(self, tmp_path: Path):
        """Sessions without cost_usd should default to 0."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        tasks = [{"id": 1, "module": "backend", "description": "T1", "status": "completed"}]
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1", tasks=tasks))

        result = load_session_summaries(sessions_dir)
        assert result[0].total_cost == 0.0

    def test_session_without_plan_json(self, tmp_path: Path):
        """Sessions without plan_json should have zero tasks."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        data = {"session_id": "s1", "goal": "Test", "status": "completed"}
        _write_session(sessions_dir, "s1", data)

        result = load_session_summaries(sessions_dir)
        assert len(result) == 1
        assert result[0].task_count == 0

    def test_duration_from_timestamps(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        _write_session(
            sessions_dir,
            "s1",
            _make_session_data(
                session_id="s1",
                started_at="2026-01-01T00:00:00+00:00",
                completed_at="2026-01-01T00:10:00+00:00",
            ),
        )
        result = load_session_summaries(sessions_dir)
        assert result[0].duration_seconds == pytest.approx(600.0)

    def test_mixed_storage_layouts_are_both_loaded(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        _write_session(sessions_dir, "flat1", _make_session_data(session_id="flat1"), layout="flat")
        _write_session(sessions_dir, "dir1", _make_session_data(session_id="dir1"), layout="dir")

        result = load_session_summaries(sessions_dir)

        assert [summary.session_id for summary in result] == ["dir1", "flat1"]


class TestParseLogEntries:
    def test_empty_file(self, tmp_path: Path):
        log_path = tmp_path / "actions.jsonl"
        log_path.write_text("", encoding="utf-8")
        assert parse_log_entries(log_path) == []

    def test_nonexistent_file(self, tmp_path: Path):
        assert parse_log_entries(tmp_path / "nope.jsonl") == []

    def test_valid_entries(self, tmp_path: Path):
        log_path = tmp_path / "actions.jsonl"
        lines = [
            json.dumps(
                {"timestamp": "2026-01-01T00:00:00", "action": "dispatch", "result": "success"}
            ),
            json.dumps(
                {
                    "timestamp": "2026-01-01T00:01:00",
                    "action": "quality_gate",
                    "result": "pass",
                    "details": {"passed": True},
                }
            ),
        ]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        entries = parse_log_entries(log_path)
        assert len(entries) == 2
        assert entries[0].action == "dispatch"
        assert entries[1].action == "quality_gate"

    def test_malformed_lines_skipped(self, tmp_path: Path):
        log_path = tmp_path / "actions.jsonl"
        lines = [
            json.dumps({"action": "dispatch", "result": "success"}),
            "not json at all",
            json.dumps({"action": "quality_gate", "result": "fail"}),
        ]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        entries = parse_log_entries(log_path)
        assert len(entries) == 2


class TestAggregateLogMetrics:
    def test_counts_dispatches_and_qa(self):
        entries = [
            LogEntry(action="dispatch", result="success"),
            LogEntry(action="dispatch", result="error"),
            LogEntry(action="quality_gate", result="pass", details={"passed": True}),
            LogEntry(action="quality_gate", result="fail", details={"passed": False}),
            LogEntry(action="quality_gate", result="pass", details={"passed": True}),
            LogEntry(action="session_start", result="success"),
        ]
        m = aggregate_log_metrics(entries)
        assert m.dispatch_count == 2
        assert m.dispatch_success == 1
        assert m.dispatch_error == 1
        assert m.qa_total == 3
        assert m.qa_pass == 2
        assert m.qa_fail == 1

    def test_empty_entries(self):
        m = aggregate_log_metrics([])
        assert m.dispatch_count == 0
        assert m.qa_total == 0


class TestComputeAggregateStats:
    def test_empty_sessions(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        stats = compute_aggregate_stats(sessions_dir, log_path=None)
        assert stats.total_tasks == 0
        assert stats.per_session == []

    def test_single_session_aggregate(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        tasks = [
            {
                "id": 1,
                "module": "backend",
                "description": "T1",
                "status": "completed",
                "cost_usd": 0.10,
            },
            {
                "id": 2,
                "module": "frontend",
                "description": "T2",
                "status": "failed",
                "cost_usd": 0.05,
            },
        ]
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1", tasks=tasks))

        stats = compute_aggregate_stats(sessions_dir, log_path=None)
        assert stats.total_cost == pytest.approx(0.15)
        assert stats.total_tasks == 2
        assert stats.completed == 1
        assert stats.failed == 1
        assert stats.failure_rate == pytest.approx(0.5)
        assert "backend" in stats.per_module
        assert "frontend" in stats.per_module

    def test_qa_rate_from_log(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1"))

        log_path = tmp_path / "actions.jsonl"
        lines = [
            json.dumps({"action": "quality_gate", "result": "pass", "details": {"passed": True}}),
            json.dumps({"action": "quality_gate", "result": "pass", "details": {"passed": True}}),
            json.dumps({"action": "quality_gate", "result": "fail", "details": {"passed": False}}),
        ]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        stats = compute_aggregate_stats(sessions_dir, log_path=log_path)
        assert stats.qa_pass_rate == pytest.approx(2 / 3)

    def test_limit_param(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        for i in range(5):
            _write_session(sessions_dir, f"s{i}", _make_session_data(session_id=f"s{i}"))

        stats = compute_aggregate_stats(sessions_dir, log_path=None, limit=2)
        assert len(stats.per_session) == 2

    def test_module_filter(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        _write_session(
            sessions_dir,
            "s1",
            _make_session_data(
                session_id="s1",
                tasks=[
                    {
                        "id": 1,
                        "module": "backend",
                        "description": "T1",
                        "status": "completed",
                        "cost_usd": 0.10,
                    },
                    {
                        "id": 2,
                        "module": "frontend",
                        "description": "T2",
                        "status": "completed",
                        "cost_usd": 0.05,
                    },
                ],
            ),
        )

        stats = compute_aggregate_stats(sessions_dir, log_path=None, module_filter="backend")
        assert "backend" in stats.per_module
        assert "frontend" not in stats.per_module


class TestAnalyticsEdgeCases:
    """Additional edge case coverage for analytics module."""

    def test_session_with_non_dict_data(self, tmp_path: Path):
        """Files containing non-dict JSON (e.g. a list) should be skipped."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "array.json").write_text("[]", encoding="utf-8")
        result = load_session_summaries(sessions_dir)
        assert result == []

    def test_session_with_invalid_timestamps(self, tmp_path: Path):
        """Invalid timestamps should result in 0 duration, not crash."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        data = _make_session_data(
            session_id="bad_ts",
            started_at="not-a-date",
            completed_at="also-not-a-date",
        )
        _write_session(sessions_dir, "bad_ts", data)
        result = load_session_summaries(sessions_dir)
        assert len(result) == 1
        assert result[0].duration_seconds == 0.0

    def test_session_with_missing_timestamps(self, tmp_path: Path):
        """Sessions without timestamps should have 0 duration."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        data = _make_session_data(session_id="no_ts", started_at="", completed_at="")
        _write_session(sessions_dir, "no_ts", data)
        result = load_session_summaries(sessions_dir)
        assert len(result) == 1
        assert result[0].duration_seconds == 0.0

    def test_aggregate_with_no_log_path(self, tmp_path: Path):
        """compute_aggregate_stats with log_path=None uses session-level QA rate."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        tasks = [
            {"id": 1, "module": "mod", "status": "completed", "cost_usd": 0.01},
            {"id": 2, "module": "mod", "status": "failed", "cost_usd": 0.01},
        ]
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1", tasks=tasks))
        stats = compute_aggregate_stats(sessions_dir, log_path=None)
        assert stats.qa_pass_rate == pytest.approx(0.5)

    def test_aggregate_with_empty_log_file(self, tmp_path: Path):
        """Empty log file should fall back to session-level QA rate."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1"))
        log_path = tmp_path / "actions.jsonl"
        log_path.write_text("", encoding="utf-8")
        stats = compute_aggregate_stats(sessions_dir, log_path=log_path)
        # With 1 completed and 0 failed, qa_pass_rate = 1.0
        assert stats.qa_pass_rate == pytest.approx(1.0)

    def test_per_module_duration_with_task_timestamps(self, tmp_path: Path):
        """Per-module avg_duration should compute from task-level timestamps."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        tasks = [
            {
                "id": 1,
                "module": "backend",
                "status": "completed",
                "cost_usd": 0.01,
                "started_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:01:00+00:00",
            },
        ]
        _write_session(sessions_dir, "s1", _make_session_data(session_id="s1", tasks=tasks))
        stats = compute_aggregate_stats(sessions_dir, log_path=None)
        assert "backend" in stats.per_module
        assert stats.per_module["backend"].avg_duration == pytest.approx(60.0)

    def test_deleted_session_file_during_module_breakdown(self, tmp_path: Path):
        """_get_plan_tasks_from_summary handles missing file gracefully."""
        from lindy_orchestrator.analytics import SessionSummary, _get_plan_tasks_from_summary

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        summary = SessionSummary(session_id="ghost")
        tasks = _get_plan_tasks_from_summary(summary, sessions_dir)
        assert tasks == []

    def test_log_entry_with_non_dict_json(self, tmp_path: Path):
        """JSONL lines containing non-dict JSON should be skipped."""
        log_path = tmp_path / "actions.jsonl"
        lines = [
            '"just a string"',
            "42",
            json.dumps({"action": "dispatch", "result": "success"}),
        ]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        entries = parse_log_entries(log_path)
        assert len(entries) == 1
