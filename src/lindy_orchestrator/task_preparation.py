"""QA gate injection, delivery checks, and CI param auto-fill."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable

from .config import OrchestratorConfig
from .models import QACheck, TaskSpec

log = logging.getLogger(__name__)

__all__ = [
    "_autofill_ci_params",
    "_check_delivery",
    "prepare_qa_checks",
]


def _check_delivery(project_root: Path, branch_name: str) -> tuple[bool, str]:
    """Check whether the task branch exists and has commits."""
    try:
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if not result.stdout.strip():
            result = subprocess.run(
                ["git", "branch", "-r", "--list", f"*/{branch_name}"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if not result.stdout.strip():
                return False, f"Branch {branch_name} not found (local or remote)"

        merge_result = subprocess.run(
            ["git", "merge-base", "HEAD", branch_name],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        merge_base = merge_result.stdout.strip() if merge_result.returncode == 0 else ""
        rev_range = f"{merge_base}..{branch_name}" if merge_base else branch_name
        result = subprocess.run(
            ["git", "rev-list", "--count", rev_range],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        count = int(result.stdout.strip()) if result.stdout.strip() else 0
        if count == 0:
            return False, f"Branch {branch_name} exists but has no new commits"
        return True, f"Branch {branch_name}: {count} new commit(s)"
    except Exception as exc:
        return False, f"Delivery check error: {exc}"


def prepare_qa_checks(
    task: TaskSpec,
    config: OrchestratorConfig,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Populate default QA gates for a task."""
    if task.skip_qa:
        if progress:
            progress("    [dim]QA gates skipped (skip_qa=true)[/]")
        return

    skip_gates = set(task.skip_gates)
    has_structural = any(q.gate == "structural_check" for q in task.qa_checks)
    if not has_structural and "structural_check" not in skip_gates:
        sc = config.qa_gates.structural
        task.qa_checks.append(
            QACheck(
                gate="structural_check",
                params={
                    "enforce_module_boundary": sc.enforce_module_boundary,
                    "sensitive_patterns": sc.sensitive_patterns,
                },
            )
        )
        if progress:
            progress("    [dim]Auto-injected QA: structural_check[/]")

    existing_commands = {
        q.params.get("command") for q in task.qa_checks if q.gate == "command_check"
    }
    for gate in config.qa_gates.custom:
        if gate.command in existing_commands:
            continue
        if gate.modules and task.module not in gate.modules:
            continue
        if gate.name in skip_gates:
            continue
        params: dict[str, object] = {"command": gate.command, "cwd": gate.cwd}
        if not gate.required:
            params["required"] = False
        if gate.diff_only:
            params["diff_only"] = True
            if "{changed_files}" not in gate.command:
                params["required"] = False
        task.qa_checks.append(QACheck(gate="command_check", params=params))
        if progress:
            progress(f"    [dim]Auto-injected QA: command_check ({gate.command})[/]")



def _autofill_ci_params(
    qa_checks: list[QACheck],
    branch_name: str,
    config: OrchestratorConfig,
    module_name: str,
) -> None:
    """Auto-fill ci_check branch/repo params if missing."""
    for qa in qa_checks:
        if qa.gate != "ci_check":
            continue
        if not qa.params.get("branch"):
            qa.params["branch"] = branch_name
        if not qa.params.get("repo"):
            try:
                mod_cfg = config.get_module(module_name)
            except ValueError:
                continue
            if mod_cfg.repo:
                qa.params["repo"] = mod_cfg.repo
