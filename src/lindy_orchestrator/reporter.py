"""Rich terminal output formatting for the orchestrator."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def print_goal_report(
    report_text: str,
    dispatches: int = 0,
    duration: float = 0.0,
    console: Console | None = None,
) -> None:
    """Print a formatted goal completion report to the console."""
    con = console or Console()

    con.print()
    con.print(Panel(report_text, title="Orchestrator Report", border_style="green"))
    con.print()

    table = Table(title="Execution Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Total dispatches", str(dispatches))
    table.add_row("Duration", f"{duration:.1f}s")
    table.add_row("Est. cost", f"${dispatches * 2.0:.2f}")
    con.print(table)


def print_status_table(
    modules: list[dict],
    console: Console | None = None,
) -> None:
    """Print a module status overview table."""
    con = console or Console()

    table = Table(title="Module Status Overview")
    table.add_column("Module")
    table.add_column("Health")
    table.add_column("Last Updated")
    table.add_column("Active")
    table.add_column("Open Requests")
    table.add_column("Blockers")

    for mod in modules:
        health = mod.get("health", "?")
        style = {"GREEN": "green", "YELLOW": "yellow", "RED": "red"}.get(health, "white")
        table.add_row(
            mod.get("name", "?"),
            f"[{style}]{health}[/]",
            mod.get("last_updated", "?"),
            str(mod.get("active_count", 0)),
            str(mod.get("open_requests", 0)),
            str(mod.get("blocker_count", 0)),
        )

    con.print(table)
