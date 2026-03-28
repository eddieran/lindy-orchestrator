"""Rich terminal output formatting for the orchestrator."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .dag import truncate_goal
from .models import TaskSpec, TaskPlan, TaskStatus


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


# ---------------------------------------------------------------------------
# Execution summary helpers
# ---------------------------------------------------------------------------

_STATUS_STYLE = {
    TaskStatus.COMPLETED: ("green", "PASS"),
    TaskStatus.FAILED: ("red", "FAIL"),
    TaskStatus.SKIPPED: ("yellow", "SKIP"),
    TaskStatus.PENDING: ("dim", "PEND"),
    TaskStatus.IN_PROGRESS: ("cyan", "RUN"),
}


def _task_duration(task: TaskSpec) -> float | None:
    """Calculate task duration in seconds from timestamps, or None."""
    if not task.started_at or not task.completed_at:
        return None
    try:
        start = datetime.fromisoformat(task.started_at)
        end = datetime.fromisoformat(task.completed_at)
        return (end - start).total_seconds()
    except (ValueError, TypeError):
        return None


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}m{secs:02d}s"


def _qa_summary(task: TaskSpec) -> str:
    """One-line QA summary for a task."""
    if not task.qa_results:
        return "-"
    parts = []
    for r in task.qa_results:
        tag = "PASS" if r.passed else "FAIL"
        parts.append(f"{r.gate}:{tag}")
    return ", ".join(parts)


def generate_execution_summary(
    plan: TaskPlan,
    duration: float,
    session_id: str,
    console: Console | None = None,
) -> None:
    """Print a detailed per-task execution summary to the console."""
    con = console or Console()

    completed = [t for t in plan.tasks if t.status == TaskStatus.COMPLETED]
    failed = [t for t in plan.tasks if t.status == TaskStatus.FAILED]
    skipped = [t for t in plan.tasks if t.status == TaskStatus.SKIPPED]

    # Header panel
    if failed:
        title = f"GOAL PAUSED: {truncate_goal(plan.goal)}"
        border = "red"
    else:
        title = f"GOAL COMPLETED: {truncate_goal(plan.goal)}"
        border = "green"

    header_lines = [
        f"Session: {session_id}",
        f"Tasks: {len(completed)} passed, {len(failed)} failed, "
        f"{len(skipped)} skipped / {len(plan.tasks)} total",
        f"Duration: {_format_duration(duration)}",
    ]
    con.print()
    con.print(Panel("\n".join(header_lines), title=title, border_style=border))

    # Per-task detail table
    table = Table(title="Task Details", show_lines=True)
    table.add_column("#", style="bold", width=3)
    table.add_column("Module", style="cyan", width=14)
    table.add_column("Description", min_width=20)
    table.add_column("Status", width=6, justify="center")
    table.add_column("Duration", width=8, justify="right")
    table.add_column("Retries", width=7, justify="center")
    table.add_column("Cost", width=7, justify="right")
    table.add_column("QA Results", min_width=16)
    table.add_column("Output", min_width=20, max_width=40)

    for task in plan.tasks:
        style, label = _STATUS_STYLE.get(task.status, ("white", "?"))
        dur = _task_duration(task)
        cost = f"${task.cost_usd:.2f}" if task.cost_usd > 0 else "-"
        output_preview = (task.result or "")[:120].replace("\n", " ")
        if len(task.result or "") > 120:
            output_preview += "..."
        table.add_row(
            str(task.id),
            task.module,
            task.description,
            f"[{style}]{label}[/]",
            _format_duration(dur),
            str(task.retries) if task.retries else "-",
            cost,
            _qa_summary(task),
            output_preview or "-",
        )

    con.print(table)

    # Overall metrics table (kept for backward compat feel)
    metrics = Table(title="Execution Metrics")
    metrics.add_column("Metric", style="bold")
    metrics.add_column("Value", justify="right")
    metrics.add_row("Total tasks", str(len(plan.tasks)))
    metrics.add_row("Completed", f"[green]{len(completed)}[/]")
    metrics.add_row("Failed", f"[red]{len(failed)}[/]" if failed else "0")
    metrics.add_row("Skipped", str(len(skipped)))
    metrics.add_row("Total duration", _format_duration(duration))
    total_cost = sum(t.cost_usd for t in plan.tasks)
    if total_cost > 0:
        metrics.add_row("Cost", f"${total_cost:.2f}")
    else:
        metrics.add_row("Est. cost", f"${len(plan.tasks) * 2.0:.2f}")
    con.print(metrics)


def save_summary_report(
    plan: TaskPlan,
    duration: float,
    session_id: str,
    root: Path,
) -> Path:
    """Save a Markdown execution summary to .orchestrator/reports/."""
    reports_dir = root / ".orchestrator" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{session_id}_summary.md"

    completed = [t for t in plan.tasks if t.status == TaskStatus.COMPLETED]
    failed = [t for t in plan.tasks if t.status == TaskStatus.FAILED]
    skipped = [t for t in plan.tasks if t.status == TaskStatus.SKIPPED]

    status_word = "PAUSED" if failed else "COMPLETED"
    total_cost = sum(t.cost_usd for t in plan.tasks)
    cost_str = f"${total_cost:.2f}" if total_cost > 0 else f"~${len(plan.tasks) * 2.0:.2f} (est.)"
    lines = [
        "# Execution Summary",
        "",
        f"- **Goal**: {plan.goal}",
        f"- **Status**: {status_word}",
        f"- **Session**: {session_id}",
        f"- **Duration**: {_format_duration(duration)}",
        f"- **Cost**: {cost_str}",
        f"- **Tasks**: {len(completed)} passed, {len(failed)} failed, "
        f"{len(skipped)} skipped / {len(plan.tasks)} total",
        "",
        "## Task Details",
        "",
        "| # | Module | Description | Status | Duration | Cost | Retries | QA |",
        "|---|--------|-------------|--------|----------|------|---------|-----|",
    ]

    for task in plan.tasks:
        _, label = _STATUS_STYLE.get(task.status, ("white", "?"))
        dur = _format_duration(_task_duration(task))
        qa = _qa_summary(task)
        retries = str(task.retries) if task.retries else "-"
        cost = f"${task.cost_usd:.2f}" if task.cost_usd > 0 else "-"
        lines.append(
            f"| {task.id} | {task.module} | {task.description} "
            f"| {label} | {dur} | {cost} | {retries} | {qa} |"
        )

    lines.append("")

    # Per-task output sections for tasks with results
    for task in plan.tasks:
        if not task.result:
            continue
        _, label = _STATUS_STYLE.get(task.status, ("white", "?"))
        lines.append(f"### Task {task.id}: [{task.module}] {task.description} ({label})")
        lines.append("")
        if task.qa_results:
            lines.append("**QA Results:**")
            for r in task.qa_results:
                tag = "PASS" if r.passed else "FAIL"
                output_line = r.output[:200].replace("\n", " ") if r.output else ""
                lines.append(f"- {r.gate}: **{tag}** — {output_line}")
            lines.append("")
        preview = task.result[:500]
        lines.append("**Output preview:**")
        lines.append("```")
        lines.append(preview)
        lines.append("```")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
