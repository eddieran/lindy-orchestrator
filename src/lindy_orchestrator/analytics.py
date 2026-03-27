"""Cross-session analytics aggregation from local session and log files."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SessionSummary:
    """Summary of a single orchestration session."""

    session_id: str = ""
    goal: str = ""
    status: str = ""
    task_count: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    total_cost: float = 0.0
    duration_seconds: float = 0.0
    modules: list[str] = field(default_factory=list)
    _raw_tasks: list[dict] = field(default_factory=list, repr=False)


@dataclass
class ModuleStats:
    """Aggregated statistics for a single module across sessions."""

    name: str = ""
    total_cost: float = 0.0
    task_count: int = 0
    completed: int = 0
    failed: int = 0
    qa_pass_rate: float = 0.0
    avg_duration: float = 0.0


@dataclass
class AggregateStats:
    """Top-level aggregate statistics across all sessions."""

    total_cost: float = 0.0
    total_tasks: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    qa_pass_rate: float = 0.0
    avg_duration: float = 0.0
    failure_rate: float = 0.0
    per_module: dict[str, ModuleStats] = field(default_factory=dict)
    per_session: list[SessionSummary] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Session loading
# ---------------------------------------------------------------------------


def load_session_summaries(
    sessions_dir: Path,
    limit: int | None = None,
    module_filter: str | None = None,
) -> list[SessionSummary]:
    """Load session JSON files and extract summaries.

    Reads ``sessions_dir/*.json``, extracts task costs from ``plan_json``,
    and uses defensive ``.get()`` for old/incomplete formats.
    Skips malformed files silently.
    """
    if not sessions_dir.exists():
        return []

    files = sorted(
        sessions_dir.glob("*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    summaries: list[SessionSummary] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            log.warning("Skipping malformed session file: %s", path)
            continue

        if not isinstance(data, dict):
            continue

        plan = data.get("plan_json") or {}
        tasks = plan.get("tasks", [])

        # Extract per-task info
        task_count = len(tasks)
        completed = sum(1 for t in tasks if t.get("status") == "completed")
        failed = sum(1 for t in tasks if t.get("status") == "failed")
        skipped = sum(1 for t in tasks if t.get("status") == "skipped")
        total_cost = sum(float(t.get("cost_usd", 0.0)) for t in tasks)
        modules = sorted(set(t.get("module", "") for t in tasks if t.get("module")))

        # Compute duration from started_at / completed_at if available
        duration = 0.0
        started_at = data.get("started_at", "")
        completed_at = data.get("completed_at", "")
        if started_at and completed_at:
            try:
                start = datetime.fromisoformat(started_at)
                end = datetime.fromisoformat(completed_at)
                duration = max(0.0, (end - start).total_seconds())
            except (ValueError, TypeError):
                pass

        # Apply module filter
        if module_filter and module_filter not in modules:
            continue

        summaries.append(
            SessionSummary(
                session_id=data.get("session_id", path.stem),
                goal=data.get("goal", ""),
                status=data.get("status", "unknown"),
                task_count=task_count,
                completed=completed,
                failed=failed,
                skipped=skipped,
                total_cost=total_cost,
                duration_seconds=duration,
                modules=modules,
                _raw_tasks=tasks,
            )
        )

    if limit is not None:
        summaries = summaries[:limit]

    return summaries


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------


@dataclass
class LogEntry:
    """A single parsed JSONL log entry."""

    timestamp: str = ""
    action: str = ""
    result: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    output: Any = None


def parse_log_entries(log_path: Path) -> list[LogEntry]:
    """Parse JSONL log file into LogEntry objects. Skips malformed lines."""
    if not log_path.exists():
        return []

    entries: list[LogEntry] = []
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        log.warning("Cannot read log file: %s", log_path)
        return []

    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        entries.append(
            LogEntry(
                timestamp=data.get("timestamp", ""),
                action=data.get("action", ""),
                result=data.get("result", ""),
                details=data.get("details", {}),
                output=data.get("output"),
            )
        )
    return entries


@dataclass
class LogMetrics:
    """Metrics aggregated from log entries."""

    dispatch_count: int = 0
    dispatch_success: int = 0
    dispatch_error: int = 0
    qa_total: int = 0
    qa_pass: int = 0
    qa_fail: int = 0


def aggregate_log_metrics(entries: list[LogEntry]) -> LogMetrics:
    """Count dispatch events and QA pass/fail from log entries."""
    metrics = LogMetrics()
    for e in entries:
        if e.action == "dispatch":
            metrics.dispatch_count += 1
            if e.result == "success":
                metrics.dispatch_success += 1
            else:
                metrics.dispatch_error += 1
        elif e.action == "quality_gate":
            metrics.qa_total += 1
            passed = e.details.get("passed", False)
            if passed:
                metrics.qa_pass += 1
            else:
                metrics.qa_fail += 1
    return metrics


# ---------------------------------------------------------------------------
# Main aggregation
# ---------------------------------------------------------------------------


def compute_aggregate_stats(
    sessions_dir: Path,
    log_path: Path | None = None,
    limit: int | None = None,
    module_filter: str | None = None,
) -> AggregateStats:
    """Compute aggregate statistics from session files and optional log.

    This is the main entry point for analytics. It reads local session JSON
    files and optionally parses the JSONL action log for QA metrics.
    """
    summaries = load_session_summaries(sessions_dir, limit=limit, module_filter=module_filter)

    stats = AggregateStats(per_session=summaries)

    if not summaries:
        return stats

    # Aggregate totals
    stats.total_cost = sum(s.total_cost for s in summaries)
    stats.total_tasks = sum(s.task_count for s in summaries)
    stats.completed = sum(s.completed for s in summaries)
    stats.failed = sum(s.failed for s in summaries)
    stats.skipped = sum(s.skipped for s in summaries)

    durations = [s.duration_seconds for s in summaries if s.duration_seconds > 0]
    stats.avg_duration = sum(durations) / len(durations) if durations else 0.0
    stats.failure_rate = stats.failed / stats.total_tasks if stats.total_tasks > 0 else 0.0

    # Per-module breakdown
    module_data: dict[str, ModuleStats] = {}
    for s in summaries:
        plan_tasks = _get_plan_tasks_from_summary(s, sessions_dir)
        for t in plan_tasks:
            mod_name = t.get("module", "")
            if not mod_name:
                continue
            if module_filter and mod_name != module_filter:
                continue
            if mod_name not in module_data:
                module_data[mod_name] = ModuleStats(name=mod_name)
            ms = module_data[mod_name]
            ms.task_count += 1
            ms.total_cost += float(t.get("cost_usd", 0.0))
            status = t.get("status", "")
            if status == "completed":
                ms.completed += 1
            elif status == "failed":
                ms.failed += 1

            # Track duration for averaging
            started = t.get("started_at")
            ended = t.get("completed_at")
            if started and ended:
                try:
                    s_dt = datetime.fromisoformat(started)
                    e_dt = datetime.fromisoformat(ended)
                    dur = max(0.0, (e_dt - s_dt).total_seconds())
                    ms.avg_duration += dur
                except (ValueError, TypeError):
                    pass

    # Finalize per-module averages
    for ms in module_data.values():
        if ms.task_count > 0:
            ms.avg_duration = ms.avg_duration / ms.task_count
            # QA pass rate from completed/total
            completed_or_failed = ms.completed + ms.failed
            ms.qa_pass_rate = ms.completed / completed_or_failed if completed_or_failed > 0 else 0.0

    stats.per_module = module_data

    # QA pass rate from log if available
    if log_path:
        log_entries = parse_log_entries(log_path)
        log_metrics = aggregate_log_metrics(log_entries)
        if log_metrics.qa_total > 0:
            stats.qa_pass_rate = log_metrics.qa_pass / log_metrics.qa_total
        else:
            # Fall back to session-level QA rate
            total_terminal = stats.completed + stats.failed
            stats.qa_pass_rate = stats.completed / total_terminal if total_terminal > 0 else 0.0
    else:
        total_terminal = stats.completed + stats.failed
        stats.qa_pass_rate = stats.completed / total_terminal if total_terminal > 0 else 0.0

    return stats


def _get_plan_tasks_from_summary(summary: SessionSummary, sessions_dir: Path) -> list[dict]:
    """Get raw task list for per-module breakdown (cached from initial load)."""
    return summary._raw_tasks
