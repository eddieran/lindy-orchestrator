"""Parse STATUS.md files into structured Python data.

Designed to be lenient: extracts what it can, never crashes on unexpected formats.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import (
    ActiveTask,
    CompletedTask,
    CrossModuleDeliverable,
    CrossModuleRequest,
    ModuleMeta,
    ModuleStatus,
)


def parse_status_md(path: Path) -> ModuleStatus:
    """Parse a module STATUS.md into structured data."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ModuleStatus()

    sections = _split_by_h2(text)

    return ModuleStatus(
        meta=_parse_meta(sections.get("Meta", "")),
        active_work=_parse_active_work(sections.get("Active Work", "")),
        completed=_parse_completed(sections.get("Completed (Recent)", "")),
        backlog=_parse_backlog(sections.get("Backlog", "")),
        requests=_parse_requests(
            sections.get("Cross-Department Requests", "")
            or sections.get("Cross-Module Requests", "")
        ),
        deliverables=_parse_deliverables(
            sections.get("Cross-Department Deliverables", "")
            or sections.get("Cross-Module Deliverables", "")
        ),
        key_metrics=_parse_key_metrics(sections.get("Key Metrics", "")),
        blockers=_parse_blockers(sections.get("Blockers", "")),
        raw_text=text,
    )


# ---------------------------------------------------------------------------
# Generic markdown table parser
# ---------------------------------------------------------------------------


def _parse_markdown_table(text: str) -> list[dict[str, str]]:
    """Parse a markdown table into a list of dicts."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    table_lines = [ln for ln in lines if ln.startswith("|")]
    if len(table_lines) < 3:
        return []

    headers = _split_row(table_lines[0])
    rows = []
    for line in table_lines[2:]:
        cells = _split_row(line)
        if len(cells) != len(headers):
            cells = (cells + [""] * len(headers))[: len(headers)]
        row = {}
        for h, c in zip(headers, cells):
            row[h] = _clean_cell(c)
        rows.append(row)
    return rows


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into cells."""
    stripped = line.strip("|").strip()
    return [cell.strip() for cell in stripped.split("|")]


def _clean_cell(value: str) -> str:
    """Clean a table cell value: remove bold markers, backticks, etc."""
    v = value.strip()
    v = re.sub(r"\*\*(.+?)\*\*", r"\1", v)
    v = v.strip("`")
    return v.strip()


# ---------------------------------------------------------------------------
# Section splitter
# ---------------------------------------------------------------------------


def _split_by_h2(text: str) -> dict[str, str]:
    """Split markdown text into sections by ## headings."""
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        match = re.match(r"^##\s+(.+)$", line)
        if match:
            if current_heading:
                sections[current_heading] = "\n".join(current_lines)
            current_heading = match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_lines)

    return sections


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_meta(text: str) -> ModuleMeta:
    if not text.strip():
        return ModuleMeta()
    rows = _parse_markdown_table(text)
    kv = {r.get("Key", "").strip(): r.get("Value", "").strip() for r in rows if r.get("Key")}

    return ModuleMeta(
        module=kv.get("module", kv.get("department", "")),
        last_updated=kv.get("last_updated", ""),
        agent_session=kv.get("agent_session", ""),
        overall_health=kv.get("overall_health", "GREEN").upper(),
    )


def _parse_active_work(text: str) -> list[ActiveTask]:
    if not text.strip():
        return []
    rows = _parse_markdown_table(text)
    tasks = []
    for r in rows:
        task_id = r.get("ID", "").strip()
        task_name = r.get("Task", "").strip()
        if not task_id or task_id == "\u2014" or (not task_id and not task_name):
            continue
        tasks.append(
            ActiveTask(
                id=task_id,
                task=task_name,
                status=r.get("Status", "").strip(),
                blocked_by=r.get("BlockedBy", "").strip(),
                started=r.get("Started", "").strip(),
                notes=r.get("Notes", "").strip(),
            )
        )
    return tasks


def _parse_completed(text: str) -> list[CompletedTask]:
    if not text.strip():
        return []
    rows = _parse_markdown_table(text)
    tasks = []
    for r in rows:
        task_id = r.get("ID", "").strip()
        if not task_id or task_id == "\u2014":
            task_id = ""
        tasks.append(
            CompletedTask(
                id=task_id,
                task=r.get("Task", "").strip(),
                completed=r.get("Completed", "").strip(),
                outcome=r.get("Outcome", "").strip(),
            )
        )
    return tasks


def _parse_backlog(text: str) -> list[str]:
    if not text.strip():
        return []
    items = []
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"^-\s*\[.\]\s*(.+)$", line)
        if match:
            items.append(match.group(1).strip())
        elif line.startswith("- ") and not line.startswith("- (none"):
            items.append(line[2:].strip())
    return items


def _parse_requests(text: str) -> list[CrossModuleRequest]:
    if not text.strip():
        return []
    rows = _parse_markdown_table(text)
    requests = []
    for r in rows:
        req_id = r.get("ID", "").strip()
        if not req_id:
            continue
        requests.append(
            CrossModuleRequest(
                id=req_id,
                from_module=r.get("From", "").strip(),
                to_module=r.get("To", "").strip(),
                request=r.get("Request", "").strip(),
                priority=r.get("Priority", "").strip(),
                status=r.get("Status", "").strip(),
            )
        )
    return requests


def _parse_deliverables(text: str) -> list[CrossModuleDeliverable]:
    if not text.strip():
        return []
    rows = _parse_markdown_table(text)
    deliverables = []
    for r in rows:
        del_id = r.get("ID", "").strip()
        if not del_id:
            continue
        deliverables.append(
            CrossModuleDeliverable(
                id=del_id,
                from_module=r.get("From", "").strip(),
                to_module=r.get("To", "").strip(),
                deliverable=r.get("Deliverable", "").strip(),
                status=r.get("Status", "").strip(),
                path=r.get("Path", "").strip(),
            )
        )
    return deliverables


def _parse_key_metrics(text: str) -> dict[str, str]:
    if not text.strip():
        return {}
    rows = _parse_markdown_table(text)
    return {
        r.get("Metric", "").strip(): r.get("Value", "").strip()
        for r in rows
        if r.get("Metric", "").strip()
    }


def _parse_blockers(text: str) -> list[str]:
    if not text.strip():
        return []
    items = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- ") and "(none)" not in line.lower():
            items.append(line[2:].strip())
    return items
