"""Command check gate: runs an arbitrary shell command and checks exit code."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..models import QAResult
from . import register


@register("command_check")
class CommandCheckGate:
    """Runs a shell command and passes if exit code is 0.

    params:
        command: str | list[str]
        cwd: str (relative to project root, default ".")
        timeout: int (default 300)
    """

    def check(
        self,
        params: dict[str, Any],
        project_root: Path,
        module_name: str = "",
        task_output: str = "",
        **kwargs,
    ) -> QAResult:
        command = params.get("command", "")
        raw_cwd = params.get("cwd", module_name or ".")
        # Resolve {module_path} template if present
        if "{module_path}" in raw_cwd:
            module_path = str(project_root / module_name) if module_name else str(project_root)
            raw_cwd = raw_cwd.format(module_path=module_path)
        cwd = project_root / raw_cwd
        timeout = params.get("timeout", 300)

        if not command:
            return QAResult(
                gate="command_check",
                passed=False,
                output="No command specified",
            )

        use_shell = isinstance(command, str)

        try:
            proc = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=use_shell,
            )
        except subprocess.TimeoutExpired:
            return QAResult(
                gate="command_check",
                passed=False,
                output=f"Command timed out after {timeout}s",
                details={"command": command, "timeout": True},
            )
        except OSError as exc:
            return QAResult(
                gate="command_check",
                passed=False,
                output=f"Failed to run command: {exc}",
                details={"command": command, "cwd": str(cwd), "error": str(exc)},
            )

        passed = proc.returncode == 0
        output = proc.stdout[-5000:] if len(proc.stdout) > 5000 else proc.stdout
        if proc.stderr:
            output += "\n--- stderr ---\n" + proc.stderr[-2000:]

        return QAResult(
            gate="command_check",
            passed=passed,
            output=output,
            details={"exit_code": proc.returncode, "command": command},
        )
