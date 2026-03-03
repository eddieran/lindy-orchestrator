"""Garbage collection — clean up entropy from agent-generated artifacts.

Handles: stale branches, old sessions, log rotation, STATUS.md drift,
orphan plan files.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import OrchestratorConfig


@dataclass
class GCAction:
    """A single garbage collection action."""

    category: str  # stale_branch, old_session, log_rotation, status_drift, orphan_plan
    description: str
    path: str = ""
    applied: bool = False


@dataclass
class GCReport:
    """Result of a garbage collection run."""

    actions: list[GCAction] = field(default_factory=list)
    dry_run: bool = True

    @property
    def action_count(self) -> int:
        return len(self.actions)

    def by_category(self) -> dict[str, list[GCAction]]:
        result: dict[str, list[GCAction]] = {}
        for a in self.actions:
            result.setdefault(a.category, []).append(a)
        return result


def run_gc(
    config: OrchestratorConfig,
    apply: bool = False,
    max_branch_age_days: int = 14,
    max_session_age_days: int = 30,
    max_log_size_mb: int = 10,
    status_stale_days: int = 7,
) -> GCReport:
    """Run garbage collection on the orchestrator workspace.

    With apply=False (default), reports what would be cleaned.
    With apply=True, actually performs the cleanup.
    """
    report = GCReport(dry_run=not apply)

    # 1. Stale branches
    report.actions.extend(
        _find_stale_branches(config.root, config.project.branch_prefix, max_branch_age_days, apply)
    )

    # 2. Old sessions
    report.actions.extend(_find_old_sessions(config.sessions_path, max_session_age_days, apply))

    # 3. Log rotation
    report.actions.extend(_check_log_rotation(config.log_path, max_log_size_mb, apply))

    # 4. STATUS.md drift
    report.actions.extend(_check_status_drift(config, status_stale_days))

    # 5. Orphan plan files
    report.actions.extend(_find_orphan_plans(config.root, config.sessions_path))

    return report


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _find_stale_branches(
    project_root: Path,
    branch_prefix: str,
    max_age_days: int,
    apply: bool,
) -> list[GCAction]:
    """Find task branches older than max_age_days."""
    actions: list[GCAction] = []
    pattern = f"{branch_prefix}/task-*"

    try:
        result = subprocess.run(
            ["git", "branch", "--list", pattern, "--format=%(refname:short) %(committerdate:iso)"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            parts = line.strip().split(" ", 1)
            if len(parts) < 2:
                continue
            branch_name = parts[0]
            date_str = parts[1].strip()

            try:
                # Parse ISO date from git (e.g. "2024-01-15 10:30:00 +0000")
                commit_date = datetime.fromisoformat(date_str.replace(" +", "+").replace(" -", "-"))
                if commit_date.tzinfo is None:
                    commit_date = commit_date.replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                continue

            if commit_date < cutoff:
                age_days = (datetime.now(timezone.utc) - commit_date).days
                action = GCAction(
                    category="stale_branch",
                    description=f"Branch `{branch_name}` is {age_days} days old (limit: {max_age_days})",
                    path=branch_name,
                )
                if apply:
                    _delete_branch(project_root, branch_name)
                    action.applied = True
                actions.append(action)
    except (subprocess.TimeoutExpired, OSError):
        pass

    return actions


def _delete_branch(project_root: Path, branch_name: str) -> None:
    """Delete a local branch."""
    subprocess.run(
        ["git", "branch", "-d", branch_name],
        cwd=project_root,
        capture_output=True,
        timeout=10,
    )


def _find_old_sessions(
    sessions_path: Path,
    max_age_days: int,
    apply: bool,
) -> list[GCAction]:
    """Find session files older than max_age_days."""
    actions: list[GCAction] = []

    if not sessions_path.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    archive_dir = sessions_path / "archive"

    for session_file in sessions_path.glob("*.json"):
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            started = data.get("started_at", "")
            if not started:
                continue

            start_date = datetime.fromisoformat(started)
            if start_date.tzinfo is None:
                start_date = start_date.replace(tzinfo=timezone.utc)

            if start_date < cutoff:
                age_days = (datetime.now(timezone.utc) - start_date).days
                action = GCAction(
                    category="old_session",
                    description=(
                        f"Session `{session_file.stem}` is {age_days} days old "
                        f"(status: {data.get('status', '?')})"
                    ),
                    path=str(session_file),
                )
                if apply:
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(session_file), str(archive_dir / session_file.name))
                    action.applied = True
                actions.append(action)
        except (json.JSONDecodeError, OSError, ValueError):
            continue

    return actions


def _check_log_rotation(
    log_path: Path,
    max_size_mb: int,
    apply: bool,
) -> list[GCAction]:
    """Check if the action log needs rotation."""
    actions: list[GCAction] = []

    if not log_path.exists():
        return []

    size_mb = log_path.stat().st_size / (1024 * 1024)
    if size_mb > max_size_mb:
        action = GCAction(
            category="log_rotation",
            description=(f"Log file `{log_path.name}` is {size_mb:.1f}MB (limit: {max_size_mb}MB)"),
            path=str(log_path),
        )
        if apply:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            archive_path = log_path.parent / f"{log_path.stem}-{ts}{log_path.suffix}"
            shutil.move(str(log_path), str(archive_path))
            log_path.touch()  # Create fresh empty log
            action.applied = True
        actions.append(action)

    return actions


def _check_status_drift(
    config: OrchestratorConfig,
    stale_days: int,
) -> list[GCAction]:
    """Check if STATUS.md files have drifted (not updated recently)."""
    actions: list[GCAction] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)

    for mod in config.modules:
        status_path = config.status_path(mod.name)
        if not status_path.exists():
            continue

        try:
            mtime = datetime.fromtimestamp(status_path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                age_days = (datetime.now(timezone.utc) - mtime).days
                actions.append(
                    GCAction(
                        category="status_drift",
                        description=(
                            f"`{mod.name}/STATUS.md` last modified {age_days} days ago "
                            f"(limit: {stale_days})"
                        ),
                        path=str(status_path),
                    )
                )
        except OSError:
            continue

    return actions


def _find_orphan_plans(
    project_root: Path,
    sessions_path: Path,
) -> list[GCAction]:
    """Find plan JSON files not referenced by any session."""
    actions: list[GCAction] = []
    plans_dir = project_root / ".orchestrator" / "plans"

    if not plans_dir.exists():
        return []

    # Collect all plan filenames referenced by sessions
    referenced: set[str] = set()
    if sessions_path.exists():
        for session_file in sessions_path.glob("*.json"):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                if data.get("plan_json"):
                    # Sessions store plan inline, not as file refs.
                    # But we can match by goal slug
                    referenced.add(session_file.stem)
            except (json.JSONDecodeError, OSError):
                continue

    # Check plan files (skip latest.md which is always overwritten)
    for plan_file in plans_dir.glob("*.json"):
        if plan_file.name == "latest.json":
            continue

        # A plan is orphaned if it's old and there's no session with matching timestamp
        try:
            mtime = datetime.fromtimestamp(plan_file.stat().st_mtime, tz=timezone.utc)
            age_days = (datetime.now(timezone.utc) - mtime).days
            if age_days > 30:
                actions.append(
                    GCAction(
                        category="orphan_plan",
                        description=(
                            f"Plan `{plan_file.name}` is {age_days} days old "
                            f"with no recent session reference"
                        ),
                        path=str(plan_file),
                    )
                )
        except OSError:
            continue

    return actions


def format_gc_report(report: GCReport) -> str:
    """Format a GC report for display."""
    if not report.actions:
        return "No cleanup needed. Workspace is clean."

    mode = "DRY RUN" if report.dry_run else "APPLIED"
    lines = [f"GC Report ({mode}) — {report.action_count} action(s)\n"]

    by_cat = report.by_category()
    for category, actions in by_cat.items():
        label = category.replace("_", " ").title()
        lines.append(f"## {label} ({len(actions)})")
        for a in actions:
            status = "[applied]" if a.applied else "[would apply]"
            lines.append(f"  {status} {a.description}")
        lines.append("")

    if report.dry_run:
        lines.append("Run with --apply to execute these actions.")

    return "\n".join(lines)
