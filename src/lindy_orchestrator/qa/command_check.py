"""Command check gate: runs an arbitrary shell command and checks exit code."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from ..models import QAResult
from . import register

# Shell metacharacters that require sh -c wrapping (&&, ||, |, ;, redirects)
_SHELL_META_RE = re.compile(r"&&|\|\||[|;<>]")


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
        **kwargs: Any,
    ) -> QAResult:
        command = params.get("command", "")
        resolved = kwargs.get("module_path")

        # diff_only: resolve {changed_files} or skip if no changes
        if params.get("diff_only") and isinstance(command, str):
            changed = _get_changed_files(project_root, resolved)
            if not changed:
                return QAResult(
                    gate="command_check",
                    passed=True,
                    output="No changed files to check (diff_only mode)",
                )
            if "{changed_files}" in command:
                command = command.replace("{changed_files}", " ".join(changed))
        if "cwd" in params:
            raw_cwd = params["cwd"]
        else:
            # No cwd specified (e.g. plan-defined gates) — default to project root.
            # Auto-injected gates always have cwd in params, so this branch only
            # applies to plan-defined or user-defined qa_checks.
            raw_cwd = "."
        # Resolve {module_path} template if present — use str.replace() not
        # str.format() to prevent attribute access via format specifiers (M-28).
        if "{module_path}" in raw_cwd:
            if resolved:
                module_path = resolved
            else:
                module_path = str(project_root / module_name) if module_name else str(project_root)
            raw_cwd = raw_cwd.replace("{module_path}", module_path)
        cwd = project_root / raw_cwd
        timeout = params.get("timeout", 300)

        if not command:
            return QAResult(
                gate="command_check",
                passed=False,
                output="No command specified",
            )

        # SECURITY: use shell=False.  For compound commands containing shell
        # operators (&&, ||, |, ;, redirects), wrap via ["sh", "-c", command]
        # so the shell interprets operators while subprocess itself stays safe.
        if isinstance(command, str):
            if _SHELL_META_RE.search(command):
                cmd_args = ["sh", "-c", command]
            else:
                try:
                    cmd_args = shlex.split(command)
                except ValueError as exc:
                    return QAResult(
                        gate="command_check",
                        passed=False,
                        output=f"Failed to parse command: {exc}",
                        details={"command": command, "error": str(exc)},
                    )
        else:
            cmd_args = command

        try:
            proc = subprocess.run(
                cmd_args,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
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


def _get_changed_files(project_root: Path, module_path: str | None = None) -> list[str]:
    """Get files changed on the current branch vs main/master."""
    for base in ("main", "master"):
        try:
            merge_result = subprocess.run(
                ["git", "merge-base", base, "HEAD"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if merge_result.returncode != 0:
                continue
            base_sha = merge_result.stdout.strip()
            diff_result = subprocess.run(
                ["git", "diff", "--name-only", f"{base_sha}..HEAD"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if diff_result.returncode == 0 and diff_result.stdout.strip():
                files = diff_result.stdout.strip().splitlines()
                if module_path:
                    # Filter to module path
                    try:
                        prefix = str(Path(module_path).relative_to(project_root))
                        if prefix != ".":
                            files = [f for f in files if f.startswith(prefix)]
                    except ValueError:
                        pass
                return files
        except (subprocess.TimeoutExpired, OSError):
            continue
    return []
