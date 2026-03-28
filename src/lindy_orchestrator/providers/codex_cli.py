"""Codex CLI dispatch provider and helpers."""

from __future__ import annotations

import shutil
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


def find_codex_cli() -> str | None:
    """Find the codex CLI binary path."""
    return shutil.which("codex")


def _extract_result_from_lines(lines: list[str]) -> str:
    """Extract the final result from Codex JSONL lines."""
    for line in reversed(lines):
        event = parse_event(line)
        if event and event.get("type") == "result":
            result_text = event.get("result", "")
            if result_text:
                return result_text

    text_parts: list[str] = []
    for line in lines:
        event = parse_event(line)
        if not event:
            continue
        if event.get("type") == "message" and event.get("content"):
            text_parts.append(str(event["content"]))
        nested = event.get("msg")
        if isinstance(nested, dict) and nested.get("type") == "agent_message":
            agent_msg = nested.get("message", "")
            if agent_msg:
                text_parts.append(agent_msg)
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                agent_msg = item.get("text", "")
                if agent_msg:
                    text_parts.append(agent_msg)

    return "\n".join(text_parts) if text_parts else ""


def dispatch_codex_agent_simple(
    module: str,
    working_dir: Path,
    prompt: str,
    config: DispatcherConfig,
) -> DispatchResult:
    """Run codex as a blocking JSON request."""
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


def dispatch_codex_agent(
    module: str,
    working_dir: Path,
    prompt: str,
    config: DispatcherConfig,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    stall_seconds: int | None = None,
) -> DispatchResult:
    """Run codex with streaming JSON output."""
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


class CodexCLIProvider:
    """Provider backed by the Codex CLI."""

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
