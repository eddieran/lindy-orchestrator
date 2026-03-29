"""Shared dispatch infrastructure for CLI-based agent providers.

Contains the streaming dispatch loop, simple dispatch, and common helpers
shared by dispatcher.py (Claude CLI) and codex_dispatcher.py (Codex CLI).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import DispatcherConfig
from .models import DispatchResult

log = logging.getLogger(__name__)

_LONG_RUNNING_TOOLS = frozenset({"Bash", "bash", "execute_bash"})


# ---------------------------------------------------------------------------
# Universal event info extraction
# ---------------------------------------------------------------------------


def extract_event_info(event: dict) -> tuple[str, str]:
    """Extract (tool_name, reasoning_text) from any provider's event format.

    Supports Claude, Codex flat, Codex v1 nested, and Codex v2 item formats.
    Returns ("", "") for unrecognised events.
    """
    tool_name = ""
    reasoning_text = ""

    # --- Claude-style: {"type":"assistant","message":{"content":[...]}} ---
    content = event.get("message", {}).get("content", [])
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                tool_name = block.get("name", "")
            elif btype in ("thinking", "text"):
                snippet = block.get("text", "")
                if snippet:
                    reasoning_text = snippet

    # --- Codex flat: {"type":"function_call","name":"shell"} ---
    if not tool_name and event.get("type") == "function_call":
        tool_name = event.get("name", "")

    # --- Codex v1 nested: {"msg":{"type":"function_call","name":"shell"}} ---
    nested = event.get("msg")
    if isinstance(nested, dict):
        if not tool_name and nested.get("type") == "function_call":
            tool_name = nested.get("name", "")
        if not reasoning_text and nested.get("type") == "agent_message":
            reasoning_text = nested.get("message", "")

    # --- Codex v2 item: {"type":"item.started"|"item.completed","item":{...}} ---
    if event.get("type") in ("item.started", "item.completed"):
        item = event.get("item", {})
        if isinstance(item, dict):
            if not tool_name and item.get("type") == "command_execution":
                tool_name = "shell"
            if not reasoning_text and item.get("type") == "agent_message":
                reasoning_text = item.get("text", "")

    return (tool_name, reasoning_text)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def parse_event(line: str) -> dict[str, Any] | None:
    """Parse a single JSONL line into a dict."""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None


def read_stderr(proc: subprocess.Popen) -> str:
    """Read stderr from a completed process, safely."""
    if proc.stderr:
        try:
            return (proc.stderr.read() or "").strip()
        except Exception:
            log.warning("Failed to read stderr from process", exc_info=True)
    return ""


def truncate_output(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate text if it exceeds max_chars. Returns (text, was_truncated)."""
    if len(text) > max_chars:
        half = max_chars // 2
        return text[:half] + "\n\n... [TRUNCATED] ...\n\n" + text[-half:], True
    return text, False


