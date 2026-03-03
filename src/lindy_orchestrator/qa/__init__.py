"""QA gate registry and runner.

Built-in gates: ci_check, command_check, agent_check.
Users can also define custom command-based gates in orchestrator.yaml.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..config import CustomGateConfig, DispatcherConfig, ModuleConfig
from ..models import QACheck, QAResult

# Gate registry
_GATES: dict[str, Any] = {}


def register(name: str):
    """Decorator to register a built-in QA gate."""

    def decorator(cls):
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
) -> QAResult:
    """Execute a QA gate check.

    Resolution order:
    1. Custom gates from config (command-based)
    2. Built-in registered gates
    3. Unknown → fail
    """
    # 1. Check config-defined custom gates
    if custom_gates:
        for cg in custom_gates:
            if cg.name == check.gate:
                return _run_custom_command_gate(cg, check.params, project_root, module_name)

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
        )

    # 3. Unknown gate
    return QAResult(
        gate=check.gate,
        passed=False,
        output=f"Unknown QA gate: {check.gate}",
    )


def _run_custom_command_gate(
    gate_def: CustomGateConfig,
    params: dict[str, Any],
    project_root: Path,
    module_name: str,
) -> QAResult:
    """Run a config-defined command gate."""
    module_path = str(project_root / module_name) if module_name else str(project_root)
    command = gate_def.command.format(module_path=module_path)
    cwd = gate_def.cwd.format(module_path=module_path)

    try:
        proc = subprocess.run(
            command,
            shell=True,
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
            details={"command": command, "timeout": True},
        )

    passed = proc.returncode == 0
    output = proc.stdout[-5000:] if len(proc.stdout) > 5000 else proc.stdout
    if proc.stderr:
        output += "\n--- stderr ---\n" + proc.stderr[-2000:]

    return QAResult(
        gate=gate_def.name,
        passed=passed,
        output=output,
        details={"exit_code": proc.returncode, "command": command},
    )


# Import built-in gates to trigger registration
from . import ci_check as _ci_check_mod  # noqa: E402, F401
from . import command_check as _cmd_check_mod  # noqa: E402, F401
from . import agent_check as _agent_check_mod  # noqa: E402, F401
from . import structural_check as _struct_check_mod  # noqa: E402, F401
