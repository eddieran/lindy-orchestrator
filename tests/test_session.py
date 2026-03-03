"""Tests for session state persistence and resume."""

import os
import time
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


def test_session_load_latest_returns_newest_by_mtime(tmp_path: Path):
    """load_latest must return the most recently modified session, not
    the one whose UUID-based filename sorts highest."""
    sessions = SessionManager(tmp_path / "sessions")
    old = sessions.create(goal="Old session")
    new = sessions.create(goal="New session")

    # Force "old" file to have a newer mtime than "new"
    new_path = sessions.sessions_dir / f"{new.session_id}.json"

    # Set new_path to the past, old's file keeps current mtime → newest
    past = time.time() - 100
    os.utime(new_path, (past, past))

    latest = sessions.load_latest()
    assert latest is not None
    assert latest.session_id == old.session_id
    assert latest.goal == "Old session"


def test_session_list(tmp_path: Path):
    sessions = SessionManager(tmp_path / "sessions")
    sessions.create(goal="A")
    sessions.create(goal="B")
    sessions.create(goal="C")

    listed = sessions.list_sessions(limit=2)
    assert len(listed) == 2


def test_session_list_ordered_by_mtime(tmp_path: Path):
    """list_sessions should return sessions ordered newest-first by mtime."""
    sessions = SessionManager(tmp_path / "sessions")
    a = sessions.create(goal="A")
    b = sessions.create(goal="B")
    c = sessions.create(goal="C")

    # Make "A" the newest by touching it (it already has the latest mtime)
    b_path = sessions.sessions_dir / f"{b.session_id}.json"
    c_path = sessions.sessions_dir / f"{c.session_id}.json"

    past = time.time() - 200
    os.utime(b_path, (past, past))
    os.utime(c_path, (past - 100, past - 100))
    # a's file keeps current time → newest

    listed = sessions.list_sessions()
    assert listed[0].session_id == a.session_id
    assert listed[-1].session_id == c.session_id
