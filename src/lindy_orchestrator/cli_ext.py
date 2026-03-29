"""Extension commands for lindy-orchestrate."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from .cli_helpers import load_cfg as _load_cfg
from .providers.claude_cli import find_claude_cli
from .status.parser import parse_status_md


def register_ext_commands(app: typer.Typer, console: Console) -> None:
    """Register extension commands on the Typer app."""

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
