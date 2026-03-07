"""Session state persistence for multi-session continuity."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


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
        files = sorted(
            self.sessions_dir.glob("*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return None
        return self._load(files[0])

    def load(self, session_id: str) -> SessionState | None:
        path = self.sessions_dir / f"{session_id}.json"
        if not path.exists():
            return None
        return self._load(path)

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
        files = sorted(
            self.sessions_dir.glob("*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:limit]
        sessions = []
        for f in files:
            try:
                sessions.append(self._load(f))
            except Exception:
                log.warning("Failed to load session file %s", f, exc_info=True)
        return sessions

    def _save(self, state: SessionState) -> None:
        path = self.sessions_dir / f"{state.session_id}.json"
        try:
            path.write_text(json.dumps(asdict(state), indent=2, default=str))
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
