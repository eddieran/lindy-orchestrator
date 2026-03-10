"""Live DAG dashboard for task execution.

Renders a Rich Live panel showing the task DAG as a compact ASCII tree
with real-time status icons and optional annotation bubbles.  Falls back
to text-based progress when not on a TTY.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .dag import STATUS_ICONS, render_dag
from .hooks import Event, EventType, HookRegistry
from .models import TaskPlan, TaskStatus


@dataclass
class _TaskDetail:
    """Per-task runtime detail for the verbose dashboard view."""

    tool_trail: list[str] = field(default_factory=list)
    reasoning: str = ""
    event_count: int = 0
    started_at: float = 0.0

    def add_tool(self, name: str) -> None:
        self.tool_trail.append(name)
        if len(self.tool_trail) > 5:
            self.tool_trail = self.tool_trail[-5:]

    def set_reasoning(self, text: str) -> None:
        collapsed = " ".join(text.split())
        self.reasoning = collapsed[:80]


class Dashboard:
    """Live-updating DAG dashboard driven by hook events.

    Subscribes to HookRegistry events and re-renders the DAG tree panel
    on every state change.  Falls back to plain text on non-TTY.

    When *verbose* is True, annotation bubbles show latest tool use or
    status snippets next to active/recently-completed tasks.
    """

    def __init__(
        self,
        plan: TaskPlan,
        hooks: HookRegistry,
        console: Console | None = None,
        verbose: bool = False,
    ) -> None:
        self._plan = plan
        self._hooks = hooks
        self._console = console or Console()
        self._verbose = verbose
        self._interactive = self._console.is_terminal
        self._live: Live | None = None
        self._start_time = time.monotonic()
        self._lock = threading.Lock()

        # Per-task annotation strings (shown when verbose)
        self._annotations: dict[int, str] = {}
        # Per-task runtime details (shown when verbose)
        self._task_details: dict[int, _TaskDetail] = {}

    # -- public API -----------------------------------------------------------

    def start(self) -> None:
        """Start the live dashboard and subscribe to hook events."""
        self._start_time = time.monotonic()
        self._hooks.on(EventType.TASK_STARTED, self._on_task_started)
        self._hooks.on(EventType.TASK_COMPLETED, self._on_task_completed)
        self._hooks.on(EventType.TASK_FAILED, self._on_task_failed)
        self._hooks.on(EventType.TASK_RETRYING, self._on_task_retrying)
        self._hooks.on(EventType.TASK_SKIPPED, self._on_task_skipped)
        self._hooks.on(EventType.TASK_HEARTBEAT, self._on_heartbeat)
        self._hooks.on(EventType.QA_PASSED, self._on_qa_event)
        self._hooks.on(EventType.QA_FAILED, self._on_qa_event)
        self._hooks.on(EventType.CHECKPOINT_SAVED, self._on_generic)
        self._hooks.on(EventType.STALL_WARNING, self._on_generic)

        if self._interactive:
            self._live = Live(
                self._build_panel(),
                console=self._console,
                refresh_per_second=4,
                transient=True,
            )
            self._live.start()

    def stop(self) -> None:
        """Stop the live display and print the final DAG state."""
        if self._live is not None:
            self._live.stop()
            self._live = None
        # Print final static DAG
        self._console.print(self._build_panel(final=True))

    def update_annotation(self, task_id: int, message: str) -> None:
        """Set the annotation bubble for *task_id*."""
        with self._lock:
            self._annotations[task_id] = message
        self._refresh()

    # -- event handlers -------------------------------------------------------

    def _on_task_started(self, event: Event) -> None:
        with self._lock:
            if event.task_id is not None:
                self._annotations[event.task_id] = "starting\u2026"
                self._task_details[event.task_id] = _TaskDetail(started_at=time.monotonic())
        self._refresh()

    def _on_task_completed(self, event: Event) -> None:
        with self._lock:
            if event.task_id is not None:
                self._annotations[event.task_id] = "done"
        self._refresh()

    def _on_task_failed(self, event: Event) -> None:
        with self._lock:
            if event.task_id is not None:
                reason = event.data.get("reason", "failed")
                self._annotations[event.task_id] = reason
        self._refresh()

    def _on_task_retrying(self, event: Event) -> None:
        with self._lock:
            if event.task_id is not None:
                retry = event.data.get("retry", "?")
                self._annotations[event.task_id] = f"retry {retry}"
        self._refresh()

    def _on_task_skipped(self, event: Event) -> None:
        with self._lock:
            if event.task_id is not None:
                self._annotations[event.task_id] = "skipped"
        self._refresh()

    def _on_heartbeat(self, event: Event) -> None:
        with self._lock:
            if event.task_id is not None:
                tool = event.data.get("tool", "")
                if tool:
                    self._annotations[event.task_id] = f"tool: {tool}"
                detail = self._task_details.get(event.task_id)
                if detail:
                    if tool:
                        detail.add_tool(tool)
                    detail.event_count = event.data.get("event_count", detail.event_count)
                    reasoning = event.data.get("reasoning", "")
                    if reasoning:
                        detail.set_reasoning(reasoning)
        self._refresh()

    def _on_qa_event(self, event: Event) -> None:
        with self._lock:
            if event.task_id is not None:
                gate = event.data.get("gate", "qa")
                passed = event.type == EventType.QA_PASSED
                self._annotations[event.task_id] = (
                    f"QA {gate}: pass" if passed else f"QA {gate}: fail"
                )
        self._refresh()

    def _on_generic(self, event: Event) -> None:
        self._refresh()

    # -- rendering ------------------------------------------------------------

    def _refresh(self) -> None:
        if self._interactive and self._live is not None:
            self._live.update(self._build_panel())

    def _build_panel(self, final: bool = False) -> Panel:
        """Build the full dashboard panel."""
        with self._lock:
            annotations = dict(self._annotations)
            task_details = dict(self._task_details)

        dag_text = render_dag(
            self._plan,
            annotations=annotations if self._verbose else None,
            verbose=self._verbose,
        )
        summary = self._build_summary()

        parts = [dag_text, Text(""), summary]

        if self._verbose and not final:
            detail_section = self._build_detail_section(task_details)
            if detail_section:
                parts.append(Text(""))
                parts.append(detail_section)

        title = "Execution Complete" if final else "Executing"
        border = "green" if final and not self._plan.has_failures() else "cyan"
        if final and self._plan.has_failures():
            border = "red"

        return Panel(
            Group(*parts),
            title=title,
            border_style=border,
        )

    def _build_detail_section(self, task_details: dict[int, _TaskDetail]) -> Text | None:
        """Build a per-task detail section for running tasks."""
        running = [t for t in self._plan.tasks if t.status == TaskStatus.IN_PROGRESS]
        if not running:
            return None

        now = time.monotonic()
        text = Text()
        text.append("  Active tasks:\n", style="bold dim")

        for task in running:
            detail = task_details.get(task.id)
            if not detail:
                continue

            elapsed = now - detail.started_at
            mins, secs = divmod(int(elapsed), 60)
            elapsed_str = f"{mins}m {secs:02d}s"

            text.append(f"  {STATUS_ICONS[TaskStatus.IN_PROGRESS]} ", style="bold blue")
            text.append(f"task-{task.id}", style="bold")
            text.append(f" [{elapsed_str}] ", style="cyan")

            if detail.tool_trail:
                trail = " \u2192 ".join(detail.tool_trail)
                text.append(trail, style="dim")

            text.append(f" ({detail.event_count} events)", style="dim")

            if detail.reasoning:
                snippet = detail.reasoning[:60]
                if len(detail.reasoning) > 60:
                    snippet += "\u2026"
                text.append(f' "{snippet}"', style="italic dim")

            text.append("\n")

        return text

    def _build_summary(self) -> Text:
        """Build the summary bar with task counts and elapsed time."""
        counts = _count_statuses(self._plan)
        elapsed = time.monotonic() - self._start_time
        mins, secs = divmod(int(elapsed), 60)

        text = Text()
        text.append("  ")
        text.append(f"{STATUS_ICONS[TaskStatus.COMPLETED]}", style="green")
        text.append(f" {counts['completed']} completed", style="green")
        text.append("  ", style="dim")
        text.append(f"{STATUS_ICONS[TaskStatus.FAILED]}", style="red")
        text.append(f" {counts['failed']} failed", style="red")
        text.append("  ", style="dim")
        text.append(f"{STATUS_ICONS[TaskStatus.IN_PROGRESS]}", style="bold blue")
        text.append(f" {counts['in_progress']} running", style="bold blue")
        text.append("  ", style="dim")
        text.append(f"{STATUS_ICONS[TaskStatus.PENDING]}", style="dim")
        text.append(f" {counts['pending']} pending", style="dim")
        if counts["skipped"]:
            text.append("  ", style="dim")
            text.append(f"{STATUS_ICONS[TaskStatus.SKIPPED]}", style="dim")
            text.append(f" {counts['skipped']} skipped", style="dim")
        text.append(f"  {mins}:{secs:02d}", style="bold cyan")
        return text


def _count_statuses(plan: TaskPlan) -> dict[str, int]:
    """Count tasks in each status."""
    counts = {
        "completed": 0,
        "failed": 0,
        "in_progress": 0,
        "pending": 0,
        "skipped": 0,
    }
    for t in plan.tasks:
        counts[t.status.value] = counts.get(t.status.value, 0) + 1
    return counts
