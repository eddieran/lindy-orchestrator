"""Agent dispatcher via Claude Code CLI.

Runs `claude -p "<prompt>"` as a subprocess in the target module directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

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
) -> DispatchResult:
    """Run a Claude Code CLI agent in a module directory.

    Uses: claude -p "<prompt>" --permission-mode <mode> --output-format json
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

    # Remove CLAUDECODE env var to allow nested sessions
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

        # Try to parse JSON output
        agent_output = output
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict) and "result" in parsed:
                agent_output = parsed["result"]
        except (json.JSONDecodeError, TypeError):
            pass

        # If output is empty and there's stderr, include it for debugging
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
