"""Shared helpers for append-only JSONL writes."""

from __future__ import annotations

import json
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Any


def append_jsonl(
    path: Path,
    data: dict[str, Any],
    lock: threading.Lock | None = None,
) -> None:
    """Append a single JSON document as one line to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock if lock is not None else nullcontext():
        with path.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(data, ensure_ascii=False, default=str))
            file_obj.write("\n")
