"""JSONL append-only action logger for the orchestrator."""

from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .jsonl import append_jsonl

_log = logging.getLogger(__name__)


class ActionLogger:
    """Append-only JSONL logger for all orchestrator actions."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self._lock = threading.Lock()
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def log_action(
        self,
        action: str,
        details: dict[str, Any] | None = None,
        result: str = "success",
        output: str | dict | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "result": result,
        }
        if details:
            entry["details"] = details
        if output is not None:
            if isinstance(output, str) and len(output) > 5000:
                entry["output"] = output[:5000] + "... [truncated]"
            elif isinstance(output, dict):
                entry["output"] = output
            else:
                entry["output"] = str(output)

        try:
            append_jsonl(self.log_path, entry, lock=self._lock)
        except OSError:
            _log.warning("Failed to write action log to %s", self.log_path, exc_info=True)
            print(f"[log fallback] {action}: {result}", file=sys.stderr)

    def log_dispatch(
        self,
        module: str,
        prompt_preview: str,
        result: dict[str, Any],
    ) -> None:
        self.log_action(
            action="dispatch",
            details={"module": module, "prompt_preview": prompt_preview[:200]},
            result="success" if result.get("success") else "error",
            output=result,
        )

    def log_qa(
        self,
        gate: str,
        passed: bool,
        output: str,
    ) -> None:
        self.log_action(
            action="quality_gate",
            details={"gate": gate, "passed": passed},
            result="pass" if passed else "fail",
            output=output,
        )
