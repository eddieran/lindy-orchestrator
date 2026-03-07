"""Claude CLI dispatch provider — wraps existing dispatcher.py functions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..config import DispatcherConfig
from ..dispatcher import dispatch_agent, dispatch_agent_simple
from ..models import DispatchResult


class ClaudeCLIProvider:
    """Default provider: wraps existing dispatcher.py functions."""

    def __init__(self, config: DispatcherConfig):
        self.config = config

    def dispatch(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        stall_seconds: int | None = None,
    ) -> DispatchResult:
        return dispatch_agent(
            module, working_dir, prompt, self.config, on_event, stall_seconds=stall_seconds
        )

    def dispatch_simple(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
    ) -> DispatchResult:
        return dispatch_agent_simple(module, working_dir, prompt, self.config)
