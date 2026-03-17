"""CLI stats command — cross-session analytics and cost reporting."""

from __future__ import annotations

import dataclasses
import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .analytics import compute_aggregate_stats


def register_stats_command(app: typer.Typer, console: Console, load_cfg) -> None:
    """Register the 'stats' command on the Typer app."""

    @app.command()
    def stats(
        limit: Optional[int] = typer.Option(
            None, "-n", "--limit", help="Limit to N most recent sessions"
        ),
        module: Optional[str] = typer.Option(None, "--module", help="Filter by module name"),
        as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
        cost_only: bool = typer.Option(False, "--cost-only", help="Show only cost summary"),
        config: Optional[str] = typer.Option(None, "-c", "--config", help="Config YAML path"),
    ) -> None:
        """Show cross-session analytics: costs, task stats, and per-module breakdown."""
        cfg = load_cfg(config)

        agg = compute_aggregate_stats(
            sessions_dir=cfg.sessions_path,
            log_path=cfg.log_path if cfg.log_path.exists() else None,
            limit=limit,
            module_filter=module,
        )

        if not agg.per_session:
            if as_json:
                console.print_json(json.dumps({"sessions": [], "message": "No sessions found"}))
            else:
                console.print("[yellow]No sessions found.[/]")
            return

        if as_json:
            _print_json(console, agg)
            return

        if cost_only:
            _print_cost_table(console, agg)
            return

        # Full output: header, per-module, recent sessions
        _print_aggregate_header(console, agg)
        _print_module_table(console, agg)
        _print_sessions_table(console, agg)


def _print_json(console: Console, agg) -> None:
    """Output aggregate stats as JSON."""
    data = {
        "total_cost": round(agg.total_cost, 4),
        "total_tasks": agg.total_tasks,
        "completed": agg.completed,
        "failed": agg.failed,
        "skipped": agg.skipped,
        "qa_pass_rate": round(agg.qa_pass_rate, 4),
        "avg_duration": round(agg.avg_duration, 1),
        "failure_rate": round(agg.failure_rate, 4),
        "per_module": {name: dataclasses.asdict(ms) for name, ms in agg.per_module.items()},
        "sessions": [dataclasses.asdict(s) for s in agg.per_session],
    }
    console.print_json(json.dumps(data, default=str))


def _print_cost_table(console: Console, agg) -> None:
    """Show cost-focused summary table."""
    console.print(f"\n[bold]Total Cost:[/] ${agg.total_cost:.4f}")
    console.print(f"[bold]Sessions:[/] {len(agg.per_session)}\n")

    if agg.per_module:
        table = Table(title="Cost by Module")
        table.add_column("Module", style="bold")
        table.add_column("Cost", justify="right")
        table.add_column("Tasks", justify="right")

        for name, ms in sorted(agg.per_module.items()):
            table.add_row(name, f"${ms.total_cost:.4f}", str(ms.task_count))

        console.print(table)


def _print_aggregate_header(console: Console, agg) -> None:
    """Print high-level aggregate stats."""
    console.print("\n[bold]Aggregate Stats[/]")
    console.print(f"  Sessions: {len(agg.per_session)}")
    console.print(f"  Total tasks: {agg.total_tasks}")
    console.print(f"  Completed: {agg.completed}  Failed: {agg.failed}  Skipped: {agg.skipped}")
    console.print(f"  Total cost: ${agg.total_cost:.4f}")
    console.print(f"  QA pass rate: {agg.qa_pass_rate:.0%}")
    console.print(f"  Avg duration: {agg.avg_duration:.1f}s")
    console.print(f"  Failure rate: {agg.failure_rate:.0%}")
    console.print()


def _print_module_table(console: Console, agg) -> None:
    """Print per-module breakdown table."""
    if not agg.per_module:
        return

    table = Table(title="Per-Module Breakdown")
    table.add_column("Module", style="bold")
    table.add_column("Tasks", justify="right")
    table.add_column("Completed", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("QA Rate", justify="right")
    table.add_column("Avg Dur", justify="right")

    for name, ms in sorted(agg.per_module.items()):
        table.add_row(
            name,
            str(ms.task_count),
            str(ms.completed),
            str(ms.failed),
            f"${ms.total_cost:.4f}",
            f"{ms.qa_pass_rate:.0%}",
            f"{ms.avg_duration:.1f}s",
        )

    console.print(table)
    console.print()


def _print_sessions_table(console: Console, agg) -> None:
    """Print recent sessions table."""
    table = Table(title="Recent Sessions")
    table.add_column("Session", style="bold")
    table.add_column("Status")
    table.add_column("Tasks", justify="right")
    table.add_column("Done", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Goal", max_width=40)

    for s in agg.per_session:
        status_style = {
            "completed": "green",
            "failed": "red",
            "paused": "yellow",
        }.get(s.status, "dim")

        goal_display = s.goal[:40] + "..." if len(s.goal) > 40 else s.goal

        table.add_row(
            s.session_id,
            f"[{status_style}]{s.status}[/]",
            str(s.task_count),
            str(s.completed),
            str(s.failed),
            f"${s.total_cost:.4f}",
            goal_display,
        )

    console.print(table)
