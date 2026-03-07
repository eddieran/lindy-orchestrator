"""Rich terminal output formatting for the orchestrator."""

from __future__ import annotations

import time

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text


class PlanProgress:
    """Live planning progress display with spinner, timer, event count, and phase.

    When stdout is not a TTY (CI, piped output), falls back to simple print lines.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._interactive = self._console.is_terminal
        self._start = time.monotonic()
        self._event_count = 0
        self._phase = "Initializing..."
        self._live: Live | None = None
        self._last_print_time = 0.0

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def set_phase(self, phase: str) -> None:
        self._phase = phase
        if not self._interactive:
            self._console.print(f"  [dim]{phase}[/]")

    def tick_event(self) -> None:
        self._event_count += 1

    def _build_display(self) -> Text:
        elapsed = self.elapsed
        mins, secs = divmod(int(elapsed), 60)
        parts = Text.assemble(
            ("  ", ""),
        )
        spinner = Spinner("dots")
        parts.append_text(spinner.render(time.monotonic()))
        parts.append(f" {self._phase}  ")
        parts.append(f"{mins}:{secs:02d}", style="bold cyan")
        parts.append(f"  events: {self._event_count}", style="dim")
        return parts

    def __rich__(self) -> Text:
        """Rich renderable protocol — called on each Live refresh cycle."""
        return self._build_display()

    def start(self) -> None:
        self._start = time.monotonic()
        if self._interactive:
            self._live = Live(
                self,
                console=self._console,
                refresh_per_second=4,
                transient=True,
            )
            self._live.start()
        else:
            self._console.print(f"  [dim]{self._phase}[/]")

    def update(self) -> None:
        if self._interactive and self._live is not None:
            self._live.refresh()
        elif not self._interactive:
            now = time.monotonic()
            if now - self._last_print_time >= 30:
                elapsed = self.elapsed
                mins, secs = divmod(int(elapsed), 60)
                self._console.print(
                    f"  [dim]... {self._phase}: {self._event_count} events, {mins}m{secs:02d}s[/]"
                )
                self._last_print_time = now

    def stop(self, final_message: str | None = None) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
        elapsed = self.elapsed
        mins, secs = divmod(int(elapsed), 60)
        msg = final_message or (
            f"Plan generated in {mins}m{secs:02d}s ({self._event_count} events)"
        )
        self._console.print(f"  [dim]{msg}[/]")


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


def print_log_entries(
    lines: list[str],
    console: Console | None = None,
) -> None:
    """Print formatted log entries below the status table."""
    import json

    con = console or Console()

    con.print()
    con.print("[bold]Recent Logs[/]")

    if not lines:
        con.print("  [dim]No log entries.[/]")
        return

    for line in lines:
        try:
            entry = json.loads(line)
            ts = entry.get("timestamp", "")[:19]
            action = entry.get("action", "?")
            result = entry.get("result", "?")

            color = {"success": "green", "error": "red", "fail": "red", "pass": "green"}.get(
                result, "yellow"
            )
            con.print(f"  [{color}]{result:>7}[/] {ts} {action}")

            details = entry.get("details", {})
            if details:
                for k, v in list(details.items())[:3]:
                    con.print(f"          {k}: {v}")
        except json.JSONDecodeError:
            con.print(f"  [dim]{line[:100]}[/]")
