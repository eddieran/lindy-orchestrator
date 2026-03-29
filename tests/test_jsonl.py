from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from lindy_orchestrator.jsonl import append_jsonl


def test_append_jsonl_appends_one_json_document_per_line(tmp_path) -> None:
    log_path = tmp_path / "logs" / "events.jsonl"

    append_jsonl(log_path, {"event": "start", "ok": True})
    append_jsonl(log_path, {"event": "finish", "count": 2})

    entries = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert entries == [{"event": "start", "ok": True}, {"event": "finish", "count": 2}]


def test_append_jsonl_uses_provided_lock(tmp_path) -> None:
    log_path = tmp_path / "events.jsonl"
    lock = threading.Lock()

    def write(index: int) -> None:
        append_jsonl(log_path, {"index": index}, lock=lock)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(64)))

    entries = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(entries) == 64
    assert sorted(entry["index"] for entry in entries) == list(range(64))
