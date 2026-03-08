"""Codex CLI dispatch provider — wraps codex_dispatcher.py functions."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from ..codex_dispatcher import dispatch_codex_agent, dispatch_codex_agent_simple
from ..config import DispatcherConfig
from ..models import DispatchResult


class CodexCLIProvider:
    """Codex CLI provider: wraps codex_dispatcher.py functions."""

    def __init__(self, config: DispatcherConfig):
        self.config = config

    def validate(self) -> None:
        """Check that the codex CLI binary is installed and on PATH."""
        if not shutil.which("codex"):
            raise RuntimeError(
                "Codex CLI binary 'codex' not found on PATH. "
                "Install it from https://github.com/openai/codex"
            )

    def dispatch(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        stall_seconds: int | None = None,
    ) -> DispatchResult:
        return dispatch_codex_agent(
            module, working_dir, prompt, self.config, on_event, stall_seconds=stall_seconds
        )

    def dispatch_simple(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
    ) -> DispatchResult:
        return dispatch_codex_agent_simple(module, working_dir, prompt, self.config)
