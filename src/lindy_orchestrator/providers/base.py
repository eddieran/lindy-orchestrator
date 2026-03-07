"""Agent dispatch provider interface.

Defines the Protocol that all dispatch providers must satisfy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from ..models import DispatchResult


@runtime_checkable
class DispatchProvider(Protocol):
    """Agent dispatch provider interface."""

    def dispatch(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        stall_seconds: int | None = None,
    ) -> DispatchResult:
        """Execute an agent task with optional streaming progress.

        Args:
            module: Target module name.
            working_dir: Working directory for the agent.
            prompt: Task prompt.
            on_event: Optional callback for streaming events.
            stall_seconds: Per-task stall timeout override (None = use config default).

        Returns:
            DispatchResult with success status and output.
        """
        ...

    def dispatch_simple(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
    ) -> DispatchResult:
        """Execute a quick blocking task (planning, reports).

        Args:
            module: Target module name.
            working_dir: Working directory for the agent.
            prompt: Task prompt.

        Returns:
            DispatchResult with success status and output.
        """
        ...
