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
_SUMMARY_OMIT_FIELDS = {"full_output", "raw_output"}
_TRANSCRIPT_EVENT_TYPES = (
    EventType.AGENT_EVENT,
    EventType.AGENT_OUTPUT,
    EventType.GIT_DIFF_CAPTURED,
    EventType.TASK_HEARTBEAT,
    EventType.CHECKPOINT_SAVED,
    EventType.MAILBOX_MESSAGE,
)


class SessionLogger:
    """Write layered observability events for a session to JSONL streams."""

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

        if self.level >= 2:
            hooks.on(EventType.QA_PASSED, self._on_decision_event)
            hooks.on(EventType.QA_FAILED, self._on_decision_event)
            hooks.on(EventType.PHASE_CHANGED, self._on_decision_event)
            hooks.on(EventType.EVAL_SCORED, self._on_decision_event)
            hooks.on(EventType.TASK_RETRYING, self._on_decision_event)
            hooks.on(EventType.STALL_WARNING, self._on_decision_event)
            hooks.on(EventType.STALL_KILLED, self._on_decision_event)
            hooks.on(EventType.PROMPT_SENT, self._on_decision_event)

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

    def _on_decision_event(self, event: Event) -> None:
        self._write_decision(event)

    async def _on_transcript_event(self, event: Event) -> None:
        self._write_transcript(event)

    def _write_summary(self, event: Event, extra: dict[str, Any] | None = None) -> None:
        entry = self._build_entry(
            event,
            level=1,
            extra=extra,
            omit_fields=_SUMMARY_OMIT_FIELDS,
        )
        self._append_entry(self.summary_path, entry)

    def _write_decision(self, event: Event, extra: dict[str, Any] | None = None) -> None:
        entry = self._build_entry(event, level=2, extra=extra, use_full_output=True)
        self._append_entry(self.decisions_path, entry)

    def _write_transcript(self, event: Event) -> None:
        entry = self._build_entry(event, level=3)
        self._append_entry(self.transcript_path, entry)

    def _build_entry(
        self,
        event: Event,
        *,
        level: int,
        extra: dict[str, Any] | None = None,
        omit_fields: set[str] | None = None,
        use_full_output: bool = False,
    ) -> dict[str, Any]:
        task_id = event.data.get("task_id", event.task_id)
        module = event.data.get("module", event.module)
        entry: dict[str, Any] = {
            "ts": event.timestamp,
            "level": level,
            "event": event.type.value,
            "task_id": task_id,
        }

        if module:
            entry["module"] = module

        for key, value in event.data.items():
            if key in _RESERVED_FIELDS or (omit_fields and key in omit_fields):
                continue
            if key == "full_output":
                if use_full_output:
                    entry["output"] = value
                continue
            entry[key] = value

        if extra:
            entry.update(extra)

        return entry

    def _append_entry(self, path: Path, entry: dict[str, Any]) -> None:
        try:
            append_jsonl(path, entry, lock=self._lock)
        except OSError:
            self._fallback_write(path, entry)

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

    def _fallback_write(self, path: Path, entry: dict[str, Any]) -> None:
        _log.warning("Failed to write session log to %s", path, exc_info=True)
        print(
            f"[session log fallback] {entry['event']}: task_id={entry.get('task_id')}",
            file=sys.stderr,
        )
