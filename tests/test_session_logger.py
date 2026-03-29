"""Tests for SessionLogger and the L1 summary stream."""

from __future__ import annotations

import json
import threading
from unittest.mock import ANY

from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.session_logger import SessionLogger


class TestSessionLoggerInit:
    def test_level_one_creates_summary_only(self, tmp_path) -> None:
        logger = SessionLogger(tmp_path, level=1)

        assert logger.summary_path.exists()
        assert not logger.decisions_path.exists()
        assert not logger.transcript_path.exists()

    def test_level_three_prepares_future_stream_files(self, tmp_path) -> None:
        logger = SessionLogger(tmp_path, level=3)

        assert logger.summary_path.exists()
        assert logger.decisions_path.exists()
        assert logger.transcript_path.exists()


class TestSessionLoggerAttach:
    def test_uses_shared_append_jsonl_helper(self, tmp_path, mocker) -> None:
        append_jsonl = mocker.patch("lindy_orchestrator.session_logger.append_jsonl")
        logger = SessionLogger(tmp_path, level=1)
        hooks = HookRegistry()
        logger.attach(hooks)

        hooks.emit(
            Event(
                type=EventType.TASK_STARTED,
                timestamp="2026-03-29T10:18:05+00:00",
                task_id=7,
                module="backend",
                data={"description": "ship it"},
            )
        )

        append_jsonl.assert_called_once_with(
            logger.summary_path,
            {
                "ts": "2026-03-29T10:18:05+00:00",
                "level": 1,
                "event": "task_started",
                "task_id": 7,
                "module": "backend",
                "description": "ship it",
            },
            lock=logger._lock,
        )

    def test_routes_l1_events_to_summary_jsonl(self, tmp_path) -> None:
        logger = SessionLogger(tmp_path, level=1)
        hooks = HookRegistry()
        logger.attach(hooks)

        hooks.emit(
            Event(
                type=EventType.SESSION_START,
                timestamp="2026-03-29T10:18:05+00:00",
                data={"goal": "Add SessionLogger"},
            )
        )
        hooks.emit(
            Event(
                type=EventType.TASK_STARTED,
                timestamp="2026-03-29T10:18:06+00:00",
                task_id=1,
                module="backend",
                data={"description": "Implement logger"},
            )
        )
        hooks.emit(
            Event(
                type=EventType.TASK_COMPLETED,
                timestamp="2026-03-29T10:18:07+00:00",
                task_id=1,
                module="backend",
                data={"description": "Implement logger", "duration_seconds": 1.0, "cost_usd": 0.12},
            )
        )
        hooks.emit(
            Event(
                type=EventType.TASK_FAILED,
                timestamp="2026-03-29T10:18:08+00:00",
                task_id=2,
                module="frontend",
                data={"reason": "lint", "description": "Fix UI"},
            )
        )
        hooks.emit(
            Event(
                type=EventType.TASK_SKIPPED,
                timestamp="2026-03-29T10:18:09+00:00",
                task_id=3,
                module="qa",
                data={"reason": "dependency failed", "description": "Run QA"},
            )
        )
        hooks.emit(
            Event(
                type=EventType.QA_PASSED,
                timestamp="2026-03-29T10:18:10+00:00",
                task_id=1,
                module="backend",
                data={"gate": "pytest", "output": "all green"},
            )
        )
        hooks.emit(
            Event(
                type=EventType.QA_FAILED,
                timestamp="2026-03-29T10:18:11+00:00",
                task_id=2,
                module="frontend",
                data={"gate": "ruff", "output": "F401"},
            )
        )
        hooks.emit(
            Event(
                type=EventType.SESSION_END,
                timestamp="2026-03-29T10:18:12+00:00",
                data={"goal": "Add SessionLogger", "total_dispatches": 2, "has_failures": True},
            )
        )

        entries = [
            json.loads(line)
            for line in logger.summary_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

        assert [entry["event"] for entry in entries] == [
            "session_start",
            "task_started",
            "task_completed",
            "task_completed",
            "task_completed",
            "qa_passed",
            "qa_failed",
            "session_end",
        ]

        assert entries[0] == {
            "ts": "2026-03-29T10:18:05+00:00",
            "level": 1,
            "event": "session_start",
            "task_id": None,
            "goal": "Add SessionLogger",
        }
        assert entries[2]["status"] == "completed"
        assert entries[2]["duration_seconds"] == 1.0
        assert entries[2]["cost_usd"] == 0.12
        assert entries[3]["status"] == "failed"
        assert entries[3]["reason"] == "lint"
        assert entries[4]["status"] == "skipped"
        assert entries[5]["gate"] == "pytest"
        assert entries[6]["output"] == "F401"
        assert entries[7]["has_failures"] is True

        for entry in entries:
            assert entry["ts"] == ANY
            assert entry["level"] == 1
            assert "event" in entry
            assert "task_id" in entry

    def test_routes_planning_phase_and_session_resumed(self, tmp_path) -> None:
        logger = SessionLogger(tmp_path, level=1)
        hooks = HookRegistry()
        logger.attach(hooks)

        hooks.emit(
            Event(
                type=EventType.PHASE_CHANGED,
                timestamp="2026-03-29T10:18:05+00:00",
                data={"phase": "planning", "status": "failed", "error": "planner blew up"},
            )
        )
        hooks.emit(
            Event(
                type=EventType.SESSION_RESUMED,
                timestamp="2026-03-29T10:18:06+00:00",
                data={"goal": "Add SessionLogger", "session_id": "abc123"},
            )
        )
        hooks.emit(
            Event(
                type=EventType.PHASE_CHANGED,
                timestamp="2026-03-29T10:18:07+00:00",
                data={"phase": "qa", "status": "started"},
            )
        )

        entries = [
            json.loads(line)
            for line in logger.summary_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

        assert entries == [
            {
                "ts": "2026-03-29T10:18:05+00:00",
                "level": 1,
                "event": "phase_changed",
                "task_id": None,
                "phase": "planning",
                "status": "failed",
                "error": "planner blew up",
            },
            {
                "ts": "2026-03-29T10:18:06+00:00",
                "level": 1,
                "event": "session_resumed",
                "task_id": None,
                "goal": "Add SessionLogger",
                "session_id": "abc123",
            },
        ]

    def test_level_one_ignores_non_l1_events(self, tmp_path) -> None:
        logger = SessionLogger(tmp_path, level=1)
        hooks = HookRegistry()
        logger.attach(hooks)

        hooks.emit(Event(type=EventType.AGENT_EVENT, task_id=1, data={"kind": "tool_use"}))
        hooks.emit(Event(type=EventType.AGENT_OUTPUT, task_id=1, data={"text": "hello"}))
        hooks.emit(Event(type=EventType.GIT_DIFF_CAPTURED, task_id=1, data={"files": 2}))
        hooks.emit(Event(type=EventType.TASK_HEARTBEAT, task_id=1, data={"tool": "Edit"}))
        hooks.emit(Event(type=EventType.PHASE_CHANGED, task_id=1, data={"phase": "qa"}))
        hooks.emit(Event(type=EventType.EVAL_SCORED, task_id=1, data={"score": 0.8}))

        assert logger.summary_path.read_text(encoding="utf-8") == ""

    def test_concurrent_writes_produce_valid_jsonl(self, tmp_path) -> None:
        logger = SessionLogger(tmp_path, level=1)
        hooks = HookRegistry()
        logger.attach(hooks)
        barrier = threading.Barrier(10)

        def emit_batch(worker_id: int) -> None:
            barrier.wait()
            for offset in range(100):
                hooks.emit(
                    Event(
                        type=EventType.TASK_STARTED,
                        task_id=worker_id * 100 + offset,
                        module=f"mod-{worker_id}",
                        data={"description": "concurrent emit"},
                    )
                )

        threads = [
            threading.Thread(target=emit_batch, args=(worker_id,)) for worker_id in range(10)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        lines = logger.summary_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1000

        entries = [json.loads(line) for line in lines]
        assert all(entry["event"] == "task_started" for entry in entries)
        assert all(entry["level"] == 1 for entry in entries)
        assert {entry["task_id"] for entry in entries} == set(range(1000))

    def test_existing_session_start_is_not_duplicated(self, tmp_path) -> None:
        summary = tmp_path / "summary.jsonl"
        summary.write_text(
            json.dumps(
                {
                    "ts": "2026-03-29T10:18:05+00:00",
                    "level": 1,
                    "event": "session_start",
                    "task_id": None,
                    "goal": "existing goal",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        logger = SessionLogger(tmp_path, level=1)
        hooks = HookRegistry()
        logger.attach(hooks)

        hooks.emit(
            Event(
                type=EventType.SESSION_START,
                timestamp="2026-03-29T10:18:06+00:00",
                data={"goal": "new goal"},
            )
        )

        entries = [
            json.loads(line)
            for line in logger.summary_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

        assert len(entries) == 1
        assert entries[0]["goal"] == "existing goal"

    def test_os_error_fallback_to_stderr(self, tmp_path, capsys, mocker) -> None:
        logger = SessionLogger(tmp_path, level=1)
        hooks = HookRegistry()
        logger.attach(hooks)
        mocker.patch(
            "lindy_orchestrator.session_logger.append_jsonl", side_effect=OSError("disk full")
        )

        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=11))

        captured = capsys.readouterr()
        assert "[session log fallback]" in captured.err
        assert "task_started" in captured.err
        assert "task_id=11" in captured.err
