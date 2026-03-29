"""Session-level JSONL logging for layered observability."""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from .hooks import Event, EventType, HookRegistry
from .jsonl import append_jsonl

_log = logging.getLogger(__name__)

_RESERVED_FIELDS = {"ts", "level", "event", "task_id", "module"}
_TRANSCRIPT_EVENT_TYPES = (
    EventType.AGENT_EVENT,
    EventType.AGENT_OUTPUT,
    EventType.GIT_DIFF_CAPTURED,
    EventType.TASK_HEARTBEAT,
    EventType.CHECKPOINT_SAVED,
    EventType.MAILBOX_MESSAGE,
)


class SessionLogger:
    """Write L1 observability events for a session to JSONL streams."""

    def __init__(self, session_dir: Path | str, level: int) -> None:
        self.session_dir = Path(session_dir)
        self.level = level
        self._lock = threading.Lock()
        self.summary_path = self.session_dir / "summary.jsonl"
        self.decisions_path = self.session_dir / "decisions.jsonl"
        self.transcript_path = self.session_dir / "transcript.jsonl"
        self._ensure_paths()
        self._session_started_logged = self._has_existing_event(EventType.SESSION_START.value)

    def attach(self, hooks: HookRegistry) -> None:
        """Subscribe observability handlers for the configured levels."""
        if self.level < 1:
            return

        hooks.on(EventType.TASK_STARTED, self._on_task_started)
        hooks.on(EventType.TASK_COMPLETED, self._on_task_completed)
        hooks.on(EventType.TASK_FAILED, self._on_task_failed)
        hooks.on(EventType.TASK_SKIPPED, self._on_task_skipped)
        hooks.on(EventType.QA_PASSED, self._on_qa_passed)
        hooks.on(EventType.QA_FAILED, self._on_qa_failed)
        hooks.on(EventType.PHASE_CHANGED, self._on_phase_changed)
        hooks.on(EventType.SESSION_START, self._on_session_start)
        hooks.on(EventType.SESSION_RESUMED, self._on_session_resumed)
        hooks.on(EventType.SESSION_END, self._on_session_end)

        if self.level >= 3:
            for event_type in _TRANSCRIPT_EVENT_TYPES:
                hooks.on_async(event_type, self._on_transcript_event)

    def _ensure_paths(self) -> None:
        for path in self._selected_paths():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch(exist_ok=True)
            except OSError:
                self._fallback_prepare(path)

    def _selected_paths(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        if self.level >= 1:
            paths.append(self.summary_path)
        if self.level >= 2:
            paths.append(self.decisions_path)
        if self.level >= 3:
            paths.append(self.transcript_path)
        return tuple(paths)

    def _on_task_started(self, event: Event) -> None:
        self._write_summary(event)

    def _on_task_completed(self, event: Event) -> None:
        self._write_summary(event, extra={"status": "completed"})

    def _on_task_failed(self, event: Event) -> None:
        self._write_summary(event, extra={"event": "task_completed", "status": "failed"})

    def _on_task_skipped(self, event: Event) -> None:
        self._write_summary(event, extra={"event": "task_completed", "status": "skipped"})

    def _on_qa_passed(self, event: Event) -> None:
        self._write_summary(event)

    def _on_qa_failed(self, event: Event) -> None:
        self._write_summary(event)

    def _on_phase_changed(self, event: Event) -> None:
        if event.data.get("phase") == "planning":
            self._write_summary(event)

    def _on_session_start(self, event: Event) -> None:
        if self._session_started_logged:
            return
        self._session_started_logged = True
        self._write_summary(event)

    def _on_session_resumed(self, event: Event) -> None:
        self._write_summary(event)

    def _on_session_end(self, event: Event) -> None:
        self._write_summary(event)

    async def _on_transcript_event(self, event: Event) -> None:
        self._write_transcript(event)

    def _write_summary(self, event: Event, extra: dict[str, Any] | None = None) -> None:
        task_id = event.data.get("task_id", event.task_id)
        module = event.data.get("module", event.module)
        entry: dict[str, Any] = {
            "ts": event.timestamp,
            "level": 1,
            "event": event.type.value,
            "task_id": task_id,
        }

        if module:
            entry["module"] = module

        for key, value in event.data.items():
            if key not in _RESERVED_FIELDS:
                entry[key] = value

        if extra:
            entry.update(extra)

        try:
            append_jsonl(self.summary_path, entry, lock=self._lock)
        except OSError:
            self._fallback_write(entry, self.summary_path)

    def _write_transcript(self, event: Event) -> None:
        task_id = event.data.get("task_id", event.task_id)
        module = event.data.get("module", event.module)
        entry: dict[str, Any] = {
            "ts": event.timestamp,
            "level": 3,
            "event": event.type.value,
            "task_id": task_id,
        }

        if module:
            entry["module"] = module

        for key, value in event.data.items():
            if key not in _RESERVED_FIELDS:
                entry[key] = value

        try:
            append_jsonl(self.transcript_path, entry, lock=self._lock)
        except OSError:
            self._fallback_write(entry, self.transcript_path)

    def _fallback_prepare(self, path: Path) -> None:
        _log.warning("Failed to prepare session log path %s", path, exc_info=True)
        print(f"[session log fallback] failed to prepare {path}", file=sys.stderr)

    def _has_existing_event(self, event_name: str) -> bool:
        if not self.summary_path.exists():
            return False

        try:
            for line in self.summary_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("event") == event_name:
                    return True
        except OSError:
            _log.warning("Failed to inspect session summary log at %s", self.summary_path)

        return False

    def _fallback_write(self, entry: dict[str, Any], path: Path) -> None:
        _log.warning("Failed to write session log to %s", path, exc_info=True)
        print(
            f"[session log fallback] {entry['event']}: task_id={entry.get('task_id')}",
            file=sys.stderr,
        )
