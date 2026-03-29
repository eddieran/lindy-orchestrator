"""Session state persistence for multi-session continuity."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Session IDs must be safe path components (hex chars from uuid4[:8])
_SAFE_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
SESSION_FILENAME = "session.json"


@dataclass
class SessionState:
    """Persisted session state."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    goal: str = ""
    status: str = "in_progress"  # in_progress, completed, paused, failed
    actions_taken: list[dict[str, Any]] = field(default_factory=list)
    pending_tasks: list[dict[str, Any]] = field(default_factory=list)
    completed_tasks: list[dict[str, Any]] = field(default_factory=list)
    plan_json: dict[str, Any] | None = None  # Full TaskPlan snapshot for resume
    checkpoint_count: int = 0
    last_checkpoint_at: str | None = None


class SessionManager:
    """Manage session state persistence."""

    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir
        sessions_dir.mkdir(parents=True, exist_ok=True)

    def create(self, goal: str = "") -> SessionState:
        state = SessionState(goal=goal)
        self._save(state)
        return state

    def load_latest(self) -> SessionState | None:
        files = iter_session_files(self.sessions_dir)
        if not files:
            return None
        return self._load(files[0])

    def load(self, session_id: str) -> SessionState | None:
        # SECURITY: validate session_id to prevent path traversal
        if not _SAFE_SESSION_ID_RE.match(session_id):
            log.warning("Rejected unsafe session_id: %r", session_id)
            return None
        for path in (
            session_file_path(self.sessions_dir, session_id),
            legacy_session_file_path(self.sessions_dir, session_id),
        ):
            if not path.resolve().is_relative_to(self.sessions_dir.resolve()):
                log.warning("Path traversal detected for session_id: %r", session_id)
                return None
            if path.exists():
                return self._load(path)
        return None

    def save(self, state: SessionState) -> None:
        self._save(state)

    def complete(self, state: SessionState) -> None:
        state.status = "completed"
        state.completed_at = datetime.now(timezone.utc).isoformat()
        self._save(state)

    def checkpoint(self, state: SessionState, plan_dict: dict) -> None:
        """Save a mid-execution checkpoint with current plan state."""
        state.plan_json = plan_dict
        state.checkpoint_count += 1
        state.last_checkpoint_at = datetime.now(timezone.utc).isoformat()
        self._save(state)

    def list_sessions(self, limit: int = 10) -> list[SessionState]:
        files = iter_session_files(self.sessions_dir)[:limit]
        sessions = []
        for f in files:
            try:
                sessions.append(self._load(f))
            except Exception:
                log.warning("Failed to load session file %s", f, exc_info=True)
        return sessions

    def _save(self, state: SessionState) -> None:
        path = session_file_path(self.sessions_dir, state.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Write to temp file then atomically rename to prevent corruption
            fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix="session_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(asdict(state), f, indent=2, default=str)
                os.replace(tmp_path, path)
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError:
            log.exception("Failed to save session %s to %s", state.session_id, path)
            raise

    def _load(self, path: Path) -> SessionState:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            log.exception("Failed to load session from %s", path)
            raise
        return SessionState(**data)


def session_file_path(sessions_dir: Path, session_id: str) -> Path:
    """Return the canonical per-session path for a session."""
    return sessions_dir / session_id / SESSION_FILENAME


def legacy_session_file_path(sessions_dir: Path, session_id: str) -> Path:
    """Return the legacy flat-file path for a session."""
    return sessions_dir / f"{session_id}.json"


def session_id_from_path(path: Path) -> str:
    """Extract a session ID from either the new or legacy storage layout."""
    if path.name == SESSION_FILENAME:
        return path.parent.name
    return path.stem


def iter_session_files(sessions_dir: Path) -> list[Path]:
    """Return canonical session file paths across mixed storage layouts.

    Results are sorted newest-first by file mtime and deduplicated by session ID
    so mixed-format transitions do not surface duplicate sessions.
    """
    if not sessions_dir.exists():
        return []

    candidates = [
        *[path for path in sessions_dir.glob("*.json") if path.is_file()],
        *[
            path
            for path in sessions_dir.glob(f"*/{SESSION_FILENAME}")
            if path.is_file() and path.parent.name != "archive"
        ],
    ]
    candidates.sort(key=_session_file_mtime, reverse=True)

    seen: set[str] = set()
    files: list[Path] = []
    for path in candidates:
        session_id = session_id_from_path(path)
        if session_id in seen:
            continue
        seen.add(session_id)
        files.append(path)
    return files


def _session_file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
