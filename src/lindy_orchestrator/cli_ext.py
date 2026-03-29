"""Extension commands for lindy-orchestrate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from .cli_helpers import load_cfg as _load_cfg
from .providers.claude_cli import find_claude_cli
from .session import SessionManager
from .status.parser import parse_status_md

_DETAIL_PRIORITY = (
    "goal",
    "description",
    "reason",
    "decision",
    "gate",
    "passed",
    "output",
    "text",
    "phase",
    "total_dispatches",
    "has_failures",
)


def _load_jsonl_entries(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of mapping entries."""
    entries: list[dict[str, Any]] = []
    if not path.exists():
        return entries

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            entries.append({"event": "invalid_json", "raw": line[:120]})
            continue

        if isinstance(data, dict):
            entries.append(data)
        else:
            entries.append({"event": "value", "value": data})

    return entries


def _entry_task_id(entry: dict[str, Any]) -> int | None:
    """Return an integer task id when the entry exposes one."""
    task_id = entry.get("task_id")
    if isinstance(task_id, bool):
        return None
    if isinstance(task_id, int):
        return task_id
    if isinstance(task_id, str) and task_id.strip().isdigit():
        return int(task_id)
    return None


def _is_failure_entry(entry: dict[str, Any]) -> bool:
    """Return whether an entry represents a failure-oriented event."""
    event = str(entry.get("event", ""))
    return (
        entry.get("status") == "failed"
        or event in {"retry_decision", "qa_failed", "task_failed"}
        or (event == "qa_detail" and entry.get("passed") is False)
    )


def _matches_filters(
    entry: dict[str, Any],
    *,
    task_id: int | None,
    failures_only: bool,
) -> bool:
    """Return whether the entry should be displayed."""
    if task_id is not None and _entry_task_id(entry) != task_id:
        return False
    if failures_only and not _is_failure_entry(entry):
        return False
    return True


def _format_timestamp(raw: Any) -> str:
    """Format an ISO-ish timestamp for compact table display."""
    if not isinstance(raw, str) or not raw:
        return "-"
    return raw.replace("T", " ")[:19]


