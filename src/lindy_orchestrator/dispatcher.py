"""Agent dispatcher via Claude Code CLI.

Two dispatch modes:
- dispatch_agent(): Streaming (stream-json) with heartbeat/stall detection for long tasks
- dispatch_agent_simple(): Classic subprocess.run with json output for quick calls (plan, report)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import DispatcherConfig
from .dispatch_core import (
    make_env,
    parse_event,
    read_stderr,
    simple_dispatch,
    streaming_dispatch,
)
from .models import DispatchResult

# Re-export shared helpers for backward compatibility (tests import these)
_parse_event = parse_event
_read_stderr = read_stderr


def find_claude_cli() -> str | None:
    """Find the claude CLI binary path."""
    return shutil.which("claude")


# ---------------------------------------------------------------------------
# Simple dispatch (plan generation, reports — no heartbeat needed)
# ---------------------------------------------------------------------------


def _parse_claude_result(output: str) -> str:
    """Extract result from Claude JSON output."""
    try:
        parsed = json.loads(output)
        if isinstance(parsed, dict) and "result" in parsed:
            return parsed["result"]
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


def dispatch_agent_simple(
    module: str,
    working_dir: Path,
    prompt: str,
    config: DispatcherConfig,
) -> DispatchResult:
    """Run claude -p with --output-format json (blocking, no heartbeat).

    Best for short-lived calls like plan generation and report formatting.
    """
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


# ---------------------------------------------------------------------------
# Streaming dispatch (task execution — heartbeat + stall detection)
# ---------------------------------------------------------------------------


def dispatch_agent(
    module: str,
    working_dir: Path,
    prompt: str,
    config: DispatcherConfig,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    stall_seconds: int | None = None,
) -> DispatchResult:
    """Run a Claude Code CLI agent in a module directory.

    Uses: claude -p "<prompt>" --permission-mode <mode> --output-format stream-json --verbose

    Monitors the JSONL event stream for heartbeat/stall detection:
    - Any stdout line resets the stall timer
    - If no output for `config.stall_timeout_seconds`, the process is killed
    - Hard timeout `config.timeout_seconds` is a safety net
    """
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

    env = make_env()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(working_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
    except FileNotFoundError:
        # Distinguish missing working_dir from missing CLI binary
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_result_from_lines(lines: list[str]) -> str:
    """Extract the final result from stream-json JSONL lines.

    Looks for the last {"type": "result"} event.
    Falls back to concatenating assistant text blocks.
    """
    # First pass: look for result event (last one wins)
    for line in reversed(lines):
        event = parse_event(line)
        if event and event.get("type") == "result":
            result_text = event.get("result", "")
            if result_text:
                return result_text

    # Fallback: concatenate assistant text blocks
    text_parts = []
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
