"""Helper utilities for the task scheduler."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .models import QACheck, TaskItem


@dataclass
class ExecutionProgress:
    """Tracks overall execution progress."""

    total_tasks: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    in_progress: int = 0
    total_dispatches: int = 0
    start_time: float = field(default_factory=time.monotonic)

    @property
    def pending(self) -> int:
        return self.total_tasks - self.completed - self.failed - self.skipped - self.in_progress

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time if self.start_time else 0.0


def _check_delivery(project_root: Path, branch_name: str) -> tuple[bool, str]:
    """Check if a branch exists and has new commits since the fork point.

    Uses `git merge-base` to find the correct fork point, avoiding false
    negatives when HEAD has advanced past the branch point.

    Returns (ok, message). ok is True if branch has commits; False is a warning
    (not a hard failure — the agent may have committed to a different branch).
    """
    try:
        # Check branch exists
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if not result.stdout.strip():
            # Also check remote branches
            result = subprocess.run(
                ["git", "branch", "-r", "--list", f"*/{branch_name}"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if not result.stdout.strip():
                return False, f"Branch {branch_name} not found (local or remote)"

        # Find fork point via merge-base
        merge_result = subprocess.run(
            ["git", "merge-base", "HEAD", branch_name],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if merge_result.returncode != 0:
            # Fallback: branches may be unrelated; count all commits on branch
            merge_base = ""
        else:
            merge_base = merge_result.stdout.strip()

        # Count commits since fork point
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
    except Exception as e:
        return False, f"Delivery check error: {e}"


def inject_qa_gates(
    task: TaskItem,
    config: object,
    progress: Callable[[str], None],
) -> None:
    """Auto-inject standard QA gates (structural, layer, command) into a task."""
    # Auto-inject structural check gate
    has_structural = any(q.gate == "structural_check" for q in task.qa_checks)
    if not has_structural:
        sc = config.qa_gates.structural
        task.qa_checks.append(
            QACheck(
                gate="structural_check",
                params={
                    "max_file_lines": sc.max_file_lines,
                    "enforce_module_boundary": sc.enforce_module_boundary,
                    "sensitive_patterns": sc.sensitive_patterns,
                },
            )
        )
        progress("    [dim]Auto-injected QA: structural_check[/]")

    # Auto-inject layer_check gate
    has_layer = any(q.gate == "layer_check" for q in task.qa_checks)
    arch_path = config.root / "ARCHITECTURE.md"
    if not has_layer and config.qa_gates.layer_check.enabled and arch_path.exists():
        task.qa_checks.append(
            QACheck(
                gate="layer_check",
                params={
                    "enabled": config.qa_gates.layer_check.enabled,
                    "unknown_file_policy": config.qa_gates.layer_check.unknown_file_policy,
                },
            )
        )
        progress("    [dim]Auto-injected QA: layer_check[/]")

    # Auto-inject custom command gates if task has no command_check gates
    has_command = any(q.gate == "command_check" for q in task.qa_checks)
    if not has_command and config.qa_gates.custom:
        for gate in config.qa_gates.custom:
            if gate.modules and task.module not in gate.modules:
                continue
            task.qa_checks.append(
                QACheck(
                    gate="command_check",
                    params={"command": gate.command, "cwd": gate.cwd},
                )
            )
            progress(f"    [dim]Auto-injected QA: command_check ({gate.command})[/]")