def _stringify_detail(value: Any) -> str:
    """Serialize an arbitrary JSON-ish value into a short table cell."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def _entry_detail(entry: dict[str, Any]) -> str:
    """Summarize non-core entry fields for the details column."""
    details: list[str] = []
    seen: set[str] = set()

    for key in _DETAIL_PRIORITY:
        if key in entry:
            details.append(f"{key}={_stringify_detail(entry[key])}")
            seen.add(key)

    for key in sorted(entry):
        if key in {"ts", "timestamp", "level", "event", "task_id", "module", "status"} | seen:
            continue
        details.append(f"{key}={_stringify_detail(entry[key])}")

    if not details:
        return "-"

    text = " | ".join(details)
    return text if len(text) <= 120 else f"{text[:117]}..."


def _render_session_overview(
    console: Console,
    session,
    *,
    summary_entries: list[dict[str, Any]],
    task_id: int | None,
    failures_only: bool,
) -> None:
    """Render the top-level session overview table."""
    table = Table(title="Session Overview", box=box.SIMPLE_HEAD, show_header=False)
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")

    task_ids = {
        _entry_task_id(entry) for entry in summary_entries if _entry_task_id(entry) is not None
    }
    failed_events = sum(1 for entry in summary_entries if entry.get("status") == "failed")
    filters = []
    if task_id is not None:
        filters.append(f"task={task_id}")
    if failures_only:
        filters.append("failures")

    table.add_row("Session", session.session_id)
    table.add_row("Goal", session.goal or "-")
    table.add_row("Status", session.status)
    table.add_row("Started", _format_timestamp(session.started_at))
    table.add_row("Completed", _format_timestamp(session.completed_at))
    table.add_row("Checkpoints", str(session.checkpoint_count))
    table.add_row("Tracked tasks", str(len(task_ids)))
    table.add_row("Failed events", str(failed_events))
    table.add_row("Filters", ", ".join(filters) if filters else "none")

    console.print(table)


def _render_level_table(
    console: Console,
    *,
    title: str,
    path: Path,
    task_id: int | None,
    failures_only: bool,
) -> None:
    """Render one observability level as a Rich table."""
    console.print()

    if not path.exists():
        console.print(f"[yellow]{title}: no data at this level[/]")
        return

    entries = [
        entry
        for entry in _load_jsonl_entries(path)
        if _matches_filters(entry, task_id=task_id, failures_only=failures_only)
    ]
    if not entries:
        console.print(f"[dim]{title}: no matching events[/]")
        return

    table = Table(title=title, box=box.SIMPLE_HEAD)
    table.add_column("Time", no_wrap=True)
    table.add_column("Task", justify="right", no_wrap=True)
    table.add_column("Module", style="bold")
    table.add_column("Event")
    table.add_column("Status", no_wrap=True)
    table.add_column("Details", overflow="fold")

    for entry in entries:
        table.add_row(
            _format_timestamp(entry.get("ts") or entry.get("timestamp")),
            str(_entry_task_id(entry) or "-"),
            str(entry.get("module", "-")),
            str(entry.get("event", "-")),
            str(entry.get("status", "-")),
            _entry_detail(entry),
        )

    console.print(table)


def register_ext_commands(app: typer.Typer, console: Console) -> None:
    """Register extension commands on the Typer app."""

    # -------------------------------------------------------------------
    # inspect
    # -------------------------------------------------------------------

    @app.command()
    def inspect(
        session_id: str = typer.Argument(..., help="Session ID to inspect"),
        config: Optional[str] = typer.Option(None, "-c", "--config"),
        decisions: bool = typer.Option(False, "--decisions", help="Show L2 decision events"),
        full: bool = typer.Option(False, "--full", help="Show L3 transcript events"),
        task: Optional[int] = typer.Option(None, "--task", help="Only show events for task N"),
        failures: bool = typer.Option(
            False,
            "--failures",
            help="Only show failed task completions, retry decisions, and failed QA details",
        ),
    ) -> None:
        """Inspect a session's layered observability logs."""
        cfg = _load_cfg(config)
        sessions = SessionManager(cfg.sessions_path)
        session = sessions.load(session_id)

        if session is None:
            console.print(f"[red]Session not found: {session_id}[/]")
            raise typer.Exit(1)

        session_dir = cfg.sessions_path / session_id
        summary_path = session_dir / "summary.jsonl"
        decisions_path = session_dir / "decisions.jsonl"
        transcript_path = session_dir / "transcript.jsonl"

        _render_session_overview(
            console,
            session,
            summary_entries=_load_jsonl_entries(summary_path),
            task_id=task,
            failures_only=failures,
        )
        _render_level_table(
            console,
            title="L1 Summary",
            path=summary_path,
            task_id=task,
            failures_only=failures,
        )

        if decisions or full:
            _render_level_table(
                console,
                title="L2 Decisions",
                path=decisions_path,
                task_id=task,
                failures_only=failures,
            )

        if full:
            _render_level_table(
                console,
                title="L3 Transcript",
                path=transcript_path,
                task_id=task,
                failures_only=failures,
            )

    # -------------------------------------------------------------------
    # gc
    # -------------------------------------------------------------------

    @app.command()
    def gc(
        config: Optional[str] = typer.Option(None, "-c", "--config"),
        apply: bool = typer.Option(
            False, "--apply", help="Actually perform cleanup (default: dry run)"
        ),
        branch_age: int = typer.Option(14, "--branch-age", help="Max age for task branches (days)"),
        session_age: Optional[int] = typer.Option(
            None,
            "--session-age",
            help="Override session retention age (days); defaults to observability.retention_days",
        ),
        log_size: int = typer.Option(10, "--log-size", help="Max log file size (MB)"),
        status_stale: int = typer.Option(
            7, "--status-stale", help="STATUS.md stale threshold (days)"
        ),
    ):
        """Clean up stale branches, old sessions, oversized logs, and orphan plans.

        Runs in dry-run mode by default. Use --apply to execute cleanup.
        """
        from .gc import format_gc_report, run_gc

        cfg = _load_cfg(config)
        mode = "[red]APPLY[/]" if apply else "[yellow]DRY RUN[/]"
        console.print(f"[bold]lindy-orchestrate gc[/] — {mode}\n")

        report = run_gc(
            cfg,
            apply=apply,
            max_branch_age_days=branch_age,
            max_session_age_days=session_age,
            max_log_size_mb=log_size,
            status_stale_days=status_stale,
        )

        console.print(format_gc_report(report))

        if not report.actions:
            console.print("[bold green]Workspace is clean.[/]")
        elif not apply:
            console.print(
                f"\n[yellow]{report.action_count} action(s) found.[/] Run with --apply to execute."
            )

    # -------------------------------------------------------------------
    # scan
    # -------------------------------------------------------------------

    @app.command()
    def scan(
        config: Optional[str] = typer.Option(None, "-c", "--config"),
        module: Optional[str] = typer.Option(None, "--module", help="Scan specific module"),
        grade_only: bool = typer.Option(False, "--grade-only", help="Only show grades"),
    ):
        """Scan for entropy: architecture drift, contract violations, quality decay."""
        from .entropy.scanner import format_scan_report, run_scan

        cfg = _load_cfg(config)

        console.print("[bold]lindy-orchestrate scan[/] — Entropy Scanner\n")

        report = run_scan(cfg, module_filter=module)

        output = format_scan_report(report, grade_only=grade_only)
        console.print(output)

        error_count = len([f for f in report.findings if f.severity == "error"])
        warning_count = len([f for f in report.findings if f.severity == "warning"])

        if error_count:
            console.print(f"\n[red]{error_count} error(s), {warning_count} warning(s)[/]")
        elif warning_count:
            console.print(f"\n[yellow]{warning_count} warning(s)[/]")
        else:
            console.print("\n[bold green]No issues found.[/]")

    # -------------------------------------------------------------------
    # validate
    # -------------------------------------------------------------------

    @app.command()
    def validate(
        config: Optional[str] = typer.Option(None, "-c", "--config"),
    ):
        """Validate config and STATUS.md files."""
        try:
            cfg = _load_cfg(config)
        except Exception as e:
            console.print(f"[red]Config error: {e}[/]")
            raise typer.Exit(1)

        console.print(f"[green]Config valid[/]: {len(cfg.modules)} modules")

        errors = 0
        for mod in cfg.modules:
            mod_path = cfg.module_path(mod.name)
            if not mod_path.exists():
                console.print(f"  [red]Module path missing: {mod_path}[/]")
                errors += 1
            else:
                console.print(f"  [green]{mod.name}[/]: {mod_path}")

            status_path = cfg.status_path(mod.name)
            if not status_path.exists():
                console.print(f"    [yellow]STATUS.md missing: {status_path}[/]")
            else:
                s = parse_status_md(status_path)
                console.print(
                    f"    STATUS.md: health={s.meta.overall_health}, "
                    f"active={len(s.active_work)}, blockers={len(s.blockers)}"
                )

        # Check claude CLI
        if find_claude_cli():
            console.print("[green]Claude CLI found[/]")
        else:
            console.print("[yellow]Claude CLI not found in PATH[/]")

        if errors:
            console.print(f"\n[red]{errors} error(s) found.[/]")
            raise typer.Exit(1)
        else:
            console.print("\n[bold green]All checks passed.[/]")
