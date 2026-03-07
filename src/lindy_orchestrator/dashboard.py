"""Live DAG dashboard for task execution.

Renders a Rich Live panel showing the task DAG, a summary bar with
task counts, elapsed time, and heartbeat info for the active task.
Falls back to text-based progress when not on a TTY.
"""

from __future__ import annotations

import time
import threading

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .dag import render_dag
from .hooks import Event, EventType, HookRegistry
from .models import TaskPlan


class Dashboard:
    """Live-updating DAG dashboard driven by hook events.

    Subscribes to HookRegistry events and re-renders the DAG panel
    on every state change. Falls back to plain text on non-TTY.
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

        # Heartbeat tracking for the currently active task
        self._active_task_id: int | None = None
        self._hb_event_count = 0
        self._hb_last_tool = ""

    # -- public API -----------------------------------------------------------

    def start(self) -> None:
        """Start the live dashboard and subscribe to hook events."""
        self._start_time = time.monotonic()
        self._hooks.on(EventType.TASK_STARTED, self._on_task_started)
        self._hooks.on(EventType.TASK_COMPLETED, self._on_task_completed)
        self._hooks.on(EventType.TASK_FAILED, self._on_task_failed)
        self._hooks.on(EventType.TASK_RETRYING, self._on_task_retrying)
        self._hooks.on(EventType.TASK_SKIPPED, self._on_task_skipped)
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

    def update_heartbeat(self, event_count: int, last_tool: str) -> None:
        """Update heartbeat info from the dispatching task."""
        with self._lock:
            self._hb_event_count = event_count
            self._hb_last_tool = last_tool
        self._refresh()

    # -- event handlers -------------------------------------------------------

    def _on_task_started(self, event: Event) -> None:
        with self._lock:
            self._active_task_id = event.task_id
            self._hb_event_count = 0
            self._hb_last_tool = ""
        self._refresh()

    def _on_task_completed(self, event: Event) -> None:
        with self._lock:
            if self._active_task_id == event.task_id:
                self._active_task_id = None
        self._refresh()

    def _on_task_failed(self, event: Event) -> None:
        with self._lock:
            if self._active_task_id == event.task_id:
                self._active_task_id = None
        self._refresh()

    def _on_task_retrying(self, event: Event) -> None:
        self._refresh()

    def _on_task_skipped(self, event: Event) -> None:
        self._refresh()

    def _on_qa_event(self, event: Event) -> None:
        self._refresh()

    def _on_generic(self, event: Event) -> None:
        self._refresh()

    # -- rendering ------------------------------------------------------------

    def _refresh(self) -> None:
        if self._interactive and self._live is not None:
            self._live.update(self._build_panel())

    def _build_panel(self, final: bool = False) -> Panel:
        """Build the full dashboard panel."""
        dag_text = render_dag(self._plan)
        summary = self._build_summary()
        heartbeat = self._build_heartbeat()

        parts = [dag_text, Text(""), summary]
        if heartbeat.plain.strip():
            parts.append(heartbeat)

        title = "Execution Complete" if final else "Executing"
        border = "green" if final and not self._plan.has_failures() else "cyan"
        if final and self._plan.has_failures():
            border = "red"

        return Panel(
            Group(*parts),
            title=title,
            border_style=border,
        )

    def _build_summary(self) -> Text:
        """Build the summary bar with task counts and elapsed time."""
        counts = _count_statuses(self._plan)
        elapsed = time.monotonic() - self._start_time
        mins, secs = divmod(int(elapsed), 60)

        text = Text()
        text.append("  ")
        text.append(f"{counts['completed']}", style="green")
        text.append(" completed  ", style="dim")
        text.append(f"{counts['failed']}", style="red")
        text.append(" failed  ", style="dim")
        text.append(f"{counts['in_progress']}", style="bold blue")
        text.append(" running  ", style="dim")
        text.append(f"{counts['pending']}", style="dim")
        text.append(" pending  ", style="dim")
        text.append(f"  {mins}:{secs:02d}", style="bold cyan")
        return text

    def _build_heartbeat(self) -> Text:
        """Build heartbeat info for the active task."""
        text = Text()
        with self._lock:
            task_id = self._active_task_id
            event_count = self._hb_event_count
            last_tool = self._hb_last_tool

        if task_id is not None:
            text.append(f"  Task {task_id}: ", style="dim")
            text.append(f"{event_count} events", style="dim")
            if last_tool:
                text.append(f", last tool: {last_tool}", style="dim")
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
