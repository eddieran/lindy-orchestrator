"""Agent dispatcher via Claude Code CLI.

Runs `claude -p "<prompt>"` as a subprocess in the target module directory.
Uses `--output-format stream-json --verbose` for real-time heartbeat monitoring
with stall detection.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .config import DispatcherConfig
from .models import DispatchResult


def find_claude_cli() -> str | None:
    """Find the claude CLI binary path."""
    return shutil.which("claude")


def dispatch_agent(
    module: str,
    working_dir: Path,
    prompt: str,
    config: DispatcherConfig,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> DispatchResult:
    """Run a Claude Code CLI agent in a module directory.

    Uses: claude -p "<prompt>" --permission-mode <mode> --output-format stream-json --verbose

    Monitors the JSONL event stream for heartbeat/stall detection:
    - Any stdout line resets the stall timer
    - If no output for `config.stall_timeout_seconds`, the process is killed
    - Hard timeout `config.timeout_seconds` is a safety net

    Args:
        module: Module name (e.g., "backend")
        working_dir: Path to the module directory
        prompt: The prompt to send to the agent
        config: Dispatcher configuration (timeouts, permission mode)
        on_event: Optional callback for each parsed JSONL event
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

    # Remove CLAUDECODE env var to allow nested sessions
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    start = time.monotonic()
    event_count = 0
    last_tool_use = ""
    result_text = ""

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
            output=f"Claude CLI not found at {claude_path}",
            exit_code=-1,
            error="cli_not_found",
        )

    last_activity = time.monotonic()
    all_lines: list[str] = []

    try:
        while True:
            elapsed = time.monotonic() - start
            stall_elapsed = time.monotonic() - last_activity

            # Hard timeout safety net
            if elapsed >= config.timeout_seconds:
                proc.kill()
                proc.wait()
                duration = time.monotonic() - start
                return DispatchResult(
                    module=module,
                    success=False,
                    output=f"Agent hard timeout after {int(elapsed)}s ({event_count} events received)",
                    exit_code=-1,
                    duration_seconds=round(duration, 1),
                    error="timeout",
                    event_count=event_count,
                    last_tool_use=last_tool_use,
                )

            # Stall detection
            if stall_elapsed >= config.stall_timeout_seconds:
                proc.kill()
                proc.wait()
                duration = time.monotonic() - start
                return DispatchResult(
                    module=module,
                    success=False,
                    output=(
                        f"Agent stalled: no output for {int(stall_elapsed)}s "
                        f"(last tool: {last_tool_use or 'none'}, "
                        f"{event_count} events total, {int(elapsed)}s elapsed)"
                    ),
                    exit_code=-1,
                    duration_seconds=round(duration, 1),
                    error="stall",
                    event_count=event_count,
                    last_tool_use=last_tool_use,
                )

            # Check if process has ended
            retcode = proc.poll()

            # Use select to read with a 5-second poll interval
            if proc.stdout is not None:
                ready, _, _ = select.select([proc.stdout], [], [], 5.0)
                if ready:
                    line = proc.stdout.readline()
                    if line:
                        last_activity = time.monotonic()
                        all_lines.append(line)
                        event_count += 1

                        # Parse JSONL event
                        event = _parse_event(line)
                        if event:
                            # Extract tool use info
                            tool = _extract_tool_use(event)
                            if tool:
                                last_tool_use = tool

                            # Check for result event
                            if event.get("type") == "result":
                                result_text = event.get("result", "")

                            # Fire callback
                            if on_event:
                                try:
                                    on_event(event)
                                except Exception:
                                    pass  # Never let callback crash the dispatcher
                    elif retcode is not None:
                        # EOF + process done
                        break
                elif retcode is not None:
                    # No data ready + process done
                    break
            elif retcode is not None:
                break

    except Exception as e:
        # Ensure process is cleaned up
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass
        duration = time.monotonic() - start
        return DispatchResult(
            module=module,
            success=False,
            output=f"Dispatcher error: {e}",
            exit_code=-1,
            duration_seconds=round(duration, 1),
            error="dispatcher_error",
            event_count=event_count,
            last_tool_use=last_tool_use,
        )

    duration = time.monotonic() - start
    retcode = proc.returncode or 0

    # Extract result: prefer result event, fallback to raw output
    if not result_text:
        result_text = _extract_result_from_lines(all_lines)

    # If output is empty, check stderr
    if not result_text.strip():
        stderr = ""
        if proc.stderr:
            try:
                stderr = proc.stderr.read() or ""
            except Exception:
                pass
        if stderr:
            result_text = f"[stderr] {stderr[:5000]}"

    # Truncate if needed
    truncated = False
    max_chars = config.max_output_chars
    if len(result_text) > max_chars:
        half = max_chars // 2
        result_text = result_text[:half] + "\n\n... [TRUNCATED] ...\n\n" + result_text[-half:]
        truncated = True

    return DispatchResult(
        module=module,
        success=retcode == 0,
        output=result_text,
        exit_code=retcode,
        duration_seconds=round(duration, 1),
        truncated=truncated,
        event_count=event_count,
        last_tool_use=last_tool_use,
    )


# ---------------------------------------------------------------------------
# JSONL event parsing helpers
# ---------------------------------------------------------------------------


def _parse_event(line: str) -> dict[str, Any] | None:
    """Parse a single JSONL line into a dict."""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_tool_use(event: dict[str, Any]) -> str:
    """Extract tool name from an assistant event with tool_use content."""
    if event.get("type") != "assistant":
        return ""
    msg = event.get("message", {})
    content = msg.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return block.get("name", "")
    return ""


def _extract_result_from_lines(lines: list[str]) -> str:
    """Extract the final result from stream-json JSONL lines.

    Looks for the last {"type": "result"} event.
    Falls back to concatenating assistant text blocks.
    """
    # First pass: look for result event (last one wins)
    for line in reversed(lines):
        event = _parse_event(line)
        if event and event.get("type") == "result":
            result_text = event.get("result", "")
            if result_text:
                return result_text

    # Fallback: concatenate assistant text blocks
    text_parts = []
    for line in lines:
        event = _parse_event(line)
        if event and event.get("type") == "assistant":
            msg = event.get("message", {})
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))

    return "\n".join(text_parts) if text_parts else ""
