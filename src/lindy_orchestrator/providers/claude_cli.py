"""Claude CLI dispatch provider and helpers."""

from __future__ import annotations

import shutil
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..config import DispatcherConfig
from ..dispatch_core import (
    make_env,
    parse_event,
    read_stderr,
    simple_dispatch,
    streaming_dispatch,
)
from ..models import DispatchResult

_parse_event = parse_event
_read_stderr = read_stderr


def find_claude_cli() -> str | None:
    """Find the claude CLI binary path."""
    return shutil.which("claude")


def _parse_claude_result(output: str) -> str:
    """Extract result from Claude JSON output."""
    try:
        parsed = json.loads(output)
        if isinstance(parsed, dict) and "result" in parsed:
            return parsed["result"]
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


def _extract_result_from_lines(lines: list[str]) -> str:
    """Extract the final result from Claude JSONL lines."""
    for line in reversed(lines):
        event = parse_event(line)
        if event and event.get("type") == "result":
            result_text = event.get("result", "")
            if result_text:
                return result_text

    text_parts: list[str] = []
    for line in lines:
        event = parse_event(line)
        if event and event.get("type") == "assistant":
            msg = event.get("message", {})
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))

    return "\n".join(text_parts) if text_parts else ""


def dispatch_agent_simple(
    module: str,
    working_dir: Path,
    prompt: str,
    config: DispatcherConfig,
) -> DispatchResult:
    """Run claude as a blocking JSON request."""
    claude_path = find_claude_cli()
    if not claude_path:
        return DispatchResult(
            module=module,
            success=False,
            output="Claude CLI not found in PATH",
            error="cli_not_found",
        )

    cmd = [
        claude_path,
        "-p",
        prompt,
        "--permission-mode",
        config.permission_mode,
        "--output-format",
        "json",
    ]
    return simple_dispatch(module, working_dir, cmd, config, "Claude CLI", _parse_claude_result)


def dispatch_agent(
    module: str,
    working_dir: Path,
    prompt: str,
    config: DispatcherConfig,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    stall_seconds: int | None = None,
) -> DispatchResult:
    """Run claude with streaming JSON output."""
    claude_path = find_claude_cli()
    if not claude_path:
        return DispatchResult(
            module=module,
            success=False,
            output="Claude CLI not found in PATH",
            error="cli_not_found",
        )

    cmd = [
        claude_path,
        "-p",
        prompt,
        "--permission-mode",
        config.permission_mode,
        "--output-format",
        "stream-json",
        "--verbose",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(working_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=make_env(),
        )
    except FileNotFoundError:
        if not Path(working_dir).exists():
            return DispatchResult(
                module=module,
                success=False,
                output=f"Working directory does not exist: {working_dir}",
                exit_code=-1,
                error="cwd_not_found",
            )
        return DispatchResult(
            module=module,
            success=False,
            output=f"Claude CLI not found at {claude_path}",
            exit_code=-1,
            error="cli_not_found",
        )

    return streaming_dispatch(
        module=module,
        proc=proc,
        config=config,
        extract_result_from_lines=_extract_result_from_lines,
        on_event=on_event,
        stall_seconds=stall_seconds,
        apply_long_running_multiplier=True,
    )


class ClaudeCLIProvider:
    """Default provider backed by the Claude CLI."""

    def __init__(self, config: DispatcherConfig):
        self.config = config

    def validate(self) -> None:
        """Check that the claude CLI binary is installed and on PATH."""
        binary = self.config.cli_path if hasattr(self.config, "cli_path") else "claude"
        if not shutil.which(binary):
            raise RuntimeError(
                f"Claude CLI binary '{binary}' not found on PATH. "
                f"Install it from https://docs.anthropic.com/en/docs/claude-code"
            )

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
