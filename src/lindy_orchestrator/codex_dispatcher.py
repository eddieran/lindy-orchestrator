"""Agent dispatcher via OpenAI Codex CLI.

Two dispatch modes:
- dispatch_codex_agent(): Streaming (stream-json) with heartbeat/stall detection for long tasks
- dispatch_codex_agent_simple(): Classic subprocess.run with json output for quick calls (plan, report)
"""

from __future__ import annotations

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


def find_codex_cli() -> str | None:
    """Find the codex CLI binary path."""
    return shutil.which("codex")


# ---------------------------------------------------------------------------
# Simple dispatch (plan generation, reports — no heartbeat needed)
# ---------------------------------------------------------------------------


def dispatch_codex_agent_simple(
    module: str,
    working_dir: Path,
    prompt: str,
    config: DispatcherConfig,
) -> DispatchResult:
    """Run codex --prompt with --output-format json (blocking, no heartbeat).

    Best for short-lived calls like plan generation and report formatting.
    """
    codex_path = find_codex_cli()
    if not codex_path:
        return DispatchResult(
            module=module,
            success=False,
            output="Codex CLI not found in PATH",
            error="cli_not_found",
        )

    cmd = [
        codex_path,
        "exec",
        "--full-auto",
        "--json",
        "--skip-git-repo-check",
        "--cd",
        str(working_dir),
        prompt,
    ]

    return simple_dispatch(
        module,
        working_dir,
        cmd,
        config,
        "Codex CLI",
        lambda output: _extract_result_from_lines(output.splitlines()),
    )


# ---------------------------------------------------------------------------
# Streaming dispatch (task execution — heartbeat + stall detection)
# ---------------------------------------------------------------------------


def dispatch_codex_agent(
    module: str,
    working_dir: Path,
    prompt: str,
    config: DispatcherConfig,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    stall_seconds: int | None = None,
) -> DispatchResult:
    """Run a Codex CLI agent in a module directory.

    Uses: codex --prompt "<prompt>" --approval-mode full-auto --output-format stream-json

    Monitors the JSONL event stream for heartbeat/stall detection:
    - Any stdout line resets the stall timer
    - If no output for `config.stall_timeout_seconds`, the process is killed
    - Hard timeout `config.timeout_seconds` is a safety net
    """
    codex_path = find_codex_cli()
    if not codex_path:
        return DispatchResult(
            module=module,
            success=False,
            output="Codex CLI not found in PATH",
            error="cli_not_found",
        )

    cmd = [
        codex_path,
        "exec",
        "--full-auto",
        "--json",
        "--skip-git-repo-check",
        "--cd",
        str(working_dir),
        prompt,
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
        return DispatchResult(
            module=module,
            success=False,
            output=f"Codex CLI not found at {codex_path}",
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
        warn_floor=300,
        kill_floor=600,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_result_from_lines(lines: list[str]) -> str:
    """Extract the final result from JSONL lines.

    Handles both Claude and Codex output formats:
    - Claude: {"type": "result", "result": "..."} or assistant text blocks
    - Codex: {"id": "0", "msg": {"type": "agent_message", "message": "..."}}
    """
    # First pass: look for result event (last one wins) — Claude format
    for line in reversed(lines):
        event = parse_event(line)
        if event and event.get("type") == "result":
            result_text = event.get("result", "")
            if result_text:
                return result_text

    # Second pass: collect text from all supported formats
    text_parts = []
    for line in lines:
        event = parse_event(line)
        if not event:
            continue
        # Claude-style assistant text blocks
        if event.get("type") == "assistant":
            msg = event.get("message", {})
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
        # Codex-style flat message events
        elif event.get("type") == "message" and event.get("content"):
            text_parts.append(str(event["content"]))
        # Codex v1 nested format: {"id":"0","msg":{"type":"agent_message","message":"..."}}
        nested = event.get("msg")
        if isinstance(nested, dict) and nested.get("type") == "agent_message":
            agent_msg = nested.get("message", "")
            if agent_msg:
                text_parts.append(agent_msg)

        # Codex v2 item.completed format:
        # {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                agent_msg = item.get("text", "")
                if agent_msg:
                    text_parts.append(agent_msg)

    return "\n".join(text_parts) if text_parts else ""
