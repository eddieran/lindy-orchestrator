"""Agent dispatcher via Claude Code CLI.

Two dispatch modes:
- dispatch_agent(): Streaming (stream-json) with heartbeat/stall detection for long tasks
- dispatch_agent_simple(): Classic subprocess.run with json output for quick calls (plan, report)
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import DispatcherConfig
from .models import DispatchResult

log = logging.getLogger(__name__)


def find_claude_cli() -> str | None:
    """Find the claude CLI binary path."""
    return shutil.which("claude")


# ---------------------------------------------------------------------------
# Simple dispatch (plan generation, reports — no heartbeat needed)
# ---------------------------------------------------------------------------


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

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(working_dir),
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            env=env,
        )
        duration = time.monotonic() - start

        output = proc.stdout
        truncated = False
        max_chars = config.max_output_chars
        if len(output) > max_chars:
            half = max_chars // 2
            output = output[:half] + "\n\n... [TRUNCATED] ...\n\n" + output[-half:]
            truncated = True

        # Parse JSON output to extract result text
        agent_output = output
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict) and "result" in parsed:
                agent_output = parsed["result"]
        except (json.JSONDecodeError, TypeError):
            pass

        if not agent_output.strip() and proc.stderr:
            agent_output = f"[stderr] {proc.stderr[:5000]}"

        return DispatchResult(
            module=module,
            success=proc.returncode == 0,
            output=agent_output,
            exit_code=proc.returncode,
            duration_seconds=round(duration, 1),
            truncated=truncated,
        )

    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return DispatchResult(
            module=module,
            success=False,
            output=f"Agent timed out after {config.timeout_seconds}s",
            exit_code=-1,
            duration_seconds=round(duration, 1),
            error="timeout",
        )

    except FileNotFoundError:
        return DispatchResult(
            module=module,
            success=False,
            output=f"Claude CLI not found at {claude_path}",
            exit_code=-1,
            error="cli_not_found",
        )


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
    stall_warned = False
    all_lines: list[str] = []
    line_queue: queue.Queue[str | None] = queue.Queue()

    # Background thread reads stdout lines into queue
    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line_queue.put(line)
        line_queue.put(None)  # Sentinel: EOF

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    try:
        while True:
            elapsed = time.monotonic() - start
            stall_elapsed = time.monotonic() - last_activity

            # Hard timeout safety net
            if elapsed >= config.timeout_seconds:
                proc.kill()
                proc.wait()
                stderr = _read_stderr(proc)
                duration = time.monotonic() - start
                msg = f"Agent hard timeout after {int(elapsed)}s ({event_count} events received)"
                if stderr:
                    msg += f"\n[stderr] {stderr[:2000]}"
                return DispatchResult(
                    module=module,
                    success=False,
                    output=msg,
                    exit_code=-1,
                    duration_seconds=round(duration, 1),
                    error="timeout",
                    event_count=event_count,
                    last_tool_use=last_tool_use,
                )

            # Stall detection with two-stage escalation: warn → kill
            # Per-task override takes priority over config
            if stall_seconds is not None:
                warn_threshold = stall_seconds // 2
                kill_threshold = stall_seconds
            else:
                escalation = getattr(config, "stall_escalation", None)
                if escalation:
                    warn_threshold = escalation.warn_after_seconds
                    kill_threshold = escalation.kill_after_seconds
                else:
                    warn_threshold = config.stall_timeout_seconds // 2
                    kill_threshold = config.stall_timeout_seconds

            # Bash-tool-aware: long-running shell commands (builds, tests,
            # testnet deploys) produce no JSONL events while running.
            # Give 50% more time when the last tool was Bash.
            _LONG_RUNNING_TOOLS = {"Bash", "bash", "execute_bash"}
            if last_tool_use in _LONG_RUNNING_TOOLS:
                warn_threshold = int(warn_threshold * 1.5)
                kill_threshold = int(kill_threshold * 1.5)

            # Grace period for first event (double thresholds)
            if event_count == 0:
                effective_warn = max(warn_threshold * 2, 300)
                effective_kill = max(kill_threshold * 2, 600)
            else:
                effective_warn = max(warn_threshold, 300)
                effective_kill = max(kill_threshold, 600)

            # Stage 1: Warning
            if stall_elapsed >= effective_warn and not stall_warned:
                stall_warned = True
                if on_event:
                    on_event(
                        {
                            "type": "stall_warning",
                            "stall_seconds": int(stall_elapsed),
                            "last_tool": last_tool_use,
                        }
                    )

            # Stage 2: Kill
            if stall_elapsed >= effective_kill:
                proc.kill()
                proc.wait()
                stderr = _read_stderr(proc)
                duration = time.monotonic() - start
                msg = (
                    f"Agent stalled: no output for {int(stall_elapsed)}s "
                    f"(kill limit: {int(effective_kill)}s, "
                    f"last tool: {last_tool_use or 'none'}, "
                    f"{event_count} events total, {int(elapsed)}s elapsed)"
                )
                if stderr:
                    msg += f"\n[stderr] {stderr[:2000]}"
                if on_event:
                    on_event(
                        {
                            "type": "stall_killed",
                            "stall_seconds": int(stall_elapsed),
                            "last_tool": last_tool_use,
                        }
                    )
                return DispatchResult(
                    module=module,
                    success=False,
                    output=msg,
                    exit_code=-1,
                    duration_seconds=round(duration, 1),
                    error="stall",
                    event_count=event_count,
                    last_tool_use=last_tool_use,
                )

            # Read from queue with 5-second poll interval
            try:
                line = line_queue.get(timeout=5.0)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue

            if line is None:
                # EOF sentinel
                break

            last_activity = time.monotonic()
            stall_warned = False  # reset on new activity
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
                        log.debug("on_event callback error", exc_info=True)

    except Exception as e:
        log.exception("Dispatcher error during streaming dispatch")
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

    proc.wait()
    duration = time.monotonic() - start
    retcode = proc.returncode or 0

    # Extract result: prefer result event, fallback to raw output
    if not result_text:
        result_text = _extract_result_from_lines(all_lines)

    # If output is empty, check stderr
    if not result_text.strip():
        stderr = _read_stderr(proc)
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
# Helpers
# ---------------------------------------------------------------------------


def _read_stderr(proc: subprocess.Popen) -> str:
    """Read stderr from a completed process, safely."""
    if proc.stderr:
        try:
            return (proc.stderr.read() or "").strip()
        except Exception:
            log.warning("Failed to read stderr from process", exc_info=True)
    return ""


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
