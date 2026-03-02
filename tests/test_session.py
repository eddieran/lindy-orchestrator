"""Tests for session state persistence and resume."""

from pathlib import Path

from lindy_orchestrator.session import SessionManager


def test_session_create_and_load(tmp_path: Path):
    sessions = SessionManager(tmp_path / "sessions")
    session = sessions.create(goal="Test goal")

    loaded = sessions.load(session.session_id)
    assert loaded is not None
    assert loaded.goal == "Test goal"
    assert loaded.status == "in_progress"


def test_session_complete(tmp_path: Path):
    sessions = SessionManager(tmp_path / "sessions")
    session = sessions.create(goal="Test")
    sessions.complete(session)

    loaded = sessions.load(session.session_id)
    assert loaded.status == "completed"
    assert loaded.completed_at is not None


def test_session_plan_json_roundtrip(tmp_path: Path):
    """Session can store and retrieve a full plan JSON."""
    sessions = SessionManager(tmp_path / "sessions")
    session = sessions.create(goal="Test")

    plan_data = {
        "goal": "Test",
        "tasks": [
            {
                "id": 1,
                "module": "backend",
                "description": "API changes",
                "status": "completed",
                "prompt": "Do stuff",
                "depends_on": [],
                "qa_checks": [{"gate": "command_check", "params": {"command": "pytest"}}],
                "qa_results": [],
                "result": "Done",
                "retries": 0,
            },
            {
                "id": 2,
                "module": "frontend",
                "description": "UI update",
                "status": "pending",
                "prompt": "Update UI",
                "depends_on": [1],
                "qa_checks": [],
                "qa_results": [],
                "result": "",
                "retries": 0,
            },
        ],
    }
    session.plan_json = plan_data
    sessions.save(session)

    loaded = sessions.load(session.session_id)
    assert loaded.plan_json is not None
    assert len(loaded.plan_json["tasks"]) == 2
    assert loaded.plan_json["tasks"][0]["status"] == "completed"
    assert loaded.plan_json["tasks"][1]["status"] == "pending"


def test_session_load_latest(tmp_path: Path):
    sessions = SessionManager(tmp_path / "sessions")
    sessions.create(goal="First")
    sessions.create(goal="Second")

    latest = sessions.load_latest()
    # Should load the most recent (by filename sort)
    assert latest is not None


def test_session_list(tmp_path: Path):
    sessions = SessionManager(tmp_path / "sessions")
    sessions.create(goal="A")
    sessions.create(goal="B")
    sessions.create(goal="C")

    listed = sessions.list_sessions(limit=2)
    assert len(listed) == 2
