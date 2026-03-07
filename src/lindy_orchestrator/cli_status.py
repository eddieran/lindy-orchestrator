"""Status and logging CLI commands extracted from cli.py."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console

from .reporter import print_log_entries, print_status_table
from .status.parser import parse_status_md


def _read_log_lines(cfg, last: int) -> list[str]:
    """Read the last N log lines from the configured log path."""
    log_path = cfg.log_path
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    return lines[-last:]


def _collect_modules_data(cfg) -> list[dict]:
    """Collect module status data from STATUS.md files."""
    modules_data = []
    for mod in cfg.modules:
        path = cfg.status_path(mod.name)
        if path.exists():
            s = parse_status_md(path)
            open_reqs = [r for r in s.requests if r.status.upper() == "OPEN"]
            modules_data.append(
                {
                    "name": mod.name,
                    "health": s.meta.overall_health,
                    "last_updated": s.meta.last_updated,
                    "active_count": len(s.active_work),
                    "open_requests": len(open_reqs),
                    "blocker_count": len(s.blockers),
                    "blockers": s.blockers,
                }
            )
        else:
            modules_data.append(
                {
                    "name": mod.name,
                    "health": "?",
                    "last_updated": "N/A",
                    "active_count": 0,
                    "open_requests": 0,
                    "blocker_count": 0,
                }
            )
    return modules_data


def _collect_mailbox_data(cfg) -> dict[str, int]:
    """Collect pending mailbox message counts per module."""
    from .mailbox import Mailbox

    mailbox_dir = cfg.root / cfg.mailbox.dir
    if not mailbox_dir.exists():
        return {}
    mb = Mailbox(mailbox_dir)
    counts: dict[str, int] = {}
    for mod in cfg.modules:
        counts[mod.name] = mb.pending_count(mod.name)
    return counts


def register_status_commands(app: typer.Typer, console: Console, load_cfg) -> None:
    """Register status and logs commands on the Typer app."""

    def _print_mailbox_summary(cfg) -> None:
        """Print mailbox pending counts per module."""
        counts = _collect_mailbox_data(cfg)
        if not counts:
            return
        has_pending = any(c > 0 for c in counts.values())
        if has_pending:
            console.print("\n[bold]Mailbox[/]")
            for name, count in counts.items():
                if count > 0:
                    console.print(f"  [bold]{name}[/]: {count} pending message(s)")
        else:
            console.print("\n[dim]Mailbox: no pending messages[/]")

    @app.command()
    def status(
        config: Optional[str] = typer.Option(None, "-c", "--config"),
        as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
        last: int = typer.Option(10, "-n", "--last", help="Number of recent log entries to show"),
        logs_only: bool = typer.Option(False, "--logs-only", help="Show only recent log entries"),
        status_only: bool = typer.Option(
            False, "--status-only", help="Show only module status table"
        ),
    ) -> None:
        """Show module status overview and recent log entries.

        By default shows both the module health table and recent important logs.
        Use --status-only or --logs-only to show just one section.
        """
        cfg = load_cfg(config)

        show_status = not logs_only
        show_logs = not status_only

        if as_json:
            result: dict = {}
            if show_status:
                result["modules"] = _collect_modules_data(cfg)
            if show_logs:
                result["logs"] = _read_log_lines(cfg, last)
            if show_status and cfg.mailbox.enabled:
                result["mailbox"] = _collect_mailbox_data(cfg)
            console.print_json(json.dumps(result, indent=2))
            return

        if show_status:
            modules_data = _collect_modules_data(cfg)
            print_status_table(modules_data)

        if show_status and cfg.mailbox.enabled:
            _print_mailbox_summary(cfg)

        if show_logs:
            log_lines = _read_log_lines(cfg, last)
            print_log_entries(log_lines, console=console)

    @app.command(hidden=True)
    def logs(
        last: int = typer.Option(20, "-n", "--last", help="Show last N entries"),
        config: Optional[str] = typer.Option(None, "-c", "--config"),
        as_json: bool = typer.Option(False, "--json", help="Output raw JSONL"),
    ) -> None:
        """Show recent action logs (alias for 'status --logs-only')."""
        status(config=config, as_json=as_json, last=last, logs_only=True, status_only=False)