def make_env() -> dict[str, str]:
    """Create subprocess environment with CLAUDECODE removed."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


# ---------------------------------------------------------------------------
# Stall threshold computation
# ---------------------------------------------------------------------------


def compute_stall_thresholds(
    config: DispatcherConfig,
    stall_seconds: int | None,
    event_count: int,
    last_tool_use: str,
    *,
    warn_floor: int = 0,
    kill_floor: int = 0,
    apply_long_running_multiplier: bool = False,
) -> tuple[int, int]:
    """Compute effective (warn, kill) stall thresholds.

    Args:
        config: Dispatcher configuration.
        stall_seconds: Per-task override (None = use config).
        event_count: Events received so far.
        last_tool_use: Name of last tool used.
        warn_floor: Minimum warn threshold (codex uses 300).
        kill_floor: Minimum kill threshold (codex uses 600).
        apply_long_running_multiplier: Apply 1.5x for Bash-like tools (Claude).
    """
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

    if apply_long_running_multiplier and last_tool_use in _LONG_RUNNING_TOOLS:
        warn_threshold = int(warn_threshold * 1.5)
        kill_threshold = int(kill_threshold * 1.5)

    if event_count == 0:
        effective_warn = max(warn_threshold * 2, warn_floor)
        effective_kill = max(kill_threshold * 2, kill_floor)
    else:
        effective_warn = max(warn_threshold, warn_floor)
        effective_kill = max(kill_threshold, kill_floor)

    return effective_warn, effective_kill


# ---------------------------------------------------------------------------
# Universal event processing
# ---------------------------------------------------------------------------


@dataclass
class _StreamState:
    """Mutable state for the streaming dispatch loop."""

    event_count: int = 0
    last_tool_use: str = ""
    result_text: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


def _process_event(
    event: dict[str, Any],
    state: _StreamState,
) -> None:
    """Process a parsed JSONL event, updating state.

    Handles all provider formats universally via extract_event_info:
    - Claude result events (with cost/token accumulation)
    - Codex v1 nested agent_message events
    - Codex v2 item.completed agent_message events
    """
    tool, _reasoning = extract_event_info(event)
    if tool:
        state.last_tool_use = tool

    # Claude-style result event (includes cost/token data)
    if event.get("type") == "result":
        state.result_text = event.get("result", "")
        state.cost_usd += event.get("total_cost_usd", 0.0) or 0.0
        usage = event.get("usage", {})
        if isinstance(usage, dict):
            state.input_tokens += usage.get("input_tokens", 0) or 0
            state.output_tokens += usage.get("output_tokens", 0) or 0

    # Codex v1 nested: {"msg": {"type": "agent_message", "message": "..."}}
    nested = event.get("msg")
    if isinstance(nested, dict) and nested.get("type") == "agent_message":
        msg_text = nested.get("message", "")
        if msg_text:
            state.result_text = msg_text

    # Codex v2: {"type": "item.completed", "item": {"type": "agent_message", ...}}
    if event.get("type") == "item.completed":
        item = event.get("item", {})
        if isinstance(item, dict) and item.get("type") == "agent_message":
            msg_text = item.get("text", "")
            if msg_text:
                state.result_text = msg_text


# ---------------------------------------------------------------------------
# Streaming dispatch loop
# ---------------------------------------------------------------------------


def streaming_dispatch(
    module: str,
    proc: subprocess.Popen,
    config: DispatcherConfig,
    extract_result_from_lines: Callable[[list[str]], str],
    on_event: Callable[[dict[str, Any]], None] | None = None,
    stall_seconds: int | None = None,
    *,
    warn_floor: int = 0,
    kill_floor: int = 0,
    apply_long_running_multiplier: bool = False,
) -> DispatchResult:
    """Run the streaming dispatch loop with stall detection.

    The caller creates the subprocess; this function manages the JSONL event
    stream, stall detection, and result extraction.  Tool extraction uses the
    universal ``extract_event_info`` for all provider formats.

    Args:
        module: Module name for the DispatchResult.
        proc: A started subprocess with JSONL stdout.
        config: Dispatcher configuration (timeouts, output limits).
        extract_result_from_lines: Fallback result extractor from raw JSONL lines.
        on_event: Optional callback for each parsed event.
        stall_seconds: Per-task stall timeout override.
        warn_floor: Minimum warn threshold (codex=300).
        kill_floor: Minimum kill threshold (codex=600).
        apply_long_running_multiplier: Apply 1.5x for Bash-like tools (Claude).
    """
    start = time.monotonic()
    state = _StreamState()

    last_activity = time.monotonic()
    stall_warned = False
    all_lines: list[str] = []
    line_q: queue.Queue[str | None] = queue.Queue()

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line_q.put(line)
        line_q.put(None)  # Sentinel: EOF

    threading.Thread(target=_reader, daemon=True).start()

    try:
        while True:
            elapsed = time.monotonic() - start
            stall_elapsed = time.monotonic() - last_activity

            # Hard timeout safety net
            if elapsed >= config.timeout_seconds:
                proc.kill()
                proc.wait()
                stderr = read_stderr(proc)
                duration = time.monotonic() - start
                msg = f"Agent hard timeout after {int(elapsed)}s ({state.event_count} events received)"
                if stderr:
                    msg += f"\n[stderr] {stderr[:2000]}"
                return DispatchResult(
                    module=module,
                    success=False,
                    output=msg,
                    exit_code=-1,
                    duration_seconds=round(duration, 1),
                    error="timeout",
                    event_count=state.event_count,
                    last_tool_use=state.last_tool_use,
                )

            # Stall detection with two-stage escalation: warn → kill
            effective_warn, effective_kill = compute_stall_thresholds(
                config,
                stall_seconds,
                state.event_count,
                state.last_tool_use,
                warn_floor=warn_floor,
                kill_floor=kill_floor,
                apply_long_running_multiplier=apply_long_running_multiplier,
            )

            # Stage 1: Warning
            if stall_elapsed >= effective_warn and not stall_warned:
                stall_warned = True
                if on_event:
                    on_event(
                        {
                            "type": "stall_warning",
                            "stall_seconds": int(stall_elapsed),
                            "last_tool": state.last_tool_use,
                        }
                    )

            # Stage 2: Kill
            if stall_elapsed >= effective_kill:
                proc.kill()
                proc.wait()
                stderr = read_stderr(proc)
                duration = time.monotonic() - start
                msg = (
                    f"Agent stalled: no output for {int(stall_elapsed)}s "
                    f"(kill limit: {int(effective_kill)}s, "
                    f"last tool: {state.last_tool_use or 'none'}, "
                    f"{state.event_count} events total, {int(elapsed)}s elapsed)"
                )
                if stderr:
                    msg += f"\n[stderr] {stderr[:2000]}"
                if on_event:
                    on_event(
                        {
                            "type": "stall_killed",
                            "stall_seconds": int(stall_elapsed),
                            "last_tool": state.last_tool_use,
                        }
                    )
                return DispatchResult(
                    module=module,
                    success=False,
                    output=msg,
                    exit_code=-1,
                    duration_seconds=round(duration, 1),
                    error="stall",
                    event_count=state.event_count,
                    last_tool_use=state.last_tool_use,
                )

            # Read from queue with 5-second poll interval
            try:
                line = line_q.get(timeout=5.0)
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
            state.event_count += 1

            # Parse JSONL event
            event = parse_event(line)
            if event:
                _process_event(event, state)
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
            event_count=state.event_count,
            last_tool_use=state.last_tool_use,
        )

    proc.wait()
    duration = time.monotonic() - start
    retcode = proc.returncode or 0

    # Extract result: prefer inline result, fallback to line scan
    if not state.result_text:
        state.result_text = extract_result_from_lines(all_lines)

    # If output is empty, check stderr
    if not state.result_text.strip():
        stderr = read_stderr(proc)
        if stderr:
            state.result_text = f"[stderr] {stderr[:5000]}"

    # Truncate if needed
    raw_output = state.result_text
    result_text, truncated = truncate_output(state.result_text, config.max_output_chars)

    return DispatchResult(
        module=module,
        success=retcode == 0,
        output=result_text,
        raw_output=raw_output,
        exit_code=retcode,
        duration_seconds=round(duration, 1),
        truncated=truncated,
        event_count=state.event_count,
        last_tool_use=state.last_tool_use,
        cost_usd=state.cost_usd,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
    )


# ---------------------------------------------------------------------------
# Simple (blocking) dispatch
# ---------------------------------------------------------------------------


def simple_dispatch(
    module: str,
    working_dir: Path,
    cmd: list[str],
    config: DispatcherConfig,
    cli_name: str,
    parse_result: Callable[[str], str],
) -> DispatchResult:
    """Run a blocking CLI dispatch for short-lived calls (planning, reports).

    Args:
        module: Module name for the DispatchResult.
        working_dir: Working directory for the subprocess.
        cmd: Full command to execute.
        config: Dispatcher configuration.
        cli_name: Human-readable CLI name for error messages.
        parse_result: Function to extract result text from raw stdout.
    """
    env = make_env()

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

        raw_output = proc.stdout
        output, truncated = truncate_output(proc.stdout, config.max_output_chars)

        agent_output = parse_result(raw_output)
        if not agent_output:
            agent_output = output

        if not agent_output.strip() and proc.stderr:
            agent_output = f"[stderr] {proc.stderr[:5000]}"
            raw_output = raw_output or proc.stderr

        return DispatchResult(
            module=module,
            success=proc.returncode == 0,
            output=agent_output,
            raw_output=raw_output,
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
        cli_path = cmd[0] if cmd else cli_name
        return DispatchResult(
            module=module,
            success=False,
            output=f"{cli_name} not found at {cli_path}",
            exit_code=-1,
            error="cli_not_found",
        )
