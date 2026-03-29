"""Tests for the inspect CLI command."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.session import SessionManager, SessionState

runner = CliRunner()


def _write_config(tmp_path) -> str:
    """Create a minimal config in .orchestrator/config.yaml."""
    orch_dir = tmp_path / ".orchestrator"
    orch_dir.mkdir(parents=True, exist_ok=True)
    cfg = orch_dir / "config.yaml"
    cfg.write_text(
        "project:\n  name: testproject\nmodules:\n  - name: backend\n    path: backend/\n",
        encoding="utf-8",
    )
    (tmp_path / "backend").mkdir(exist_ok=True)
    return str(cfg)


def _write_jsonl(path, entries: list[dict]) -> None:
    """Write entries as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n",
        encoding="utf-8",
    )


def _compact(text: str) -> str:
    """Remove all whitespace to make Rich table assertions width-independent."""
    return "".join(text.split())


def _seed_session(tmp_path):
    """Create a canonical session directory with summary, decisions, and transcript logs."""
    cfg_path = _write_config(tmp_path)
    sessions = SessionManager(tmp_path / ".orchestrator" / "sessions")
    session = SessionState(
        session_id="sess-1234",
        started_at="2026-03-29T10:00:00+00:00",
        completed_at="2026-03-29T10:05:00+00:00",
        goal="Inspect layered observability",
        status="paused",
        checkpoint_count=2,
    )
    sessions.save(session)

    session_dir = tmp_path / ".orchestrator" / "sessions" / session.session_id
    _write_jsonl(
        session_dir / "summary.jsonl",
        [
            {
                "ts": "2026-03-29T10:00:00+00:00",
                "level": 1,
                "event": "session_start",
                "task_id": None,
                "goal": "Inspect layered observability",
            },
            {
                "ts": "2026-03-29T10:01:00+00:00",
                "level": 1,
                "event": "task_started",
                "task_id": 1,
                "module": "backend",
                "description": "Implement parser",
            },
            {
                "ts": "2026-03-29T10:02:00+00:00",
                "level": 1,
                "event": "task_completed",
                "task_id": 1,
                "module": "backend",
                "status": "completed",
                "duration_seconds": 12.5,
            },
            {
                "ts": "2026-03-29T10:03:00+00:00",
                "level": 1,
                "event": "task_started",
                "task_id": 3,
                "module": "qa",
                "description": "Run QA",
            },
            {
                "ts": "2026-03-29T10:04:00+00:00",
                "level": 1,
                "event": "task_completed",
                "task_id": 3,
                "module": "qa",
                "status": "failed",
                "reason": "pytest failed",
            },
        ],
    )
    _write_jsonl(
        session_dir / "decisions.jsonl",
        [
            {
                "ts": "2026-03-29T10:04:10+00:00",
                "level": 2,
                "event": "dispatch_decision",
                "task_id": 1,
                "module": "backend",
                "decision": "continue",
            },
            {
                "ts": "2026-03-29T10:04:20+00:00",
                "level": 2,
                "event": "retry_decision",
                "task_id": 3,
                "module": "qa",
                "decision": "retry",
                "reason": "pytest failed",
            },
        ],
    )
    _write_jsonl(
        session_dir / "transcript.jsonl",
        [
            {
                "ts": "2026-03-29T10:04:30+00:00",
                "level": 3,
                "event": "agent_output",
                "task_id": 1,
                "module": "backend",
                "text": "Parser implemented",
            },
            {
                "ts": "2026-03-29T10:04:40+00:00",
                "level": 3,
                "event": "qa_detail",
                "task_id": 3,
                "module": "qa",
                "gate": "pytest",
                "passed": False,
                "output": "2 tests failed",
            },
        ],
    )
    return cfg_path, session.session_id


def test_inspect_shows_summary_table(tmp_path) -> None:
    cfg_path, session_id = _seed_session(tmp_path)

    result = runner.invoke(app, ["inspect", session_id, "-c", cfg_path])
    compact = _compact(result.output)

    assert result.exit_code == 0
    assert "SessionOverview" in compact
    assert "L1Summary" in compact
    assert "Inspectlayeredobservability" in compact
    assert "backend" in compact
    assert "completed" in compact
    assert "failed" in compact


def test_inspect_decisions_shows_l2_events(tmp_path) -> None:
    cfg_path, session_id = _seed_session(tmp_path)

    result = runner.invoke(app, ["inspect", session_id, "-c", cfg_path, "--decisions"])
    compact = _compact(result.output)

    assert result.exit_code == 0
    assert "L2Decisions" in compact
    assert "decision=retry" in compact
    assert "decision=continue" in compact


def test_inspect_full_shows_transcript_events(tmp_path) -> None:
    cfg_path, session_id = _seed_session(tmp_path)

    result = runner.invoke(app, ["inspect", session_id, "-c", cfg_path, "--full"])
    compact = _compact(result.output)

    assert result.exit_code == 0
    assert "L3Transcript" in compact
    assert "2testsfailed" in compact
    assert "Parserimplemented" in compact


def test_inspect_task_filter_limits_events_to_selected_task(tmp_path) -> None:
    cfg_path, session_id = _seed_session(tmp_path)

    result = runner.invoke(app, ["inspect", session_id, "-c", cfg_path, "--task", "3", "--full"])
    compact = _compact(result.output)

    assert result.exit_code == 0
    assert "task=3" in compact
    assert "pytestfailed" in compact
    assert "2testsfailed" in compact
    assert "backend" not in compact
    assert "Parserimplemented" not in compact


def test_inspect_failures_filters_to_failure_events(tmp_path) -> None:
    cfg_path, session_id = _seed_session(tmp_path)

    result = runner.invoke(app, ["inspect", session_id, "-c", cfg_path, "--full", "--failures"])
    compact = _compact(result.output)

    assert result.exit_code == 0
    assert "failed" in compact
    assert "decision=retry" in compact
    assert "2testsfailed" in compact
    assert "decision=continue" not in compact
    assert "Parserimplemented" not in compact


def test_inspect_missing_session_exits_with_clear_error(tmp_path) -> None:
    cfg_path = _write_config(tmp_path)

    result = runner.invoke(app, ["inspect", "missing-session", "-c", cfg_path])
    compact = _compact(result.output)

    assert result.exit_code == 1
    assert "Sessionnotfound:missing-session" in compact


def test_inspect_missing_level_file_reports_no_data(tmp_path) -> None:
    cfg_path, session_id = _seed_session(tmp_path)
    decisions_path = tmp_path / ".orchestrator" / "sessions" / session_id / "decisions.jsonl"
    decisions_path.unlink()

    result = runner.invoke(app, ["inspect", session_id, "-c", cfg_path, "--decisions"])
    compact = _compact(result.output)

    assert result.exit_code == 0
    assert "nodataatthislevel" in compact
