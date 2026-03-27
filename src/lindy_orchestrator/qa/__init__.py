"""QA gate registry and runner.

Built-in gates: ci_check, command_check, agent_check.
Users can also define custom command-based gates in orchestrator.yaml.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable, TypeVar

from ..config import CustomGateConfig, DispatcherConfig, ModuleConfig
from ..models import QACheck, QAResult

# Only allow safe characters in paths used for command substitution.
_SAFE_PATH_RE = re.compile(r"^[\w./\-]+$")

_T = TypeVar("_T")

# Gate registry
_GATES: dict[str, Any] = {}


def register(name: str) -> Callable[[type[_T]], type[_T]]:
    """Decorator to register a built-in QA gate."""

    def decorator(cls: type[_T]) -> type[_T]:
        _GATES[name] = cls
        return cls

    return decorator


def run_qa_gate(
    check: QACheck,
    project_root: Path,
    module_name: str = "",
    task_output: str = "",
    custom_gates: list[CustomGateConfig] | None = None,
    dispatcher_config: DispatcherConfig | None = None,
    qa_module: ModuleConfig | None = None,
    module_path: Path | None = None,
) -> QAResult:
    """Execute a QA gate check.

    Resolution order:
    1. Custom gates from config (command-based)
    2. Built-in registered gates
    3. Unknown → fail

    Args:
        module_path: Resolved filesystem path for the module. When provided,
            used instead of ``project_root / module_name`` so that modules
            whose name differs from their directory (e.g. ``path: ./``) work
            correctly.
    """
    resolved = str(module_path) if module_path else None

    # 1. Check config-defined custom gates
    if custom_gates:
        for cg in custom_gates:
            if cg.name == check.gate:
                return _run_custom_command_gate(
                    cg, check.params, project_root, module_name, resolved
                )

    # 2. Check built-in registry
    gate_cls = _GATES.get(check.gate)
    if gate_cls is not None:
        gate = gate_cls()
        return gate.check(
            params=check.params,
            project_root=project_root,
            module_name=module_name,
            task_output=task_output,
            dispatcher_config=dispatcher_config,
            qa_module=qa_module,
            module_path=resolved,
        )

    # 3. Unknown gate
    return QAResult(
        gate=check.gate,
        passed=False,
        output=f"Unknown QA gate: {check.gate}",
    )


def _validate_path_for_substitution(path: str) -> bool:
    """Validate a path is safe for use in command substitution."""
    resolved = Path(path).resolve()
    return _SAFE_PATH_RE.match(str(resolved)) is not None


def _run_custom_command_gate(
    gate_def: CustomGateConfig,
    params: dict[str, Any],
    project_root: Path,
    module_name: str,
    resolved_module_path: str | None = None,
) -> QAResult:
    """Run a config-defined command gate."""
    if resolved_module_path:
        module_path = resolved_module_path
    else:
        module_path = str(project_root / module_name) if module_name else str(project_root)

    # SECURITY: validate module_path before substitution to prevent injection
    if not _validate_path_for_substitution(module_path):
        return QAResult(
            gate=gate_def.name,
            passed=False,
            output=f"Unsafe module path rejected: {module_path!r}",
            details={"error": "path_validation_failed"},
        )

    # Use str.replace() instead of str.format() to prevent attribute access
    command_str = gate_def.command.replace("{module_path}", module_path)
    cwd = gate_def.cwd.replace("{module_path}", module_path)

    changed_files_cache: list[str] | None = None
    if gate_def.diff_only and "{changed_files}" in command_str:
        from .command_check import _get_changed_files

        changed_files_cache = _get_changed_files(project_root, resolved_module_path)
        if not changed_files_cache:
            return QAResult(
                gate=gate_def.name,
                passed=True,
                output="No changed files to check (diff_only mode)",
            )
        command_str = command_str.replace("{changed_files}", " ".join(changed_files_cache))

    # Use shlex.split + shell=False instead of shell=True
    try:
        cmd_args = shlex.split(command_str)
    except ValueError as exc:
        return QAResult(
            gate=gate_def.name,
            passed=False,
            output=f"Failed to parse command: {exc}",
            details={"command": command_str, "error": str(exc)},
        )

    try:
        proc = subprocess.run(
            cmd_args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=gate_def.timeout,
        )
    except subprocess.TimeoutExpired:
        return QAResult(
            gate=gate_def.name,
            passed=False,
            output=f"Command timed out after {gate_def.timeout}s",
            details={"command": command_str, "timeout": True},
        )
    except OSError as exc:
        return QAResult(
            gate=gate_def.name,
            passed=False,
            output=f"Failed to run command: {exc}",
            details={"command": command_str, "cwd": cwd, "error": str(exc)},
        )

    passed = proc.returncode == 0
    output = proc.stdout[-5000:] if len(proc.stdout) > 5000 else proc.stdout
    if proc.stderr:
        output += "\n--- stderr ---\n" + proc.stderr[-2000:]

    # Diff-aware retryability for custom gates (same logic as CommandCheckGate)
    retryable = True
    changed_files: list[str] | None = changed_files_cache
    if not passed:
        from .command_check import _check_retryable, _get_changed_files

        if changed_files is None:
            try:
                changed_files = _get_changed_files(project_root, resolved_module_path)
            except Exception:
                changed_files = None
        retryable = _check_retryable(project_root, Path(cwd), resolved_module_path, output)

    details: dict[str, Any] = {"exit_code": proc.returncode, "command": command_str}
    if changed_files is not None:
        details["changed_files"] = changed_files

    return QAResult(
        gate=gate_def.name,
        passed=passed,
        output=output,
        details=details,
        retryable=retryable,
    )


# Import built-in gates to trigger registration
from . import ci_check as _ci_check_mod  # noqa: E402, F401
from . import command_check as _cmd_check_mod  # noqa: E402, F401
from . import agent_check as _agent_check_mod  # noqa: E402, F401
from . import structural_check as _struct_check_mod  # noqa: E402, F401
from . import layer_check as _layer_check_mod  # noqa: E402, F401
